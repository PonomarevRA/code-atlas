"""Whitelisted logical configuration names, without passwords, URI paths or connection strings."""
import json
import re
from ..system_configuration import configured_systems, _key_lines


def safe_name(value):
    """Only public resource identifiers are exposed; arbitrary configuration values remain private."""
    return value if isinstance(value, str) and re.fullmatch(r'[\w.\-/]{1,249}', value) else None


def configuration(source):
    """Read JSON variants without selecting an active environment or exposing credential values."""
    # appsettings supports JSON comments/trailing commas. Preserve strings and line numbers.
    tokens = r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/'
    cleaned = re.sub(tokens, lambda m: m[0] if m[0].startswith('"') else ''.join('\n' if c == '\n' else ' ' for c in m[0]), source['text'])
    cleaned = re.sub(r'"(?:\\.|[^"\\])*"|,(?=\s*[}\]])', lambda m: ' ' if m[0] == ',' else m[0], cleaned)
    document = json.loads(cleaned)
    lines = _key_lines(cleaned)
    result = []
    for system in configured_systems(document, cleaned):
        if system['kind'] == 'database':
            result.append({'kind': 'database', 'name': system['logicalName'], 'engine': system.get('databaseType'),
                           'host': system.get('host'), 'file': source['file'], 'line': system.get('line', 1), 'status': 'configured'})
    pending = [((), document)]
    while pending:
        if len(result) > 2000:
            raise ValueError('infrastructure configuration budget')
        path, value = pending.pop()
        if len(path) > 32:
            continue
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            child = path + (str(key),)
            joined = '.'.join(child)
            name = safe_name(item)
            if name and key.lower() in {'topic', 'topicname', 'queue', 'queuename', 'database', 'databasename', 'dbname'}:
                kind = 'kafka' if 'topic' in key.lower() else 'rabbit' if 'queue' in key.lower() else 'database'
                result.append({'kind': kind, 'name': name, 'key': joined, 'file': source['file'], 'line': lines.get(child, 1), 'status': 'configured'})
            if isinstance(item, dict):
                pending.append((child, item))
    return result
