"""Snapshot-relative health deltas retain exact values while omitting repeated derived facts."""

import hashlib
import json

FIELDS = ('health', 'documentationHealth')
VERSION = 2


def _fingerprint(value, *, health=False) -> str:
    """Bind an immutable comparison before an evaluator can mutate its input dictionaries."""
    if health and isinstance(value, dict):
        value = {key: item for key, item in value.items() if key != 'measuredAt'}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def capture_health_baseline(graph: dict) -> dict:
    """Retain small immutable digests of the captured raw graph, never a prior overlay."""
    return {node['id']: {field: _fingerprint(node[field], health=field == 'health')
                        for field in FIELDS if field in node} for node in graph.get('nodes', [])}


def health_delta(graph: dict, baseline: dict, measured_at) -> tuple[dict, dict]:
    """Compare scored facts with the raw baseline; explicit nulls and removals remain distinct.

    A health row may be omitted only when its sole ignored value is exactly the
    global health timestamp. Missing or nonstandard timestamps keep a full row.
    """
    changed, removed = {}, {}
    if {node['id'] for node in graph.get('nodes', [])} != set(baseline):
        raise ValueError('Health evaluator changed captured node inventory')
    for node in graph.get('nodes', []):
        previous = baseline[node['id']]
        fields, deletions = {}, []
        for field in FIELDS:
            if field not in node:
                if field in previous:
                    deletions.append(field)
                continue
            value = node[field]
            same = previous.get(field) == _fingerprint(value, health=field == 'health')
            if field == 'health':
                same = same and isinstance(value, dict) and isinstance(measured_at, str) and value.get('measuredAt') == measured_at
            if not same:
                fields[field] = value
        if fields:
            changed[node['id']] = fields
        if deletions:
            removed[node['id']] = deletions
    return changed, removed


def validate_delta(overlay: dict) -> None:
    """Reject unsupported field writes or conflicting deletion instructions before applying any delta."""
    if overlay.get('formatVersion') != VERSION:
        raise ValueError('Unsupported health delta version')
    for fields in overlay['nodes'].values():
        if not isinstance(fields, dict) or any(field not in FIELDS for field in fields):
            raise ValueError('Invalid health delta fields')
    removals = overlay.get('removedFields', {})
    if not isinstance(removals, dict):
        raise ValueError('Invalid health delta removals')
    for identifier, fields in removals.items():
        if (not isinstance(fields, list) or any(field not in FIELDS for field in fields)
                or set(fields) & set(overlay['nodes'].get(identifier, {}))):
            raise ValueError('Conflicting health delta fields')
