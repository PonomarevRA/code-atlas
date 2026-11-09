"""Immutable function inventory and a bounded static path for one review side."""

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256

from .analysis_diagnostics import diagnostic_summary
from .execution_path import ExecutionPathError, build_execution_path
from .position_context.source import relative_name
from .source_viewer import SourceError, _source_path, _read_regular_file
from .store import StoreError

MAX_FUNCTIONS = 50_000
MAX_VERIFY_BYTES = 128 * 1024 * 1024
MAX_VERIFY_FILES = 4000
PATH_NODES = 32
PATH_EDGES = 100
STEP_EVENTS = 20
CALLABLE_KINDS = {'method', 'function', 'constructor'}


def function_inventory(root, graph):
    """Index unique AST declaration identities and retain explicit gaps in coverage."""
    rows, counts = [], Counter()
    unavailable = 0
    ambiguous = set()
    for node in graph.get('nodes', []):
        facts = node.get('reviewDeclaration')
        if not facts:
            kind = (node.get('callableFacts') or {}).get('kind') or node.get('kind')
            if kind in CALLABLE_KINDS or node.get('_callable') is True:
                unavailable += 1
            continue
        ambiguous.update(facts.get('ambiguousIdentities', ()))
        identity = facts['identity']
        file = relative_name(root, facts['file'])
        if not identity or not file:
            unavailable += 1
            continue
        counts[identity] += 1
        rows.append({'identity': identity, 'nodeId': node['id'], 'file': file,
                     'label': node.get('label', node['id'])[:500],
                     'line': facts['line'], 'endLine': facts['endLine'],
                     'sourceHash': facts['sourceHash'], 'declarationHash': facts['declarationHash']})
        if len(rows) > MAX_FUNCTIONS:
            raise StoreError('Слишком много функций для одного обзора.', 413)
    ambiguous.update(key for key, count in counts.items() if count > 1)
    unique = {row['identity']: row for row in rows if row['identity'] not in ambiguous}
    diagnostics = diagnostic_summary(graph)
    metadata = graph.get('metadata', {})
    partial = any((metadata.get(key) or {}).get('truncated')
                  for key in ('memberGraph', 'sourceStructure'))
    coverage = {'functions': len(unique), 'unavailable': unavailable, 'ambiguous': len(ambiguous),
                'analysisStatus': diagnostics.get('analysisStatus', 'unknown'),
                'partial': partial or bool(ambiguous or unavailable)
                or diagnostics.get('analysisStatus') != 'complete'}
    return unique, sorted(ambiguous), coverage


def _verified_source(root, file, expected_hash):
    """Hash the current file through Atlas's safe local reader without retaining its contents."""
    try:
        source_root, relative = _source_path(root, {'file': file})
        content = _read_regular_file(source_root, relative)
    except SourceError as error:
        raise StoreError('Файл из снимка недоступен. Перестройте карту checkout: ' + file, 409) from error
    if sha256(content).hexdigest() != expected_hash:
        raise StoreError('Файл изменился после снимка. Перестройте карту checkout: ' + file, 409)
    return len(content)


def _verify_inventory(root, functions, entry):
    """Verify captured files within a work budget. Unindexed and new files are outside this check."""
    hashes = {}
    for row in functions.values():
        file, digest = row['file'], row['sourceHash']
        if hashes.setdefault(file, digest) != digest:
            raise StoreError('Карта содержит разные версии файла. Перестройте карту checkout: ' + file, 409)
    if entry:
        # The selected path's source always gets verified, even on a large checkout.
        first = entry['file']
        hashes = {first: hashes[first], **hashes}
    verified = total_bytes = 0
    for file, digest in hashes.items():
        if verified >= MAX_VERIFY_FILES or total_bytes >= MAX_VERIFY_BYTES:
            break
        total_bytes += _verified_source(root, file, digest)
        verified += 1
    return {'verifiedFiles': verified, 'capturedFiles': len(hashes)}



def _source_events(step):
    """Summarize lexical branches, terminal statements and call sites in source order."""
    rows = []
    control = step.get('controlFlow') or {}
    for branch in control.get('branches', []):
        label = branch.get('condition') or branch.get('selector') or branch.get('header') or branch.get('label')
        rows.append({'kind': 'branch', 'text': label or branch.get('kind'), 'line': branch.get('line'),
                     'conditional': bool(branch.get('parentArmIds'))})
    for terminal in control.get('terminals', []):
        rows.append({'kind': terminal.get('kind'), 'text': terminal.get('text') or terminal.get('label'),
                     'line': terminal.get('line'), 'conditional': bool(terminal.get('branchIds'))})
    for event in step.get('events', []):
        if event.get('kind') not in {'comment', 'documentation'}:
            rows.append({'kind': event.get('kind'), 'text': event.get('text'), 'line': event.get('line'),
                         'conditional': bool(event.get('conditional'))})
    for row in rows:
        row['text'] = str(row['text'] or row['kind'] or '')[:400]
    rows.sort(key=lambda row: row['line'] or 0)
    return rows


