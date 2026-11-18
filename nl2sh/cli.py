import argparse
import getpass
import os
import shlex
import sys
from pathlib import Path

from .client import endpoint_root, generate_command
from .runner import display, run_command, trusted_executable
from .safety import SafetyError, assess, workspace_path
from .undo import Journal, fingerprint, plan_change
from . import ui
from .ui import confirm, ExitRequested
from .hooks import GuardrailBlocked, pre_execute

DEFAULT_ENDPOINT = "https://YOUR-NGROK-DOMAIN.ngrok-free.dev"
HELP = """Enter a natural-language shell task.
  /undo       Undo the latest supported file change (checks for later edits)
  /history    Show local action history; prompts and output are not stored
  /pwd        Show the current directory
  /help       Show this help
  /exit       Exit
Normal reads, mkdir, touch, and non-overwriting copies run automatically.
Deletion, overwrites, moves, and other risky commands require review: Enter executes, Esc cancels.
rm/cp/mv/mkdir/touch use managed backups; arbitrary programs have no undo.
Shell syntax requires review and has no automatic undo. Control characters remain blocked.
Critical hook rules cannot be approved with Enter: secret access, forced deletion, destructive Git, and primary-branch pushes.
"""


def handle_command(command, cwd, workspace, journal, dry_run=False, timeout=60, input_fn=None):
    try:
        pre_execute(command, cwd)
    except GuardrailBlocked:
        ui.panel("COMMAND", ["$ " + command], "red")
        raise
    decision = assess(command, cwd, workspace)
    plan = plan_change(decision.argv, cwd, workspace) if decision.kind == "managed" else None
    automatic_change = plan is not None and plan.automatic
    titles = {"read": "READ ONLY", "cd": "DIRECTORY", "managed": "REVIEW REQUIRED", "risk": "REVIEW REQUIRED",
              "shell": "SHELL REVIEW"}
    shown_command = command if decision.kind == "shell" else shlex.join(decision.argv)
    ui.panel("SAFE CHANGE" if automatic_change else titles[decision.kind], ["$ " + shown_command],
             "green" if automatic_change or decision.kind in ("read", "cd") else "yellow")
    if automatic_change:
        if dry_run:
            ui.notice("Preview only; nothing executed.")
        elif not plan.targets:
            ui.notice("Already exists; no changes needed.", "green")
        else:
            ui.notice("Running automatically within the workspace. Undo is available.")
            pre_execute(command, cwd)
            journal.apply(plan, automatic=True)
            ui.notice("Done. Use /undo to restore the previous state.", "green")
        return cwd, 0
    if decision.kind == "cd":
        if not dry_run:
            cwd = workspace_path(decision.argv[1], cwd, workspace)
        ui.notice("Preview only." if dry_run else f"Directory: {cwd}")
        return cwd, 0
    if decision.kind == "read":
        ui.notice("Preview only; nothing executed." if dry_run else "Running automatically within the workspace.")
        return cwd, 0 if dry_run else run_command(decision.argv, cwd, timeout)
    before = {}
    if decision.kind == "managed":
        before = {p: fingerprint(p) for p in set(plan.operands + plan.targets)}
        ui.panel("DANGEROUS", [decision.description, "", "Affected paths:", plan.describe(), "",
                               "Undo available: backups are created before this operation."], "red")
    else:
        # Validate executable availability before asking for execution approval.
        trusted_executable(decision.argv[0])
        ui.panel("DANGEROUS", [decision.description, "",
                               "This program can affect files outside the workspace.",
                               "Undo unavailable for this program."], "red")
    ui.notice("Run this command at your own risk.", "yellow")
    if dry_run:
        ui.notice("Preview only; nothing executed.")
        return cwd, 0
    if not confirm("[Enter] Execute   [Esc] Cancel   [/exit + Enter] Exit > ", input_fn):
        ui.notice("Cancelled; nothing executed.", "yellow")
        return cwd, 0
    pre_execute(command, cwd)
    if plan:
        # Revalidate after the user has spent time reviewing the confirmation.
        plan = plan_change(decision.argv, cwd, workspace)
        if {p: fingerprint(p) for p in before} != before:
            raise SafetyError("Files changed during confirmation; request and review a new command.")
        journal.apply(plan)
        ui.notice("Done. Use /undo to restore the previous state.", "green")
        return cwd, 0
    journal.mark_external(decision.argv[0])
    return cwd, run_command(decision.argv, cwd, timeout)


