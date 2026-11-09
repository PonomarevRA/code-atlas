"""Portable bounded Atlas usage DTO and Prometheus projection; no service/runtime imports."""
import json
import math
import re
SCHEMA = 'atlas-usage/v1'
MAX_RECORDS = 192
MAX_BYTES = 128000
MAX_COUNT = 2 ** 53 - 1
BUCKETS = (0.001, 0.005, 0.025, 0.1, 0.5, 2.5, 10.0, 60.0, 3600.0)
OUTCOMES = ('success', 'error', 'cancelled', 'unavailable', 'cache_hit')
MOD_OPERATIONS = ('semantic_probe', 'semantic_analyze', 'grouping', 'source_rules')
HTTP_OPERATIONS = ('map', 'node', 'semantic', 'health', 'document', 'browser', 'other')
CLIENTS = ('http', 'codex', 'cursor', 'claude', 'mcp_unknown')
IDENTIFIER = re.compile('[a-z][a-z0-9-]{0,63}\\Z')

def number(value, maximum=1e+18, integer=False):
    """Reject booleans, NaN/infinity and values beyond the portable counter/gauge budget."""
    if type(value) not in ((int,) if integer else (int, float)) or not 0 <= value <= maximum or (not math.isfinite(value)):
        raise ValueError('Invalid bounded usage number')
    return value

def validate_usage(value):
    """Validate every field before persisting/re-exporting a source-controlled usage snapshot."""
    if not isinstance(value, dict) or set(value) != {'schemaVersion', 'startedAt', 'httpInFlight', 'droppedObservations', 'records'} or value['schemaVersion'] != SCHEMA:
        raise ValueError('Unsupported Atlas usage contract')
    number(value['startedAt'])
    number(value['httpInFlight'], MAX_COUNT, True)
    number(value['droppedObservations'], MAX_COUNT, True)
    rows = value['records']
    if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
        raise ValueError('Usage series budget')
    keys = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'kind', 'mod', 'operation', 'client', 'count', 'outcomes', 'inFlight', 'seconds', 'buckets', 'lastCompleted', 'facts'}:
            raise ValueError('Invalid usage row')
        kind, mod, operation, client = (row[key] for key in ('kind', 'mod', 'operation', 'client'))
        if not all((isinstance(label, str) for label in (kind, mod, operation, client))):
            raise ValueError('Invalid usage label types')
        if kind == 'http':
            if mod != '' or operation not in HTTP_OPERATIONS or client not in CLIENTS:
                raise ValueError('Invalid HTTP labels')
        elif kind in {'mod', 'cache', 'selection'}:
            if not isinstance(mod, str) or not IDENTIFIER.fullmatch(mod) or operation not in MOD_OPERATIONS or (client != ''):
                raise ValueError('Invalid mod labels')
            if kind == 'selection' and operation != 'source_rules':
                raise ValueError('Invalid rule-pass label')
        else:
            raise ValueError('Invalid usage kind')
        key = (kind, mod, operation, client)
        if key in keys:
            raise ValueError('Duplicate usage row')
        keys.add(key)
        count = number(row['count'], MAX_COUNT, True)
        number(row['inFlight'], MAX_COUNT, True)
        number(row['seconds'])
        number(row['lastCompleted'])
        if not isinstance(row['outcomes'], dict) or set(row['outcomes']) != set(OUTCOMES):
            raise ValueError('Invalid outcomes')
        if sum((number(n, MAX_COUNT, True) for n in row['outcomes'].values())) != count:
            raise ValueError('Outcome counts differ')
        buckets = row['buckets']
        if not isinstance(buckets, list) or len(buckets) != len(BUCKETS) + 1 or sum((number(n, MAX_COUNT, True) for n in buckets)) != count:
            raise ValueError('Invalid histogram')
        if not isinstance(row['facts'], dict) or set(row['facts']) != {'confirmed_static', 'candidate', 'unknown'}:
            raise ValueError('Invalid fact counts')
        for n in row['facts'].values():
            number(n, MAX_COUNT, True)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    if len(encoded) > MAX_BYTES:
        raise ValueError('Usage byte budget')
    return json.loads(encoded)

def prometheus_lines(value, source=None, *, metadata=True):
    """Render current process counters without summing repeated diagnostic snapshots."""
    usage = validate_usage(value)
    lines = prometheus_metadata() if metadata else []

    def sample(name, value, labels):
        suffix = '{' + ','.join((k + '=' + json.dumps(str(v), ensure_ascii=False) for k, v in labels.items())) + '}' if labels else ''
        lines.append(f'{name}{suffix} {value}')
    base = {'source': source} if source is not None else {}
    sample('atlas_usage_process_started_timestamp_seconds', usage['startedAt'], base)
    sample('atlas_http_in_flight', usage['httpInFlight'], base)
    sample('atlas_usage_dropped_observations_total', usage['droppedObservations'], base)
    for row in usage['records']:
        labels = {**base, 'operation': row['operation']}
        if row['kind'] == 'http':
            labels['client'] = row['client']
            stem = 'atlas_http_requests'
        else:
            labels['mod'] = row['mod']
            stem = {'mod': 'atlas_mod_calls', 'cache': 'atlas_mod_cache', 'selection': 'atlas_mod_rule_passes'}[row['kind']]
        for outcome, count in row['outcomes'].items():
            sample(stem + '_total', count, {**labels, 'outcome': outcome})
        if row['kind'] in {'cache', 'selection'}:
            continue
        duration = 'atlas_http_request_duration_seconds' if row['kind'] == 'http' else 'atlas_mod_duration_seconds'
        cumulative = 0
        for edge, count in zip((*BUCKETS, '+Inf'), row['buckets']):
            cumulative += count
            sample(duration + '_bucket', cumulative, {**labels, 'le': edge})
        sample(duration + '_sum', row['seconds'], labels)
        sample(duration + '_count', row['count'], labels)
        if row['kind'] == 'mod':
            sample('atlas_mod_in_flight', row['inFlight'], labels)
            sample('atlas_mod_last_completed_timestamp_seconds', row['lastCompleted'], labels)
            for confidence, count in row['facts'].items():
                sample('atlas_semantic_facts_total', count, {**labels, 'confidence': confidence})
    return lines

def prometheus_metadata():
    """Describe fixed metric families once; collectors can merge several source snapshots safely."""
    families = {'atlas_usage_process_started_timestamp_seconds': 'gauge', 'atlas_http_in_flight': 'gauge', 'atlas_usage_dropped_observations_total': 'counter', 'atlas_http_requests_total': 'counter', 'atlas_mod_calls_total': 'counter', 'atlas_mod_cache_total': 'counter', 'atlas_mod_rule_passes_total': 'counter', 'atlas_mod_duration_seconds': 'histogram', 'atlas_http_request_duration_seconds': 'histogram', 'atlas_mod_in_flight': 'gauge', 'atlas_mod_last_completed_timestamp_seconds': 'gauge', 'atlas_semantic_facts_total': 'counter'}
    lines = []
    for name, kind in families.items():
        lines.extend(['# HELP ' + name + ' Process-lifetime Atlas aggregate; declarative rule passes and cache hits are separate from mod invocations.', '# TYPE ' + name + ' ' + kind])
    return lines
