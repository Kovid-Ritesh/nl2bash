"""Build one portable Python zipapp containing the installer, app, and notebooks."""

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from install import payload_files


def build(output=None):
    output = Path(output) if output else ROOT / 'dist/nl2bash-installer.pyz'
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = payload_files(ROOT)
    contents = {}
    for path in paths:
        data = path.read_bytes()
        if re.search(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|hf_[A-Za-z0-9]{25,}|as-[A-Za-z0-9]{20,}|ws-[A-Za-z0-9]{20,})', data):
            raise ValueError(f'Possible credential in payload: {path.relative_to(ROOT)}')
        if path.suffix == '.ipynb':
            notebook = json.loads(data)
            for cell in notebook['cells']:
                if cell['cell_type'] == 'code' and (cell.get('outputs') or cell.get('execution_count') is not None):
                    raise ValueError('Notebook outputs must be cleared before packaging.')
        contents[path.relative_to(ROOT).as_posix()] = data
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('__main__.py', 'from install import main\nraise SystemExit(main())\n')
        for name, data in contents.items():
            bundle.writestr(name, data)
    print(f'Built {output} ({output.stat().st_size:,} bytes; {len(contents)} payload files).')
    return output


if __name__ == '__main__':
    build()
