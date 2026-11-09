"""Register the source-install native host explicitly, without installing a browser or extension."""
import argparse
import json
from pathlib import Path
import re
import shlex
import sys

HOST_NAME = 'com.code_atlas.browser_capture'


def install(extension_id, destination, *, port=8765, interpreter=sys.executable):
    """Write one manifest/launcher for a precise extension ID and existing Python runtime."""
    if re.fullmatch('[a-p]{32}', extension_id) is None or not 1024 <= port <= 65535:
        raise ValueError('Expected an exact Chromium extension ID and loopback port')
    if sys.platform not in {'darwin', 'linux'}:
        raise ValueError('Source native-host installer supports macOS/Linux only')
    root = Path(__file__).resolve().parents[1]
    runtime = Path(interpreter).absolute()
    if not runtime.is_file():
        raise ValueError('Existing Python interpreter required')
    destination = Path(destination).expanduser().absolute()
    if destination.is_symlink():
        raise ValueError('Native-host directory must not be a symlink')
    destination.mkdir(parents=True, exist_ok=True)
    launcher = destination / 'code-atlas-browser-host'
    manifest = destination / (HOST_NAME + '.json')
    for path in (launcher, manifest):
        if path.is_symlink():
            raise ValueError('Native-host files must not be symlinks')
    command = [str(runtime), '-I', str(root / 'tools/browser_capture_host.py'), '--extension-id', extension_id, '--port', str(port)]
    launcher.write_text('#!/bin/sh\nexec ' + ' '.join(shlex.quote(part) for part in command) + ' "$@"\n', encoding='utf-8')
    launcher.chmod(0o700)
    payload = {'name': HOST_NAME, 'description': 'Temporary Code Atlas page capture',
               'path': str(launcher), 'type': 'stdio', 'allowed_origins': ['chrome-extension://' + extension_id + '/']}
    manifest.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    manifest.chmod(0o600)
    return manifest


def main():
    """Require an explicit destination; no default browser profile is changed implicitly."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extension-id', required=True)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    print(install(args.extension_id, args.destination, port=args.port))


if __name__ == '__main__':
    main()
