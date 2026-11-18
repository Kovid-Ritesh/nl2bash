# Single-file installer

The complete distribution is **`nl2bash-installer.pyz`**: a Python executable ZIP
containing the installer, CLI, guardrail hook, documentation, tests, and both
Kaggle notebooks. There is no separate dependency download for the local client.
It does not contain model weights, your private endpoint configuration, or API keys.

## Requirements

| Platform | Requirements |
| --- | --- |
| Windows | Python 3.11+, WSL with a Linux distribution, and Python 3.11+ inside that distribution under a non-root user |
| Linux / WSL | Python 3.11+; Bash or Zsh for automatic shell PATH setup |
| Model server | Your own Kaggle GPU session, Internet access, ngrok account, and Hugging Face model downloads |

The application installer runs under your normal account and does not require
administrator rights. It checks prerequisites but does not silently install WSL,
change Windows features, install CUDA, or download model weights onto your PC.
If WSL is missing, install it first using Microsoft's `wsl --install` workflow.

## Install

Download the provided `.pyz` file and run it from a terminal.

Windows:

```powershell
py nl2bash-installer.pyz
```

Linux / WSL:

```bash
python3 nl2bash-installer.pyz
```

When prompted, paste the URL printed by your Kaggle serving notebook and enter
the matching inference key at the hidden prompt. Leave either blank to configure
it later. The ngrok authtoken belongs in Kaggle Secrets, not in the local CLI.
API keys are saved only if entered, in the installation's private `.env` file;
they are never stored in PATH or Windows environment registry values.

**Close and reopen your terminal**, enter a project folder, and run:

```text
nl2bash
```

No manual PATH editing is needed. An installer subprocess cannot change the PATH
already loaded in its parent terminal, so a new terminal is needed after setup.

## What is installed

| Location | Purpose |
| --- | --- |
| Windows: `%LOCALAPPDATA%\nl2bash\app` | Application, configuration, docs, and notebooks |
| Windows: `%USERPROFILE%\.local\bin\nl2bash.cmd` | CMD / PowerShell launcher, registered on the user PATH |
| Linux: `~/.local/share/nl2bash` | Application, configuration, docs, and notebooks |
| Linux / WSL: `~/.local/bin/nl2bash` | Native shell launcher |

Windows setup also installs the WSL launcher, pointing to the same installed
application. It uses the selected Linux distribution/user and preserves the
folder from which CMD or PowerShell starts it. The installer updates user PATH
only; it does not replace the system PATH.

On Linux/WSL it appends an idempotent PATH block to `.profile` and `.bashrc`, and
to `.zshrc` for a Zsh user. Existing contents are preserved, with initial backups
named `.profile.nl2bash.bak`, `.bashrc.nl2bash.bak`, and `.zshrc.nl2bash.bak`.
It refuses to overwrite an unrelated `nl2bash` command or an unrelated installation
directory. Profile symlinks are reported instead of being replaced.

Installation copies the app to a stable location. The downloaded bundle and
original source checkout can be moved afterward. Updates preserve `.env` and
back up changed installed payload files under the app's `.backups` directory.

## Kaggle server notebook

The installer prints the full path to:

```text
notebooks/nl2sh_kaggle_ngrok.ipynb
notebooks/nl2sh_inference_test.ipynb
```

Upload the **server notebook** to a private Kaggle notebook, enable Internet and
a GPU, and add `NGROK_AUTHTOKEN` and `NL2SH_API_KEY` in Kaggle Secrets. Run the cells
and keep the final serving cell running. The model and adapter download there.

Use the **inference notebook** in a separate CPU session or local Jupyter to test
prompts without executing commands. Notebook outputs are cleared in the bundle.

The CLI can be installed before starting Kaggle. Once you have the endpoint URL,
rerun the installer to update it without changing the existing saved key:

```powershell
py nl2bash-installer.pyz --endpoint https://YOUR-NGROK-DOMAIN.ngrok-free.dev
```

On Linux, use `python3` instead of `py`. A missing API key is requested at a hidden
prompt when the CLI first needs inference. You can also edit the printed `.env`
path privately. Keep the serving notebook running while using the CLI.

## Windows distribution selection

The installer defaults to your default WSL distribution and user. Override them
when needed:

```powershell
py nl2bash-installer.pyz --distribution Ubuntu --wsl-user your-linux-user
```

## Source installation and rebuilding

From a source checkout, the same installer is available as:

```bash
python3 install.py
```

On Windows use `py install.py`. To build the single-file bundle:

```bash
python3 scripts/build_installer.py
```

The output is `dist/nl2bash-installer.pyz`. Only explicit source/document/notebook
payloads are included. `.env`, `.git`, caches, generated build directories, and
private credential files are excluded. Notebooks with saved code outputs are
rejected by the builder.

## Verification

```bash
python3 -m unittest discover -s tests -v
```

Installer tests include an actual isolated Linux install/launch from the `.pyz`,
shell-profile PATH activation, configuration preservation, upgrade backups,
Windows PATH/registry handling with mocks, prerequisite checks, and unsafe archive
path rejection. Windows registry integration is not exercised against a real user
profile by these tests.
