# nl2bash

```text
 _   _ _     ____  ____    _    ____  _   _
| \ | | |   |___ \| __ )  / \  / ___|| | | |
|  \| | |     __) |  _ \ / _ \ \___ \| |_| |
| |\  | |___ / __/| |_) / ___ \ ___) |  _  |
|_| \_|_____|_____|____/_/   \_\____/|_| |_|
```

**Describe a shell task. See the command. Review risky actions before they run.**

`nl2bash` is a terminal agent powered by the
[Qwen NL2SH LoRA adapter](https://huggingface.co/kovidritesh/qwen2.5-coder-3b-nl2sh-alfa-qlora).
The model runs behind your own inference endpoint; the CLI runs locally on Linux
or Windows through WSL. The client needs Python 3.11+ and has no runtime package
dependencies or local GPU requirement.

## What it does

- Translates natural-language requests into shell commands.
- Runs normal reads, display/search commands, file/directory creation, and non-overwriting copies automatically.
- Explains risky commands and asks for **Enter to execute** or **Esc to cancel**.
- Blocks matched critical actions with a pre-execution hook, even before confirmation.
- Supports pipes, redirection, substitutions, and escapes after review.
- Offers `/undo` for supported file changes, with backups and newer-edit checks.
- Starts from any ordinary project folder using the `nl2bash` command.
- Provides an ASCII startup wordmark, colored command cards, history, and preview mode.

**Review matters:** this is a guarded agent, not an operating-system sandbox.
Approved shell expressions and other programs run with your user permissions.
They can affect files outside the workspace and cannot be undone automatically.
See [the safety and recovery guide](docs/SAFETY.md).

## 1. Start the model endpoint

Upload [the serving notebook](notebooks/nl2sh_kaggle_ngrok.ipynb) to a private
Kaggle notebook. Enable **Internet** and a **GPU accelerator**. Add these Kaggle
Secrets and grant the notebook access:

| Secret | Purpose |
| --- | --- |
| `NGROK_AUTHTOKEN` | Opens the tunnel; obtain it from [ngrok](https://dashboard.ngrok.com/get-started/your-authtoken) |
| `NL2SH_API_KEY` | Your private inference API key; use at least 32 random ASCII characters |

Generate an inference key locally:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Run the notebook cells in order. It loads the base model and LoRA adapter,
checks inference locally, and prints an authenticated ngrok endpoint. Keep its
final serving cell running. This endpoint lasts only while the Kaggle session
and tunnel remain available; normal provider quotas and session limits apply.

Use [the testing notebook](notebooks/nl2sh_inference_test.ipynb) in a separate
CPU session or local Jupyter to enter prompts and inspect replies.

## 2. Install the CLI automatically

Use the single-file **`nl2bash-installer.pyz`** bundle. It includes the app, Python
installer, guardrail hook, guides, and Kaggle server/testing notebooks.

Windows:

```powershell
py nl2bash-installer.pyz
```

Linux / WSL:

```bash
python3 nl2bash-installer.pyz
```

The installer copies the application into your user folder, creates the launcher,
and adds its folder to your user PATH automatically. Existing PATH entries and
configuration are preserved. On Windows, it also sets up the WSL launcher.
Python 3.11+ is required; Windows needs an existing non-root WSL setup with
Python 3.11+ as well. The local client does not need a GPU or pip dependencies.

Enter your own Kaggle endpoint and API key at setup, or configure them later.
The installer prints the installed notebook and `.env` locations. Your keys are
not included in the bundle or added to PATH. Open a **new terminal** and run
`nl2bash` from your project folder.

See [the full installation guide](docs/INSTALL.md) for default folders, updating
an ngrok URL, choosing a WSL distribution, and installation behavior.

### From source

```bash
git clone https://github.com/Kovid-Ritesh/nl2bash.git
cd nl2bash
python3 install.py
```

On Windows, use `py install.py`. Build the single-file distribution with:

```bash
python3 scripts/build_installer.py
```

For direct development without copying an installation, copy `.env.example` to
`.env`, set your endpoint, and run `python3 launch.py`. Explicit environment
variables take precedence. The launcher reads configuration as data rather than
executing shell code. Never commit `.env` or notebook output containing secrets.

## Usage

```text
nl2bash [project] > Find all Python files recursively in this directory
READ ONLY
$ find . -name '*.py'
...command output...

nl2bash [project] > Remove draft.txt
REVIEW REQUIRED
$ rm draft.txt
DANGEROUS
Delete selected files/directories; save backups first.
...
Run this command at your own risk.
[Enter] Execute   [Esc] Cancel   [/exit + Enter] Exit >
```

| Input | Action |
| --- | --- |
| Natural-language task | Ask the model for a command and apply the local execution policy |
| Enter at review | Execute the displayed action |
| Esc at review | Cancel immediately |
| `/undo` | Review undo of the latest supported file change |
| `/history` | Show local action history |
| `/pwd` | Show the current directory |
| `/help` | Show command help |
| `/exit` | Leave the agent and return to your terminal |
| Ctrl+C | Cancel the current task |

One-shot and preview examples:

```bash
nl2bash "List files including hidden files"
nl2bash --dry-run "Remove draft.txt"
nl2bash --workspace /path/to/project "Show the first 20 lines of app.log"
nl2bash --timeout 120 "List files"
```

Preview never executes the command. Risky execution still requires an interactive
terminal; piped input cannot approve it. The current directory is local session
state and is not sent to the model. Use explicit paths when your request needs them.

## Command review and undo

The [pre-execution hook](docs/HOOKS.md) runs before review and again at execution
boundaries. It blocks matched secret-file access, `rm -rf`, destructive Git
operations, direct pushes to `main`/`master`, and destructive SQL. Enter cannot
override a hook block. `.env.example` templates remain readable.

Other risky commands still use Enter/Esc review. This hook is local to the agent;
it does not configure Claude Code, native Git hooks, or GitHub branch rules.

Recognized reads and display/search commands run automatically after path and
flag checks, including `ls`, `cat`, `head`, `grep`, `find`, `echo`, and `printf`.
Quoted literal text is not treated as an active shell operator.

These ordinary file operations also run without a permission prompt:

```bash
touch notes.txt
mkdir -p project/src
cp notes.txt notes-copy.txt
cp -r project project-copy
```

`touch` preserves existing contents, and automatic copies require an unused
destination. These changes are journaled and can be undone with `/undo`.
Deletion, moves, and overwriting copies still require Enter/Esc review. A copy
that discovers a newly created destination refuses to overwrite it automatically.
General Bash expressions and scripts still require review because they may
perform additional actions that cannot be classified as a simple safe operation.

Hook-allowed shell expressions such as `ls -la | head -n 10` always require review. They run
in Bash with profiles disabled and `pipefail` enabled. They have **no automatic
backups or undo**, even if they include `rm` or another managed command.

Undo refuses to overwrite files changed after an action. Backup work is limited
to 100 MiB per action and 10,000 tree entries. Default state lives in
`~/.local/state/nl2sh` (or `$XDG_STATE_HOME/nl2sh`), outside the workspace.
An external-program action prevents automatic undo across that action.

Child processes get a minimal environment without inherited API keys. Output is
escaped for terminal safety and limited to 1 MiB; execution defaults to 60 seconds.
Only the prompt goes to the model. The agent does not automatically send local
file contents or execution output. Full limitations and crash-recovery details
are in [docs/SAFETY.md](docs/SAFETY.md).

## Model and notebooks

| Resource | Location |
| --- | --- |
| LoRA weights | [Hugging Face adapter](https://huggingface.co/kovidritesh/qwen2.5-coder-3b-nl2sh-alfa-qlora) |
| Base model | [Qwen2.5-Coder-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct) |
| Ready-to-use model card | [huggingface/README.md](huggingface/README.md) |
| Kaggle server | [notebooks/nl2sh_kaggle_ngrok.ipynb](notebooks/nl2sh_kaggle_ngrok.ipynb) |
| Inference testing | [notebooks/nl2sh_inference_test.ipynb](notebooks/nl2sh_inference_test.ipynb) |

The serving notebook uses BF16/FP16 inference with PEFT and a rank-64 LoRA.
The supplied server supports non-streaming plain-text chat requests, up to
512 output tokens and a 4,096-token input-plus-output budget. It returns text;
the local CLI decides whether and how to execute it.

## Development and tests

```bash
python3 -m unittest discover -s tests -v
```

The suite covers approval/cancellation, real terminal keys, shell expressions,
protected paths, backups, undo conflicts, rollback, process limits, and credential
isolation. Tests use temporary files and mocked inference responses; model
quality and provider availability are separate from these CLI checks.

```text
nl2sh/        CLI, pre-execution hook, safety policy, execution, UI, and undo
scripts/      Windows and Linux/WSL launcher installers
notebooks/    Kaggle serving and inference-testing notebooks
huggingface/  Model README for the Hugging Face repository
docs/         Detailed safety and recovery documentation
tests/        Standard-library test suite
launch.py     Startup with local configuration loading
install.py    Automatic user installation and PATH setup
```

## Troubleshooting

- **`nl2bash` not found:** open a new terminal and check the launcher directory is on PATH.
- **HTTP 401:** use the same inference key as the serving notebook.
- **HTTP 429:** the server handles one generation at a time; retry when it is free.
- **Offline/404/502:** restart the serving notebook if needed and update `NL2SH_URL`.
- **No GPU in Kaggle:** enable an available GPU accelerator and check account access/quotas.
- **Undo refused:** preserve newer work and inspect the recovery guide; do not delete backups.
- **No colors:** check `NO_COLOR` and `TERM`. Narrow terminals use a compact startup header.

The model's license is separate from this CLI. See the
[model card's license section](huggingface/README.md#license).
