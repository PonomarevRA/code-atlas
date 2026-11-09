"""Restore certified native resource bytes after PyInstaller's Mach-O processing.

PyInstaller may rewrite load commands/signatures even for data files, and macOS
BUNDLE splits code/data into symlinked directories. A certified parser closure
must remain byte-exact and self-contained; changing its integrity rules would
hide a packaging error.
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

from build_target import normalize_target


def _managed_directory(path: Path, distribution: Path) -> None:
    """Reject symlinked parent directories before touching known build outputs."""
    relative = path.relative_to(distribution)
    current = distribution
    if current.is_symlink():
        raise ValueError('Distribution root must not be a symlink.')
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError('Native resource parent must not be a symlink: ' + str(current))
    if path.resolve().is_relative_to(distribution.resolve()) is False:
        raise ValueError('Native resource destination escaped the distribution.')


def _has_coherent_resource_alias(application: Path, distribution: Path) -> bool:
    """Allow only PyInstaller's exact Frameworks/resources → Resources/resources alias."""
    alias = application / 'Contents' / 'Frameworks' / 'resources'
    if not alias.is_symlink():
        return False
    physical = application / 'Contents' / 'Resources' / 'resources'
    _managed_directory(alias, distribution)
    # Include the physical resource directory itself in the no-parent-symlink check.
    _managed_directory(physical / 'validation-leaf', distribution)
    if alias.resolve() != physical.resolve():
        raise ValueError('Unexpected application resource directory alias: ' + str(alias))
    return True


def _replace_pack(source: Path, destination: Path, distribution: Path) -> None:
    """Replace only one known target pack; copy2 retains certified bytes and modes."""
    _managed_directory(destination, distribution)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + '.' + uuid4().hex + '.staging')
    try:
        shutil.copytree(source, staging, copy_function=shutil.copy2)
        if destination.is_symlink():
            destination.unlink()
        elif destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def restore_native_resources(root: Path, target: str, *, sign=True) -> dict:
    """Restore matching lnav resources inside root/dist and re-seal only the outer app.

    Verification of the authoritative source pack precedes every output mutation.
    This function accepts a repository root, never an installed application or
    user data directory. Missing source packs remain an unavailable capability.
    """
    root = Path(root).resolve()
    target = normalize_target(target)
    source = root / 'resources' / 'lnav' / target
    if not source.exists():
        return {'target': target, 'lnavRestored': False, 'destinations': []}
    from atlas.lnav_runtime import capability
    if not capability(source).get('available'):
        raise ValueError('Authoritative lnav source pack failed verification: ' + target)
    distribution = root / 'dist'
    if distribution.is_symlink():
        raise ValueError('Distribution root must not be a symlink.')
    locations = []
    onedir = distribution / 'CodeAtlas' / '_internal'
    if onedir.is_dir():
        locations.append(onedir / 'resources' / 'lnav' / target)
    application = distribution / 'Code Atlas.app'
    framework_pack = None
    reuse_resource_alias = False
    if target.startswith('darwin-') and application.is_dir():
        locations.append(application / 'Contents' / 'Resources' / 'resources' / 'lnav' / target)
        framework_pack = application / 'Contents' / 'Frameworks' / 'resources' / 'lnav' / target
        reuse_resource_alias = _has_coherent_resource_alias(application, distribution)
    if not locations:
        raise ValueError('No managed native build outputs were found.')
    # Validate all output roots first, avoiding a partial restore on unsafe layouts.
    for destination in locations:
        _managed_directory(destination, distribution)
    if framework_pack is not None and not reuse_resource_alias:
        _managed_directory(framework_pack, distribution)
    for destination in locations:
        _replace_pack(source, destination, distribution)
        if not capability(destination).get('available'):
            raise ValueError('Restored lnav pack failed verification: ' + str(destination))
    if framework_pack is not None:
        # One directory link resolves to a complete regular-file pack. Splitting
        # individual files would violate runtime boundaries. Resources also avoids
        # codesign interpreting .dylib-named license directories as nested bundles.
        if not reuse_resource_alias:
            physical_pack = locations[-1]
            if framework_pack.is_symlink():
                framework_pack.unlink()
            elif framework_pack.exists():
                shutil.rmtree(framework_pack)
            framework_pack.parent.mkdir(parents=True, exist_ok=True)
            relative = os.path.relpath(physical_pack, framework_pack.parent)
            framework_pack.symlink_to(relative, target_is_directory=True)
        # A pre-existing whole-resources alias already reaches the physical pack.
        # Creating a leaf link through it would overwrite the pack with a self-loop.
        if not capability(framework_pack).get('available'):
            raise ValueError('Linked application lnav pack failed verification.')
    if target.startswith('darwin-') and application.is_dir() and sign:
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none',
                        str(application)], check=True)
        # --deep is verification only: recursive signing would change certified bytes.
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2',
                        str(application)], check=True)
    return {'target': target, 'lnavRestored': True,
            'destinations': [str(path.relative_to(distribution)) for path in locations],
            'applicationPackLink': str(framework_pack.relative_to(distribution)) if framework_pack else None}


def main() -> None:
    """Support repair of managed build outputs without invoking a fresh compilation."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--target', required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    print(restore_native_resources(args.source_root, args.target))


if __name__ == '__main__':
    main()
