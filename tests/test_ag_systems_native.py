"""Actual macOS host lifecycle and reentry checks use no user windows, browser or service data."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('xcrun'), reason='native macOS compiler required')
def test_native_tray_close_flood_and_modal_reentry(tmp_path):
    """Compile the actual shared Swift code with a private, invisible AppKit regression fixture."""
    delegate = (ROOT / 'packaging/safari-browser/AppDelegate.swift').read_text().replace('@main\n', '', 1)
    shared = (ROOT / 'packaging/desktop/AGSystemsTray.swift').read_text()
    shared = shared.replace('com.ag-systems.tray.v1', 'test.ag-systems.' + uuid.uuid4().hex)
    shared = shared.replace('import Cocoa', 'import Cocoa\nvar trayIdentityLookupCount = 0', 1)
    marker = '    private func verify(_ appID: String, _ pid: pid_t) -> Bool {'
    assert marker in shared
    shared = shared.replace(marker, marker + '\n        trayIdentityLookupCount += 1')
    fixture = (ROOT / 'tests/fixtures/native/TrayChecks.swift').read_text()
    source = tmp_path / 'TrayChecks.swift'
    source.write_text(delegate + '\n' + shared + '\n' + fixture)
    executable = tmp_path / 'tray-checks'
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-framework', 'Cocoa', str(source), '-o', str(executable)],
                   check=True, capture_output=True, timeout=60)
    result = subprocess.run([str(executable)], check=True, capture_output=True, text=True, timeout=20)
    evidence = json.loads(result.stdout)
    assert evidence['status'] == 'PASS'
    assert evidence['modalCallbackReentryCoalesced'] is True
    assert evidence['thousandPresenceLookupsAtMost32'] is True
    assert evidence['windowActivated'] is False
