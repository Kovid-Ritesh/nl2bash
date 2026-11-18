"""User-level installer for the source checkout or the self-contained .pyz bundle."""

import argparse
import contextlib
import ctypes
import getpass
import json
import ntpath
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

MARKER = '.nl2bash-install.json'
VERSION = '0.1.0'
PATH_MARKER = '# >>> nl2bash PATH >>>'
PATH_BLOCK = '''# >>> nl2bash PATH >>>
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
# <<< nl2bash PATH <<<
'''
WINDOWS_BOOTSTRAP = (
    "import pathlib,runpy,subprocess,sys;"
    "p=subprocess.check_output(['wslpath','-a',sys.argv.pop(1)],text=True).strip();"
    "sys.path.insert(0,str(pathlib.Path(p).parent));"
    "sys.argv[0]=p;runpy.run_path(p,run_name='__main__')"
)


def payload_files(root):
    paths = [root / name for name in ('install.py', 'launch.py', 'pyproject.toml', 'README.md', '.env.example', '.gitignore')]
    for pattern in ('nl2sh/*.py', 'scripts/*.py', 'scripts/*.ps1', 'docs/*.md',
                    'huggingface/*.md', 'notebooks/*.ipynb', 'tests/*.py'):
        paths.extend(sorted(root.glob(pattern)))
    if any(not path.is_file() or path.is_symlink() for path in paths):
        raise ValueError('Distribution payload is missing files or contains symbolic links.')
    return sorted(set(paths))


@contextlib.contextmanager
def source_payload():
    archive = Path(sys.argv[0]).resolve()
    if archive.is_file() and zipfile.is_zipfile(archive):
        with tempfile.TemporaryDirectory(prefix='nl2bash-installer-') as directory:
            root = Path(directory)
            with zipfile.ZipFile(archive) as bundle:
                entries = bundle.infolist()
                if len(entries) > 200 or sum(item.file_size for item in entries) > 5 * 1024 * 1024:
                    raise ValueError('Installer payload exceeds its size limit.')
                for item in entries:
                    path = PurePosixPath(item.filename)
                    if path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename:
                        raise ValueError('Unsafe archive path rejected.')
                    if stat.S_ISLNK(item.external_attr >> 16):
                        raise ValueError('Archive symbolic links are not allowed.')
                    if item.is_dir():
                        continue
                    target = root.joinpath(*path.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(bundle.read(item))
            yield root
    else:
        yield Path(__file__).resolve().parent


def atomic_write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def backup_once(path):
    backup = path.with_name(path.name + '.nl2bash.bak')
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)


def install_payload(source, destination):
    source = source.resolve()
    destination = destination.expanduser()
    if destination.is_symlink():
        raise ValueError('Installation directory cannot be a symbolic link.')
    if destination.exists() and any(destination.iterdir()):
        try:
            marker = json.loads((destination / MARKER).read_text())
        except (OSError, ValueError):
            raise ValueError(f'Refusing to overwrite an unrelated directory: {destination}') from None
        if not isinstance(marker, dict) or marker.get('application') != 'nl2bash':
            raise ValueError('Installation ownership marker is invalid.')
    destination.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        destination.chmod(0o700)
    paths = payload_files(source)
    for path in paths:
        target = destination / path.relative_to(source)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f'Refusing to replace a non-file installation target: {target}')
        for parent in target.parents:
            if parent == destination:
                break
            if parent.is_symlink():
                raise ValueError(f'Installation subdirectories cannot be symbolic links: {parent}')
    marker_data = {'application': 'nl2bash', 'version': VERSION, 'status': 'installing'}
    atomic_write(destination / MARKER, json.dumps(marker_data).encode(), 0o600)
    backup_root = destination / '.backups' / str(time.time_ns())
    if (destination / '.backups').is_symlink():
        raise ValueError('Installation backup directory cannot be a symbolic link.')
    for path in paths:
        relative = path.relative_to(source)
        target = destination / relative
        content = path.read_bytes()
        if target.exists():
            if target.read_bytes() == content:
                continue
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        atomic_write(target, content)
    marker_data['status'] = 'installed'
    atomic_write(destination / MARKER, json.dumps(marker_data).encode(), 0o600)
    return destination


