import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


class SafetyError(ValueError):
    pass


@dataclass
class Decision:
    argv: list[str]
    kind: str
    description: str


PROTECTED = {".git", ".ssh", ".aws", ".gnupg", ".kube", ".config", ".netrc",
             ".modal.toml", ".proxy-token.json", "credentials", "id_rsa", "id_ed25519"}
MANAGED = {"rm", "mv", "cp", "mkdir", "touch"}
BLOCKED = {"sudo", "su", "doas", "dd", "shred", "wipefs", "fdisk", "parted", "mount",
           "umount", "reboot", "shutdown", "poweroff", "halt", "chroot", "eval", "exec",
           "source", "bash", "sh", "zsh", "fish", "dash", "nohup", "xargs", "env"}
SHORT_FLAGS = {"ls": set("alhRrtSd1F"), "cat": set("nbEsTv"), "head": set("qv"),
               "tail": set("qv"), "wc": set("clwmL"), "stat": set(), "du": set("shak"),
               "pwd": set("LP")}
LONG_FLAGS = {"ls": {"--all", "--almost-all", "--human-readable", "--directory"},
              "cat": {"--number", "--number-nonblank"}, "head": {"--quiet", "--verbose"},
              "tail": {"--quiet", "--verbose"}, "wc": {"--lines", "--words", "--bytes", "--chars"},
              "stat": set(), "du": {"--summarize", "--human-readable"}, "pwd": set()}


def protected(path):
    return any(part.lower() in PROTECTED or (part.lower().startswith(".env") and part.lower() != ".env.example")
               or part.lower().endswith((".pem", ".key")) for part in path.parts)


def workspace_path(value, cwd, workspace, *, mutation=False):
    raw = Path(value).expanduser()
    lexical = Path(os.path.abspath(raw if raw.is_absolute() else cwd / raw))
    resolved = lexical.resolve()
    if not lexical.is_relative_to(workspace) or not resolved.is_relative_to(workspace):
        raise SafetyError("Path leaves the selected workspace.")
    if protected(lexical.relative_to(workspace)) or protected(resolved.relative_to(workspace)):
        raise SafetyError("Credential, configuration, and Git metadata paths are protected.")
    if mutation:
        if resolved == workspace:
            raise SafetyError("The workspace root cannot be modified or deleted.")
        for parent in (lexical, *lexical.parents):
            if parent.is_symlink():
                raise SafetyError("Managed changes cannot traverse symbolic links.")
            if parent == workspace:
                break
    return resolved


def validate_source(command):
    if not command or len(command) > 8192:
        raise SafetyError("Model must return one command, at most 8192 characters.")
    if any(ord(c) < 32 or ord(c) == 127 for c in command):
        raise SafetyError("Control characters and multiline commands are blocked.")


def parse_command(command):
    validate_source(command)
    try:
        argv = shlex.split(command)
    except ValueError as error:
        raise SafetyError(f"Invalid command quoting: {error}") from None
    if not argv or not re.fullmatch(r"[A-Za-z0-9_.+-]+", argv[0]):
        raise SafetyError("Use a plain executable name; executable paths and assignments are blocked.")
    if argv[0] in BLOCKED or argv[0].startswith(("mkfs", "fsck")):
        raise SafetyError("Privilege escalation, shell launchers, and disk/system destruction tools are blocked.")
    return argv


def no_link_tree(path, protect=True):
    """Do not auto-read a directory tree containing links or protected files."""
    if not path.exists():
        return
    if path.is_symlink():
        raise SafetyError("Recursive commands cannot follow symbolic links.")
    if path.is_dir():
        visited = 0
        for root, dirs, files in os.walk(path, followlinks=False):
            visited += len(dirs) + len(files)
            if visited > 10000:
                raise SafetyError("Recursive automatic inspection exceeds 10000 entries.")
            for name in dirs + files:
                child = Path(root) / name
                if child.is_symlink() or (protect and protected(Path(name))):
                    raise SafetyError("Recursive command reaches a symbolic link or protected path.")


