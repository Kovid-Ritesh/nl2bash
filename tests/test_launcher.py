import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from launch import load_saved_settings


class LauncherTests(unittest.TestCase):
    def test_settings_loads_only_known_names_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text("export NL2SH_API_KEY='test key'\nNL2SH_URL=https://example.test\nPATH=/malicious\n")
            with patch.dict(os.environ, {'PATH': '/original'}, clear=True):
                load_saved_settings(path)
                self.assertEqual(os.environ['NL2SH_API_KEY'], 'test key')
                self.assertEqual(os.environ['NL2SH_URL'], 'https://example.test')
                self.assertEqual(os.environ['PATH'], '/original')

    def test_explicit_environment_wins_over_saved_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('NL2SH_API_KEY=saved\n')
            with patch.dict(os.environ, {'NL2SH_API_KEY': 'explicit'}, clear=True):
                load_saved_settings(path)
                self.assertEqual(os.environ['NL2SH_API_KEY'], 'explicit')

    def test_missing_settings_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            load_saved_settings(Path(directory) / 'missing')

    def test_malformed_settings_do_not_echo_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('NL2SH_API_KEY=private-value extra-token\n')
            with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError) as error:
                load_saved_settings(path)
            self.assertNotIn('private-value', str(error.exception))

    @unittest.skipIf(os.geteuid() == 0, 'The agent intentionally refuses root execution')
    def test_absolute_launcher_preserves_callers_directory_with_spaces(self):
        launch = Path(__file__).resolve().parent.parent / 'launch.py'
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / 'project with spaces'
            workspace.mkdir()
            result = subprocess.run(
                [sys.executable, str(launch), '--state-dir', str(base / 'state'), '/pwd'],
                cwd=workspace, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(workspace))


if __name__ == '__main__':
    unittest.main()
