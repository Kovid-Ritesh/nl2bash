"""Start the agent from any working directory without installing dependencies."""

import os
import shlex
from pathlib import Path

from nl2sh.cli import main


def load_saved_settings(path):
    """Read the existing private configuration as data, without sourcing shell code."""
    if not path.is_file():
        return
    allowed = {"NL2SH_API_KEY", "NL2SH_URL"}
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ").strip()
        name, separator, value = line.partition("=")
        if not separator or name.strip() not in allowed:
            continue
        name = name.strip()
        if os.environ.get(name):
            continue
        parts = shlex.split(value, comments=True)
        if len(parts) != 1:
            raise ValueError(f"Invalid saved setting: {name}")
        os.environ[name] = parts[0]


if __name__ == "__main__":
    try:
        project = Path(__file__).resolve().parent
        load_saved_settings(project / ".env")
        load_saved_settings(project.parent / "nl2sh-modal" / ".env")
    except (OSError, ValueError):
        raise SystemExit("Could not read saved NL2SH settings. Check your .env configuration; values were not displayed.")
    raise SystemExit(main())
