"""Adversarial publication boundaries on synthetic repositories, never on user stores."""
from pathlib import Path
import io
import json
import os
import subprocess
import sys
import tarfile

import pytest

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'tools'))
import public_release as release
from public_agent_memory import MEMORY_SOURCE, public_memory
from public_source_audit import audit_archive, digest, scan_bytes


@pytest.fixture
def source_repo(tmp_path):
    """
    Create an isolated Git source with required public files and no binary payloads.

    Args:
        tmp_path: Pytest-owned temporary directory.
    Returns:
        Repository root; no commits or author identity are required.
    """
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    files = {'README.md': '# Fixture', 'README.en.md': '# Fixture', 'INSTALL_AGENT.md': '# Install',
             'requirements.lock': '', 'atlas/cli.py': 'print("fixture")',
             'LICENSE': (SOURCE / 'LICENSE').read_text(), 'NOTICE': (SOURCE / 'NOTICE').read_text(),
             release.POLICY: (SOURCE / release.POLICY).read_text(),
             'packaging/public-binaries.json': '{"files": {}}',
             '.gitignore': '/docs/agent-memory/\n/outputs/\n',
             '.gitattributes': '/docs/agent-memory export-ignore\n/outputs export-ignore\n',
             MEMORY_SOURCE: '# Architecture'}
    policy = json.loads(files[release.POLICY])
    policy.pop('publicDesignPins', None)
    files[release.POLICY] = json.dumps(policy)
    for name in policy['requiredFiles']:
        files[name] = 'Fixture for required source'
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def test_public_design_is_pinned_and_replaces_private_assets(source_repo, tmp_path):
    """The release selects reviewed design bytes and rejects old or changed asset files."""
    pins_name = 'packaging/public-design-pins.json'
    policy_file = source_repo / release.POLICY
    policy = json.loads(policy_file.read_bytes())
    policy['publicDesignPins'] = pins_name
    if release.DESIGN_SEED not in policy['excludedPrefixes']:
        policy['excludedPrefixes'].append(release.DESIGN_SEED)
    policy_file.write_text(json.dumps(policy))
    vendor_names = {f'{release.DESIGN_VENDOR}/{name}' for name in ('index.js', 'styles.css', 'tokens.css', 'LICENSE')}
    reviewed = {name: b'public fixture\n' for name in release.DESIGN_METADATA | vendor_names}
    for name, data in reviewed.items():
        seed = source_repo / release.DESIGN_SEED / name
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_bytes(data)
    (source_repo / pins_name).write_text(json.dumps({'files': {name: digest(data) for name, data in reviewed.items()}}))
    old_vendor = source_repo / 'web/Vendor/AG-Design'
    old_vendor.mkdir(parents=True)
    (old_vendor / 'index.js').write_text('internal old implementation')
    (old_vendor / 'internal.js').write_text('internal asset')
    public = tmp_path / 'public'
    assert release.export(source_repo, public)['status'] == 'PASS'
    assert not (public / release.DESIGN_SEED).exists()
    assert not (public / release.DESIGN_VENDOR / 'internal.js').exists()
    assert (old_vendor / 'index.js').read_text() == 'internal old implementation'
    for name, data in reviewed.items():
        assert (public / name).read_bytes() == data
    (public / release.DESIGN_VENDOR / 'index.js').write_text('changed')
    with pytest.raises(ValueError, match='do not match'):
        release.audit(public, checkout=False)
    (source_repo / release.DESIGN_SEED / release.DESIGN_VENDOR / 'index.js').write_text('changed seed')
    with pytest.raises(ValueError, match='do not match'):
        release.export(source_repo, tmp_path / 'second-public')


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='POSIX named pipe')
def test_tree_audit_rejects_nonregular_entries(source_repo, tmp_path):
    """An allowed source folder does not exempt named pipes from the strict inventory."""
    public = tmp_path / 'public'
    release.export(source_repo, public)
    os.mkfifo(public / 'atlas/pipe.py')
    report, _ = release.audit(public, checkout=False)
    assert {'path': 'atlas/pipe.py', 'category': 'missing-or-nonregular'} in report['findings']


