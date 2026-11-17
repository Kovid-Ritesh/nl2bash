"""Install a user launcher; invoked by install-windows.ps1 or directly in WSL."""

import os
import shlex
from pathlib import Path

MARKER = "# NL2BASH launcher"


def install():
    launch = Path(__file__).resolve().parent.parent / "launch.py"
    target = Path.home() / ".local/bin/nl2bash"
    if target.is_symlink() or (target.exists() and MARKER not in target.read_text()):
        raise SystemExit(f"Refusing to replace an unrelated launcher: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"#!/bin/sh\n{MARKER}\nexec /usr/bin/python3 {shlex.quote(str(launch))} \"$@\"\n")
    target.chmod(0o755)
    print(f"Installed WSL command: {target}")
    if str(target.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Add {target.parent} to your shell PATH to use nl2bash.")


if __name__ == "__main__":
    install()
