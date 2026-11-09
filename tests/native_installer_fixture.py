"""Isolated macOS installer fixtures: real identity/staging and explicit signing doubles."""
from pathlib import Path
import os
import plistlib


def prepare_installer(tmp_path: Path, script: Path, bundles: list[Path], *, retain: Path | None = None):
    """
    Prepare fake app identities and a test-only signing adapter without weakening production.

    Args:
        tmp_path: Test-owned root for the copied script and signing command.
        script: Actual production installer, read only.
        bundles: Synthetic source/destination bundles receiving valid product identity.
        retain: Optional old bundle with legacy embedded data that must be retained.
    Returns:
        Copied installer path and PATH override; native ditto/PlistBuddy/lsof stay real.
    """
    for bundle in bundles:
        (bundle / 'Contents').mkdir(parents=True, exist_ok=True)
        with (bundle / 'Contents/Info.plist').open('wb') as stream:
            plistlib.dump({'CFBundleIdentifier': 'local.code-atlas.desktop'}, stream)
    if retain is not None:
        (retain / '.atlas').mkdir()
        (retain / '.atlas/projects.json').write_text('{}')
    tools = tmp_path / 'signing-tools'
    tools.mkdir()
    signer = tools / 'codesign'
    signer.write_text('#!/bin/sh\nexit 0\n')
    signer.chmod(0o755)
    copied = tmp_path / 'installer-fixture.sh'
    copied.write_text(script.read_text().replace('/usr/bin/codesign', 'codesign'))
    return copied, {'PATH': str(tools) + os.pathsep + os.environ['PATH']}
