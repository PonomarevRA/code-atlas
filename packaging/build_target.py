"""Normalize standalone targets and refuse to label a foreign architecture as native."""

import json
from pathlib import Path
import platform
import struct
import sys
import sysconfig

_OS = {'darwin': 'darwin', 'macos': 'darwin', 'mac': 'darwin', 'osx': 'darwin',
       'linux': 'linux', 'win32': 'win32', 'windows': 'win32', 'win': 'win32'}
_ARCH = {'x86_64': 'x86_64', 'x86-64': 'x86_64', 'amd64': 'x86_64', 'x64': 'x86_64',
         'arm64': 'arm64', 'aarch64': 'arm64'}


def normalize_target(value: str) -> str:
    """Accept familiar OS/CPU aliases and return the runtime-pack directory key."""
    system, separator, machine = value.strip().lower().partition('-')
    if not separator or system not in _OS or machine not in _ARCH:
        raise ValueError('Expected OS-architecture, e.g. macos-arm64, linux-x64 or windows-arm64.')
    return _OS[system] + '-' + _ARCH[machine]


def process_target(*, system=None, machine=None, python_platform=None, pointer_bits=None) -> str:
    """Identify the current Python process, including x64 emulation on ARM hosts."""
    system = sys.platform if system is None else system
    machine = platform.machine() if machine is None else machine
    python_platform = sysconfig.get_platform() if python_platform is None else python_platform
    pointer_bits = struct.calcsize('P') * 8 if pointer_bits is None else pointer_bits
    if pointer_bits != 64:
        raise ValueError('Standalone builds require a 64-bit Python process.')
    if system == 'win32':
        # platform.machine may describe the ARM host while Python itself is x64.
        windows_arch = {'win-amd64': 'x86_64', 'win-arm64': 'arm64'}
        if python_platform.lower() not in windows_arch:
            raise ValueError('Unsupported Windows Python build platform: ' + python_platform)
        machine = windows_arch[python_platform.lower()]
    return normalize_target(system + '-' + machine)


def select_target(requested=None, *, actual=None) -> str:
    """Fail before compilation if the requested target differs from this interpreter."""
    actual = process_target() if actual is None else normalize_target(actual)
    target = actual if requested is None else normalize_target(requested)
    if target != actual:
        raise ValueError(f'Cannot build {target} using {actual} Python. Use a matching native runner/interpreter.')
    return target


def lnav_pack_data(root: Path, target: str) -> list[tuple[str, str]]:
    """Include only the matching certified pack; never ship an Intel pack in ARM output."""
    target = normalize_target(target)
    directory = Path(root) / 'resources' / 'lnav' / target
    manifest_path = directory / 'manifest.json'
    if not directory.exists():
        return []
    if not manifest_path.is_file():
        raise ValueError('lnav pack is incomplete: ' + target)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('platform') != target or manifest.get('certified') is not True:
        raise ValueError('lnav pack is not certified for ' + target)
    from atlas.lnav_runtime import capability
    if not capability(directory).get('available'):
        raise ValueError('lnav runtime verification failed for ' + target)
    return [(str(directory), 'resources/lnav/' + target)]
