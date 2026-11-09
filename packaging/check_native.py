"""Run the packaged offline check without inheriting a development Python setup."""

import os
from pathlib import Path
import subprocess
import sys


def main() -> None:
    """Fail release validation if the actual standalone executable fails its check."""
    root = Path(__file__).resolve().parent.parent
    name = 'CodeAtlas.exe' if os.name == 'nt' else 'CodeAtlas'
    executables = [root / 'dist' / 'CodeAtlas' / name]
    if sys.platform == 'darwin':
        executables.append(root / 'dist' / 'Code Atlas.app' / 'Contents' / 'MacOS' / 'CodeAtlas')
    environment = dict(os.environ)
    environment.pop('PYTHONHOME', None)
    environment.pop('PYTHONPATH', None)
    # Workers must use the embedded executable, never discover Python via PATH.
    environment['PATH'] = ''
    for executable in executables:
        subprocess.run([str(executable), '--native-document-check'], env=environment,
                       check=True, timeout=240)


if __name__ == '__main__':
    main()
