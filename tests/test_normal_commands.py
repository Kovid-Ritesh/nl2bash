import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nl2sh.cli import handle_command
from nl2sh.safety import SafetyError, assess
from nl2sh.undo import Journal


class NormalCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.work = self.base / 'work'
        self.work.mkdir()
        self.journal = Journal(self.base / 'state', self.work)

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def automatic(self, command, *, dry_run=False):
        with contextlib.redirect_stdout(io.StringIO()) as output, \
             patch('nl2sh.cli.confirm', side_effect=AssertionError('Normal command requested permission')):
            _, code = handle_command(command, self.work, self.work, self.journal, dry_run=dry_run)
        self.assertEqual(code, 0, output.getvalue())
        self.assertNotIn('DANGEROUS', output.getvalue())
        return output.getvalue()

    def test_new_file_runs_without_permission_and_is_undoable(self):
        self.automatic('touch notes.txt')
        self.assertTrue((self.work / 'notes.txt').is_file())
        self.journal.undo()
        self.assertFalse((self.work / 'notes.txt').exists())

    def test_existing_touch_preserves_contents_and_restores_timestamp(self):
        path = self.work / 'notes.txt'
        path.write_text('existing contents')
        os.utime(path, ns=(1000000000, 1000000000))
        before = path.stat().st_mtime_ns
        self.automatic('touch notes.txt')
        self.assertEqual(path.read_text(), 'existing contents')
        self.journal.undo()
        self.assertEqual(path.read_text(), 'existing contents')
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_nested_directories_run_automatically_and_are_undoable(self):
        self.automatic('mkdir -p project/src')
        self.assertTrue((self.work / 'project/src').is_dir())
        self.journal.undo()
        self.assertFalse((self.work / 'project').exists())

    def test_mkdir_p_existing_directory_is_a_successful_noop(self):
        (self.work / 'project').mkdir()
        self.automatic('mkdir -p project')
        self.assertEqual(self.journal.records(), [])

    def test_new_file_copy_runs_automatically_and_is_undoable(self):
        (self.work / 'source').write_text('payload')
        self.automatic('cp source destination')
        self.assertEqual((self.work / 'destination').read_text(), 'payload')
        self.journal.undo()
        self.assertFalse((self.work / 'destination').exists())
        self.assertEqual((self.work / 'source').read_text(), 'payload')

    def test_new_directory_copy_runs_automatically_and_is_undoable(self):
        (self.work / 'source').mkdir()
        (self.work / 'source/file').write_text('payload')
        self.automatic('cp -r source destination')
        self.assertEqual((self.work / 'destination/file').read_text(), 'payload')
        self.journal.undo()
        self.assertFalse((self.work / 'destination').exists())

    def test_copy_into_existing_directory_checks_actual_destination(self):
        (self.work / 'source').write_text('payload')
        (self.work / 'destination').mkdir()
        self.automatic('cp source destination')
        self.assertEqual((self.work / 'destination/source').read_text(), 'payload')
        with contextlib.redirect_stdout(io.StringIO()), patch('nl2sh.cli.confirm', return_value=False) as confirm:
            handle_command('cp source destination', self.work, self.work, self.journal)
        confirm.assert_called_once()

    def test_overwrites_deletions_and_moves_still_require_review(self):
        (self.work / 'source').write_text('source')
        (self.work / 'destination').write_text('destination')
        for command in ('cp source destination', 'rm source', 'mv source moved'):
            with self.subTest(command=command), contextlib.redirect_stdout(io.StringIO()), \
                 patch('nl2sh.cli.confirm', return_value=False) as confirm:
                handle_command(command, self.work, self.work, self.journal)
                confirm.assert_called_once()
            self.assertEqual((self.work / 'source').read_text(), 'source')
            self.assertEqual((self.work / 'destination').read_text(), 'destination')

    def test_automatic_creation_respects_dry_run(self):
        self.automatic('touch notes.txt', dry_run=True)
        self.automatic('mkdir project', dry_run=True)
        self.assertFalse((self.work / 'notes.txt').exists())
        self.assertFalse((self.work / 'project').exists())
        self.assertEqual(self.journal.records(), [])

    def test_copy_refuses_destination_created_after_planning(self):
        (self.work / 'source').write_text('source')
        original = self.journal.create
        def create_then_race(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.work / 'destination').write_text('new work from another process')
            return result
        with patch.object(self.journal, 'create', side_effect=create_then_race), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(FileExistsError):
            self.automatic('cp source destination')
        self.assertEqual((self.work / 'destination').read_text(), 'new work from another process')
        self.assertEqual(self.journal.records()[-1][1]['status'], 'rolled-back')

    def test_mkdir_failure_does_not_remove_concurrently_created_directory(self):
        original = self.journal.create
        def create_then_race(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.work / 'two').mkdir()
            (self.work / 'two/keep').write_text('new work')
            return result
        with patch.object(self.journal, 'create', side_effect=create_then_race), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(FileExistsError):
            self.automatic('mkdir one two')
        self.assertFalse((self.work / 'one').exists())
        self.assertEqual((self.work / 'two/keep').read_text(), 'new work')

    def test_failed_copy_rolls_back_only_its_new_destination(self):
        (self.work / 'source').write_text('payload')
        def fail_after_partial_write(source, destination):
            destination.write(b'partial')
            raise OSError('simulated copy failure')
        with patch('nl2sh.undo.shutil.copyfileobj', side_effect=fail_after_partial_write), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(OSError):
            self.automatic('cp source destination')
        self.assertFalse((self.work / 'destination').exists())
        self.assertEqual((self.work / 'source').read_text(), 'payload')

    def test_failed_automatic_creation_preserves_new_work_in_created_directory(self):
        original = Path.mkdir
        def fail_second_directory(path, *args, **kwargs):
            if path == self.work / 'two':
                (self.work / 'one/foreign').write_text('new work from another process')
                raise OSError('simulated creation failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', fail_second_directory), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(SafetyError, 'preserved'):
            self.automatic('mkdir one two')
        self.assertEqual((self.work / 'one/foreign').read_text(), 'new work from another process')
        self.assertEqual(self.journal.records()[-1][1]['status'], 'pending')

    def test_normal_display_and_search_commands_run_automatically(self):
        (self.work / 'sample.txt').write_text('hello\nworld\n')
        commands = ["echo 'hello; world'", r"printf '%s\n' hello", 'whoami', 'uname -a',
                    'date +%Y', "grep -n '^hello$' sample.txt", "grep -E 'hello|world' sample.txt"]
        for command in commands:
            with self.subTest(command=command):
                self.automatic(command)

    def test_real_shell_operators_and_clock_changes_still_require_review(self):
        for command in ('echo hello | cat', 'echo $HOME', 'date -s 2020-01-01'):
            with self.subTest(command=command):
                self.assertIn(assess(command, self.work, self.work).kind, ('shell', 'risk'))


if __name__ == '__main__':
    unittest.main()
