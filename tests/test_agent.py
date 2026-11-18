import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nl2sh.cli import handle_command, main
from nl2sh.client import endpoint_root, generate_command
from nl2sh.runner import display, run_command
from nl2sh.safety import SafetyError, assess, parse_command
from nl2sh.undo import Journal, plan_change


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.workspace = self.base / 'work'
        self.workspace.mkdir()
        self.journal = Journal(self.base / 'state', self.workspace)

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def apply(self, argv):
        plan = plan_change(argv, self.workspace, self.workspace)
        self.journal.apply(plan)

    def test_control_characters_and_plain_system_commands_blocked(self):
        commands = ['ls\nrm a', '/usr/bin/ls', 'sudo ls', 'bash -c id',
                    'dd if=a of=b', 'mkfs.ext4 disk', 'env python3 code.py', 'ls\x1b[2J']
        for command in commands:
            with self.subTest(command=command), self.assertRaises(SafetyError):
                parse_command(command)

    def test_shell_syntax_requires_review_and_preserves_original_expression(self):
        commands = ['ls; rm a', 'ls && whoami', 'cat a | sh', 'echo $(id)',
                    'echo `id`', 'ls > a', 'echo $HOME',
                    'ls *.py', 'rm *.txt', 'NAME=value printf hello', 'ls ~/projects']
        for command in commands:
            with self.subTest(command=command):
                decision = assess(command, self.workspace, self.workspace)
                self.assertEqual(decision.kind, 'shell')
                self.assertEqual(decision.argv[-1], command)
                self.assertIn('undo are unavailable', decision.description)

    def test_cancelled_shell_expression_never_executes_or_records_action(self):
        with contextlib.redirect_stdout(io.StringIO()), patch('nl2sh.cli.run_command') as run:
            handle_command('printf payload > result.txt', self.workspace, self.workspace,
                           self.journal, input_fn=lambda _: '\x1b')
            run.assert_not_called()
        self.assertFalse((self.workspace / 'result.txt').exists())
        self.assertEqual(self.journal.records(), [])

    def test_approved_shell_redirection_executes_and_has_no_undo(self):
        with contextlib.redirect_stdout(io.StringIO()):
            _, code = handle_command('printf payload > result.txt', self.workspace, self.workspace,
                                     self.journal, input_fn=lambda _: '')
        self.assertEqual(code, 0)
        self.assertEqual((self.workspace / 'result.txt').read_text(), 'payload')
        self.assertFalse(self.journal.records()[-1][1]['undoable'])
        with self.assertRaisesRegex(SafetyError, 'external program'):
            self.journal.undo()

    def test_pipeline_substitution_escapes_and_pipefail(self):
        commands = [(r"printf 'a\nb\n' | grep b", 'b', 0),
                    ('printf "%s" "$(printf nested)"', 'nested', 0),
                    ('false | true', '', 1)]
        for command, expected, expected_code in commands:
            with self.subTest(command=command), contextlib.redirect_stdout(io.StringIO()) as output:
                _, code = handle_command(command, self.workspace, self.workspace, self.journal, input_fn=lambda _: '')
            self.assertEqual(code, expected_code)
            if expected:
                self.assertEqual(output.getvalue().splitlines()[-1], expected)

    def test_shell_does_not_receive_api_key(self):
        command = 'printf "%s" "${NL2SH_API_KEY-unset}"'
        startup = self.workspace / 'startup.sh'
        startup.write_text('printf unwanted > startup-ran')
        with patch.dict(os.environ, {'NL2SH_API_KEY': 'never-forward-this', 'BASH_ENV': str(startup)}), contextlib.redirect_stdout(io.StringIO()) as output:
            handle_command(command, self.workspace, self.workspace, self.journal, input_fn=lambda _: '')
        self.assertEqual(output.getvalue().splitlines()[-1], 'unset')
        self.assertFalse((self.workspace / 'startup-ran').exists())

    def test_shell_preview_and_noninteractive_input_never_execute(self):
        for preview in (True, False):
            with self.subTest(preview=preview), contextlib.redirect_stdout(io.StringIO()), \
                 patch('sys.stdin.isatty', return_value=False), patch('nl2sh.cli.run_command') as run:
                handle_command('printf no > result.txt', self.workspace, self.workspace, self.journal, dry_run=preview)
                run.assert_not_called()
        self.assertFalse((self.workspace / 'result.txt').exists())

    def test_automatic_read_allowlist_and_unsafe_flags(self):
        (self.workspace / 'sample.txt').write_text('hello\n')
        for command in ['pwd', 'ls -lah', 'cat sample.txt', 'head -n 3 sample.txt',
                        'tail -n 2 sample.txt', 'wc -l sample.txt', 'stat sample.txt',
                        'du -sh .', 'find . -type f -name "*.py"']:
            with self.subTest(command=command):
                self.assertEqual(assess(command, self.workspace, self.workspace).kind, 'read')
        for command in ['find . -delete', 'find . -exec rm a', 'sort -o sample.txt sample.txt',
                        'ls --ignore=foo', 'cat', 'tail -f sample.txt', 'rg --pre python3 a',
                        'cat /etc/passwd', 'ls ../', 'python3 script.py']:
            with self.subTest(command=command):
                self.assertEqual(assess(command, self.workspace, self.workspace).kind, 'risk')

    def test_protected_and_symlink_paths_not_automatic(self):
        (self.workspace / '.env').write_text('secret')
        outside = self.base / 'outside'
        outside.write_text('outside')
        (self.workspace / 'link').symlink_to(outside)
        for command in ['cat .env', 'cat link', 'find . -type f']:
            self.assertEqual(assess(command, self.workspace, self.workspace).kind, 'risk')
        for argv in [['rm', '.env'], ['rm', 'link'], ['rm', '.'], ['rm', '../outside']]:
            with self.assertRaises(SafetyError):
                plan_change(argv, self.workspace, self.workspace)

    def test_cancelled_removal_changes_nothing(self):
        path = self.workspace / 'keep'
        path.write_text('keep')
        with contextlib.redirect_stdout(io.StringIO()):
            handle_command('rm keep', self.workspace, self.workspace, self.journal, input_fn=lambda _: '\x1b')
        self.assertEqual(path.read_text(), 'keep')
        self.assertEqual(self.journal.records(), [])

    def test_noninteractive_confirmation_fails_closed(self):
        path = self.workspace / 'keep'
        path.write_text('keep')
        with patch('sys.stdin.isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()):
            handle_command('rm keep', self.workspace, self.workspace, self.journal)
        self.assertEqual(path.read_text(), 'keep')

    def test_dry_run_never_executes_or_creates_backups(self):
        path = self.workspace / 'keep'
        path.write_text('keep')
        with contextlib.redirect_stdout(io.StringIO()), patch('nl2sh.cli.run_command') as run:
            handle_command('rm keep', self.workspace, self.workspace, self.journal, dry_run=True)
            handle_command('ls', self.workspace, self.workspace, self.journal, dry_run=True)
            run.assert_not_called()
        self.assertEqual(path.read_text(), 'keep')
        self.assertEqual(self.journal.records(), [])

    def test_delete_and_undo_file_and_directory(self):
        (self.workspace / 'a').write_text('original')
        self.apply(['rm', 'a'])
        self.assertFalse((self.workspace / 'a').exists())
        self.assertEqual(self.journal.undo(), 'rm')
        self.assertEqual((self.workspace / 'a').read_text(), 'original')
        (self.workspace / 'folder').mkdir()
        (self.workspace / 'folder' / 'x').write_text('nested')
        self.apply(['rm', '-r', 'folder'])
        self.journal.undo()
        self.assertEqual((self.workspace / 'folder' / 'x').read_text(), 'nested')

    def test_move_overwrite_undo_restores_both_files(self):
        (self.workspace / 'src').write_text('source')
        (self.workspace / 'dst').write_text('destination')
        self.apply(['mv', 'src', 'dst'])
        self.assertFalse((self.workspace / 'src').exists())
        self.assertEqual((self.workspace / 'dst').read_text(), 'source')
        self.journal.undo()
        self.assertEqual((self.workspace / 'src').read_text(), 'source')
        self.assertEqual((self.workspace / 'dst').read_text(), 'destination')

    def test_copy_undo_preserves_existing_destination(self):
        (self.workspace / 'src').write_text('source')
        (self.workspace / 'dst').write_text('destination')
        self.apply(['cp', 'src', 'dst'])
        self.assertEqual((self.workspace / 'dst').read_text(), 'source')
        self.journal.undo()
        self.assertEqual((self.workspace / 'dst').read_text(), 'destination')
        self.assertEqual((self.workspace / 'src').read_text(), 'source')

    def test_copy_directory_and_undo(self):
        (self.workspace / 'src').mkdir()
        (self.workspace / 'src' / 'x').write_text('x')
        self.apply(['cp', '-r', 'src', 'dst'])
        self.assertEqual((self.workspace / 'dst' / 'x').read_text(), 'x')
        self.journal.undo()
        self.assertFalse((self.workspace / 'dst').exists())

    def test_multiple_undos_with_new_directories_and_touch(self):
        self.apply(['mkdir', '-p', 'a/b'])
        self.apply(['touch', 'a/b/new.txt'])
        self.journal.undo()
        self.assertFalse((self.workspace / 'a/b/new.txt').exists())
        self.journal.undo()
        self.assertFalse((self.workspace / 'a').exists())

    def test_undo_refuses_to_overwrite_newer_changes(self):
        (self.workspace / 'src').write_text('source')
        self.apply(['cp', 'src', 'dst'])
        (self.workspace / 'dst').write_text('newer work')
        with self.assertRaisesRegex(SafetyError, 'changed since'):
            self.journal.undo()
        self.assertEqual((self.workspace / 'dst').read_text(), 'newer work')

    def test_undo_refuses_recreated_deleted_file(self):
        (self.workspace / 'src').write_text('source')
        self.apply(['rm', 'src'])
        (self.workspace / 'src').write_text('new source')
        with self.assertRaises(SafetyError):
            self.journal.undo()
        self.assertEqual((self.workspace / 'src').read_text(), 'new source')

    def test_undo_refuses_corrupted_backup(self):
        (self.workspace / 'src').write_text('source')
        self.apply(['rm', 'src'])
        record_path, _ = self.journal.records()[-1]
        (record_path.parent / '0').write_text('corruption')
        with self.assertRaisesRegex(SafetyError, 'integrity'):
            self.journal.undo()
        self.assertFalse((self.workspace / 'src').exists())

    def test_changes_during_confirmation_are_rejected(self):
        target = self.workspace / 'a'
        target.write_text('original')
        def approve(_):
            target.write_text('new work during review')
            return ''
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SafetyError):
            handle_command('rm a', self.workspace, self.workspace, self.journal, input_fn=approve)
        self.assertEqual(target.read_text(), 'new work during review')

    def test_managed_operation_rolls_back_after_partial_failure(self):
        (self.workspace / 'src').write_text('source')
        (self.workspace / 'dst').write_text('destination')
        with patch('nl2sh.undo.shutil.move', side_effect=OSError('simulated move failure')):
            with self.assertRaises(OSError):
                self.apply(['mv', 'src', 'dst'])
        self.assertEqual((self.workspace / 'src').read_text(), 'source')
        self.assertEqual((self.workspace / 'dst').read_text(), 'destination')
        self.assertEqual(self.journal.records()[-1][1]['status'], 'rolled-back')

    def test_mutations_reject_overlap_symlinks_hardlinks_and_special_files(self):
        (self.workspace / 'a').mkdir()
        (self.workspace / 'a' / 'file').write_text('x')
        for argv in [['rm', '-r', 'a', 'a/file'], ['mv', 'a', 'a/new'], ['cp', '-r', 'a', '.']]:
            with self.subTest(argv=argv), self.assertRaises(SafetyError):
                plan_change(argv, self.workspace, self.workspace)
        (self.workspace / 'link').symlink_to(self.workspace / 'a')
        with self.assertRaises(SafetyError):
            plan_change(['touch', 'link/new'], self.workspace, self.workspace)
        os.link(self.workspace / 'a/file', self.workspace / 'hardlink')
        with self.assertRaises(SafetyError):
            plan_change(['touch', 'hardlink'], self.workspace, self.workspace)
        os.mkfifo(self.workspace / 'fifo')
        with self.assertRaises(SafetyError):
            plan_change(['rm', 'fifo'], self.workspace, self.workspace)

    def test_external_action_is_an_undo_barrier(self):
        self.apply(['touch', 'file'])
        self.journal.mark_external('python3')
        with self.assertRaisesRegex(SafetyError, 'external program'):
            self.journal.undo()

    def test_pending_journal_blocks_more_changes(self):
        self.journal.create('rm', True)
        with self.assertRaisesRegex(SafetyError, 'interrupted'):
            self.apply(['touch', 'file'])

    def test_journal_lock(self):
        with self.assertRaises(SafetyError):
            Journal(self.base / 'state', self.workspace)

    def test_output_escape(self):
        self.assertEqual(display('a\x1b[2J\r\u202eb'), 'a\\u001b[2J\\u000d\\u202eb')

    def test_read_command_runs_without_confirmation(self):
        (self.workspace / 'a').write_text('hello\n')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            _, code = handle_command('cat a', self.workspace, self.workspace, self.journal,
                                     input_fn=lambda _: self.fail('Unexpected confirmation'))
        self.assertEqual(code, 0)
        self.assertIn('hello', output.getvalue())

    def test_secret_environment_not_forwarded(self):
        (self.workspace / 'probe.py').write_text('import os; print(os.environ.get("NL2SH_API_KEY", "absent"))')
        with patch.dict(os.environ, {'NL2SH_API_KEY': 'never-forward-this'}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_command(['python3', 'probe.py'], self.workspace), 0)
        self.assertEqual(output.getvalue().strip(), 'absent')

    def test_timeout_and_output_limit(self):
        (self.workspace / 'sleep.py').write_text('import time; time.sleep(30)')
        (self.workspace / 'output.py').write_text('print("x" * 10000)')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_command(['python3', 'sleep.py'], self.workspace, timeout=0.1), 124)
            self.assertEqual(run_command(['python3', 'output.py'], self.workspace, output_limit=100), 124)


