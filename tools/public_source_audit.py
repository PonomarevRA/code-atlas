"""Redacted publication checks; source text and credentials never appear in findings."""
from __future__ import annotations

import hashlib
import io
import re
import tarfile
from pathlib import PurePosixPath

MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 1000

# Generic examples and public CI accounts are portable documentation, not a local identity.
EXAMPLE_USERS = {'example', 'user', 'name', 'you', 'runner', 'test-user', 'username', '...', '…'}
HOME = re.compile(rb'(?:/(?:Users|home)/|[A-Za-z]:[\\/]Users[\\/])([^/\\\s<>\x00"\']+)')
SECRETS = {
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    'cloud-access-key': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'gitlab-token': re.compile(rb'\bglpat-[A-Za-z0-9_-]{20,}\b'),
    'api-token': re.compile(rb'\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{35,}|xox[baprs]-[A-Za-z0-9-]{20,})\b'),
    'http-url-credential': re.compile(
        rb'https?://[^/\s:@<>"\']+:[^/\s@<>"\']+@(?:\[[a-f0-9:]+\]|[a-z0-9._-]+)', re.IGNORECASE,
    ),
}
EMAIL = re.compile(rb'(?<![A-Za-z0-9._%+\\-])[A-Za-z0-9._%+-]{1,64}@([A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24})')

# Synthetic URL origins used to test credential redaction. Each pin applies to one exact file.
REVIEWED_TEST_URLS = {
    'tests/test_architecture_enrichment.py': {
        '6784215effd6e34990809c9ce8a986d09fdc63d0125e8000a1c2f0bff7ad6ce3',
        'ad8bdd3584c59dcb2d76f9b64cfe2daad273e3e021f52bd6a62c1deb237f8e08',
    },
    'tests/test_architecture_export.mjs': {'d55a51f06e5d885c6cef0de40fa867171974e504e9722273962704b676a0cec3'},
    'tests/test_architecture_pipeline.py': {
        '201a28d73c37d49b41fecdc3758129762236ae7d95b3a50df3264148bd042301',
        'dafd1398329ccc52bcc7bbb85d9c6f1bdd4d7fcf4afbafb2eebf04410f305003',
    },
    'tests/test_code_patterns.py': {'5615950465cdf8def5c02d3c074e9699790a02e5d0814a68d98d592446d87571'},
    'tests/test_documentation_mcp.py': {'fe30fb721f4e8b1d50f281ae338da254a0e34dba6804776c231b8666d5856055'},
    'tests/test_documentation_view.mjs': {'d55a51f06e5d885c6cef0de40fa867171974e504e9722273962704b676a0cec3'},
    'tests/test_project_wiki_mods.mjs': {'2cddf90e75e16fa7a7a918bdf84deb454aa7945ed44d1bdcc82caf97c25b1448'},
    'tests/test_remote_repositories.py': {
        '7848d0545a9c82c818c60a5f7896da7ee66530ef0807c494ce554cc5b344aaa4',
        '870638c871cf52c8bb24d50d9883a4470fe859351ad6bf3a84f95b05c34bfe03',
    },
    'tests/test_system_architecture.py': {'7bdde2fb8773b3fbbfbc4a374e540672800105918daa0948a5be211c839b4ba6'},
    'tests/test_test_coverage_mcp.py': {'b73e7ea539073d8689e76ebc7719d46e59820b9ec8f2d5f91f41693fb5480b52'},
    'tests/test_tray_activity.py': {'48ac60f88b01dbd92dce8f5e831b11afb594fcedeb9810358ec7ef22a9e026b2'},
    'tests/test_wiki_sync.py': {'870638c871cf52c8bb24d50d9883a4470fe859351ad6bf3a84f95b05c34bfe03'},
}


def digest(data: bytes) -> str:
    """
    Return the SHA256 identity used by manifests and reviewed binary pins.

    Args:
        data: Exact file bytes, without filesystem side effects.
    Returns:
        Lowercase hexadecimal digest.
    """
    return hashlib.sha256(data).hexdigest()