def configure(app, endpoint=None, *, interactive=False):
    from nl2sh.client import endpoint_root
    path = app / '.env'
    if path.exists():
        if endpoint:
            endpoint = endpoint_root(endpoint)
            lines = path.read_text().splitlines()
            replacement = 'NL2SH_URL=' + shlex.quote(endpoint)
            found = False
            for i, line in enumerate(lines):
                if re.match(r'^\s*(?:export\s+)?NL2SH_URL\s*=', line):
                    lines[i] = replacement
                    found = True
            if not found:
                lines.append(replacement)
            backup_once(path)
            atomic_write(path, ('\n'.join(lines) + '\n').encode(), 0o600)
            print(f'Endpoint updated; existing keys preserved: {path}')
            return
        print(f'Existing configuration preserved: {path}')
        return
    if interactive and not endpoint:
        endpoint = input('Kaggle/ngrok endpoint URL (Enter to configure later): ').strip()
    if not endpoint:
        atomic_write(path, (app / '.env.example').read_bytes(), 0o600)
        print(f'Configure your endpoint later in: {path}')
        return
    endpoint = endpoint_root(endpoint)
    values = {'NL2SH_URL': endpoint}
    if interactive:
        key = getpass.getpass('NL2SH_API_KEY (hidden; Enter to enter it at launch): ').strip()
        if key:
            if len(key) < 32 or not key.isascii() or any(c.isspace() for c in key):
                raise ValueError('API key must have at least 32 ASCII characters without whitespace.')
            values['NL2SH_API_KEY'] = key
    content = '# Private configuration. Do not publish.\n'
    content += ''.join(f'{name}={shlex.quote(value)}\n' for name, value in values.items())
    atomic_write(path, content.encode(), 0o600)
    print(f'Configuration saved: {path} (key values hidden)')


def install_linux_launcher(app, home, *, interpreter=None):
    interpreter = interpreter or sys.executable
    home = Path(home)
    target = home / '.local/bin/nl2bash'
    if target.is_symlink() or (target.exists() and '# NL2BASH launcher' not in target.read_text()):
        raise ValueError(f'Refusing to replace an unrelated command: {target}')
    profiles = [home / '.profile', home / '.bashrc']
    if Path(os.environ.get('SHELL', '')).name == 'zsh':
        profiles.append(home / '.zshrc')
    for profile in profiles:
        if profile.is_symlink():
            raise ValueError(f'Profile is a symbolic link; resolve it before installation: {profile}')
    script = f'#!/bin/sh\n# NL2BASH launcher\nexec {shlex.quote(str(interpreter))} {shlex.quote(str(app / "launch.py"))} "$@"\n'
    atomic_write(target, script.encode(), 0o755)
    for profile in profiles:
        current = profile.read_text() if profile.exists() else ''
        if PATH_MARKER not in current:
            backup_once(profile)
            mode = stat.S_IMODE(profile.stat().st_mode) if profile.exists() else 0o644
            atomic_write(profile, (current.rstrip('\n') + '\n\n' + PATH_BLOCK).encode(), mode)
    print(f'Installed command: {target}')
    print('User shell PATH configured automatically. Open a new terminal to use nl2bash.')
    return target


def windows_path_value(current, entry):
    normal = lambda value: ntpath.normcase(ntpath.normpath(ntpath.expandvars(value.strip().strip('"'))))
    entries = [value for value in current.split(';') if value.strip()]
    if any(normal(value) == normal(entry) for value in entries):
        return current
    return current.rstrip(';') + (';' if current else '') + entry


def update_windows_path(bin_directory):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, 'Environment', access=winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, 'Path')
        except FileNotFoundError:
            current, kind = '', winreg.REG_EXPAND_SZ
        updated = windows_path_value(current, str(bin_directory))
        if updated != current:
            winreg.SetValueEx(key, 'Path', 0, kind, updated)
    result = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(0xffff, 0x1a, 0, ctypes.c_wchar_p('Environment'), 2, 5000, ctypes.byref(result))


