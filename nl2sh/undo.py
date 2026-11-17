import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .safety import SafetyError, no_link_tree, workspace_path

MAX_BACKUP_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 10000


def inspect_tree(path):
    """Bound backup/hash work and reject links, secrets, devices, sockets, and FIFOs."""
    if not path.exists() and not path.is_symlink():
        return []
    no_link_tree(path)
    items = [path]
    if path.is_dir():
        for root, dirs, files in os.walk(path):
            items.extend(Path(root) / name for name in dirs + files)
            if len(items) > MAX_ENTRIES:
                raise SafetyError("Operation exceeds the 10000-entry backup limit.")
    total = 0
    for item in items:
        info = item.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise SafetyError("Only ordinary files and directories can be backed up.")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise SafetyError("Managed changes cannot operate on hard-linked files.")
        total += info.st_size if stat.S_ISREG(info.st_mode) else 0
    if total > MAX_BACKUP_BYTES:
        raise SafetyError("Operation exceeds the 100 MiB backup limit.")
    return items


def fingerprint(path):
    items = inspect_tree(path)
    if not items:
        return None
    digest = hashlib.sha256()
    for item in sorted(items):
        info = item.lstat()
        digest.update(str(item.relative_to(path)).encode())
        metadata = f"{info.st_mode}:{info.st_mtime_ns}:{info.st_size}" if item.is_file() else str(info.st_mode)
        digest.update(f"\0{metadata}\0".encode())
        if item.is_file():
            with item.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def copy_path(source, target):
    if source.is_dir():
        shutil.copytree(source, target, copy_function=shutil.copy2)
    else:
        shutil.copy2(source, target)