def scan_bytes(name: str, data: bytes, forbidden_terms: tuple[bytes, ...] = (), *, check_email: bool = True) -> list[dict]:
    """
    Report known private paths, identities and credential formats without their values.

    Args:
        name: Repository-relative file/member name used only to locate a finding.
        data: Bounded bytes; binary strings are scanned too.
        forbidden_terms: Optional local identity/project tokens, never embedded in public policy.
        check_email: False only for exact reviewed third-party binaries; secrets and identities stay checked.
    Returns:
        Category/path/line records. Third-party attribution emails remain permitted.
    """
    location_bytes = name.encode('utf-8')
    sensitive_name = HOME.search(location_bytes) or EMAIL.search(location_bytes)
    sensitive_name = sensitive_name or any(pattern.search(location_bytes) for pattern in SECRETS.values())
    sensitive_name = sensitive_name or any(term and term.lower() in location_bytes.lower() for term in forbidden_terms)
    location = '<redacted-path-' + digest(location_bytes)[:12] + '>' if sensitive_name else name
    findings = []
    folded = data.lower()
    for match in HOME.finditer(data):
        if match[1].decode('utf-8', errors='replace') not in EXAMPLE_USERS:
            findings.append({'path': location, 'category': 'personal-home-path', 'line': data.count(b'\n', 0, match.start()) + 1})
            break
    for term in forbidden_terms:
        if term and term.lower() in folded:
            findings.append({'path': location, 'category': 'private-project-or-identity'})
            break
    for category, pattern in SECRETS.items():
        reviewed_urls = REVIEWED_TEST_URLS.get(name, ()) if category == 'http-url-credential' else ()
        for match in pattern.finditer(data):
            if reviewed_urls and digest(match[0]) in reviewed_urls:
                continue
            findings.append({'path': location, 'category': category, 'line': data.count(b'\n', 0, match.start()) + 1})
            break
    attribution = any(word in name.lower() for word in ['license', 'notice', 'copyright'])
    if not attribution and check_email and b'@' in data:
        for match in EMAIL.finditer(data):
            domain = match[1].decode('ascii').lower()
            if domain in {'example.com', 'example.net', 'example.org'} or domain.endswith(('.test', '.invalid', '.example', '.dll', '.dylib', '.so')):
                continue
            findings.append({'path': location, 'category': 'email-needs-review', 'line': data.count(b'\n', 0, match.start()) + 1})
            break
    return findings


def audit_archive(name: str, data: bytes, forbidden_terms: tuple[bytes, ...] = ()) -> list[dict]:
    """
    Inspect bounded vendored npm tarballs in memory; never extract archive paths.

    Args:
        name: Reviewed repository-relative tarball path.
        data: Compressed bytes already checked against the binary manifest.
        forbidden_terms: Local private tokens passed through to each member check.
    Returns:
        Redacted content, unsafe-member or capacity findings; malformed archives fail closed.
    """
    findings = []
    total = 0
    count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r|gz') as archive:
            for member in archive:
                count += 1
                member_path = PurePosixPath(member.name)
                if count > MAX_ARCHIVE_MEMBERS or member.size > MAX_ARCHIVE_BYTES - total:
                    return [{'path': name, 'category': 'archive-budget'}]
                if member_path.is_absolute() or '..' in member_path.parts or '\\' in member.name:
                    return [{'path': name, 'category': 'unsafe-archive-path'}]
                findings.extend(scan_bytes(name + '!/' + member.name, member.name.encode('utf-8'), forbidden_terms))
                if member.isdir():
                    continue
                if not member.isfile():
                    return [{'path': name, 'category': 'unsafe-archive-member'}]
                total += member.size
                stream = archive.extractfile(member)
                if stream is None:
                    return [{'path': name, 'category': 'unreadable-archive-member'}]
                content = stream.read(MAX_ARCHIVE_BYTES + 1)
                findings.extend(scan_bytes(name + '!/' + member.name, content, forbidden_terms))
    except (OSError, tarfile.TarError, EOFError):
        findings.append({'path': name, 'category': 'invalid-archive'})
    return findings


def is_binary(data: bytes) -> bool:
    """
    Distinguish UTF8 source from opaque payloads requiring exact manual review pins.

    Args:
        data: Exact file bytes.
    Returns:
        True for NUL-containing or non-UTF8 content.
    """
    if b'\x00' in data:
        return True
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        return True
    return False
