"""Audit/export current public source without copying Git history or private workspace state."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from public_agent_memory import MEMORY_SOURCE, public_git_metadata, public_memory
from public_source_audit import MAX_FILE_BYTES, audit_archive, digest, is_binary, scan_bytes

POLICY = 'packaging/public-source-policy.json'
MANIFEST = 'PUBLIC_SOURCE_MANIFEST.json'
DESIGN_SEED = 'packaging/public-design'
DESIGN_VENDOR = 'web/vendor/ag-design'
DESIGN_METADATA = {'package.json', 'package-lock.json', 'ag-design.integration.json'}


def included(name: str, policy: dict) -> bool:
    """
    Select only known source areas, excluding all private/build/runtime subtrees.

    Args:
        name: Repository-relative POSIX path.
        policy: Reviewed public-source policy.
    Returns:
        Whether the path is eligible for a public payload; content is checked separately.
    """
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        return False
    if any(part.casefold() in policy['excludedParts'] for part in path.parts):
        return False
    folded = name.casefold()
    for prefix in policy['excludedPrefixes']:
        excluded_prefix = prefix.casefold()
        if folded == excluded_prefix or folded.startswith(excluded_prefix + '/'):
            return False
    if (any(folded == excluded.casefold() for excluded in policy['excludedFiles'])
            or path.suffix.casefold() in policy['excludedSuffixes']):
        return False
    # Credential/config dotfiles must not hitchhike inside an otherwise allowed source area.
    if any(part.casefold().startswith('.env') or part.casefold() in {'.ds_store', '.npmrc', '.pypirc', '.netrc'} for part in path.parts):
        return False
    return name in policy['files'] or (len(path.parts) > 1 and path.parts[0] in policy['directories'])


def source_files(root: Path, policy: dict, *, checkout: bool, memory_paths: set[str]) -> list[str]:
    """
    List current tracked and nonignored new files, or every file in an exported tree.

    Args:
        root: Explicit source checkout/export root.
        policy: Reviewed path exclusions for strict tree inventory.
        checkout: True uses Git's tracked+untracked inventory; False never consults parent Git.
        memory_paths: Exact generated file names allowed in the public tree.
    Returns:
        Sorted unique relative names. No broad traversal of ignored local data in checkout mode.
    """
    if checkout:
        raw = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard', '-z'])
        return sorted(set(name for name in os.fsdecode(raw).split('\x00') if name))
    names = []
    memory_directories = {parent.as_posix() for name in memory_paths for parent in PurePosixPath(name).parents}
    for path in root.rglob('*'):
        name = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.is_dir() or (name not in memory_directories
                                                      and not included(name + '/__probe__', policy)):
            names.append(name)
    return sorted(names)


def read_regular(root: Path, name: str) -> tuple[bytes | None, str | None]:
    """
    Read a bounded regular file without following symlinks, including parent components.

    Args:
        root: Canonical selected root.
        name: Relative candidate path.
    Returns:
        Bytes and no error, or no bytes and a redacted rejection category.
    """
    path = root
    for part in PurePosixPath(name).parts:
        path = path / part
        if path.is_symlink():
            return None, 'symlink'
    if not path.is_file():
        return None, 'missing-or-nonregular'
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
        return None, 'file-kind-or-budget'
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            return None, 'changed-file-identity'
        data = stream.read(MAX_FILE_BYTES + 1)
    after = path.stat()
    if len(data) > MAX_FILE_BYTES or (after.st_dev, after.st_ino, after.st_mtime_ns) != (info.st_dev, info.st_ino, info.st_mtime_ns):
        return None, 'changed-or-oversize'
    return data, None


def public_design(root: Path, policy: dict, *, use_seed: bool) -> dict[str, bytes]:
    """Load the reviewed public AG Design files and verify their exact pinned bytes."""
    pins_name = policy.get('publicDesignPins')
    if pins_name is None:
        return {}
    if pins_name != 'packaging/public-design-pins.json':
        raise ValueError('Invalid public AG Design pin path')
    raw, error = read_regular(root, pins_name)
    if error:
        raise ValueError('Public AG Design pins are unavailable')
    pins = json.loads(raw)['files']
    required = DESIGN_METADATA | {f'{DESIGN_VENDOR}/{name}' for name in ('index.js', 'styles.css', 'tokens.css', 'LICENSE')}
    if not isinstance(pins, dict) or not required <= pins.keys():
        raise ValueError('Public AG Design pins are incomplete')
    result = {}
    for name, expected in sorted(pins.items()):
        path = PurePosixPath(name)
        vendor_file = (path.parent.as_posix() == DESIGN_VENDOR and path.as_posix() == name
                       and '\\' not in name and ':' not in name and '..' not in path.parts)
        if name not in DESIGN_METADATA and not vendor_file:
            raise ValueError('Unexpected path in public AG Design pins')
        source_name = f'{DESIGN_SEED}/{name}' if use_seed else name
        data, error = read_regular(root, source_name)
        if error or digest(data) != expected:
            raise ValueError('Public AG Design bytes do not match their pins')
        result[name] = data
    return result


def audit(root: Path, *, checkout: bool, forbidden_terms: tuple[bytes, ...] = (), published: bool = False) -> tuple[dict, dict[str, bytes]]:
    """
    Qualify a complete selected payload; a public-tree audit rejects excluded extras.

    Args:
        root: Canonical root containing policy and reviewed-binary manifest.
        checkout: Select current Git source, or strictly verify an existing export.
        forbidden_terms: Optional local tokens kept outside the publication payload.
        published: Reject private tracked files in an already published Git checkout.
    Returns:
        Sanitized report and exact qualified bytes used by export; no content is printed.
    """
    policy_data, error = read_regular(root, POLICY)
    if error:
        raise ValueError('Publication policy must be a bounded regular file')
    policy = json.loads(policy_data)
    pins_path = policy['reviewedBinaries']
    if PurePosixPath(pins_path).is_absolute() or '..' in PurePosixPath(pins_path).parts:
        raise ValueError('Binary pins must be source-relative')
    pins_data, error = read_regular(root, pins_path)
    if error:
        raise ValueError('Binary pins must be a bounded regular file')
    binary_pins = json.loads(pins_data)['files']
    findings, selected, excluded = [], {}, 0
    memory = public_memory() if published or not checkout else {}
    use_design_seed = checkout and not published and not (root / MANIFEST).exists()
    design = public_design(root, policy, use_seed=use_design_seed)
    names = set(source_files(root, policy, checkout=checkout, memory_paths=set(memory)))
    if use_design_seed:
        names.update(design)
    for name in sorted(names):
        if name == MANIFEST:
            continue
        unapproved_vendor = design and name.casefold().startswith(DESIGN_VENDOR + '/') and name not in design
        if unapproved_vendor or (name not in memory and not included(name, policy)):
            excluded += 1
            if not checkout or published:
                findings.append({'path': name, 'category': 'excluded-file-in-public-tree'})
            continue
        findings.extend(scan_bytes(name, name.encode('utf-8'), forbidden_terms))
        data, error = (design[name], None) if use_design_seed and name in design else read_regular(root, name)
        if error:
            findings.append({'path': name, 'category': error})
            continue
        if name in memory and data != memory[name]:
            findings.append({'path': name, 'category': 'public-memory-mismatch'})
        binary = is_binary(data)
        reviewed = binary and binary_pins.get(name) == digest(data)
        findings.extend(scan_bytes(name, data, forbidden_terms, check_email=not reviewed))
        if binary:
            if binary_pins.get(name) != digest(data):
                findings.append({'path': name, 'category': 'unreviewed-binary'})
            elif name.endswith('.tgz'):
                findings.extend(audit_archive(name, data, forbidden_terms))
        selected[name] = data
    required = {'README.md', 'README.en.md', 'INSTALL_AGENT.md', 'requirements.lock',
                'atlas/cli.py', 'LICENSE', 'NOTICE', MEMORY_SOURCE, POLICY}
    required.update(policy.get('requiredFiles', ()))
    required.update(memory)
    required.update(design)
    for name in sorted(required):
        if name not in selected:
            findings.append({'path': name, 'category': 'required-source-missing'})
    # A malicious filename must not expose the same private token in the report.
    for finding in findings:
        location = finding['path']
        if scan_bytes('', location.encode('utf-8'), forbidden_terms):
            finding['path'] = '<redacted-path-' + digest(location.encode())[:12] + '>'
    hashes = {name: digest(data) for name, data in sorted(selected.items())}
    if published or not checkout or (root / MANIFEST).exists():
        try:
            manifest_data, error = read_regular(root, MANIFEST)
            if error:
                raise ValueError('Manifest must be a bounded regular file')
            findings.extend(scan_bytes(MANIFEST, manifest_data, forbidden_terms))
            manifest = json.loads(manifest_data)
            if (set(manifest) != {'schemaVersion', 'historyIncluded', 'files'} or manifest.get('schemaVersion') != 1
                    or manifest.get('historyIncluded') is not False
                    or (not checkout and manifest.get('files') != hashes)):
                findings.append({'path': MANIFEST, 'category': 'manifest-mismatch'})
        except (OSError, ValueError):
            findings.append({'path': MANIFEST, 'category': 'manifest-missing-or-invalid'})
    report = {'status': 'PASS' if not findings else 'FAIL', 'files': len(selected), 'bytes': sum(map(len, selected.values())),
              'excludedCount': excluded, 'findings': findings, 'historyIncluded': False,
              'scope': 'Known credential/identity patterns plus reviewed binary pins; not proof against arbitrary hidden data'}
    return report, selected


def export(root: Path, destination: Path, forbidden_terms: tuple[bytes, ...] = ()) -> dict:
    """
    Copy a newly audited source directory exclusively; never overwrite a destination.

    Args:
        root: Current Git checkout. Original index/history and private files remain unchanged.
        destination: New directory; parent must already exist and must not be a symlink.
        forbidden_terms: Local private identity/project tokens used for both audits.
    Returns:
        Passing privacy report. Raises ValueError on an unsafe path or failed audit.
    """
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ValueError('Destination must be new and its parent must exist')
    if destination.parent.resolve() != destination.parent:
        raise ValueError('Destination parent must be canonical')
    if destination == root or destination in root.parents:
        raise ValueError('Destination cannot contain the source')
    if root in destination.parents and destination.relative_to(root).parts[0] != 'outputs':
        raise ValueError('Nested exports must stay in the excluded outputs directory')
    report, selected = audit(root, checkout=True, forbidden_terms=forbidden_terms)
    if report['status'] != 'PASS':
        raise ValueError('Public-source audit failed; run audit for redacted findings')
    temporary = Path(tempfile.mkdtemp(prefix='.atlas-public-', dir=destination.parent))
    try:
        memory = public_memory()
        policy = json.loads(selected[POLICY])
        generated_design = public_design(root, policy, use_seed=not (root / MANIFEST).exists())
        payload = {name: public_git_metadata(name, data) for name, data in selected.items()}
        payload.update(memory)
        for name, data in payload.items():
            target = temporary / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            executable = name not in memory and name not in generated_design and (root / name).stat().st_mode & 0o111
            target.chmod(0o755 if executable else 0o644)
        manifest = {'schemaVersion': 1, 'historyIncluded': False, 'files': {name: digest(data) for name, data in sorted(payload.items())}}
        (temporary / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        checked, _ = audit(temporary, checkout=False, forbidden_terms=forbidden_terms)
        if checked['status'] != 'PASS':
            raise ValueError('Export verification failed')
        # Detect source changes made by another editor while constructing the snapshot.
        for name, data in selected.items():
            if name in generated_design:
                continue
            current, error = read_regular(root, name)
            if error or current != data:
                raise ValueError('Source changed during export; no destination published')
        current_names = {name for name in source_files(root, policy, checkout=True, memory_paths=set())
                         if included(name, policy) and name != MANIFEST
                         and (not generated_design or not name.casefold().startswith(DESIGN_VENDOR + '/'))}
        final_design = public_design(root, policy, use_seed=not (root / MANIFEST).exists())
        if final_design != generated_design or any(selected[name] != data for name, data in final_design.items()):
            raise ValueError('Public AG Design changed during export')
        current_names.update(final_design)
        if current_names != set(selected):
            raise ValueError('Source inventory changed during export; no destination published')
        if destination.exists():
            raise ValueError('Destination appeared during export')
        # copytree's exclusive mkdir also rejects a destination appearing after our
        # last observation. A failed copy may leave an incomplete destination;
        # never delete it blindly, because another writer may have added files.
        shutil.copytree(temporary, destination, dirs_exist_ok=False)
        checked, _ = audit(destination, checkout=False, forbidden_terms=forbidden_terms)
        if checked['status'] != 'PASS':
            raise ValueError('Published copy verification failed')
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return checked


def main() -> int:
    """
    Execute a local audit or new export; report metadata only and never push to a remote.

    Returns:
        Zero for a passing audit/export, one for findings or a bounded operational failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['audit', 'export'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--tree', action='store_true', help='Strictly audit an exported tree without consulting Git')
    parser.add_argument('--published', action='store_true', help='Reject excluded files in a public Git checkout')
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--forbidden-terms', type=Path, help='Local JSON string list; keep outside public source')
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        terms = ()
        if args.forbidden_terms:
            values = json.loads(args.forbidden_terms.read_text())
            if not isinstance(values, list) or not all(isinstance(value, str) and 3 <= len(value) <= 200 for value in values):
                raise ValueError('Forbidden terms must be a bounded string list')
            if len(values) > 100:
                raise ValueError('Too many private tokens')
            terms = tuple(value.encode() for value in values)
        if args.command == 'export':
            if args.destination is None or args.tree:
                raise ValueError('Export requires --destination and a Git source checkout')
            report = export(root, args.destination.absolute(), terms)
        else:
            report, _ = audit(root, checkout=not args.tree, forbidden_terms=terms, published=args.published)
    except (OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({'status': 'FAIL', 'error': 'Publication operation failed; no secret values are emitted'}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
