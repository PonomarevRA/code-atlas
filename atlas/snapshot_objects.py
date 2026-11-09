"""
Lossless file-owned graph chunks and small, atomically published snapshot manifests.
"""
from collections import OrderedDict
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from .snapshot_object_store import canonical_bytes, checked_object_path, read_object, write_object

VERSION = 'atlas-snapshot-objects-v1'
MARKER = '_snapshotManifest'
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_RECORDS = 256
TARGET_CHUNK_BYTES = 1024 * 1024
MAX_RECORD_COUNT = 2_000_000


def object_root(snapshot: Path) -> Path:
    """
    Derive the shared project store without accepting a manifest-controlled path.
    """
    return Path(snapshot).parent.parent / '_objects'


def _owner(node: dict) -> str:
    """
    Group captured symbols by exact source file, preserving unknown-file symbols too.
    """
    return str(node.get('file') or node.get('source_file') or '')


def _chunks(records: list, owners: list[str]):
    """
    Yield bounded, file-owned groups with original positions for lossless reconstruction.
    """
    groups = OrderedDict()
    for index, (record, owner) in enumerate(zip(records, owners)):
        groups.setdefault(owner, []).append((index, record))
    for entries in groups.values():
        current, size = [], 0
        for entry in entries:
            encoded_size = len(canonical_bytes(entry[1]))
            if current and (len(current) >= MAX_RECORDS or size + encoded_size > TARGET_CHUNK_BYTES):
                yield current
                current, size = [], 0
            current.append(entry)
            size += encoded_size
        if current:
            yield current


def _pack_edges(edges: list[dict]) -> dict:
    """
    Intern endpoint strings inside one chunk while retaining every ordered edge occurrence.
    """
    names, indexes, rows = [], {}, []
    for edge in edges:
        endpoints = []
        for key in ('source', 'target'):
            name = edge[key]
            if name not in indexes:
                indexes[name] = len(names)
                names.append(name)
            endpoints.append(indexes[name])
        remainder = {key: value for key, value in edge.items() if key not in {'source', 'target'}}
        rows.append([*endpoints, remainder])
    return {'endpoints': names, 'rows': rows}


def _unpack_edges(payload: object) -> list[dict]:
    """
    Validate the local endpoint table and restore original edge attributes and duplicates.
    """
    if not isinstance(payload, dict) or set(payload) != {'endpoints', 'rows'}:
        raise ValueError('Invalid snapshot edge chunk')
    names, rows = payload['endpoints'], payload['rows']
    if (not isinstance(names, list) or not all(isinstance(name, str) for name in names)
            or not isinstance(rows, list)):
        raise ValueError('Invalid snapshot edge table')
    # Endpoint references can repeat a long name thousands of times even though
    # the compressed table is tiny. Charge their restored JSON size before
    # constructing an expanded edge collection or serializing repeated strings.
    name_sizes = [_json_size(name) for name in names]
    result, expanded = [], 0
    for row in rows:
        if (not isinstance(row, list) or len(row) != 3 or not isinstance(row[2], dict)
                or any(type(index) is not int or not 0 <= index < len(names) for index in row[:2])
                or 'source' in row[2] or 'target' in row[2]):
            raise ValueError('Invalid snapshot edge row')
        row_size = (_json_size(row[2]) + len(b'"source":') + len(b'"target":')
                    + name_sizes[row[0]] + name_sizes[row[1]] + (2 if row[2] else 1))
        expanded += row_size + (1 if result else 0)
        if expanded > MAX_EXPANDED_BYTES:
            raise ValueError('Snapshot edge expansion exceeds budget')
        result.append({'source': names[row[0]], 'target': names[row[1]], **row[2]})
    return result


def _write_collection(root: Path, records: list, owners: list[str], *, edges: bool) -> dict:
    """
    Store groups independently and preserve original graph order using compact index pairs.
    """
    references, order = [], [None] * len(records)
    for entries in _chunks(records, owners):
        values = [entry[1] for entry in entries]
        payload = _pack_edges(values) if edges else values
        reference = write_object(root, payload)
        number = len(references)
        references.append({**reference, 'count': len(values)})
        for local, (original, _) in enumerate(entries):
            order[original] = [number, local]
    return {'chunks': references, 'order': order, 'count': len(records)}