class ClientTests(unittest.TestCase):
    def test_endpoint_validation(self):
        self.assertEqual(endpoint_root('https://example.test/v1/chat/completions'), 'https://example.test')
        for url in ['http://example.test', 'https://user:pass@example.test', 'https://example.test/?key=secret', 'file:///a']:
            with self.assertRaises(ValueError):
                endpoint_root(url)

    def test_model_response_validation_and_auth_header(self):
        class Response(io.BytesIO):
            pass
        reply = {'choices': [{'finish_reason': 'stop', 'message': {'content': '```bash\nls -la\n```'}}]}
        opener = unittest.mock.Mock()
        opener.open.return_value = Response(json.dumps(reply).encode())
        with patch('nl2sh.client.urllib.request.build_opener', return_value=opener):
            self.assertEqual(generate_command('https://example.test', 'test-key', 'List files'), 'ls -la')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.headers['Authorization'], 'Bearer test-key')
        self.assertNotIn('test-key', request.full_url)
        reply['choices'][0]['finish_reason'] = 'length'
        opener.open.return_value = Response(json.dumps(reply).encode())
        with patch('nl2sh.client.urllib.request.build_opener', return_value=opener), self.assertRaisesRegex(ValueError, 'truncated'):
            generate_command('https://example.test', 'test-key', 'List files')

    def test_one_shot_help_does_not_contact_endpoint(self):
        with tempfile.TemporaryDirectory() as directory, patch('os.geteuid', return_value=1000), contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            (root / 'work').mkdir()
            with patch('nl2sh.cli.generate_command') as generate:
                self.assertEqual(main(['--workspace', str(root / 'work'), '--state-dir', str(root / 'state'), '/help']), 0)
                generate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