@pytest.mark.parametrize('content', [
    b'ghp_' + b'a' * 40,
    b'github_pat_' + b'b' * 45,
    b'AKIA' + b'C' * 16,
    b'ASIA' + b'C' * 16,
    b'glpat-' + b'F' * 20,
    b'sk-proj-' + b'D' * 45,
    b'-----BEGIN ' + b'PRIVATE KEY-----',
    b'https://' + b'private-user:' + b'private-token@github.example.invalid/repo',
    b'/Users/' + b'private-person/work',
    b'C:\\Users\\' + b'private-person\\work',
])
def test_known_private_formats_are_rejected_without_values(content):
    """
    Ensure supported credential/home formats are found and never echoed in findings.

    Args:
        content: Synthetic private marker constructed at runtime.
    """
    findings = scan_bytes('atlas/fixture.py', content)
    assert findings
    assert content.decode() not in json.dumps(findings)


def test_portable_examples_and_public_attribution_are_not_deleted():
    """
    Keep generic setup examples and required third-party contact/copyright notices.
    """
    assert not scan_bytes('README.md', b'/home/example/code contact@example.invalid')
    assert not scan_bytes('vendor/LICENSE', b'Copyright Author contact@' + b'vendor.dev')
    assert scan_bytes('atlas/config.py', b'contact@' + b'private.dev')
    assert not scan_bytes('tests/fixture.py', b"app=App()\\n@app.get('/health')")


def test_reviewed_url_fixtures_do_not_exempt_new_credentials():
    """Accept the reviewed redaction fixture only at its source path."""
    name = 'tests/test_architecture_export.mjs'
    fixture = (SOURCE / name).read_bytes()
    assert not scan_bytes(name, fixture)
    assert scan_bytes('tests/copied_fixture.mjs', fixture)
    new_url = b'https://' + b'another-user:' + b'another-token@host.example.invalid'
    assert scan_bytes(name, fixture + b'\n' + new_url)


def test_export_includes_current_untracked_features_and_excludes_private_state(source_repo, tmp_path):
    """
    Publish present working features while leaving tracked private state and history behind.
    """
    private = source_repo / 'docs/agent-memory/private.a2a.yaml'
    private.parent.mkdir(parents=True)
    private.write_text('not public')
    subprocess.run(['git', '-C', str(source_repo), 'add', '-f', str(private)], check=True)
    private_registry = private.parent / 'registry.a2a.yaml'
    private_registry.write_text('private registry')
    subprocess.run(['git', '-C', str(source_repo), 'add', '-f', str(private_registry)], check=True)
    (source_repo / 'atlas/new_feature.py').write_text('answer = 42')
    result = release.export(source_repo, tmp_path / 'public')
    assert result['status'] == 'PASS'
    public = tmp_path / 'public'
    assert (public / 'atlas/new_feature.py').read_text() == 'answer = 42'
    assert not (public / '.git').exists()
    assert not (public / 'docs/agent-memory/private.a2a.yaml').exists()
    assert (public / 'docs/agent-memory/registry.a2a.yaml').read_bytes() == public_memory()['docs/agent-memory/registry.a2a.yaml']
    assert private.read_text() == 'not public'
    assert private_registry.read_text() == 'private registry'
    assert release.audit(public, checkout=False)[0]['status'] == 'PASS'
    assert release.audit(source_repo, checkout=True, published=True)[0]['status'] == 'FAIL'