def wsl_command(distribution=None, user=None):
    # Use the Windows system executable, never a wsl.exe from the current project folder.
    command = [str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/wsl.exe')]
    if distribution:
        command.extend(['--distribution', distribution])
    if user:
        command.extend(['--user', user])
    return command


def check_wsl(distribution=None, user=None):
    if not shutil.which('wsl.exe'):
        raise ValueError('Install WSL and a Linux distribution with Python 3.11+ first (wsl --install).')
    probe = (
        "import json,os,pwd,sys;print(json.dumps({'version':list(sys.version_info[:2]),"
        "'user':pwd.getpwuid(os.getuid()).pw_name,'uid':os.getuid(),"
        "'distribution':os.environ.get('WSL_DISTRO_NAME',''),'python':sys.executable}))"
    )
    result = subprocess.run([*wsl_command(distribution, user), '--exec', 'python3', '-c', probe],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('WSL could not run Python. Start your distribution and install Python 3.11+ first.')
    info = json.loads(result.stdout)
    if tuple(info['version']) < (3, 11) or info['uid'] == 0:
        raise ValueError('WSL needs Python 3.11+ and a non-root default user; use --wsl-user if needed.')
    if not re.fullmatch(r'[A-Za-z0-9._-]+', info['distribution']) or not re.fullmatch(r'[a-z_][a-z0-9_-]*', info['user']):
        raise ValueError('Unsupported WSL distribution or user name.')
    if not info['python'].isascii() or any(c in info['python'] for c in '"%\r\n'):
        raise ValueError('Unsupported WSL Python executable path.')
    return info


def windows_launcher(info):
    return (
        '@echo off\r\nREM NL2BASH launcher\r\n'
        f'"%SystemRoot%\\System32\\wsl.exe" --distribution {info["distribution"]} --user {info["user"]} '
        f'--cd "%CD%" --exec "{info["python"]}" -c "{WINDOWS_BOOTSTRAP}" '
        '"%LOCALAPPDATA%\\nl2bash\\app\\launch.py" %*\r\n'
        'exit /b %errorlevel%\r\n'
    )


def install_windows(source, distribution=None, user=None, endpoint=None, *, interactive=False):
    info = check_wsl(distribution, user)
    app = Path(os.environ['LOCALAPPDATA']) / 'nl2bash/app'
    command = Path.home() / '.local/bin/nl2bash.cmd'
    if command.is_symlink() or (command.exists() and 'REM NL2BASH launcher' not in command.read_text()):
        raise ValueError(f'Refusing to replace an unrelated command: {command}')
    app = install_payload(source, app)
    configure(app, endpoint, interactive=interactive)
    translate = subprocess.run([*wsl_command(info['distribution'], info['user']), '--exec', 'wslpath', '-a', str(app)],
                               capture_output=True, text=True, check=True, timeout=10)
    linux_app = translate.stdout.strip()
    subprocess.run([*wsl_command(info['distribution'], info['user']), '--exec', info['python'],
                    linux_app + '/install.py', '--launcher-only', linux_app], check=True, timeout=30)
    atomic_write(command, windows_launcher(info).encode('ascii'))
    update_windows_path(command.parent)
    print(f'Installed Windows command: {command}')
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description='Install nl2bash, its launchers, user PATH, and Kaggle notebooks.')
    parser.add_argument('--distribution', help='Windows: WSL distribution (default: your WSL default)')
    parser.add_argument('--wsl-user', help='Windows: non-root Linux user')
    parser.add_argument('--endpoint', help='Your HTTPS inference endpoint; API keys are never command-line arguments')
    parser.add_argument('--launcher-only', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 11):
            raise ValueError('Run this installer with Python 3.11 or newer.')
        if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0:
            raise ValueError('Run the installer as your normal user, without sudo.')
        if args.launcher_only:
            app = args.launcher_only.resolve()
            if not (app / 'launch.py').is_file() or not (app / 'nl2sh').is_dir():
                raise ValueError('The app directory is incomplete.')
            install_linux_launcher(app, Path.home())
            return 0
        with source_payload() as source:
            if os.name == 'nt':
                app = install_windows(source, args.distribution, args.wsl_user, args.endpoint,
                                      interactive=sys.stdin.isatty())
            elif sys.platform.startswith('linux'):
                app = install_payload(source, Path.home() / '.local/share/nl2bash')
                configure(app, args.endpoint, interactive=sys.stdin.isatty())
                install_linux_launcher(app, Path.home())
            else:
                raise ValueError('This version supports Linux and Windows with WSL.')
        print(f'Kaggle server notebook: {app / "notebooks/nl2sh_kaggle_ngrok.ipynb"}')
        print(f'Inference test notebook: {app / "notebooks/nl2sh_inference_test.ipynb"}')
        print(f'Configuration: {app / ".env"}')
        print('Installation complete. Open a NEW terminal and run: nl2bash')
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(f'Installation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
