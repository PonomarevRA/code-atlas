"""Package exact SDK bytes as data; unsigned NativeAOT libraries are never nested app code."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED


def prepared_privacy_data(root):
    """Pack verified SDK bytes when supplied. An incomplete SDK fails the build."""
    source = Path(root) / 'vendor/ag-privacy'
    if source.is_symlink():
        raise ValueError('Privacy SDK directory is redirected')
    if not source.exists():
        return {}
    manifest_bytes = (source / 'manifest.json').read_bytes()
    if len(manifest_bytes) > 65536:
        raise ValueError('Privacy manifest exceeds budget')
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest.get('files'), dict) or not 1 <= len(manifest['files']) <= 16:
        raise ValueError('Invalid privacy artifact manifest')
    prepared = {'manifest.json': manifest_bytes}
    archive_bytes = io.BytesIO()
    with ZipFile(archive_bytes, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, digest in manifest['files'].items():
            name = Path(relative)
            if name.is_absolute() or '..' in name.parts or len(name.parts) > 2:
                raise ValueError('Privacy artifact path is not local')
            path = source / name
            if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('Privacy artifact is redirected or oversized')
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Privacy input artifact hash differs')
            if name.suffix in {'.dylib', '.dll', '.so'}:
                info = ZipInfo(name.as_posix(), (1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                archive.writestr(info, data)
            else:
                prepared[name.as_posix()] = data
    prepared['native-runtime.zip'] = archive_bytes.getvalue()
    return prepared


def privacy_pack_data(root):
    """Collect only verified assets, excluding development caches and unsigned nested Mach-O code."""
    root = Path(root)
    prepared = prepared_privacy_data(root)
    auth = root / 'vendor/atlas-browser-auth'
    result = [(str(auth / name), 'vendor/atlas-browser-auth') for name in ('confirm', 'manifest.json')]
    if not prepared:
        return result
    stage = root / 'build/browser-privacy-data'
    if stage.is_symlink():
        raise ValueError('Privacy staging directory is redirected')
    stage.mkdir(parents=True, exist_ok=True)
    for name, data in prepared.items():
        path = stage / name
        if path.is_symlink():
            raise ValueError('Privacy staging file is redirected')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        result.append((str(path), str(Path('vendor/ag-privacy') / Path(name).parent)))
    return result


def managed_parents(path, distribution):
    """Build repair is restricted to real parent directories under this invocation's dist tree."""
    current = distribution
    if current.is_symlink():
        raise ValueError('Distribution root is redirected')
    for part in path.relative_to(distribution).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError('Privacy resource directory is redirected')


def restore_privacy_pack(root):
    """Replace only owned fresh SDK outputs and use a coherent whole-data alias for macOS."""
    root = Path(root)
    prepared = prepared_privacy_data(root)
    if not prepared:
        return
    distribution = root / 'dist'
    if distribution.is_symlink():
        raise ValueError('Distribution root is redirected')
    targets = [distribution / 'CodeAtlas/_internal/vendor/ag-privacy', distribution / 'Code Atlas.app/Contents/Resources/vendor/ag-privacy']
    framework = distribution / 'Code Atlas.app/Contents/Frameworks/vendor/ag-privacy'
    resources = targets[-1]
    for target in targets:
        if target.exists() or target.is_symlink():
            managed_parents(target, distribution)
            if target.is_symlink():
                raise ValueError('Privacy data root is redirected')
    if framework.exists() or framework.is_symlink():
        managed_parents(framework, distribution)
        if framework.is_symlink() and framework.resolve() != resources.resolve():
            raise ValueError('Privacy framework alias differs')
    for target in targets:
        if not target.is_dir():
            continue
        shutil.rmtree(target)  # Only this fresh build's validated fixed SDK directory; child links are not followed.
        target.mkdir()
        for name, data in prepared.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    if framework.exists() or framework.is_symlink():
        if framework.is_symlink():
            framework.unlink()
        else:
            shutil.rmtree(framework)
        framework.symlink_to(os.path.relpath(resources, framework.parent), target_is_directory=True)


def restore_human_auth_pack(root):
    """Restore the exact already-signed universal helper in both macOS resource/code roots."""
    root = Path(root)
    source = root / 'vendor/atlas-browser-auth'
    manifest = json.loads((source / 'manifest.json').read_bytes())
    data = (source / 'confirm').read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest['binarySha256']:
        raise ValueError('Human authentication helper hash differs')
    distribution = root / 'dist'
    for base in ('CodeAtlas/_internal', 'Code Atlas.app/Contents/Resources', 'Code Atlas.app/Contents/Frameworks'):
        target = distribution / base / 'vendor/atlas-browser-auth'
        if not target.is_dir():
            continue
        managed_parents(target / 'confirm', distribution)
        for name, payload in [('confirm', data), ('manifest.json', (source / 'manifest.json').read_bytes())]:
            path = target / name
            if path.is_symlink():
                path.resolve().relative_to(distribution.resolve())
                path.unlink()
            if base == 'Code Atlas.app/Contents/Frameworks' and name == 'manifest.json':
                # Frameworks may contain signed code and resource aliases, never plain JSON files.
                resource = distribution / 'Code Atlas.app/Contents/Resources/vendor/atlas-browser-auth/manifest.json'
                path.unlink(missing_ok=True)
                path.symlink_to(os.path.relpath(resource, path.parent))
            else:
                path.write_bytes(payload)
        (target / 'confirm').chmod(0o755)