def test_public_memory_is_staged_and_archived_as_a_closed_seed(source_repo, tmp_path):
    """Normal staging includes every generated registry/object and retains only local source references."""
    public = tmp_path / 'public'
    release.export(source_repo, public)
    subprocess.run(['git', 'init', '-q', str(public)], check=True)
    subprocess.run(['git', '-C', str(public), 'add', '.'], check=True)
    tree = subprocess.check_output(['git', '-C', str(public), 'write-tree'], text=True).strip()
    archive = subprocess.check_output(['git', '-C', str(public), 'archive', tree])
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        assert set(public_memory()) <= set(files.getnames())
    root = public / 'docs/agent-memory'
    assert {path.relative_to(public).as_posix() for path in root.rglob('*.yaml')} == set(public_memory())
    for path in root.rglob('*.yaml'):
        assert b'://' not in path.read_bytes()
    assert release.audit(public, checkout=True, published=True)[0]['status'] == 'PASS'


def test_external_reference_fails_even_with_a_new_manifest_hash(source_repo, tmp_path):
    """A replacement or extra memory object cannot use the generic documentation allowlist."""
    public = tmp_path / 'public'
    release.export(source_repo, public)
    record = next(name for name in public_memory() if name.endswith('snapshot-evidence.a2a.yaml'))
    data = json.loads((public / record).read_bytes())
    data['sources'] = [{'kind': 'external', 'ref': 'https://example.invalid/internal'}]
    content = json.dumps(data).encode()
    (public / record).write_bytes(content)
    manifest_path = public / release.MANIFEST
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files'][record] = digest(content)
    manifest_path.write_text(json.dumps(manifest))
    (public / 'docs/agent-memory/new-private.a2a.yaml').write_text('local-only text')
    report, _ = release.audit(public, checkout=False)
    findings = {item['category'] for item in report['findings']}
    assert {'public-memory-mismatch', 'excluded-file-in-public-tree'} <= findings


def test_missing_skill_reference_prevents_public_export(source_repo, tmp_path):
    """A SKILL.md without its setup reference cannot pass publication audit."""
    missing = 'skills/code-atlas-engines/references/setup.md'
    (source_repo / missing).unlink()
    destination = tmp_path / 'public'
    with pytest.raises(ValueError, match='audit failed'):
        release.export(source_repo, destination)
    assert not destination.exists()
    report, _ = release.audit(source_repo, checkout=True)
    assert {'path': missing, 'category': 'required-source-missing'} in report['findings']


def test_internal_receipt_and_case_variant_secrets_are_excluded(source_repo):
    """Ignore local verification and credential files even if Git tracks them."""
    private_names = [
        'tools/performance/module-search/atlas-checkout-verification.json',
        'tools/performance/module-search/source-hashes.json',
        'extensions/atlas-browser/manifest.json',
        'vendor/ag-privacy/manifest.json',
        'vendor/npm/legacy-package.tgz',
        'atlas/.ENV.production',
        'resources/keys/Server.KEY',
        'resources/state/cache.DB-WAL',
    ]
    for name in private_names:
        path = source_repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private material')
    subprocess.run(['git', '-C', str(source_repo), 'add', '-f', *private_names], check=True)
    report, selected = release.audit(source_repo, checkout=True)
    assert report['status'] == 'PASS'
    assert set(private_names).isdisjoint(selected)
    assert release.audit(source_repo, checkout=True, published=True)[0]['status'] == 'FAIL'


def test_private_text_blocks_export_without_partial_destination(source_repo, tmp_path):
    """
    Fail closed for an untracked secret; do not create or replace the output directory.
    """
    token = 'ghp_' + 'E' * 40
    (source_repo / 'atlas/private.py').write_text(token)
    destination = tmp_path / 'public'
    with pytest.raises(ValueError, match='audit failed'):
        release.export(source_repo, destination)
    assert not destination.exists()
    report, _ = release.audit(source_repo, checkout=True)
    assert token not in json.dumps(report)


def test_symlink_source_never_reads_external_content(source_repo, tmp_path):
    """
    Reject an eligible symlink even when its target bytes would otherwise look public.
    """
    outside = tmp_path / 'outside.txt'
    outside.write_text('Unrelated user data')
    (source_repo / 'atlas/link.py').symlink_to(outside)
    report, selected = release.audit(source_repo, checkout=True)
    assert report['status'] == 'FAIL'
    assert any(item['category'] == 'symlink' for item in report['findings'])
    assert 'atlas/link.py' not in selected
    assert outside.read_text() == 'Unrelated user data'


