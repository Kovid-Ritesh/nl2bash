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
- Runs allowlisted read-only commands automatically inside your workspace.
- Explains risky commands and asks for **Enter to execute** or **Esc to cancel**.
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

## 2. Configure the CLI

```bash
git clone https://github.com/Kovid-Ritesh/nl2bash.git
cd nl2bash
```

Copy `.env.example` to `.env` (`cp .env.example .env` on Linux/WSL or
`Copy-Item .env.example .env` in PowerShell). Edit the copy:

```dotenv
NL2SH_URL=https://YOUR-NGROK-DOMAIN.ngrok-free.dev
# Optional; leave commented to enter the key at a hidden prompt:
# NL2SH_API_KEY=the-same-private-key-you-saved-in-kaggle
```

The URL may be the endpoint root, `/v1`, or `/v1/chat/completions`. Use the URL
printed by **your** serving notebook. The inference key must match its
`NL2SH_API_KEY`; the ngrok authtoken is not the inference key.

`launch.py` loads the repository's `.env` as data, without executing shell code.
Explicit environment variables take precedence. An adjacent
`../nl2sh-modal/.env` is also supported for earlier local setups. Configuration is
read from the installed source location, not from an arbitrary working folder.
Never commit `.env` or publish keys in notebook output.

Start immediately without installing packages:

```bash
python3 launch.py --workspace /path/to/your/project
```

## 3. Install the `nl2bash` command

### Windows CMD / PowerShell

Install Ubuntu WSL with Python 3.11+ and a non-root Linux user first. In
PowerShell, from this repository:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\install-windows.ps1" -Distribution Ubuntu
```

If needed, select the Linux user with `-WslUser your-linux-user`. The installer
creates `%USERPROFILE%\.local\bin\nl2bash.cmd`, adds that directory to your user
PATH if absent, and installs the WSL launcher. It does not change system PATH.

Open a new CMD or PowerShell terminal, enter your project folder, and run:

```text
nl2bash
```

The Windows launcher starts the Linux agent through WSL and preserves your
current folder as the workspace. Generated commands use Linux/Bash syntax.

### Linux / WSL

```bash
python3 scripts/install_wsl_launcher.py
export PATH="$HOME/.local/bin:$PATH"
nl2bash
```

Ensure `~/.local/bin` is included by your shell profile for future terminals.
Keep this source checkout in place; rerun the installer after moving it.

For a conventional Python installation, use a virtual environment and
`python -m pip install -e .`. Its console entry point reads `NL2SH_URL` and
`NL2SH_API_KEY` from the environment; use `launch.py` or the launchers above for
automatic `.env` loading.

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

Recognized `pwd`, `ls`, `cat`, `head`, `tail`, `wc`, `stat`, `du`, and restricted
`find` reads can run automatically after path and flag checks. Basic `rm`, `mv`,
`cp`, `mkdir`, and `touch` actions require review and use managed backups.

Shell expressions such as `ls -la | head -n 10` always require review. They run
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
nl2sh/        CLI, safety policy, execution, terminal UI, and undo journal
scripts/      Windows and Linux/WSL launcher installers
notebooks/    Kaggle serving and inference-testing notebooks
huggingface/  Model README for the Hugging Face repository
docs/         Detailed safety and recovery documentation
tests/        Standard-library test suite
launch.py     Startup with local configuration loading
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