def _pack_nodes(originals: list[dict]) -> tuple[list, dict, list, int]:
    """
    Separate shared project facts and volatile health timestamps from stable node chunks.
    """
    facts, nodes, times, time_indexes, expanded = {}, [], [], {}, 0
    for original in originals:
        if not isinstance(original, dict):
            raise ValueError('Invalid snapshot node')
        node = dict(original)
        extras = {}
        if 'codeProjectFacts' in node:
            value = node.pop('codeProjectFacts')
            encoded = canonical_bytes(value)
            expanded += len(encoded)
            key = sha256(encoded).hexdigest()
            facts[key] = value
            extras['projectFact'] = key
        health = node.get('health')
        if isinstance(health, dict) and 'measuredAt' in health:
            health = dict(health)
            value = health.pop('measuredAt')
            node['health'] = health
            encoded = canonical_bytes(value)
            expanded += len(encoded)
            if encoded not in time_indexes:
                time_indexes[encoded] = len(times)
                times.append(value)
            extras['healthMeasuredAt'] = time_indexes[encoded]
        # The wrapper is unambiguous even for original values containing codec
        # marker-like keys. Timestamps live separately so rebuild time alone does
        # not rewrite every otherwise unchanged node object.
        nodes.append([node, extras] if extras else [node])
    return nodes, facts, times, expanded


def _publish_manifest(path: Path, manifest: dict) -> None:
    """
    Replace one manifest only after every referenced immutable object is available.
    """
    encoded = canonical_bytes({MARKER: manifest})
    if len(encoded) > MAX_MANIFEST_BYTES or manifest['logicalBytes'] > MAX_EXPANDED_BYTES:
        raise ValueError('Snapshot manifest exceeds budget')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Snapshot manifest must not be redirected')
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(encoded)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_snapshot(path: Path, payload: dict) -> dict:
    """
    Write immutable compressed objects, then atomically publish their manifest.

    The payload is a storage projection (health codec may already be applied).
    Unknown top-level fields and graph list ordering are preserved. Failure leaves
    the previous manifest intact; unreferenced new objects can be collected later.
    """
    if (not isinstance(payload, dict) or not isinstance(payload.get('nodes'), list)
            or not isinstance(payload.get('edges'), list) or MARKER in payload
            or len(payload['nodes']) > MAX_RECORD_COUNT or len(payload['edges']) > MAX_RECORD_COUNT):
        raise ValueError('Invalid graph for snapshot storage')
    root = object_root(path)
    nodes, facts, times, fact_expanded = _pack_nodes(payload['nodes'])
    by_id = {node['id']: _owner(node) for node in payload['nodes']}
    node_owners = [_owner(node) for node in payload['nodes']]
    edge_owners = [by_id.get(edge.get('source'), '') for edge in payload['edges']]
    header = {key: value for key, value in payload.items() if key not in {'nodes', 'edges'}}
    manifest = {'version': VERSION, 'logicalBytes': len(canonical_bytes(payload)),
                'header': write_object(root, header), 'facts': write_object(root, facts),
                'healthTimes': write_object(root, times),
                'nodes': _write_collection(root, nodes, node_owners, edges=False),
                'edges': _write_collection(root, payload['edges'], edge_owners, edges=True)}
    if sum(reference['bytes'] for reference in _references(manifest)) + fact_expanded > MAX_EXPANDED_BYTES:
        raise ValueError('Snapshot fact expansion exceeds budget')
    _publish_manifest(path, manifest)
    return manifest


def _manifest(value: object) -> dict | None:
    """
    Recognize one strict version; legacy graph dictionaries pass through unchanged.
    """
    if not isinstance(value, dict) or MARKER not in value:
        return None
    result = value[MARKER]
    if (set(value) != {MARKER} or not isinstance(result, dict)
            or set(result) != {'version', 'logicalBytes', 'header', 'facts', 'healthTimes', 'nodes', 'edges'}
            or result.get('version') != VERSION or type(result.get('logicalBytes')) is not int
            or not 0 < result['logicalBytes'] <= MAX_EXPANDED_BYTES):
        raise ValueError('Invalid snapshot manifest')
    return result