def remove_path(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


@dataclass
class Plan:
    name: str
    operands: list[Path]
    targets: list[Path]
    recursive: bool = False
    parents: bool = False

    def describe(self):
        return "\n".join("  " + str(path) for path in self.targets)


def plan_change(argv, cwd, workspace):
    name = argv[0]
    recursive = parents = force = False
    paths = []
    operands_only = False
    allowed = {"rm": set("rfR"), "cp": set("rR"), "mv": set(), "mkdir": set("p"), "touch": set()}
    for arg in argv[1:]:
        if arg == "--" and not operands_only:
            operands_only = True
            continue
        if arg.startswith("-") and not operands_only:
            long = {"--recursive": "r", "--force": "f", "--parents": "p"}
            flags = long.get(arg, arg[1:] if not arg.startswith("--") else "!")
            if not flags or not set(flags) <= allowed[name]:
                raise SafetyError(f"Unsupported {name} option: {arg}. Use only basic managed file operations.")
            recursive |= bool(set(flags) & set("rR"))
            parents |= "p" in flags
            force |= "f" in flags
        else:
            if any(c in arg for c in "*?[]"):
                raise SafetyError("Managed file operations require explicit paths, without wildcards.")
            paths.append(workspace_path(arg, cwd, workspace, mutation=True))
    if not paths:
        raise SafetyError(f"{name} requires explicit paths.")
    for path in paths:
        inspect_tree(path)
    if name in ("cp", "mv"):
        if len(paths) != 2:
            raise SafetyError(f"Use {name} with exactly one source and one destination.")
        source, target = paths
        if not source.exists():
            raise SafetyError("Source does not exist.")
        if target.is_dir():
            target = workspace_path(str(target / source.name), cwd, workspace, mutation=True)
        if target == source or target.is_relative_to(source) or source.is_relative_to(target):
            raise SafetyError("Source and destination cannot overlap.")
        inspect_tree(target)
        if not target.parent.is_dir():
            raise SafetyError("Destination parent must already exist.")
        if source.is_dir() and name == "cp" and not recursive:
            raise SafetyError("Use cp -r to copy a directory.")
        if target.exists() and source.is_dir() != target.is_dir():
            raise SafetyError("Source and destination must have the same file/directory type.")
        paths = [source, target]
        targets = [target] if name == "cp" else paths[:]
    elif name == "rm":
        if not force and any(not path.exists() for path in paths):
            raise SafetyError("Removal target does not exist.")
        paths = [path for path in paths if path.exists()]
        if any(path.is_dir() for path in paths) and not recursive:
            raise SafetyError("Use rm -r to remove a directory.")
        targets = paths[:]
    elif name == "mkdir":
        if any(path.exists() and (not parents or not path.is_dir()) for path in paths):
            raise SafetyError("Directory target already exists.")
        targets = []
        for path in paths:
            if path.exists():
                continue
            target = path
            if parents:
                while not target.parent.exists() and target.parent != workspace:
                    target = target.parent
            elif not target.parent.is_dir():
                raise SafetyError("Parent directory does not exist; use mkdir -p.")
            targets.append(target)
        targets = [p for p in set(targets) if not any(q != p and p.is_relative_to(q) for q in targets)]
    else:
        if any(path.is_dir() or not path.parent.is_dir() for path in paths):
            raise SafetyError("touch requires file targets with existing parent directories.")
        targets = paths[:]
    if not targets:
        raise SafetyError("No filesystem changes are needed.")
    if len(set(targets)) != len(targets) or any(
        p != q and (p.is_relative_to(q) or q.is_relative_to(p)) for p in targets for q in targets
    ):
        raise SafetyError("Change targets cannot be duplicated or overlap.")
    size = sum(p.stat().st_size for target in targets for p in inspect_tree(target) if p.is_file())
    if size > MAX_BACKUP_BYTES:
        raise SafetyError("Combined backup exceeds 100 MiB.")
    return Plan(name, paths, targets, recursive, parents)


class Journal:
    def __init__(self, state_dir, workspace):
        self.workspace = workspace.resolve()
        self.directory = state_dir.expanduser().resolve()
        if self.directory.is_relative_to(self.workspace):
            raise SafetyError("Undo state must be outside the workspace; select --state-dir elsewhere.")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        # Keep one process in control of the journal for the duration of a CLI session.
        import fcntl
        lock_path = self.directory / "lock"
        if lock_path.is_symlink():
            raise SafetyError("Journal lock cannot be a symbolic link.")
        self.lock = lock_path.open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise SafetyError("Another NL2SH session is using this state directory.") from None

    def close(self):
        self.lock.close()

    def records(self):
        records = []
        for path in sorted(self.directory.glob("*/record.json")):
            data = json.loads(path.read_text())
            if data["workspace"] == str(self.workspace):
                records.append((path, data))
        return sorted(records, key=lambda pair: pair[1]["time"])

    def save(self, path, data):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2))
        temporary.chmod(0o600)
        temporary.replace(path)

    def create(self, name, undoable, targets=()):
        if any(r["status"] == "pending" for _, r in self.records()):
            raise SafetyError("Resolve the interrupted action in the state directory before making more changes.")
        directory = self.directory / uuid.uuid4().hex
        directory.mkdir(mode=0o700)
        record = {"time": time.time_ns(), "workspace": str(self.workspace), "name": name,
                  "undoable": undoable, "status": "pending", "targets": []}
        for index, path in enumerate(targets):
            before = fingerprint(path)
            if before is not None:
                copy_path(path, directory / str(index))
                if fingerprint(directory / str(index)) != before:
                    raise SafetyError("A file changed while creating its backup; no operation was executed.")
            record["targets"].append({"path": str(path), "backup": str(index), "before": before})
        destination = directory / "record.json"
        self.save(destination, record)
        return destination, record

    def restore(self, record_path, record):
        for item in record["targets"]:
            if item["before"] is not None:
                backup = record_path.parent / item["backup"]
                if fingerprint(backup) != item["before"]:
                    raise SafetyError(f"Backup integrity check failed: {backup}. No files restored.")
        for item in record["targets"]:
            path = workspace_path(item["path"], self.workspace, self.workspace, mutation=True)
            remove_path(path)
            if item["before"] is not None:
                copy_path(record_path.parent / item["backup"], path)

    def apply(self, plan):
        record_path, record = self.create(plan.name, True, plan.targets)
        try:
            if plan.name == "rm":
                for path in plan.operands:
                    remove_path(path)
            elif plan.name == "cp":
                source, target = plan.operands
                if source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True, copy_function=shutil.copy2)
                else:
                    shutil.copy2(source, target)
            elif plan.name == "mv":
                source, target = plan.operands
                if target.exists():
                    remove_path(target)
                shutil.move(str(source), str(target))
            elif plan.name == "mkdir":
                for path in plan.operands:
                    path.mkdir(parents=plan.parents, exist_ok=plan.parents)
            elif plan.name == "touch":
                for path in plan.operands:
                    path.touch()
            for item in record["targets"]:
                item["after"] = fingerprint(Path(item["path"]))
            record["status"] = "done"
            self.save(record_path, record)
        except Exception:
            self.restore(record_path, record)
            record["status"] = "rolled-back"
            self.save(record_path, record)
            raise

    def mark_external(self, command):
        path, record = self.create(command, False)
        record["status"] = "done"
        self.save(path, record)

    def undo(self):
        records = [(p, r) for p, r in self.records() if r["status"] not in ("undone", "rolled-back")]
        if not records:
            raise SafetyError("Nothing to undo.")
        path, record = records[-1]
        if record["status"] != "done":
            raise SafetyError(f"An interrupted operation needs manual recovery from {path.parent}. No files were restored automatically.")
        if not record["undoable"]:
            raise SafetyError("The latest action ran an external program and cannot be undone automatically.")
        for item in record["targets"]:
            target = workspace_path(item["path"], self.workspace, self.workspace, mutation=True)
            if fingerprint(target) != item["after"]:
                raise SafetyError(f"Undo refused: {target} changed since the action. Your newer edits were preserved.")
        self.restore(path, record)
        record["status"] = "undone"
        self.save(path, record)
        return record["name"]
