"""ARM build helper confines dependency discovery and pins primary source identities."""

import hashlib
import importlib.util
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

import pytest


def helper_module():
    """Load the build command independently from the PyPI packaging distribution."""
    path = Path(__file__).parents[1] / 'packaging/build_lnav_arm64.py'
    spec = importlib.util.spec_from_file_location('atlas_arm_builder_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_environment_pins_arm_sdk_baseline_and_blocks_x86_search_paths(tmp_path):
    """Inherited developer include/library variables cannot silently link x86 Homebrew artifacts."""
    module = helper_module()
    prefix, sdk, tools = tmp_path / 'prefix', tmp_path / 'sdk', tmp_path / 'tools'
    with patch.dict(module.os.environ, {'CPATH': '/usr/local/include', 'LIBRARY_PATH': '/usr/local/lib'}):
        env = module.build_environment(prefix, sdk, tools, '13.0')
    assert env['CC'].endswith('clang -arch arm64')
    assert env['MACOSX_DEPLOYMENT_TARGET'] == '13.0'
    assert env['LDFLAGS'] == '-L' + str(prefix / 'lib')
    assert env['PKG_CONFIG_LIBDIR'] == str(prefix / 'lib/pkgconfig')
    assert env['CFLAGS'].startswith('-O2 ')
    assert 'CPATH' not in env and 'LIBRARY_PATH' not in env


def test_sdk_can_be_selected_without_user_specific_sdk_path(tmp_path):
    """SDKROOT/--sdk accept a validated SDK layout, independent of a machine's username/version."""
    module = helper_module()
    sdk = tmp_path / 'Selected.sdk'
    header = sdk / 'usr/include/c++/v1/initializer_list'
    header.parent.mkdir(parents=True)
    header.write_text('fixture')
    with patch.dict(module.os.environ, {'SDKROOT': str(sdk)}):
        assert module.select_sdk() == sdk
    with pytest.raises(ValueError, match='installed macOS SDK'):
        module.select_sdk(tmp_path / 'missing')


def test_pinned_source_mirror_fallback_keeps_upstream_hash(tmp_path):
    """A reachable GNU mirror is accepted only when its bytes match the primary release pin."""
    module = helper_module()
    payload = b'pinned archive bytes'
    expected = hashlib.sha256(payload).hexdigest()
    package = {'name': 'fixture', 'url': 'https://primary.invalid/source',
               'mirrors': ['https://mirror.invalid/source'], 'sha256': expected}
    hashing = SimpleNamespace(sha256=lambda path: hashlib.sha256(path.read_bytes()).hexdigest())
    with patch.object(module.urllib.request, 'urlopen', side_effect=[URLError('offline'), BytesIO(payload)]):
        archive = module.source_archive(package, tmp_path, hashing)
    assert archive.read_bytes() == payload
    with patch.object(module.urllib.request, 'urlopen', side_effect=AssertionError('unneeded download')):
        assert module.source_archive(package, tmp_path, hashing) == archive
    archive.write_bytes(b'corrupt cache')
    with pytest.raises(ValueError, match='Cached source hash'):
        module.source_archive(package, tmp_path, hashing)


def test_complete_pcre_license_is_preserved(tmp_path):
    """PCRE COPYING is only a pointer; the full British-spelled LICENCE.md must be included."""
    module = helper_module()
    source, destination = tmp_path / 'source', tmp_path / 'notices'
    source.mkdir()
    (source / 'COPYING').write_text('See LICENCE')
    (source / 'LICENCE.md').write_text('Full BSD exception terms')
    module.copy_notices(source, destination)
    assert (destination / 'LICENCE.md').read_text() == 'Full BSD exception terms'


def test_foreign_or_newer_dependency_cannot_be_reused(tmp_path):
    """A source pin marker does not override actual native CPU/load-command validation."""
    module = helper_module()
    package = {'name': 'fixture', 'library': 'fixture.dylib', 'sha256': 'fixed'}
    (tmp_path / 'fixture.installed').write_text('fixed')
    (tmp_path / 'lib').mkdir()
    (tmp_path / 'lib/fixture.dylib').write_bytes(b'fixture')
    helper = SimpleNamespace(_check_macho_architecture=lambda path, arch: None,
                             _macho_build_version=lambda path, arch: {'minos': '15.4'},
                             _version_tuple=lambda value: tuple(int(part) for part in value.split('.')))
    assert not module.dependency_current(package, tmp_path, helper, '13.0')