def test_changed_binary_requires_a_new_review_pin(source_repo):
    """
    A reviewed payload is accepted exactly; subsequent byte changes cannot hitchhike.
    """
    name = 'resources/fixture.bin'
    path = source_repo / name
    path.parent.mkdir()
    path.write_bytes(b'\0Synthetic public binary')
    pins = source_repo / 'packaging/public-binaries.json'
    pins.write_text(json.dumps({'files': {name: digest(path.read_bytes())}}))
    assert release.audit(source_repo, checkout=True)[0]['status'] == 'PASS'
    path.write_bytes(path.read_bytes() + b'changed')
    findings = release.audit(source_repo, checkout=True)[0]['findings']
    assert any(item['category'] == 'unreviewed-binary' for item in findings)


def test_strict_tree_rejects_manifest_changes_and_empty_private_directories(source_repo, tmp_path):
    """
    Verify exact payload bytes and reject even an empty Git directory after export.
    """
    public = tmp_path / 'public'
    release.export(source_repo, public)
    (public / '.git').mkdir()
    report, _ = release.audit(public, checkout=False)
    assert any(item['category'] == 'excluded-file-in-public-tree' for item in report['findings'])
    (public / '.git').rmdir()
    (public / 'atlas/cli.py').write_text('changed = True')
    report, _ = release.audit(public, checkout=False)
    assert any(item['category'] == 'manifest-mismatch' for item in report['findings'])


def test_existing_destination_and_recursive_source_exports_are_rejected(source_repo, tmp_path):
    """
    Protect unrelated destinations and prevent later exports from recursively ingesting themselves.
    """
    public = tmp_path / 'existing'
    public.mkdir()
    marker = public / 'keep.txt'
    marker.write_text('keep')
    with pytest.raises(ValueError, match='Destination must be new'):
        release.export(source_repo, public)
    assert marker.read_text() == 'keep'
    with pytest.raises(ValueError, match='excluded outputs'):
        release.export(source_repo, source_repo / 'atlas/public')


def test_source_change_during_copy_is_not_published(source_repo, tmp_path, monkeypatch):
    """
    A concurrent editor invalidates the copied snapshot before its destination is published.
    """
    original = release.read_regular
    reads = 0

    def changing(root, name):
        """
        Simulate a README edit at the second source read, after initial qualification.
        """
        nonlocal reads
        if root == source_repo and name == 'README.md':
            reads += 1
            if reads == 2:
                (root / name).write_text('edited concurrently')
        return original(root, name)

    monkeypatch.setattr(release, 'read_regular', changing)
    with pytest.raises(ValueError, match='Source changed'):
        release.export(source_repo, tmp_path / 'public')
    assert not (tmp_path / 'public').exists()
    assert not list(tmp_path.glob('.atlas-public-*'))


def test_new_source_during_copy_is_not_published(source_repo, tmp_path, monkeypatch):
    """An eligible file added during export invalidates the initial inventory."""
    original = release.source_files
    checkout_lists = 0

    def listing(root, policy, *, checkout, memory_paths):
        """Add a source file immediately before the final checkout inventory."""
        nonlocal checkout_lists
        if root == source_repo and checkout:
            checkout_lists += 1
            if checkout_lists == 2:
                (root / 'atlas/new_feature.py').write_text('answer = 42')
        return original(root, policy, checkout=checkout, memory_paths=memory_paths)

    monkeypatch.setattr(release, 'source_files', listing)
    destination = tmp_path / 'public'
    with pytest.raises(ValueError, match='Source inventory changed'):
        release.export(source_repo, destination)
    assert not destination.exists()


