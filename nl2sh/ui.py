"""Small terminal UI with single-key confirmation and no runtime dependencies."""

import os
import select
import shutil
import sys
import termios
import textwrap
import tty

from .runner import display

# Extended color with a basic ANSI fallback for older terminals.
PALETTE = {
    "cyan": ("38;5;117", "96"),
    "violet": ("38;5;141", "95"),
    "green": ("38;5;114", "32"),
    "red": ("1;38;5;203", "1;31"),
    "yellow": ("38;5;221", "33"),
    "dim": ("38;5;245", "2"),
    "border": ("38;5;240", "2"),
}

ASCII_LOGO = (
    r" _   _ _     ____  ____    _    ____  _   _",
    r"| \ | | |   |___ \| __ )  / \  / ___|| | | |",
    r"|  \| | |     __) |  _ \ / _ \ \___ \| |_| |",
    r"| |\  | |___ / __/| |_) / ___ \ ___) |  _  |",
    r"|_| \_|_____|_____|____/_/   \_\____/|_| |_|",
)


class ExitRequested(Exception):
    """Leave the agent, including when /exit is entered at confirmation."""


def color(text, tone="cyan", stream=None):
    stream = stream or sys.stdout
    if stream.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb":
        extended = "256color" in os.environ.get("TERM", "") or os.environ.get("COLORTERM") in ("truecolor", "24bit")
        code = PALETTE[tone][0 if extended else 1]
        return f"\033[{code}m{text}\033[0m"
    return text


def unicode_terminal():
    try:
        "╭─╮│╰╯".encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def notice(message, tone="dim", stream=None):
    stream = stream or sys.stdout
    print(color(display(message), tone, stream), file=stream)


def panel(title, lines, tone="cyan"):
    title = display(title)
    if not sys.stdout.isatty():
        print(title)
        for line in lines:
            print(display(line))
        return
    width = max(12, min(shutil.get_terminal_size((88, 24)).columns - 2, 100))
    top_l, rule, top_r, vertical, bottom_l, bottom_r = "╭─╮│╰╯" if unicode_terminal() else "+-+|++"
    label = f" {title} "[:width - 4]
    print(color(top_l + rule, "border") + color(label, tone)
          + color(rule * (width - len(label) - 3) + top_r, "border"))
    for line in lines:
        for part in display(line).splitlines() or [""]:
            for wrapped in textwrap.wrap(part, width=width - 4, replace_whitespace=False) or [""]:
                body = wrapped.ljust(width - 4)
                if wrapped.lstrip().startswith("$ "):
                    body = color(body, "cyan")
                print(color(vertical, "border") + " " + body + " " + color(vertical, "border"))
    print(color(bottom_l + rule * (width - 2) + bottom_r, "border"))


def banner(workspace, dry_run):
    print()
    columns = shutil.get_terminal_size((88, 24)).columns
    if sys.stdout.isatty() and columns >= max(map(len, ASCII_LOGO)) + 4:
        for index, line in enumerate(ASCII_LOGO):
            print("  " + color(line, "violet" if index < 2 else "cyan"))
    else:
        notice("  [ nl2bash ]", "violet")
    for line in textwrap.wrap("NATURAL LANGUAGE -> BASH  /  local terminal agent", width=max(12, columns - 4)):
        notice("  " + line, "dim")
    print()
    panel("SESSION", [f"Workspace  {workspace}",
                      "Mode       Preview only" if dry_run else "Mode       Guarded execution",
                      "", "Enter executes  /  Esc cancels  /  Ctrl+C interrupts"], "violet")
    for line in textwrap.wrap("/undo   /history   /pwd   /help   /exit", width=max(12, columns - 4)):
        notice("  " + line, "dim")
    print()


def prompt(cwd):
    return f"\nnl2bash [{display(cwd.name or '/')}] > "


def confirm(message, input_fn=None):
    """Only a fresh Enter approves; Esc/EOF/Ctrl+C cancel. Restore terminal on every path."""
    if input_fn is not None:
        try:
            answer = input_fn(message)
        except (EOFError, KeyboardInterrupt):
            return False
        if answer.strip() == "/exit":
            raise ExitRequested
        return answer == ""
    if not sys.stdin.isatty():
        notice("Execution refused: confirmation requires an interactive terminal.", "yellow")
        return False
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd, termios.TCSANOW)
        # Discard queued input so a pasted prompt/newline cannot approve its own command.
        termios.tcflush(fd, termios.TCIFLUSH)
        print(color(message, "yellow"), end="", flush=True)
        typed = bytearray()
        while True:
            char = os.read(fd, 1)
            if not char or char in (b"\x03", b"\x04"):
                return False
            if char == b"\x1b":
                # Consume the rest of arrow/paste escape sequences before returning to input().
                if select.select([fd], [], [], 0.05)[0]:
                    os.read(fd, 4096)
                return False
            if char in (b"\r", b"\n"):
                if bytes(typed) == b"/exit":
                    raise ExitRequested
                return not typed
            if char in (b"\x7f", b"\x08"):
                if typed:
                    typed.pop()
                continue
            # Other text never approves execution. /exit is handled once Enter is pressed.
            if len(typed) < 32:
                typed.extend(char)
    except (EOFError, KeyboardInterrupt):
        return False
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, original)
        print()
