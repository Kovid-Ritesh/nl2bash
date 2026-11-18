# Safety, command review, and undo

## Pre-execution hook

The [hook layer](HOOKS.md) runs before command review, repeats the check after
approval, and also guards the subprocess and managed-file execution boundaries.
Matched critical rules block execution even if the user would press Enter.
The startup card shows that this hook is active. Ordinary read-only commands
and safe file changes use the automatic allowlist; other nonblocked risky commands use review.

| Command category | Behavior |
| --- | --- |
| Matched secret access, forced recursive deletion, destructive Git/SQL, primary-branch pushes | Blocked by hook; no confirmation override |
| `pwd`, `ls`, `cat`, `head`, `tail`, `wc`, `stat`, `du`, restricted `find` | Automatic only with recognized flags, workspace paths, and the required read-only checks |
| `echo`, `printf`, `whoami`, `id`, `uname`, date display, restricted `grep` | Automatic for recognized non-mutating forms; file searches validate paths |
| `cd` | Changes the agent's working directory within the workspace |
| `mkdir`, `touch`, copies to unused destinations | Automatic, with managed undo; `touch` preserves file contents |
| `rm`, `mv`, copies overwriting existing destinations | Requires confirmation; uses managed file operations and backups |
| Other installed programs, unsupported read options | Requires confirmation; displays a risk description; no automatic undo |
| Pipes, chaining, redirection, substitutions, escapes, expansions, assignments | Warns and requires Enter; runs the original expression in Bash; no automatic undo |
| Control characters, multiline commands, plain executable paths | Blocked |
| Plain shell-launcher, privilege-escalation, disk-format/wipe, shutdown invocations | Blocked as simple commands; an approved Bash expression is not restricted to this list |

For example, `ls -la | head -n 10` now displays a **SHELL REVIEW** card and a risk
warning. Enter executes the expression, Esc cancels, and `/exit` leaves the agent.
The complete original expression is shown and passed to Bash without rewriting
its quotes or escapes. Active shell expressions require confirmation. Harmless
quoted literals such as `echo 'hello; world'` or `printf '%s\n' hello` can run
directly as argument lists without invoking Bash or asking permission.

Hook-allowed shell expressions can call programs normally blocked as simple commands and can
write outside the workspace. Review every step, redirect, and substitution before
approving. Undo and managed-file backups do not apply to these expressions.

Automatic file reads reject credential/configuration names such as `.env`,
`.ssh`, `.aws`, `.git`, private-key files, and paths leaving the workspace.
Recursive reads reject symbolic links. `find` automatically permits only explicit
paths with `-name`, `-iname`, `-type f/d`, depth/size filters, and `-print`.
`find -delete` is blocked by the hook. Prefer a plain `rm` or managed `rm -r`
with explicit paths when undo is needed; `rm -rf` is blocked.

Managed file changes reject protected paths, symbolic-link traversal, hard-linked
or special files, overlapping targets, and changes to the workspace root.
Supported flags are `rm -r/-R/-f`, `cp -r/-R`, and `mkdir -p`; `--` is supported
before filenames. File-change commands with wildcards go through Bash review
and have no managed backups. Use explicit filenames for undoable changes.

Automatic copies create destinations exclusively rather than truncating an
existing file. If another process creates the destination concurrently, the copy
fails and preserves that file. Failed automatic creation only rolls back targets
that the action actually changed. Non-empty newly created directories are retained
for manual recovery on failure, preserving any work another process added there.
Existing concurrent-process and hostile-filesystem
race limitations described below still apply.

Other security measures:

- The deterministic hook inspects matched critical operations independently of
  the model's safety claims; see its [coverage and limits](HOOKS.md#limits).
- Simple commands execute as argument lists from `/usr/bin` or `/bin`. Approved
  shell expressions use an explicit Bash invocation, with startup profiles
  disabled and `pipefail` enabled so a failing pipeline step reports failure.
- Model output cannot approve execution or claim a command is safe.
- Terminal-control characters in output are escaped.
- Root execution is refused. Child processes get a minimal environment without
  the model API key or inherited credential variables.
- Execution has a 60-second limit (configurable with `--timeout`, up to 300) and
  a 1 MiB output limit. The process group is stopped on cancellation or limits.
- Prompts and command output are not written to history. Only the prompt is sent
  to the model; local file contents and execution output are not automatically sent.
- The journal uses a process lock, private directory/file permissions, backup
  integrity checks, and validation after confirmation.

## Undo and recovery

Undo supports the five managed file operations only. It restores overwritten
files, deleted files/directories, moves, and newly created files/directories.
Reads do not add history, so reading a file does not prevent undoing the latest
supported change. Multiple supported changes can be undone in reverse order.

Before restoring, the CLI checks content, file mode, and modification time
against the post-action fingerprint. If something changed afterward, undo refuses
to overwrite the newer work. Backups have a 100 MiB combined per-action limit
and a 10,000-entry tree limit; operations exceeding either are refused.

State is stored outside the workspace at `$XDG_STATE_HOME/nl2sh`, or
`~/.local/state/nl2sh`. Use `--state-dir /some/private/directory` to change it.
History is scoped to the workspace. Backups persist across launches and are not
automatically expired; monitor their disk usage. They can contain private file
contents, so keep the state directory private. On WSL/Windows-mounted drives,
Linux permission bits may not enforce Windows access controls; prefer the Linux
home directory for state.

An external program action is an **undo barrier**: it may change arbitrary state,
so the CLI will not jump backward through it and claim to undo earlier work.
Commands such as `git`, `curl`, interpreters, and installers have no general undo.
No tool can guarantee reversal of network requests, arbitrary scripts, package
installation, secure deletion, databases, or other external effects.

If a managed operation fails normally, it attempts to restore the saved files.
If the process crashes, is interrupted during a file change, or restoration
fails, its journal remains pending. The CLI refuses further journaled actions;
inspect the reported state directory's `record.json` and numbered backups before
performing manual recovery. Do not delete that directory before recovery.
File ownership, ACLs, extended attributes, and filesystem layout are not guaranteed
to be restored; this is file-content recovery, not a filesystem snapshot system.

## Boundaries

This is a guarded local agent, **not an OS sandbox or a complete security
guarantee**. A confirmed external program runs with your user permissions and
can access data outside the workspace, launch detached processes, or make
irreversible changes. Workspace restrictions apply to automatic reads and managed
file operations. Do not approve commands you do not understand. Concurrent
filesystem changes or a hostile local process can race path checks; run in a
disposable container/VM when working with untrusted repositories or programs.

Model replies can be incorrect even when they are read-only. The CLI displays
the complete command or expression instead of extracting executable pieces from it.
The model backend supports non-streaming plain-text prompts; it must remain online.