def _read_document(path: Path) -> object:
    """
    Read a manifest or legacy JSON under an expanded snapshot cap without following links.
    """
    if path.is_symlink():
        raise ValueError('Snapshot must not be redirected')
    with path.open('rb') as source:
        content = source.read(MAX_EXPANDED_BYTES + 1)
    if len(content) > MAX_EXPANDED_BYTES:
        raise ValueError('Snapshot document exceeds budget')
    value = json.loads(content)
    if isinstance(value, dict) and MARKER in value and len(content) > MAX_MANIFEST_BYTES:
        raise ValueError('Snapshot manifest exceeds budget')
    return value


def _references(manifest: dict):
    """
    Validate collection descriptors before opening any manifest-selected object.
    """
    for key in ('header', 'facts', 'healthTimes'):
        reference = manifest[key]
        if (not isinstance(reference, dict) or set(reference) != {'hash', 'bytes'}
                or type(reference.get('bytes')) is not int or reference['bytes'] <= 0):
            raise ValueError('Invalid snapshot header reference')
        yield reference
    for key in ('nodes', 'edges'):
        collection = manifest[key]
        if (not isinstance(collection, dict) or set(collection) != {'chunks', 'order', 'count'}
                or not isinstance(collection['chunks'], list) or not isinstance(collection['order'], list)
                or type(collection['count']) is not int or not 0 <= collection['count'] <= MAX_RECORD_COUNT
                or len(collection['order']) != collection['count']):
            raise ValueError('Invalid snapshot collection')
        total = 0
        for reference in collection['chunks']:
            if (not isinstance(reference, dict) or set(reference) != {'hash', 'bytes', 'count'}
                    or type(reference['count']) is not int or not 0 < reference['count'] <= MAX_RECORDS):
                raise ValueError('Invalid snapshot chunk reference')
            total += reference['count']
            yield {key: reference[key] for key in ('hash', 'bytes')}
        if total != collection['count']:
            raise ValueError('Snapshot collection count differs from chunks')


def _read_collection(root: Path, collection: dict, *, edges: bool):
    """
    Load one collection and reject duplicate, missing or out-of-range order coordinates.
    """
    references = collection['chunks']
    remaining = [reference['count'] for reference in references]
    chunks, seen = {}, set()
    for pair in collection['order']:
        if (not isinstance(pair, list) or len(pair) != 2 or any(type(i) is not int for i in pair)
                or not 0 <= pair[0] < len(references) or not 0 <= pair[1] < references[pair[0]]['count']
                or tuple(pair) in seen):
            raise ValueError('Invalid snapshot record order')
        seen.add(tuple(pair))
        number, local = pair
        if number not in chunks:
            reference = references[number]
            value = read_object(root, {key: reference[key] for key in ('hash', 'bytes')})
            rows = _unpack_edges(value) if edges else value
            if not isinstance(rows, list) or len(rows) != reference['count']:
                raise ValueError('Snapshot chunk count mismatch')
            chunks[number] = rows
        row = chunks[number][local]
        remaining[number] -= 1
        if remaining[number] == 0:
            # A source file may be interleaved with other owners in graph order.
            # Keep its chunk only until its final actual occurrence is consumed.
            del chunks[number]
        yield row


