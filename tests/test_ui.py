import contextlib
import io
import os
import pty
import select
import subprocess
import sys
import termios
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nl2sh import ui
from nl2sh.cli import handle_command, main
from nl2sh.undo import Journal
import tempfile


class UITests(unittest.TestCase):
    def test_enter_confirms_escape_and_other_text_cancel(self):
        self.assertTrue(ui.confirm('Confirm: ', lambda _: ''))
        for answer in ('\x1b', 'execute', 'EXECUTE', ' ', 'no'):
            with self.subTest(answer=answer):
                self.assertFalse(ui.confirm('Confirm: ', lambda _: answer))
        with self.assertRaises(ui.ExitRequested):
            ui.confirm('Confirm: ', lambda _: '/exit')

    def test_noninteractive_confirmation_fails_closed(self):
        with patch('sys.stdin.isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(ui.confirm('Confirm: '))

    def test_plain_output_escapes_terminal_controls(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            ui.panel('COMMAND', ['hello\x1b[2J', 'next'])
        self.assertNotIn('\x1b', output.getvalue())
        self.assertIn('hello\\u001b[2J', output.getvalue())

    def test_no_color_respected_in_tty(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        terminal = Terminal()
        with patch.dict(os.environ, {'NO_COLOR': '1', 'TERM': 'xterm'}):
            self.assertEqual(ui.color('hello', stream=terminal), 'hello')

    def test_enter_runs_managed_change_and_undo_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / 'work'
            workspace.mkdir()
            (workspace / 'a').write_text('original')
            journal = Journal(root / 'state', workspace)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    handle_command('rm a', workspace, workspace, journal, input_fn=lambda _: '')
                self.assertFalse((workspace / 'a').exists())
                journal.undo()
                self.assertEqual((workspace / 'a').read_text(), 'original')
            finally:
                journal.close()

    def test_exit_at_prompt_does_not_contact_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'work').mkdir()
            with patch('os.geteuid', return_value=1000), patch('builtins.input', return_value='/exit'), \
                 patch('nl2sh.cli.generate_command') as generate, contextlib.redirect_stdout(io.StringIO()) as output:
                result = main(['--workspace', str(root / 'work'), '--state-dir', str(root / 'state')])
            self.assertEqual(result, 0)
            generate.assert_not_called()
            self.assertIn('Session closed', output.getvalue())

    def read_until(self, fd, expected, process):
        output = b''
        deadline = time.monotonic() + 5
        while expected not in output:
            self.assertLess(time.monotonic(), deadline, output)
            if select.select([fd], [], [], 0.1)[0]:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    self.fail(f'PTY closed before expected output: {output!r}')
                self.assertTrue(chunk, output)
                output += chunk
            elif process.poll() is not None:
                self.fail(f'Confirmation process exited early: {output!r}')
        return output

    def check_pty_confirmation(self, keys, expected, queued=None):
        master, slave = pty.openpty()
        before = termios.tcgetattr(slave)
        code = (
            'from nl2sh.ui import confirm, ExitRequested\n'
            'try:\n'
            ' print("RESULT", confirm("Confirm: "), flush=True)\n'
            'except ExitRequested:\n'
            ' print("RESULT EXIT", flush=True)\n'
        )
        if queued is not None:
            code = 'import time\nprint("READY", flush=True)\ntime.sleep(0.2)\n' + code
        process = subprocess.Popen([sys.executable, '-c', code], stdin=slave, stdout=slave, stderr=slave,
                                   cwd=Path(__file__).resolve().parent.parent)
        try:
            if queued is not None:
                self.read_until(master, b'READY', process)
                os.write(master, queued)
            self.read_until(master, b'Confirm: ', process)
            os.write(master, keys)
            self.read_until(master, expected, process)
            self.assertEqual(process.wait(timeout=5), 0)
            self.assertEqual(termios.tcgetattr(slave), before, 'Terminal settings were not restored')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
            os.close(slave)

    def test_real_terminal_enter_executes(self):
        self.check_pty_confirmation(b'\r', b'RESULT True')

    def test_real_terminal_escape_cancels_without_enter(self):
        self.check_pty_confirmation(b'\x1b', b'RESULT False')

    def test_real_terminal_arrow_sequence_cancels(self):
        self.check_pty_confirmation(b'\x1b[A', b'RESULT False')

    def test_real_terminal_exit_request(self):
        self.check_pty_confirmation(b'/exit\r', b'RESULT EXIT')

    def test_real_terminal_other_text_does_not_approve(self):
        self.check_pty_confirmation(b'execute\r', b'RESULT False')

    def test_queued_enter_cannot_approve_a_new_confirmation(self):
        self.check_pty_confirmation(b'\x1b', b'RESULT False', queued=b'\r')


if __name__ == '__main__':
    unittest.main()