def read_only(argv, cwd, workspace):
    name = argv[0]
    if name in ("echo", "printf", "whoami", "id", "uname"):
        return
    if name == "date":
        if len(argv) == 1 or (len(argv) == 2 and argv[1].startswith("+")):
            return
        raise SafetyError("Only date display/formatting is automatic; clock changes require review.")
    if name == "grep":
        i = 1
        pattern = recursive = operands_only = False
        while i < len(argv):
            arg = argv[i]
            if arg == "--":
                operands_only = True
                i += 1
                break
            if arg == "-e":
                if i + 1 >= len(argv):
                    raise SafetyError("grep -e requires a pattern.")
                pattern = True
                i += 2
            elif arg.startswith("-"):
                if len(arg) < 2 or arg.startswith("--") or not set(arg[1:]) <= set("nivrlcwFEhHsoxLaIR"):
                    raise SafetyError("This grep option requires review.")
                recursive |= bool(set(arg[1:]) & set("rR"))
                i += 1
            else:
                break
        if not pattern:
            if i >= len(argv):
                raise SafetyError("grep requires a pattern and file paths.")
            i += 1
        files = argv[i:]
        if not files or any(arg.startswith("-") and not operands_only for arg in files):
            raise SafetyError("grep requires explicit file paths, with options before the pattern.")
        for arg in files:
            path = workspace_path(arg, cwd, workspace)
            if recursive:
                no_link_tree(path)
            elif not path.is_file():
                raise SafetyError("Automatic grep requires regular files.")
        return
    if name == "find":
        if len(argv) < 2 or argv[1].startswith("-"):
            raise SafetyError("Use find with an explicit workspace path.")
        path = workspace_path(argv[1], cwd, workspace)
        no_link_tree(path, protect=False)
        i = 2
        while i < len(argv):
            flag = argv[i]
            if flag in ("-name", "-iname", "-type", "-maxdepth", "-mindepth", "-size"):
                if i + 1 >= len(argv):
                    raise SafetyError("Missing find argument.")
                value = argv[i + 1]
                if flag == "-type" and value not in ("f", "d"):
                    raise SafetyError("Only find types f and d are allowed automatically.")
                if flag in ("-maxdepth", "-mindepth") and not re.fullmatch(r"\d{1,3}", value):
                    raise SafetyError("Invalid find depth.")
                if flag == "-size" and not re.fullmatch(r"[+-]?\d{1,12}[ckMG]?", value):
                    raise SafetyError("Invalid find size.")
                i += 2
            elif flag == "-print":
                i += 1
            else:
                raise SafetyError("This find option is not allowed automatically (including -exec and -delete).")
        return
    if name not in SHORT_FLAGS:
        raise SafetyError("This program is outside the automatic read-only allowlist.")
    paths = []
    operands = False
    i = 1
    while i < len(argv):
        arg = argv[i]
        if not operands and arg == "--":
            operands = True
        elif not operands and name in ("head", "tail") and arg in ("-n", "-c"):
            i += 1
            if i >= len(argv) or not re.fullmatch(r"\d{1,5}", argv[i]):
                raise SafetyError("Use a nonnegative head/tail count of at most five digits.")
        elif not operands and arg.startswith("-"):
            if arg.startswith("--"):
                if arg not in LONG_FLAGS[name]:
                    raise SafetyError("Unrecognized option requires confirmation.")
            elif len(arg) < 2 or not set(arg[1:]) <= SHORT_FLAGS[name]:
                raise SafetyError("Unrecognized option requires confirmation.")
        else:
            if arg == "-" or any(c in arg for c in "*?[]"):
                raise SafetyError("Standard input and wildcard expansion are not run automatically.")
            paths.append(workspace_path(arg, cwd, workspace))
        i += 1
    if name == "pwd" and paths:
        raise SafetyError("pwd does not take path operands.")
    if name in ("cat", "head", "tail", "wc") and not paths:
        raise SafetyError("A file path is required; interactive standard input is disabled.")
    if name in ("ls", "du"):
        for path in paths or [cwd]:
            # du always traverses; ls -R traverses when explicitly requested.
            if name == "du" or any("R" in a for a in argv[1:] if a.startswith("-")):
                no_link_tree(path, protect=False)
    if name in ("cat", "head", "tail", "wc"):
        if any(not path.is_file() for path in paths):
            raise SafetyError("Automatic reads require regular files.")


def shell_decision(command):
    return Decision(
        ["bash", "--noprofile", "--norc", "-o", "pipefail", "-c", command],
        "shell",
        "Bash will interpret this expression, including quoted text, expansions, pipes, "
        "and redirects. It can run multiple programs, remove or overwrite files, and "
        "access data outside the workspace. Automatic backups and undo are unavailable.",
    )


def needs_shell(command):
    """Distinguish active shell syntax from literal characters inside quotes."""
    quote = None
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            continue
        if quote == "'":
            if char == "'":
                quote = None
            continue
        if char == "\\":
            escaped = True
            continue
        if quote == '"':
            if char == '"':
                quote = None
            elif char in "$`":
                return True
            continue
        if char in "'\"":
            quote = char
        elif char in "|&;<>`$()~":
            return True
    return False


def assess(command, cwd, workspace):
    validate_source(command)
    if needs_shell(command) or re.match(r"\s*[A-Za-z_][A-Za-z0-9_]*=", command):
        return shell_decision(command)
    argv = parse_command(command)
    if argv[0] == "cd":
        if len(argv) != 2:
            raise SafetyError("Use cd with one explicit directory.")
        path = workspace_path(argv[1], cwd, workspace)
        if not path.is_dir():
            raise SafetyError("Directory does not exist.")
        return Decision(argv, "cd", f"Change directory to {path}")
    if argv[0] in MANAGED:
        if any(c in command for c in "*?[]"):
            return shell_decision(command)
        meanings = {"rm": "Delete selected files/directories; save backups first.",
                    "mv": "Move or rename files/directories; destinations may be overwritten.",
                    "cp": "Copy files/directories; destinations may be overwritten.",
                    "mkdir": "Create directories.", "touch": "Create files or update file timestamps."}
        return Decision(argv, "managed", meanings[argv[0]])
    try:
        read_only(argv, cwd, workspace)
        return Decision(argv, "read", "Read-only command inside the workspace.")
    except SafetyError as error:
        if any(c in command for c in "*?[]"):
            return shell_decision(command)
        descriptions = {
            "chmod": "Changes file access permissions.", "chown": "Changes file ownership.",
            "git": "Runs Git; options can change or discard repository files and history.",
            "curl": "Contacts a remote server; options may upload local files or write downloads.",
            "wget": "Downloads remote content and can write files.",
            "python": "Executes Python code with your user permissions.",
            "python3": "Executes Python code with your user permissions.",
            "node": "Executes JavaScript code with your user permissions.",
            "pip": "Installs or changes Python packages and can execute package build code.",
            "npm": "Manages packages and can execute install scripts.",
            "find": "Searches a directory tree; -delete removes files and -exec launches programs.",
        }
        meaning = descriptions.get(argv[0], "This program may change files, access the network, or launch other programs.")
        return Decision(argv, "risk", f"{error} {meaning} Undo is unavailable; execution is not sandboxed.")