def _json_size(value: object) -> int:
    """
    Count exact compact JSON bytes for one bounded record; key sorting cannot affect length.
    """
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def read_snapshot(path: Path) -> dict:
    """
    Restore exact graph facts from a manifest, or read an unchanged legacy graph.

    Each object is bounded and checksummed. Aggregate declared expanded bytes are
    checked before decompression; numeric intern references never escape this API.
    """
    value = _read_document(path)
    manifest = _manifest(value)
    if manifest is None:
        return value
    references = list(_references(manifest))
    if sum(reference.get('bytes', MAX_EXPANDED_BYTES + 1) for reference in references) > MAX_EXPANDED_BYTES:
        raise ValueError('Snapshot expanded object budget exceeded')
    root = object_root(path)
    header = read_object(root, manifest['header'])
    facts = read_object(root, manifest['facts'])
    times = read_object(root, manifest['healthTimes'])
    if (not isinstance(header, dict) or 'nodes' in header or 'edges' in header
            or not isinstance(facts, dict) or not isinstance(times, list)):
        raise ValueError('Invalid snapshot header or fact table')
    fact_bytes = {}
    for key, value in facts.items():
        encoded = canonical_bytes(value)
        if not isinstance(key, str) or sha256(encoded).hexdigest() != key:
            raise ValueError('Invalid snapshot fact content key')
        fact_bytes[key] = encoded
    expanded = sum(reference['bytes'] for reference in references)
    time_bytes = [canonical_bytes(value) for value in times]
    logical_base = _json_size(header) + len(b'"nodes":') + len(b'"edges":') + 4 + (2 if header else 1)
    logical_limit = min(MAX_EXPANDED_BYTES, manifest['logicalBytes'])
    if logical_base > logical_limit:
        raise ValueError('Snapshot reconstructed length exceeds budget')
    nodes, node_bytes = [], 0
    for row in _read_collection(root, manifest['nodes'], edges=False):
        if (not isinstance(row, list) or len(row) not in {1, 2} or not isinstance(row[0], dict)
                or 'codeProjectFacts' in row[0]):
            raise ValueError('Invalid snapshot node row')
        node = dict(row[0])
        extras = row[1] if len(row) == 2 else {}
        if not isinstance(extras, dict) or set(extras) - {'projectFact', 'healthMeasuredAt'}:
            raise ValueError('Invalid snapshot node extras')
        if 'projectFact' in extras:
            index = extras['projectFact']
            if not isinstance(index, str) or index not in facts:
                raise ValueError('Invalid snapshot fact reference')
            expanded += len(fact_bytes[index])
            if expanded > MAX_EXPANDED_BYTES:
                raise ValueError('Snapshot fact expansion exceeds budget')
            # Fact values are response-owned: one mutation must not affect a peer.
            node['codeProjectFacts'] = json.loads(fact_bytes[index])
        if 'healthMeasuredAt' in extras:
            index = extras['healthMeasuredAt']
            if (type(index) is not int or not 0 <= index < len(times)
                    or not isinstance(node.get('health'), dict) or 'measuredAt' in node['health']):
                raise ValueError('Invalid snapshot health timestamp')
            expanded += len(time_bytes[index])
            if expanded > MAX_EXPANDED_BYTES:
                raise ValueError('Snapshot timestamp expansion exceeds budget')
            node['health'] = {**node['health'], 'measuredAt': json.loads(time_bytes[index])}
        node_bytes += _json_size(node) + (1 if nodes else 0)
        if logical_base + node_bytes > logical_limit:
            raise ValueError('Snapshot node expansion exceeds budget')
        nodes.append(node)
    edges, edge_bytes = [], 0
    for edge in _read_collection(root, manifest['edges'], edges=True):
        edge_bytes += _json_size(edge) + (1 if edges else 0)
        if logical_base + node_bytes + edge_bytes > logical_limit:
            raise ValueError('Snapshot edge expansion exceeds budget')
        edges.append(edge)
    # The exact length can be checked from independently bounded records. Avoid
    # allocating and sorting a second full-graph JSON string solely for counting.
    logical_bytes = logical_base + node_bytes + edge_bytes
    result = {**header, 'nodes': nodes, 'edges': edges}
    if logical_bytes != manifest['logicalBytes']:
        raise ValueError('Snapshot reconstructed length mismatch')
    return result


def snapshot_source_stamp(path: Path) -> list[int]:
    """
    Stamp manifest identity plus referenced object revisions, with logical bytes for LRU.

    Objects are immutable and content-verified when read. Their stat revisions are
    included so missing/replaced/corrupt files cannot reuse an old warm snapshot.
    Legacy JSON keeps its original inexpensive two-integer stat contract.
    """
    stat = path.stat()
    with path.open('rb') as source:
        prefix = source.read(80)
    if b'"_snapshotManifest"' not in prefix:
        return [stat.st_mtime_ns, stat.st_size]
    manifest = _manifest(_read_document(path))
    if manifest is None:
        raise ValueError('Invalid snapshot manifest marker')
    digest = sha256(canonical_bytes(manifest))
    root = object_root(path)
    for reference in _references(manifest):
        object_path = checked_object_path(root, reference.get('hash'))
        current = object_path.stat()
        digest.update(canonical_bytes([reference, current.st_mtime_ns, current.st_size]))
    return [int.from_bytes(digest.digest()[:16], 'big'), manifest['logicalBytes']]
