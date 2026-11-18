import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import install
from scripts.build_installer import build

ROOT = Path(__file__).resolve().parent.parent


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_payload_includes_both_notebooks_but_no_private_files(self):
        paths = {path.relative_to(ROOT).as_posix() for path in install.payload_files(ROOT)}
        self.assertIn('notebooks/nl2sh_kaggle_ngrok.ipynb', paths)
        self.assertIn('notebooks/nl2sh_inference_test.ipynb', paths)
        self.assertIn('nl2sh/hooks.py', paths)
        self.assertNotIn('.env', paths)
        self.assertFalse(any('.git/' in path or '__pycache__' in path or path.startswith('dist/') for path in paths))

    def test_existing_unrelated_directory_is_not_overwritten(self):
        target = self.base / 'app'
        target.mkdir()
        (target / 'keep').write_text('user work')
        with self.assertRaises(ValueError):
            install.install_payload(ROOT, target)
        self.assertEqual((target / 'keep').read_text(), 'user work')

    def test_update_preserves_configuration_and_backs_up_changed_payload(self):
        target = install.install_payload(ROOT, self.base / 'app')
        (target / '.env').write_text('NL2SH_API_KEY=private-test-value\n')
        (target / 'README.md').write_text('user edited documentation')
        install.install_payload(ROOT, target)
        self.assertEqual((target / '.env').read_text(), 'NL2SH_API_KEY=private-test-value\n')
        backups = list((target / '.backups').glob('*/README.md'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), 'user edited documentation')

    def test_installation_subdirectory_symlink_is_rejected(self):
        target = self.base / 'app'
        target.mkdir()
        (target / install.MARKER).write_text('{"application":"nl2bash"}')
        elsewhere = self.base / 'elsewhere'
        elsewhere.mkdir()
        (target / 'nl2sh').symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(ValueError):
            install.install_payload(ROOT, target)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_linux_paths_and_profiles_are_automatic_idempotent_and_preserved(self):
        home = self.base / 'home with spaces'
        home.mkdir()
        (home / '.bashrc').write_text('# existing setup\nexport USER_SETTING=keep\n')
        app = install.install_payload(ROOT, home / '.local/share/nl2bash')
        with contextlib.redirect_stdout(io.StringIO()):
            launcher = install.install_linux_launcher(app, home)
            install.install_linux_launcher(app, home)
        self.assertTrue(launcher.stat().st_mode & stat.S_IXUSR)
        self.assertEqual((home / '.bashrc').read_text().count(install.PATH_MARKER), 1)
        self.assertIn('USER_SETTING=keep', (home / '.bashrc').read_text())
        self.assertEqual((home / '.bashrc.nl2bash.bak').read_text(), '# existing setup\nexport USER_SETTING=keep\n')
        result = subprocess.run(['/bin/bash', '--noprofile', '--rcfile', str(home / '.bashrc'), '-ic', 'command -v nl2bash'],
                                env={**os.environ, 'HOME': str(home), 'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(launcher))

    def test_linux_installer_does_not_replace_an_unrelated_command(self):
        home = self.base / 'home'
        target = home / '.local/bin/nl2bash'
        target.parent.mkdir(parents=True)
        target.write_text('unrelated user program')
        with self.assertRaises(ValueError):
            install.install_linux_launcher(ROOT, home)
        self.assertEqual(target.read_text(), 'unrelated user program')

    def test_endpoint_updates_preserve_keys_and_never_print_them(self):
        app = self.base / 'app'
        app.mkdir()
        secret = 'test-private-key-' * 3
        path = app / '.env'
        path.write_text(f'NL2SH_URL=https://old.example\nNL2SH_API_KEY={secret}\n# custom setting\n')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            install.configure(app, 'https://new.example/v1')
        self.assertIn('NL2SH_URL=https://new.example', path.read_text())
        self.assertIn(secret, path.read_text())
        self.assertIn('# custom setting', path.read_text())
        self.assertNotIn(secret, output.getvalue())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_hidden_key_setup(self):
        app = self.base / 'app'
        app.mkdir()
        secret = 'test-private-key-' * 3
        with patch('getpass.getpass', return_value=secret), contextlib.redirect_stdout(io.StringIO()) as output:
            install.configure(app, 'https://endpoint.example', interactive=True)
        self.assertIn(secret, (app / '.env').read_text())
        self.assertNotIn(secret, output.getvalue())

    def test_windows_path_preserves_existing_values_and_does_not_duplicate(self):
        entry = r'C:\Users\Some Person\.local\bin'
        existing = r'C:\Tools;C:\Windows'
        updated = install.windows_path_value(existing, entry)
        self.assertEqual(updated, existing + ';' + entry)
        self.assertEqual(install.windows_path_value(updated, entry.upper() + '\\'), updated)
        with patch.dict(os.environ, {'USERPROFILE': r'C:\Users\Some Person'}):
            original = r'C:\Tools;%USERPROFILE%\.local\bin'
            self.assertEqual(install.windows_path_value(original, entry), original)

    def test_windows_registry_uses_only_user_path_and_preserves_value_type(self):
        context = Mock()
        context.__enter__ = Mock(return_value='user-key')
        context.__exit__ = Mock(return_value=False)
        registry = types.SimpleNamespace(
            HKEY_CURRENT_USER='CURRENT_USER', KEY_READ=1, KEY_SET_VALUE=2, REG_EXPAND_SZ=2,
            CreateKeyEx=Mock(return_value=context), QueryValueEx=Mock(return_value=(r'%SystemRoot%\bin', 2)),
            SetValueEx=Mock(),
        )
        windows = types.SimpleNamespace(user32=types.SimpleNamespace(SendMessageTimeoutW=Mock(return_value=1)))
        with patch.dict(sys.modules, {'winreg': registry}), patch.object(install.ctypes, 'windll', windows, create=True):
            install.update_windows_path(Path(r'C:\Users\Some Person\.local\bin'))
        self.assertEqual(registry.CreateKeyEx.call_args.args[0], 'CURRENT_USER')
        self.assertEqual(registry.SetValueEx.call_args.args[3], 2)
        self.assertTrue(registry.SetValueEx.call_args.args[4].startswith(r'%SystemRoot%\bin;'))

    def test_windows_launcher_preserves_cwd_and_uses_runtime_unicode_path(self):
        text = install.windows_launcher({'distribution': 'Ubuntu', 'user': 'tester', 'python': '/usr/bin/python3'})
        text.encode('ascii')
        self.assertIn('--cd "%CD%"', text)
        self.assertIn('"%SystemRoot%\\System32\\wsl.exe"', text)
        self.assertIn('%LOCALAPPDATA%\\nl2bash\\app\\launch.py', text)
        self.assertIn('%*', text)
        self.assertNotIn('Deepak', text)

    def test_wsl_preflight_rejects_old_python_and_root(self):
        for version, uid in (([3, 10], 1000), ([3, 12], 0)):
            result = types.SimpleNamespace(returncode=0, stdout=json.dumps({'version': version, 'uid': uid}))
            with self.subTest(version=version, uid=uid), patch('shutil.which', return_value='wsl.exe'), \
                 patch('subprocess.run', return_value=result), self.assertRaises(ValueError):
                install.check_wsl()

    def test_archive_traversal_and_symlinks_are_rejected(self):
        for name, symlink in (('../escape', False), ('link', True)):
            archive = self.base / 'malicious.pyz'
            with zipfile.ZipFile(archive, 'w') as bundle:
                info = zipfile.ZipInfo(name)
                if symlink:
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                bundle.writestr(info, 'outside')
            with patch.object(sys, 'argv', [str(archive)]), self.assertRaises(ValueError):
                with install.source_payload():
                    self.fail('Unsafe archive was accepted')
        self.assertFalse((self.base / 'escape').exists())

    @unittest.skipIf(os.geteuid() == 0, 'Installer intentionally refuses root')
    def test_single_file_installer_installs_and_runs_after_bundle_is_removed(self):
        archive = self.base / 'nl2bash-installer.pyz'
        with contextlib.redirect_stdout(io.StringIO()):
            build(archive)
        home = self.base / 'fresh user with spaces'
        home.mkdir()
        env = {**os.environ, 'HOME': str(home)}
        result = subprocess.run([sys.executable, str(archive), '--endpoint', 'https://endpoint.example'],
                                env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        archive.unlink()
        app = home / '.local/share/nl2bash'
        self.assertTrue((app / 'notebooks/nl2sh_kaggle_ngrok.ipynb').is_file())
        self.assertTrue((app / 'notebooks/nl2sh_inference_test.ipynb').is_file())
        work = self.base / 'working folder'
        work.mkdir()
        launch = subprocess.run([str(home / '.local/bin/nl2bash'), '--state-dir', str(self.base / 'state'), '/pwd'],
                                cwd=work, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(launch.returncode, 0, launch.stderr)
        self.assertEqual(launch.stdout.strip(), str(work))


if __name__ == '__main__':
    unittest.main()