def _path_step(step, root):
    """Store source events only when their bytes match the captured graph declaration."""
    provenance = step.get('sourceProvenance') or {}
    verified = provenance.get('status') == 'match'
    relevant = _source_events(step) if verified else []
    truncated = bool((step.get('controlFlow') or {}).get('truncated') or step.get('eventsTruncated'))
    return {'nodeId': step['nodeId'], 'label': step['label'][:500], 'depth': step['depth'],
            'file': relative_name(root, step.get('file')), 'line': step.get('line') if verified else None,
            'sourceVerified': verified, 'events': relevant[:STEP_EVENTS],
            'eventsTruncated': truncated or len(relevant) > STEP_EVENTS,
            'unresolvedCount': len(step.get('unresolved', []))}


def _static_path(root, graph, snapshot_id, entry):
    """Capture one possible static path, rejecting a race during entry source analysis."""
    _verified_source(root, entry['file'], entry['sourceHash'])
    try:
        path = build_execution_path(root, graph, entry['nodeId'], snapshot_id,
                                    max_depth=5, max_nodes=PATH_NODES,
                                    snapshot_calls_only=True)
    except ExecutionPathError as error:
        raise StoreError(str(error), error.status) from error
    _verified_source(root, entry['file'], entry['sourceHash'])
    steps = [_path_step(step, root) for step in path['steps']]
    edges = []
    for edge in path['edges']:
        if edge['relation'] in {'calls', 'dispatches_to'}:
            edges.append({key: edge[key] for key in ('source', 'target', 'relation', 'confidence')})
    return {'steps': steps, 'edges': edges[:PATH_EDGES],
            'truncated': any(path['truncation'].values()) or len(edges) > PATH_EDGES
            or any(step['eventsTruncated'] for step in steps)}


def capture_side(root, graph, snapshot_id, *, node_id=None, entry_identity=None):
    """Materialize a snapshot side so a later review never reads historical files live."""
    functions, ambiguous, coverage = function_inventory(root, graph)
    if node_id is not None:
        entry = next((row for row in functions.values() if row['nodeId'] == node_id), None)
        if entry is None:
            raise StoreError('Выберите функцию с однозначным объявлением на свежей карте checkout.', 422)
    else:
        entry = functions.get(entry_identity)
        if not functions and not (graph.get('metadata', {}).get('memberGraph') or
                                  graph.get('metadata', {}).get('sourceStructure')):
            raise StoreError('Для итогового снимка требуется свежая карта с фактами объявлений.', 409)
    coverage.update(_verify_inventory(root, functions, entry))
    metadata = graph.get('metadata', {})
    diagnostics = metadata.get('analysisDiagnostics') or {}
    failed_files = set()
    for issue in diagnostics.get('issues', []):
        file = relative_name(root, issue.get('path'))
        if file:
            failed_files.add(file)
    path = _static_path(root, graph, snapshot_id, entry) if entry else None
    return {'snapshotId': snapshot_id, 'capturedAt': datetime.now(timezone.utc).isoformat(),
            'snapshotCreatedAt': metadata.get('graphUpdatedAt'),
            'coverage': coverage, 'entry': entry, 'path': path,
            'functions': functions, 'ambiguous': ambiguous, 'failedFiles': sorted(failed_files)}


def compare_functions(before, after):
    """Pair exact lexical identities and compare declaration bytes, ignoring line shifts."""
    left, right = before['functions'], after['functions']
    ambiguous = set(before['ambiguous']) | set(after['ambiguous'])
    failed_before = set(before.get('failedFiles', ()))
    failed_after = set(after.get('failedFiles', ()))
    changes = []
    for identity in (left.keys() | right.keys()) - ambiguous:
        old, new = left.get(identity), right.get(identity)
        if old and new:
            if old['declarationHash'] == new['declarationHash']:
                continue
            kind = 'modified'
        elif new:
            kind = 'unconfirmed' if new['file'] in failed_before else 'added'
        else:
            kind = 'unconfirmed' if old['file'] in failed_after else 'removed'
        changes.append({'kind': kind, 'before': old, 'after': new})
    changes.sort(key=lambda row: ((row['after'] or row['before'])['file'],
                                  (row['after'] or row['before'])['line'], row['kind']))
    return changes
