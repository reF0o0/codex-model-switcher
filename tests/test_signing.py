import importlib.util
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1]/'scripts/signing.py'


class SigningTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('switcher_signing',SCRIPT)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_signing_temporarily_adds_its_keychain_and_restores_search_list(self):
        signing=self.module()
        root=Path('/private/tmp/synthetic signing')
        original=['/Users/test/Library/Keychains/login.keychain-db']
        calls=[]
        def run(args):
            args=[str(a) for a in args]; calls.append(args)
            return '"'+original[0]+'"\n' if args==['/usr/bin/security','list-keychains','-d','user'] else ''
        requirement='identifier "'+signing.BUNDLE_ID+'" and anchor H"'+'a'*40+'"'
        with patch.object(signing,'load_identity',return_value={'identity':'A'*40}), \
                patch.object(signing,'identity_ready',return_value=True), \
                patch.object(signing,'run',side_effect=run), \
                patch.object(signing,'requirement_for',return_value=requirement), patch.object(signing,'pin_requirement'):
            signing.sign_app(root,Path('/private/tmp/synthetic.app'))
        add=['/usr/bin/security','list-keychains','-d','user','-s',*original,str(root/'identity.keychain-db')]
        restore=['/usr/bin/security','list-keychains','-d','user','-s',*original]
        self.assertIn(add,calls)
        self.assertLess(calls.index(add),next(i for i,c in enumerate(calls) if c[0]=='/usr/bin/codesign'))
        self.assertIn(restore,calls)
        self.assertGreater(calls.index(restore),next(i for i,c in enumerate(calls) if c[0]=='/usr/bin/codesign'))

    def test_failed_signing_also_restores_original_keychain_search_list(self):
        signing=self.module();calls=[]
        original='/Users/test/Library/Keychains/login.keychain-db'
        def run(args):
            args=[str(a) for a in args];calls.append(args)
            if args[0]=='/usr/bin/codesign':raise ValueError('simulated signing failure')
            return '"'+original+'"\n' if args==['/usr/bin/security','list-keychains','-d','user'] else ''
        with patch.object(signing,'load_identity',return_value={'identity':'A'*40}), \
                patch.object(signing,'identity_ready',return_value=True), patch.object(signing,'run',side_effect=run):
            with self.assertRaises(ValueError):signing.sign_app(Path('/private/tmp/signing'),Path('/private/tmp/app'))
        self.assertEqual(calls[-1],['/usr/bin/security','list-keychains','-d','user','-s',original])

    def test_ad_hoc_requirement_is_refused_for_releases(self):
        signing=self.module()
        with self.assertRaisesRegex(ValueError,'临时签名'):
            signing.validate_requirement('cdhash H"abcdef0123"')

    def test_certificate_requirement_is_accepted(self):
        signing=self.module()
        requirement='identifier "io.github.ref0o0.codex-model-switcher" and anchor H"'+'a'*40+'"'
        self.assertEqual(signing.validate_requirement(requirement),requirement)

    def test_identifier_only_requirement_is_not_a_stable_identity(self):
        signing=self.module()
        with self.assertRaises(ValueError):
            signing.validate_requirement('identifier "io.github.ref0o0.codex-model-switcher"')

    def test_changed_identity_is_not_silently_accepted(self):
        signing=self.module()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'requirement.txt').write_text('original certificate identity')
            with self.assertRaisesRegex(ValueError,'身份'):
                signing.pin_requirement(root,'another certificate identity')
            self.assertEqual((root/'requirement.txt').read_text(),'original certificate identity')

    def test_partial_identity_is_not_recreated(self):
        signing=self.module()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'signing';root.mkdir();(root/'identity.cer').write_bytes(b'original')
            with patch.object(signing,'run') as runner:
                with self.assertRaisesRegex(ValueError,'保留'):
                    signing.prepare_identity(root)
                runner.assert_not_called()
            self.assertEqual((root/'identity.cer').read_bytes(),b'original')

    def test_real_ad_hoc_application_fails_requirement_validation(self):
        signing=self.module()
        if not Path('/usr/bin/codesign').is_file():self.skipTest('requires macOS')
        with tempfile.TemporaryDirectory() as temp:
            app=Path(temp)/'synthetic-tool'
            shutil.copyfile('/usr/bin/true',app)
            subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(app)],capture_output=True,check=True)
            with self.assertRaisesRegex(ValueError,'临时签名'):
                signing.validate_requirement(signing.requirement_for(app))

    def test_same_certificate_identity_survives_an_update(self):
        signing=self.module()
        req='identifier "io.github.ref0o0.codex-model-switcher" and anchor H"'+'a'*40+'"'
        signing.validate_update(req,req)

    def test_legacy_migration_is_explicit_and_cannot_allow_another_certificate(self):
        signing=self.module()
        new='identifier "io.github.ref0o0.codex-model-switcher" and anchor H"'+'a'*40+'"'
        other=new.replace('a'*40,'b'*40)
        with self.assertRaisesRegex(ValueError,'首次迁移'):
            signing.validate_update('cdhash H"old"',new)
        signing.validate_update('cdhash H"old"',new,migrate_legacy=True)
        with self.assertRaisesRegex(ValueError,'不同'):
            signing.validate_update(new,other,migrate_legacy=True)
