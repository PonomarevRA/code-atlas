"""Standalone source worker bootstrap; never opens a ProjectStore or data directory.

Invoked by the trusted supervisor with isolated Python and an owned copied bundle.
Only the fixed module-projection operation is available. Hashes verify identity,
not publisher trust; this is process isolation, not a sandbox for untrusted code.
"""

from contextlib import redirect_stdout
from hashlib import sha256
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import struct
import sys

MAX_BYTES = 8 * 1024 * 1024


def send(value):
    """Write one bounded response; stdout belongs exclusively to this protocol."""
    data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(data) > MAX_BYTES:
        raise ValueError('Response budget exceeded')
    sys.stdout.buffer.write(struct.pack('!I', len(data)) + data)
    sys.stdout.buffer.flush()


def receive():
    """Bound payload allocation before parsing the next local pipe request."""
    header = sys.stdin.buffer.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError('Incomplete header')
    size = struct.unpack('!I', header)[0]
    if size > MAX_BYTES:
        raise ValueError('Request budget exceeded')
    data = sys.stdin.buffer.read(size)
    if len(data) != size:
        raise ValueError('Incomplete payload')
    def reject_constant(value):
        """The child enforces the same finite JSON boundary as its supervisor."""
        raise ValueError('Nonfinite constant')
    def finite_float(value):
        """Reject overflow exponents as well as explicit infinity tokens."""
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Nonfinite number')
        return number
    return json.loads(data, parse_constant=reject_constant, parse_float=finite_float)


def main():
    """Verify deployment bytes, import actual compute code and serve finite jobs."""
    root, expected_hash = Path(sys.argv[1]), sys.argv[2]
    encoded = (root / 'manifest.json').read_bytes()
    if sha256(encoded).hexdigest() != expected_hash:
        raise ValueError('Manifest mismatch')
    manifest = json.loads(encoded)
    if (manifest['protocol'] != 'atlas-module-worker/v1'
            or manifest['semantics'] != 'module-analysis-v1'
            or manifest['storage'] != 'no-store/v1'
            or manifest['runtime'] != {'python': platform.python_version(),
                                       'networkx': importlib.metadata.version('networkx')}):
        raise ValueError('Incompatible worker profile')
    for name, expected in manifest['files'].items():
        if sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Source mismatch')
    # -I excludes checkout/CWD/PYTHONPATH. Only this verified owned source copy is
    # added; installed dependencies still belong to the selected interpreter.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from atlas.module_compute import compute_module_projection
    identity = {'generationId': manifest['generationId'], 'bundleHash': expected_hash,
                'computeHash': manifest['files']['atlas/module_compute.py'], 'pid': os.getpid(),
                'protocol': manifest['protocol'], 'semantics': manifest['semantics'],
                'storage': manifest['storage'], 'runtime': manifest['runtime'],
                'bootstrapHash': sha256(Path(__file__).read_bytes()).hexdigest()}
    send({'ready': True, 'identity': identity})
    while True:
        message = receive()
        if message is None:
            break
        request_id = message.get('requestId')
        if message.get('operation') != 'module-projection' or not isinstance(request_id, str):
            raise ValueError('Unsupported operation')
        try:
            with redirect_stdout(sys.stderr):
                result = compute_module_projection(message['projection'],
                    algorithms=message['algorithms'], seed=message['seed'], resolution=message['resolution'])
            send({'requestId': request_id, 'identity': identity, 'result': result})
        except Exception as error:
            # The approved function's domain errors remain typed. Unexpected
            # implementation exceptions do not expose stack traces or input.
            code = getattr(error, 'code', 'worker_compute_failed')
            status = getattr(error, 'status', 503)
            send({'requestId': request_id, 'identity': identity,
                  'error': {'code': code, 'status': status, 'message': 'Module computation failed'}})


if __name__ == '__main__':
    main()
