"""
AST-only HTTP destinations for the architecture projection, with host-only output.
"""
from hashlib import sha256
from urllib.parse import urlsplit
import re

from .infrastructure.syntax import literal, parse, text


def _known_call(syntax, callee: str) -> bool:
    """
    Match known call spellings in the parsed language, without treating comments as imports.

    Args:
        syntax: Already parsed bounded source inventory, including actual import AST nodes.
        callee: Call expression spelling; receiver types are not runtime-resolved.
    Returns:
        A syntactic candidate classification, never proof of runtime dispatch.
    """
    if syntax.language == 'python':
        return bool(re.fullmatch(r'(requests|httpx)\.(get|post|put|delete|patch|head|options)', callee))
    if syntax.language in {'javascript', 'typescript'}:
        return callee in {'fetch', 'window.fetch', 'globalThis.fetch'}
    if syntax.language != 'csharp':
        return False
    imported = any(node.type == 'using_directive' and 'System.Net.Http' in text(node, syntax.content)
                   for node in syntax.nodes)
    if not imported:
        return False
    return bool(re.fullmatch(r'(?:httpClient|_httpClient|client)\.(?:GetAsync|PostAsync|PutAsync|DeleteAsync|PatchAsync|GetStringAsync)', callee))


def http_facts(source: dict) -> list[dict]:
    """
    Extract bounded known HTTP calls from one already-budgeted source.

    Args:
        source: Trusted already-budgeted source-viewer DTO with file/text/sourceByteHash.
    Returns:
        Host-only candidate facts; paths, credentials and query are discarded, with no network I/O.
    Raises:
        ImportError: A required grammar is unavailable.
        ValueError: The bounded source cannot be parsed or exceeds the AST node budget.
    """
    syntax = parse(source)
    if syntax is None:
        return []
    result = []
    for node, callee, values in syntax.calls():
        # A candidate still needs an actual call AST node and an ordinary literal destination.
        if not _known_call(syntax, callee) or not values:
            continue
        value = literal(values[0])
        if not value:
            continue
        try:
            url = urlsplit(value)
            host = url.hostname
            if url.scheme.lower() not in {'http', 'https'} or not host or len(host) > 253:
                continue
            # Only DNS/IP-like characters survive; neither userinfo nor URL path is retained.
            if not re.fullmatch(r'[A-Za-z0-9.:_-]+', host):
                continue
            port = url.port
        except ValueError:
            continue
        public_host = '[' + host.lower() + ']' if ':' in host else host.lower()
        endpoint = public_host + (':' + str(port) if port else '')
        line = node.start_point.row + 1
        identity = f"{source['file']}:{line}:{node.start_point.column}:http:{url.scheme.lower()}:{endpoint}"
        result.append({'id': 'architecture-http:' + sha256(identity.encode()).hexdigest()[:24],
            'kind': 'http', 'resourceKind': 'endpoint', 'operation': 'requests', 'name': endpoint,
            'host': endpoint, 'scheme': url.scheme.lower(), 'status': 'candidate',
            'line': line, 'file': source['file'], 'sourceHash': source['sourceByteHash'],
            'basis': 'known-http-call-spelling', 'provider': 'architecture-http-v1',
            'runtimeSelectionKnown': False, 'configurationBindingKnown': False,
            'configurationCandidates': [], 'evidence': []})
        if len(result) >= 12000:
            break
    return result
