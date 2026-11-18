# Pre-execution hook

This layer adapts the command-hook and deny-list ideas in
[Cup of Abhi's guardrail guide](https://cupofabhi.web.app/setup.md) to nl2bash.
It uses Python's standard library; no Claude Code installation or `jq` is needed.

## Enforcement

`nl2sh/hooks.py` checks a command before the CLI offers confirmation. It checks
again after confirmation, immediately before subprocess creation, and before
managed file changes create backups or modify files. A matched rule raises
`GuardrailBlocked`; the CLI displays the command, rule, and reason. One-shot
invocations return status **2**. There is no Enter override or disable flag.

Commands not matched by a critical rule continue through the existing policy:
allowlisted reads, ordinary file/directory creation, and non-overwriting copies
run automatically, while deletion, overwrites, moves, and other risky commands
require review. Automatic file changes retain managed undo.
Shell expressions remain usable after that review. Backed-up `rm file` and
`rm -r directory` remain supported; forced recursive removal is blocked.

The hook does not ask the model whether an action is safe. It does not execute
the proposed command to test it. Its only helper process is a bounded local Git
branch-metadata query when inspecting a push.

## Critical rules

| Rule | Examples and behavior |
| --- | --- |
| Primary-branch pushes | Blocks explicit `main`/`master` destinations, including full refspecs, and pushes while the inspected local branch is `main`/`master` |
| Force pushes / remote deletion | Blocks force flags, forced refspecs, mirror/deletion/prune pushes, and deletion refspecs |
| Skipping Git checks | Blocks push/commit `--no-verify` and commit `-n` |
| Discarding work | Blocks hard resets, forced clean, full-tree checkout/restore, and force branch deletion |
| Forced recursive deletion | Blocks combined/separate `rm -r/-R` and `-f`, including long options |
| Secret-file access | Blocks recognized readers/copiers/modifiers targeting `.env`, `.env.*`, credential/configuration paths, and private-key names; follows path aliases for this check |
| Unrecoverable file selection | Blocks `find -delete` and checks recognized commands inside `find -exec` |
| Destructive SQL | Blocks recognized database-client invocations containing DROP TABLE/DATABASE/SCHEMA or TRUNCATE TABLE |

`.env.example` is an allowed template name. A template symlink resolving to a
protected secret is still blocked. The scanner also checks command lists,
pipelines, substitutions, common execution wrappers, nested `bash -c`/`sh -c`,
and redirects through protected files.

## Guide verification cases

The tests run these with a temporary non-Git working directory, matching the
guide's `/tmp` context. They inspect commands; they do not perform any push,
reset, deletion, or secret read.

| Command | Result |
| --- | --- |
| `git push origin main` | Blocked |
| `git push origin master` | Blocked |
| `git push --force origin my-branch` | Blocked |
| `git reset --hard HEAD~1` | Blocked |
| `rm -rf build` | Blocked |
| `cat .env` | Blocked |
| `git push origin my-branch` | Allowed by hook |
| `cat .env.example` | Allowed by hook |
| `git status` | Allowed by hook |

"Allowed by hook" is not approval to run automatically. The normal CLI policy
still applies. A feature-branch push from a local `main`/`master` checkout is
blocked by the branch check.

Run all checks:

```bash
python3 -m unittest discover -s tests -v
```

## JSON hook protocol

Other local integrations can invoke the checker from this checkout:

```bash
printf '%s' '{"tool_input":{"command":"git status"},"cwd":"/tmp"}' | python3 -m nl2sh.hooks
```

Exit **0** means no critical rule matched; exit **2** means blocked, with a reason
on stderr. Malformed or oversized events fail closed. This command only evaluates
the event; it does not execute `git status` or install an integration elsewhere.

## Limits

This is a deterministic command-inspection layer, **not a shell interpreter,
an OS sandbox, or proof that an allowed command is safe**. It can conservatively
block text that resembles a dangerous operation. It cannot fully resolve aliases,
variables, encoded/generated code, arbitrary interpreter programs, scripts read
from files/stdin, wildcard-selected contents, or every change made during execution.
An approved program can still perform actions that the scanner cannot infer.

Git checks cover explicit protected destinations and the inspected current branch.
They do not fully resolve all Git configuration, push defaults, or upstream/refspec
mappings. Use a native pre-push hook and GitHub branch protection for enforcement
outside this CLI and protection of the actual destination refs.

This implementation changes only the nl2bash application. It does not install
global Claude settings, overwrite `.git/hooks`, or change GitHub rules/settings.
Review unknown commands and use a disposable container/VM for untrusted execution.
