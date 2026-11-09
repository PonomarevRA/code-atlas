"""Run an isolated source Atlas for checkout-session integration probes."""

import argparse
import asyncio
import json
from pathlib import Path
import socket
import sys
import tempfile

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from atlas.server import create_app
from atlas.process_lock import ProcessLock


def main():
    """
    Start source Atlas on an ephemeral loopback port with a new private data root.

    The operator owns stopping the process. No installed application, canonical
    map registry, native integration or MCP configuration is changed.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    data = args.data_dir or Path(tempfile.mkdtemp(prefix='atlas-checkout-source-'))
    data.mkdir(parents=True, exist_ok=True)
    marker = data / '.atlas-checkout-source.json'
    if data.is_symlink() or (any(data.iterdir()) and
            (not marker.is_file() or marker.is_symlink() or marker.read_text() != 'atlas-source-probe/v1\n')):
        parser.error('--data-dir must be empty or an owned source-probe directory')
    with ProcessLock(data / 'server.lock'):
        marker.write_text('atlas-source-probe/v1\n')
        with socket.socket() as connection:
            connection.bind(('127.0.0.1', 0))
            connection.listen(128)
            port = connection.getsockname()[1]
            app = create_app(data)
            print(json.dumps({'port': port, 'dataDirectory': str(data),
                              'settings': f'http://127.0.0.1:{port}/technical.html#atlas-temporary-checkouts'}), flush=True)
            server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))
            asyncio.run(server.serve(sockets=[connection]))


if __name__ == '__main__':
    main()
