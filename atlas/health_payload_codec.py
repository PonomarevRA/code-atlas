"""Lossless persistence-only interning of repeated health profiles; API values stay expanded."""

import json

VERSION = 'health-payload-v1'
MARKER = '_healthPayloadCodec'
FIELDS = ('organization', 'componentCoverage')
MAX_EXPANDED_HEALTH_BYTES = 256 * 1024 * 1024


def _nodes(payload: dict):
    """Read canonical node lists or overlay node maps without interpreting other graph facts."""
    nodes = payload.get('nodes', [])
    if isinstance(nodes, dict):
        return nodes.values()
    if isinstance(nodes, list):
        return nodes
    raise ValueError('Invalid health payload node collection')


def _canonical(value) -> str:
    """Use exact JSON value equality for table keys, independent of dictionary insertion order."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def encode_health_payloads(payload: dict) -> dict:
    """Return a storage projection without mutating the canonical graph or overlay.

    Only two known health fields are interned. Oversized logical expansion keeps
    the original plain representation instead of producing an unreadable codec.
    """
    if MARKER in payload:
        raise ValueError('Health payload must be decoded before encoding')
    tables = {field: [] for field in FIELDS}
    indexes = {field: {} for field in FIELDS}
    identities, expanded, changed = {}, 0, False
    projected = []
    for node in _nodes(payload):
        if not isinstance(node, dict):
            raise ValueError('Invalid health payload node')
        health = node.get('health')
        if not isinstance(health, dict):
            projected.append(node)
            continue
        packed_health = dict(health)
        for field in FIELDS:
            value = health.get(field)
            if not isinstance(value, dict):
                continue
            identity = field, id(value)
            cached = identities.get(identity)
            if cached is None:
                canonical = _canonical(value)
                index = indexes[field].get(canonical)
                if index is None:
                    index = len(tables[field])
                    indexes[field][canonical] = index
                    tables[field].append(value)
                cached = index, len(canonical.encode('utf-8'))
                identities[identity] = cached
            index, size = cached
            expanded += size
            if expanded > MAX_EXPANDED_HEALTH_BYTES:
                return payload
            packed_health[field] = {'$healthPayload': field, 'index': index}
            changed = True
        projected.append({**node, 'health': packed_health})
    if not changed:
        return payload
    result = dict(payload)
    original = payload.get('nodes', [])
    result['nodes'] = dict(zip(original, projected)) if isinstance(original, dict) else projected
    result[MARKER] = {'version': VERSION, 'tables': tables}
    return result


def decode_health_payloads(payload: dict) -> dict:
    """Expand validated references into independent JSON objects for each node.

    Plain legacy JSON is unchanged. Bad versions/references fail closed; a logical
    health expansion budget prevents compact damaged input from allocating without
    bound. The budget matches the existing background snapshot byte allowance.
    """
    if MARKER not in payload:
        return payload
    codec = payload[MARKER]
    if not isinstance(codec, dict) or set(codec) != {'version', 'tables'} or codec.get('version') != VERSION:
        raise ValueError('Unsupported health payload codec')
    tables = codec.get('tables')
    if not isinstance(tables, dict) or set(tables) != set(FIELDS):
        raise ValueError('Invalid health payload tables')
    sizes, encoded_rows = {}, {}
    for field in FIELDS:
        rows = tables[field]
        if not isinstance(rows, list) or any(not isinstance(row, dict) or '$healthPayload' in row for row in rows):
            raise ValueError('Invalid health payload table')
        encoded_rows[field] = [json.dumps(row, ensure_ascii=False, separators=(',', ':')) for row in rows]
        sizes[field] = [len(row.encode('utf-8')) for row in encoded_rows[field]]
    references, expanded = [], 0
    for node in _nodes(payload):
        if not isinstance(node, dict):
            raise ValueError('Invalid health payload node')
        health = node.get('health')
        if not isinstance(health, dict):
            continue
        for field in FIELDS:
            value = health.get(field)
            if not isinstance(value, dict) or '$healthPayload' not in value:
                continue
            index = value.get('index')
            if (set(value) != {'$healthPayload', 'index'} or value['$healthPayload'] != field
                    or type(index) is not int or not 0 <= index < len(tables[field])):
                raise ValueError('Invalid health payload reference')
            expanded += sizes[field][index]
            if expanded > MAX_EXPANDED_HEALTH_BYTES:
                raise ValueError('Health payload expansion exceeds snapshot budget')
            references.append((health, field, index))
    for health, field, index in references:
        # Native JSON reconstruction keeps the same owned dictionary/list
        # isolation as deepcopy, without a Python-level visit for every leaf.
        health[field] = json.loads(encoded_rows[field][index])
    payload.pop(MARKER)
    return payload