def show_history(journal):
    records = journal.records()
    if not records:
        ui.notice("No file changes or external-program actions recorded.")
        return
    lines = []
    for _, record in records[-20:]:
        recoverable = "undo supported" if record["undoable"] else "no undo"
        targets = ", ".join(item["path"] for item in record["targets"])
        lines.append(f"{record['name']}: {record['status']} | {recoverable}" + (f" | {targets}" if targets else ""))
    ui.panel("ACTION HISTORY", lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nl2bash", description="Turn prompts into guarded shell actions (Linux/WSL).")
    parser.add_argument("prompt", nargs="*", help="Optional one-shot natural-language task")
    parser.add_argument("--endpoint", default=os.environ.get("NL2SH_URL", DEFAULT_ENDPOINT))
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Boundary for automatic reads and managed changes")
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "nl2sh")
    parser.add_argument("--dry-run", action="store_true", help="Show and classify commands without executing them")
    parser.add_argument("--timeout", type=int, default=60, help="Subprocess time limit in seconds (1–300)")
    args = parser.parse_args(argv)
    if os.name != "posix" or not sys.platform.startswith("linux"):
        parser.error("This version supports Linux and WSL. Run it inside WSL on Windows.")
    if not 1 <= args.timeout <= 300:
        parser.error("--timeout must be between 1 and 300 seconds")
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        parser.error("--workspace must be an existing directory")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        parser.error("Do not run this agent as root or with sudo.")
    journal = None
    try:
        endpoint = endpoint_root(args.endpoint)
        journal = Journal(args.state_dir, workspace)
        key = os.environ.get("NL2SH_API_KEY", "").strip()
        cwd = workspace
        if not args.prompt:
            ui.banner(workspace, args.dry_run)
            # Enable normal arrow-key editing; no history file is written.
            try:
                import readline
            except ImportError:
                pass
        while True:
            try:
                if args.prompt:
                    prompt = " ".join(args.prompt)
                else:
                    prompt = input(ui.prompt(cwd)).strip()
                if not prompt:
                    continue
                if prompt in ("/exit", "/quit"):
                    if not args.prompt:
                        ui.notice("Session closed. Back at your terminal.", "cyan")
                    break
                if prompt == "/help":
                    print(HELP)
                    if args.prompt:
                        return 0
                    continue
                if prompt == "/pwd":
                    print(display(str(cwd)))
                    if args.prompt:
                        return 0
                    continue
                if prompt == "/history":
                    show_history(journal)
                    if args.prompt:
                        return 0
                    continue
                if prompt == "/undo":
                    if args.dry_run:
                        print("Preview mode: undo was not executed.")
                        if args.prompt:
                            return 0
                        continue
                    ui.notice("Undo restores files and refuses to overwrite newer edits.", "yellow")
                    if confirm("[Enter] Undo   [Esc] Cancel   [/exit + Enter] Exit > "):
                        ui.notice("Undone: " + journal.undo(), "green")
                    else:
                        ui.notice("Undo cancelled; nothing changed.", "yellow")
                    if args.prompt:
                        return 0
                    continue
                if prompt.startswith("/"):
                    raise SafetyError("Unknown slash command; use /help.")
                if not key:
                    if not sys.stdin.isatty():
                        raise SafetyError("Set NL2SH_API_KEY in the environment; credentials cannot be read noninteractively.")
                    key = getpass.getpass("NL2SH_API_KEY (hidden): ").strip()
                if not key:
                    raise SafetyError("API key is required.")
                ui.notice("Asking your model...", "cyan")
                command = generate_command(endpoint, key, prompt)
                cwd, code = handle_command(command, cwd, workspace, journal, args.dry_run, args.timeout)
                if code:
                    ui.notice(f"Command exit status: {code}", "yellow")
                if args.prompt:
                    return code
            except GuardrailBlocked as error:
                ui.panel("BLOCKED BY HOOK", [f"Rule: {error.rule}", str(error), "",
                                             "Nothing executed. This rule cannot be overridden with Enter."], "red")
                if args.prompt:
                    return 2
            except ExitRequested:
                ui.notice("Session closed. Back at your terminal.", "cyan")
                break
            except KeyboardInterrupt:
                ui.notice("\nCancelled. Use /exit to leave the agent.", "yellow")
                if args.prompt:
                    return 130
            except EOFError:
                break
            except (ValueError, OSError) as error:
                message = str(error).replace(key, "[REDACTED]") if key else str(error)
                ui.notice("Error: " + message, "red", sys.stderr)
                if args.prompt:
                    return 1
    except (ValueError, OSError) as error:
        print(display("Error: " + str(error)), file=sys.stderr)
        return 1
    finally:
        if journal:
            journal.close()
    return 0
