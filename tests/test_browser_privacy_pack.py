"""Offline SDK packaging preserves pinned bytes and refuses redirected build roots."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest


def module():
    """Load the standalone packaging helper without a globally installed packaging namespace."""
    path=Path(__file__).resolve().parents[1]/'packaging/browser_privacy_pack.py'
    spec=importlib.util.spec_from_file_location('browser_privacy_pack_fixture',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def fixture(tmp_path):
    """No real SDK or user files are modified by the build-boundary tests."""
    source=tmp_path/'vendor/ag-privacy';source.mkdir(parents=True)
    payload=b'synthetic-native-artifact';(source/'adapter.py').write_bytes(payload)
    (source/'manifest.json').write_text(json.dumps({'files':{'adapter.py':hashlib.sha256(payload).hexdigest()}}))
    target=tmp_path/'dist/CodeAtlas/_internal/vendor/ag-privacy';target.mkdir(parents=True)
    return source,target,payload


def test_source_only_build_omits_privacy_sdk(tmp_path):
    """A source-only build can proceed without the separately supplied SDK."""
    helper = module()
    assert helper.prepared_privacy_data(tmp_path) == {}
    data = helper.privacy_pack_data(tmp_path)
    assert {destination for _, destination in data} == {'vendor/atlas-browser-auth'}
    helper.restore_privacy_pack(tmp_path)
    assert not (tmp_path / 'build').exists()
    assert not (tmp_path / 'dist').exists()

    sdk = tmp_path / 'vendor/ag-privacy'
    sdk.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        helper.privacy_pack_data(tmp_path)


def test_exact_restore_and_bad_hash_refusal(tmp_path):
    source,target,payload=fixture(tmp_path);helper=module()
    helper.restore_privacy_pack(tmp_path);assert (target/'adapter.py').read_bytes()==payload
    (source/'adapter.py').write_bytes(b'tampered')
    with pytest.raises(ValueError):helper.restore_privacy_pack(tmp_path)
    assert (target/'adapter.py').read_bytes()==payload


def test_redirected_distribution_is_not_written(tmp_path):
    source,target,payload=fixture(tmp_path);helper=module()
    outside=tmp_path/'outside';outside.mkdir()
    import shutil
    shutil.rmtree(tmp_path/'dist');(tmp_path/'dist').symlink_to(outside)
    with pytest.raises(ValueError):helper.restore_privacy_pack(tmp_path)
    assert list(outside.iterdir())==[]


def test_exact_pyinstaller_architecture_alias_is_restored(tmp_path):
    """Only the known Resources→Frameworks architecture alias may retain a binary parent link."""
    source=tmp_path/'vendor/ag-privacy';(source/'osx-arm64').mkdir(parents=True)
    payload=b'original-signed-native';(source/'osx-arm64/AgPrivacy.Native.dylib').write_bytes(payload)
    (source/'manifest.json').write_text(json.dumps({'files':{'osx-arm64/AgPrivacy.Native.dylib':hashlib.sha256(payload).hexdigest()}}))
    resources=tmp_path/'dist/Code Atlas.app/Contents/Resources/vendor/ag-privacy';resources.mkdir(parents=True)
    physical=tmp_path/'dist/Code Atlas.app/Contents/Frameworks/vendor/ag-privacy/osx-arm64';physical.mkdir(parents=True)
    (physical/'AgPrivacy.Native.dylib').write_bytes(b'pyinstaller-rewritten')
    (resources/'osx-arm64').symlink_to('../../../Frameworks/vendor/ag-privacy/osx-arm64')
    helper=module();helper.restore_privacy_pack(tmp_path)
    from zipfile import ZipFile
    with ZipFile(resources/'native-runtime.zip') as archive:
        assert archive.read('osx-arm64/AgPrivacy.Native.dylib')==payload
    framework=physical.parent
    assert framework.is_symlink() and framework.resolve()==resources.resolve()
    framework.unlink();outside=tmp_path/'outside';outside.mkdir()
    framework.symlink_to(outside)
    with pytest.raises(ValueError):helper.restore_privacy_pack(tmp_path)
    assert list(outside.iterdir())==[]


def test_human_helper_json_stays_a_resource_alias(tmp_path):
    """macOS Frameworks contains signed code plus data aliases, not plain JSON subcomponents."""
    source=tmp_path/'vendor/atlas-browser-auth';source.mkdir(parents=True)
    binary=b'synthetic-signed-helper';(source/'confirm').write_bytes(binary)
    (source/'manifest.json').write_text(json.dumps({'binarySha256':hashlib.sha256(binary).hexdigest()}))
    for base in ['Resources','Frameworks']:
        (tmp_path/'dist/Code Atlas.app/Contents'/base/'vendor/atlas-browser-auth').mkdir(parents=True)
    module().restore_human_auth_pack(tmp_path)
    framework=tmp_path/'dist/Code Atlas.app/Contents/Frameworks/vendor/atlas-browser-auth'
    assert (framework/'confirm').read_bytes()==binary
    assert (framework/'manifest.json').is_symlink()
    assert json.loads((framework/'manifest.json').read_bytes())['binarySha256']==hashlib.sha256(binary).hexdigest()
