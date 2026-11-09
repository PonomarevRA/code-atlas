"""Private environment identity shared by semantic coordination and discovery caches."""
from hashlib import sha256
import json
import os
import sys


DISCOVERY_ENVIRONMENT_VERSION = 'atlas-semantic-environment-v1'
DISCOVERY_ENVIRONMENT_VARIABLES = (
    'PATH', 'PATHEXT', 'HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH',
    'ProgramFiles', 'ProgramW6432', 'DOTNET_ROOT', 'JAVA_HOME', 'GOROOT',
    'GOPATH', 'SDKROOT', 'DEVELOPER_DIR', 'TOOLCHAINS', 'RUSTUP_HOME',
    'CARGO_HOME', 'ATLAS_JEDI_PYTHON', 'ATLAS_TYPESCRIPT_PATH',
)


def discovery_environment_fingerprint() -> str:
    """Hash host choices that can change semantic engine discovery or toolchain context.

    Returns a SHA-256 hex digest; raw environment values and executable/bundle
    paths are never returned. Only a fixed allowlist and Python host identity
    are read: no filesystem checks, process launches or recursive discovery.
    An unchanged PATH with a newly installed binary still follows the existing
    discovery TTL; an explicit forced recheck remains available to the user.
    """
    identity = {
        'version': DISCOVERY_ENVIRONMENT_VERSION,
        'environment': {name: os.environ.get(name) for name in DISCOVERY_ENVIRONMENT_VARIABLES},
        'python': {'executable': sys.executable, 'prefix': sys.prefix,
                   'basePrefix': sys.base_prefix, 'frozen': bool(getattr(sys, 'frozen', False)),
                   'bundleRoot': str(getattr(sys, '_MEIPASS', ''))},
    }
    payload = json.dumps(identity, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
    return sha256(payload).hexdigest()
