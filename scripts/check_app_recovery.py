"""Run the production AppModel against a synthetic home; no real keychain, model or windows."""
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo/'src'), str(repo/'tests')]
from test_switcher import CodexHomeFixture

with tempfile.TemporaryDirectory(prefix='switcher-recovery-check-', dir='/private/tmp') as temp:
    fixture = CodexHomeFixture(); fixture.setUp()
    try:
        fixture.app.backup()
        fixture.app.config.unlink()
        (fixture.home/'desktop-state.txt').write_text('stopped')
        bundle = Path(temp)/'Recovery Checks.app'
        resources = bundle/'Contents/Resources'
        backend = resources/'backend'
        shutil.copytree(repo/'src', backend, ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        (resources/'fixture-home.txt').write_text(str(fixture.home))
        cli = backend/'cli.py'
        cli.rename(backend/'_production_cli.py')
        cli.write_text('''from pathlib import Path
import runpy, sys
import switcher.core as core
backend = Path(__file__).resolve().parent
fixture = Path((backend.parent/'fixture-home.txt').read_text())
assert fixture.name == 'codex' and fixture.parent.name.startswith('tmp')
class FakeKeychain:
    def get(self): return 'fixture-key'
Original = core.Switcher
class FixtureSwitcher(Original):
    def __init__(self, *args, **kwargs):
        super().__init__(fixture, keychain=FakeKeychain())
core.Switcher = FixtureSwitcher
core.desktop_state = lambda: (fixture/'desktop-state.txt').read_text()
sys.argv[1:1] = ['--home',str(fixture)]
runpy.run_path(str(backend/'_production_cli.py'), run_name='__main__')
''')
        (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier':'io.github.ref0o0.switcher-recovery-check',
            'CFBundleExecutable':'ProviderSwitcher', 'CFBundlePackageType':'APPL'}))
        executable = bundle/'Contents/MacOS/ProviderSwitcher'
        executable.parent.mkdir()
        subprocess.run(['swiftc','-parse-as-library','-D','SWITCHER_MODEL_CHECKS',
                        str(repo/'app/App.swift'),str(repo/'tests/RecoveryModelChecks.swift'),
                        '-o',str(executable)],check=True)
        subprocess.run([str(executable)],check=True)
    finally:fixture.tearDown()
