"""Bounded derived health overlays keep published source snapshots immutable."""
from copy import deepcopy
from functools import lru_cache
import json
import hashlib

from .code_health_policy import policy
from .health_payload_codec import decode_health_payloads
from .health_snapshot_delta import VERSION as DELTA_VERSION, validate_delta

MAX_OVERLAY_BYTES = 64 * 1024 * 1024


def file_stamp(path):
    """Return a cheap immutable cache key; absent derivative files are ordinary before the first refresh."""
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return 0, 0


def overlay_stamp(snapshot):
    """Changing a derived overlay invalidates the in-memory graph without changing source snapshot bytes."""
    return file_stamp(snapshot.with_name('code-health-overlay.json'))


@lru_cache(maxsize=2)
def _read(snapshot, source_stamp, derived_stamp, policy_version, analyzer_version):
    """Keep at most two bounded host-produced overlays, tied to exact snapshot bytes and active policy."""
    path = snapshot.with_name('code-health-overlay.json')
    if not 0 < derived_stamp[1] <= MAX_OVERLAY_BYTES:
        return None
    try:
        result = decode_health_payloads(json.loads(path.read_text(encoding='utf-8')))
        if (result.get('snapshotId') != snapshot.parent.name or result.get('sourceStamp') != list(source_stamp)
                or result.get('policyVersion') != policy_version or result.get('analyzerVersion') != analyzer_version
                or result.get('sourceHash') != snapshot_hash(snapshot, source_stamp) or not isinstance(result.get('nodes'), dict)
                or not isinstance(result.get('codeHealth'), dict)):
            return None
        if 'formatVersion' in result:
            validate_delta(result)
        return result
    except (OSError, ValueError, TypeError, AttributeError):
        return None


@lru_cache(maxsize=2)
def snapshot_hash(snapshot, source_stamp):
    """Hash exact immutable snapshot bytes once per stat revision, with bounded retention."""
    with snapshot.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def read_overlay(snapshot):
    """Ignore missing, obsolete or damaged derivatives; the captured graph remains usable."""
    from .code_health import ANALYZER_VERSION
    return _read(snapshot, file_stamp(snapshot), overlay_stamp(snapshot), policy()['version'], ANALYZER_VERSION)


def apply_overlay(graph, snapshot):
    """Merge only health/documentation fields into an owned response graph, preserving topology and raw IDs."""
    overlay = read_overlay(snapshot)
    if overlay is None:
        return graph
    delta = overlay.get('formatVersion') == DELTA_VERSION
    for node in graph.get('nodes', []):
        fields = overlay['nodes'].get(node['id'], {})
        if not isinstance(fields, dict):
            continue
        removed = overlay.get('removedFields', {}).get(node['id'], []) if delta else []
        for key in removed:
            node.pop(key, None)
        for key in ('health', 'documentationHealth'):
            if key in fields and (delta or isinstance(fields[key], dict)):
                node[key] = deepcopy(fields[key])
        # Explicit rows retain their exact timestamp, including missing/custom
        # values. Only omitted rows receive the shared measured-at value.
        if (delta and 'health' not in fields and 'health' not in removed
                and isinstance(node.get('health'), dict) and isinstance(overlay.get('healthMeasuredAt'), str)):
            node['health']['measuredAt'] = overlay['healthMeasuredAt']
    graph.setdefault('metadata', {})['codeHealth'] = deepcopy(overlay['codeHealth'])
    graph['metadata']['healthRefresh'] = {key: overlay[key] for key in ('jobId', 'snapshotId', 'policyVersion', 'analyzerVersion', 'sourceHash', 'measuredAt')}
    return graph


def rebase_health(graph: dict, captured: dict) -> dict:
    """Replace only health fields from the same pinned canonical graph after a publication race."""
    by_id = {node['id']: node for node in captured.get('nodes', [])}
    for node in graph.get('nodes', []):
        source = by_id.get(node['id'], {})
        for key in ('health', 'documentationHealth'):
            if key in source:
                node[key] = deepcopy(source[key])
            else:
                node.pop(key, None)
    metadata = graph.setdefault('metadata', {})
    for key in ('codeHealth', 'healthRefresh'):
        if key in captured.get('metadata', {}):
            metadata[key] = deepcopy(captured['metadata'][key])
        else:
            metadata.pop(key, None)
    return graph
