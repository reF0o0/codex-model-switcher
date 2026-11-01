"""Capture the app's four pages with synthetic data, without using real Codex data."""
import argparse
import json
import plistlib
import shutil
import uuid
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    app = args.app or repo/'dist/Codex 模型切换器-dev.app'
    output = args.output or Path(tempfile.mkdtemp(prefix='switcher-ui-'))
    output.mkdir(parents=True, exist_ok=True)
    # A unique bundle keeps the user's running app and saved window state separate.
    with tempfile.TemporaryDirectory(prefix='switcher-preview-',dir='/private/tmp') as temporary:
        copy=Path(temporary)/'Switcher Preview.app'
        shutil.copytree(app,copy)
        plist=copy/'Contents/Info.plist'
        info=plistlib.loads(plist.read_bytes())
        info['CFBundleIdentifier']='local.switcher.preview.'+uuid.uuid4().hex
        plist.write_bytes(plistlib.dumps(info))
        subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(copy)],check=True,capture_output=True)
        subprocess.run([str(copy/'Contents/MacOS/ProviderSwitcher'), '--preview', '--snapshot-dir', str(output)],
                       check=True, timeout=45)
    result = json.loads((output/'result.json').read_text())
    assert result['real_backend_used'] is False
    for appearance in ('light', 'dark'):
        for page in range(4):
            shot = output/f'{appearance}-{page}.png'
            assert shot.stat().st_size > 1000, shot
    print('UI snapshots: PASS (4 pages, light/dark, synthetic data only)')
    print(output)


if __name__ == '__main__':
    main()
