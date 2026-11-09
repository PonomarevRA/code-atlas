"""macOS user-presence guard for reducing browser privacy; never a general agent command."""
import hashlib
import os
import subprocess
import sys
from .runtime_paths import resource_root


class HumanConfirmationRequired(ValueError):
    """Only a fixed public error, never OS authentication diagnostics."""


def confirm_privacy_change():
    """Run the fixed signed helper with no arguments; cancellation, timeout or tamper refuses reduction."""
    if sys.platform != 'darwin':
        return False
    try:
        path = resource_root() / 'vendor/atlas-browser-auth/confirm'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != '6cb60170c0a2b6085bc6371a55cb4c160bcef8a871e20315cfed5afd7368bb46':
            return False
        environment = {key: value for key, value in os.environ.items() if not key.startswith(('DYLD_', 'LD_'))}
        result = subprocess.run([str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=22, check=False, env=environment)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
