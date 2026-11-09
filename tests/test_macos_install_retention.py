"""Run the real shell installer on signed synthetic macOS bundles, without any browser."""
import plistlib
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='macOS codesign/lsof contract')
INSTALLER = Path(__file__).parents[1] / 'packaging/install-macos.sh'


@pytest.fixture
def app_pair(tmp_path):
    """Build tiny ad-hoc-signed fixtures; never read a real application or user store."""
    source = tmp_path / 'Payload.app'
    native = source / 'Contents/MacOS'
    native.mkdir(parents=True)
    code = tmp_path / 'main.c'
    code.write_text('#include <unistd.h>\n#include <string.h>\nint main(int n,char**v){if(n>1 && !strcmp(v[1],"--hold"))sleep(30);return 0;}\n')
    subprocess.run(['xcrun', 'clang', str(code), '-o', str(native / 'CodeAtlas')], check=True)
    (source / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'local.code-atlas.desktop', 'CFBundleExecutable': 'CodeAtlas', 'CFBundlePackageType': 'APPL'}))
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(source)], check=True, capture_output=True)
    return source, tmp_path / 'Installed.app'


def install(source, target, tmp_path):
    """Invoke the shipped script with an isolated external data directory."""
    import os
    environment = dict(os.environ, ATLAS_DATA_DIR=str(tmp_path / 'user-data'))
    return subprocess.run(['/bin/sh', str(INSTALLER), str(source), str(target)], env=environment, capture_output=True, text=True, timeout=60)


def test_success_retires_previous_but_preserves_embedded_configuration(app_pair, tmp_path):
    """Success leaves zero obsolete app bundles unless protected data prevents retirement."""
    source, target = app_pair
    assert install(source, target, tmp_path).returncode == 0
    assert install(source, target, tmp_path).returncode == 0
    assert not list(tmp_path.glob('Installed.previous-*.app'))
    local = target / 'Contents/APPSETTINGS.LOCAL.JSON'
    local.write_bytes(b'synthetic-config')
    assert install(source, target, tmp_path).returncode == 0
    leftovers = list(tmp_path.glob('Installed.previous-*.app'))
    assert len(leftovers) == 1
    assert (leftovers[0] / 'Contents/APPSETTINGS.LOCAL.JSON').read_bytes() == b'synthetic-config'


def test_open_resource_and_foreign_identity_block_replacement(app_pair, tmp_path):
    """Another process may hold resources even without running the app executable."""
    source, target = app_pair
    assert install(source, target, tmp_path).returncode == 0
    resource = target / 'Contents/held-resource'
    resource.write_bytes(b'synthetic')
    with resource.open('rb'):
        refused = install(source, target, tmp_path)
    assert refused.returncode != 0 and 'still in use' in refused.stderr
    info = target / 'Contents/Info.plist'
    value = plistlib.loads(info.read_bytes()); value['CFBundleIdentifier'] = 'foreign.product'
    info.write_bytes(plistlib.dumps(value))
    refused = install(source, target, tmp_path)
    assert refused.returncode != 0 and 'Unexpected application identity' in refused.stderr
    assert target.exists() and not list(tmp_path.glob('Installed.previous-*.app'))
