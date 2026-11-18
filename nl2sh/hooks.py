"""Pre-execution guardrails inspired by https://cupofabhi.web.app/setup.md.

This is command inspection, not an OS sandbox or an arbitrary-code analyser.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from .safety import SafetyError, protected


class GuardrailBlocked(SafetyError):
    def __init__(self, rule, message):
        self.rule = rule
        super().__init__(message)


READERS = {"cat", "less", "more", "head", "tail", "cp", "mv", "scp", "curl", "base64",
           "grep", "rg", "sed", "awk", "sort", "uniq", "wc", "tee", "tar", "zip", "rsync",
           "rm", "touch", "chmod", "chown"}
SHELLS = {"bash", "sh", "dash", "zsh", "ksh"}
WRAPPERS = {"command", "builtin", "exec", "nohup", "env", "sudo", "doas", "timeout", "xargs"}
PREFIXES = {"then", "do", "else", "if", "while", "until", "!", "{"}
OPERATORS = set(";&|()<>")


def deny(rule, message):
    raise GuardrailBlocked(rule, message)


def secret_path(value, cwd):
    if "=" in value:
        value = value.split("=", 1)[1]
    if not value or value.startswith("-"):
        return False
    value = value.removeprefix("@").removeprefix("file://")
    path = Path(value).expanduser()
    if protected(path):
        return True
    try:
        resolved = (path if path.is_absolute() else cwd / path).resolve()
    except (OSError, RuntimeError):
        deny("uninspectable-path", "Cannot safely inspect a file path; no command was executed.")
    return protected(resolved)


def current_branch(cwd, global_args):
    # Query branch metadata only; never run the requested push or use a shell.
    try:
        result = subprocess.run(
            ["/usr/bin/git", *global_args, "symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=cwd, env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "LANG": "C.UTF-8"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=2, text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def check_git(args, cwd):
    global_args = []
    i = 0
    while i < len(args) and args[i].startswith("-"):
        flag = args[i]
        if flag in ("-C", "--git-dir", "--work-tree", "-c"):
            if i + 1 >= len(args):
                return
            if flag != "-c":
                global_args.extend(args[i:i + 2])
            i += 2
        else:
            if flag.startswith(("-C", "--git-dir=", "--work-tree=")):
                global_args.append(flag)
            i += 1
    if i >= len(args):
        return
    operation, options = args[i], args[i + 1:]
    if operation == "push":
        if any(a.startswith(("--force", "--mirror", "--delete", "--prune"))
               or (a.startswith("-") and not a.startswith("--") and bool(set(a[1:]) & set("fd")))
               or a.startswith(("+", ":")) for a in options):
            deny("git-force-push", "Force pushes and remote-branch deletion can destroy shared history.")
        if "--no-verify" in options:
            deny("git-skip-hooks", "Skipping Git safety checks is blocked.")
        if any(a.rsplit(":", 1)[-1] in {"main", "master", "refs/heads/main", "refs/heads/master"}
               for a in options):
            deny("git-primary-push", "Direct pushes to main/master are blocked. Use a feature branch and pull request.")
        if current_branch(cwd, global_args) in ("main", "master"):
            deny("git-primary-push", "Pushing while on main/master is blocked. Switch to a feature branch first.")
    elif operation == "reset" and any(a.startswith("--hard") for a in options):
        deny("git-hard-reset", "git reset --hard discards working-tree changes.")
    elif operation == "clean" and any(a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a[1:]) for a in options):
        deny("git-clean", "Forced git clean permanently deletes untracked files.")
    elif operation in ("checkout", "restore") and "." in options:
        deny("git-discard", "Restoring the entire working tree can discard unsaved work.")
    elif operation == "branch" and any(a.startswith("-") and not a.startswith("--") and "D" in a[1:] for a in options):
        deny("git-force-branch-delete", "Force-deleting a branch can lose unmerged work.")
    elif operation == "commit" and any(a in ("--no-verify", "-n") for a in options):
        deny("git-skip-hooks", "Skipping Git commit safety checks is blocked.")


def scan_argv(tokens, cwd, depth):
    if depth > 8:
        deny("nested-execution", "Nested command execution is too deep to inspect safely.")
    tokens = list(tokens)
    while tokens and (tokens[0] in PREFIXES or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0])):
        tokens.pop(0)
    if not tokens:
        return
    name = Path(tokens[0]).name
    args = tokens[1:]
    if name in WRAPPERS:
        # Recognise the protected programs behind common execution wrappers.
        for i, token in enumerate(args):
            if Path(token).name in READERS | SHELLS | {"git", "psql", "mysql", "mariadb", "sqlite3"}:
                scan_argv(args[i:], cwd, depth + 1)
                break
        return
    if name in SHELLS:
        for i, token in enumerate(args):
            if token.startswith("-") and not token.startswith("--") and "c" in token[1:] and i + 1 < len(args):
                scan_text(args[i + 1], cwd, depth + 1)
                return
    if name == "git":
        check_git(args, cwd)
    if name == "find":
        if "-delete" in args:
            deny("find-delete", "find -delete removes matching files without managed backups.")
        for i, arg in enumerate(args):
            if arg in ("-exec", "-execdir", "-ok", "-okdir"):
                scan_argv(args[i + 1:], cwd, depth + 1)
    if name == "rm":
        flags = []
        for arg in args:
            if arg == "--":
                break
            if arg.startswith("-"):
                flags.append(arg)
        recursive = "--recursive" in flags or any(not a.startswith("--") and bool(set(a[1:]) & set("rR")) for a in flags)
        force = "--force" in flags or any(not a.startswith("--") and "f" in a[1:] for a in flags)
        if recursive and force:
            deny("recursive-force-delete", "Forced recursive deletion (rm -rf) is blocked. Use an explicit managed rm -r for backed-up removal.")
    if name in READERS and any(secret_path(arg, cwd) for arg in args):
        deny("secret-file", "Reading, copying, or modifying credential/configuration files is blocked. Templates such as .env.example are allowed.")
    if name in {"psql", "mysql", "mariadb", "sqlite3", "drop", "truncate"}:
        if re.search(r"\b(?:drop\s+(?:table|database|schema)|truncate\s+table)\b", " ".join(tokens), re.I):
            deny("database-destruction", "Dropping or emptying database tables/schemas is blocked.")


def scan_text(command, cwd, depth=0):
    if depth > 8 or len(command) > 8192:
        deny("invalid-command", "The command exceeds the hook's inspection limit.")
    if any(ord(c) < 32 or ord(c) == 127 for c in command):
        deny("invalid-command", "Control characters and multiline commands are blocked.")
    # Inspect substitutions as well as shell command lists; do not execute expansions.
    for match in re.finditer(r"\$\(([^()]*)\)|`([^`]*)`", command):
        scan_text(next(group for group in match.groups() if group is not None), cwd, depth + 1)
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        deny("invalid-command", "Unbalanced command quoting cannot be inspected safely.")
    segment = []
    for i, token in enumerate(tokens):
        if token and set(token) <= OPERATORS:
            scan_argv(segment, cwd, depth)
            segment = []
            if ("<" in token or ">" in token) and i + 1 < len(tokens) and secret_path(tokens[i + 1], cwd):
                deny("secret-file", "Redirecting input/output through credential files is blocked.")
        else:
            segment.append(token)
    scan_argv(segment, cwd, depth)


def pre_execute(command, cwd):
    """Raise GuardrailBlocked before a protected command can execute; allow otherwise."""
    cwd = Path(cwd).resolve()
    if isinstance(command, str):
        scan_text(command, cwd)
    else:
        if not command or any(not isinstance(arg, str) for arg in command):
            deny("invalid-command", "Provide a nonempty command argument list.")
        scan_argv(command, cwd, 0)


def main():
    """JSON hook protocol: exit 0 allows, exit 2 blocks. Requested commands never run."""
    try:
        raw = sys.stdin.read(65537)
        if len(raw) > 65536:
            raise ValueError
        data = json.loads(raw)
        command = data["tool_input"]["command"]
        cwd = data.get("cwd") or os.getcwd()
        if not isinstance(command, str) or not command.strip() or not isinstance(cwd, str):
            raise ValueError
        pre_execute(command, cwd)
    except GuardrailBlocked as error:
        print(f"Blocked by hook [{error.rule}]: {error}", file=sys.stderr)
        return 2
    except (ValueError, KeyError, TypeError, OSError, RuntimeError):
        print("Blocked by hook [invalid-input]: provide a valid command event.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
