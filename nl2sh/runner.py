import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

from .safety import SafetyError
from .hooks import pre_execute


def display(text):
    """Escape terminal-control characters from model text, filenames, and subprocess output."""
    return "".join(c if (c in "\n\t" or (c.isprintable() and c != "\x1b"))
                   else f"\\u{ord(c):04x}" for c in text)


def trusted_executable(name):
    for directory in ("/usr/bin", "/bin"):
        path = Path(directory) / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise SafetyError(f"Executable is not installed in /usr/bin or /bin: {name}")


def run_command(argv, cwd, timeout=60, output_limit=1024 * 1024):
    pre_execute(argv, cwd)
    executable = trusted_executable(argv[0])
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "LANG": "C.UTF-8",
           "LC_ALL": "C.UTF-8", "TERM": "dumb"}
    process = subprocess.Popen(
        [executable, *argv[1:]], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    chunks = []
    size = 0
    deadline = time.monotonic() + timeout
    limit = None
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                limit = "Command stopped: time limit reached."
                break
            for key, _ in selector.select(timeout=min(0.1, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), min(65536, output_limit - size + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                chunks.append(chunk[:output_limit - size])
                size += len(chunk)
                if size > output_limit:
                    limit = f"Command stopped: output exceeded {output_limit} bytes."
                    break
            if limit:
                break
        # A process can close stdout before it exits; keep the same execution deadline.
        if not limit:
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                limit = "Command stopped: time limit reached."
    finally:
        # Kill the whole process group, including descendants that outlive the main process.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        selector.close()
        process.stdout.close()
    text = display(b"".join(chunks).decode(errors="replace"))
    if text:
        print(text, end="" if text.endswith("\n") else "\n")
    if limit:
        print(limit)
        return 124
    return process.returncode