def test_sensitive_filename_is_redacted(source_repo):
    """
    A credential inside a filename must neither be published nor echoed by the report.
    """
    token = 'ghp_' + 'F' * 40
    (source_repo / 'atlas' / (token + '.txt')).write_text('fixture')
    report, _ = release.audit(source_repo, checkout=True)
    assert report['status'] == 'FAIL'
    assert token not in json.dumps(report)


def test_archive_paths_links_and_hidden_private_content():
    """
    Inspect compressed members without extracting traversal paths or following links.
    """
    for name, kind, content in [('package/info.txt', tarfile.REGTYPE, b'/Users/' + b'private-person/work'),
                                ('../outside', tarfile.REGTYPE, b'ok'),
                                ('package/link', tarfile.SYMTYPE, b'')]:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(content)
            member.linkname = '/unrelated'
            archive.addfile(member, io.BytesIO(content) if kind == tarfile.REGTYPE else None)
        assert audit_archive('vendor/fixture.tgz', buffer.getvalue())
    assert audit_archive('vendor/fixture.tgz', b'not gzip')


def test_manifest_metadata_cannot_smuggle_private_values(source_repo, tmp_path):
    """
    A valid file map does not exempt untrusted manifest bytes from credential scanning.
    """
    public = tmp_path / 'public'
    release.export(source_repo, public)
    path = public / release.MANIFEST
    manifest = json.loads(path.read_text())
    token = 'ghp_' + 'Z' * 40
    manifest['unreviewed'] = token
    path.write_text(json.dumps(manifest))
    report, _ = release.audit(public, checkout=False)
    assert report['status'] == 'FAIL'
    assert any(item['category'] == 'github-token' for item in report['findings'])
    assert token not in json.dumps(report)


def test_archive_member_names_and_directories_are_scanned_without_echoing_tokens():
    """
    Credentials and private terms in compressed member names are rejected and redacted.
    """
    marker = 'ghp_' + 'Y' * 40
    for kind in [tarfile.DIRTYPE, tarfile.REGTYPE]:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            member = tarfile.TarInfo('package/' + marker)
            member.type = kind
            archive.addfile(member, io.BytesIO(b'') if kind == tarfile.REGTYPE else None)
        findings = audit_archive('vendor/fixture.tgz', buffer.getvalue())
        assert findings
        assert marker not in json.dumps(findings)


def test_concurrently_created_destination_is_preserved(source_repo, tmp_path, monkeypatch):
    """
    Exclusive publication must preserve an empty directory appearing at the last moment.
    """
    original = release.shutil.copytree
    destination = tmp_path / 'public'

    def competing_copy(source, target, **kwargs):
        """
        Simulate another owner creating the destination immediately before copytree.
        """
        target.mkdir()
        (target / 'owner.txt').write_text('foreign')
        return original(source, target, **kwargs)

    monkeypatch.setattr(release.shutil, 'copytree', competing_copy)
    with pytest.raises(FileExistsError):
        release.export(source_repo, destination)
    assert (destination / 'owner.txt').read_text() == 'foreign'


def test_public_git_checkout_accepts_and_scans_its_export_manifest(source_repo, tmp_path):
    """
    The exported manifest remains safe when a new public Git repository tracks it.
    """
    public = tmp_path / 'public'
    release.export(source_repo, public)
    subprocess.run(['git', 'init', '-q', str(public)], check=True)
    subprocess.run(['git', '-C', str(public), 'add', '.'], check=True)
    assert release.audit(public, checkout=True, published=True)[0]['status'] == 'PASS'
    path = public / release.MANIFEST
    value = json.loads(path.read_text())
    value['hidden'] = 'ghp_' + 'Q' * 40
    path.write_text(json.dumps(value))
    assert release.audit(public, checkout=True, published=True)[0]['status'] == 'FAIL'
    path.unlink()
    report, _ = release.audit(public, checkout=True, published=True)
    assert report['status'] == 'FAIL'
    assert {'path': release.MANIFEST, 'category': 'manifest-missing-or-invalid'} in report['findings']
