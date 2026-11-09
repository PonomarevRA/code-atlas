"""Create the small public A2A registry independently of private workspace memory."""
import json

MEMORY_ROOT = 'docs/agent-memory'
MEMORY_SOURCE = 'docs/architecture.md'


def public_memory() -> dict[str, bytes]:
    """Return four deterministic A2A files with local references and no private input."""
    area = 'code-analysis'
    kind = 'fact'
    name = 'snapshot-evidence.a2a.yaml'
    identity = f'{area}.{kind}.snapshot-evidence'
    title = 'Interpret Atlas results as static snapshot evidence'
    summary = 'Check the snapshot and verify important facts against the matching source file.'
    date = '2026-11-09'
    metadata = {'id': identity, 'title': title, 'summary': summary, 'status': 'active',
                'recorded': date, 'updated': date, 'tags': ['snapshots', 'evidence']}
    files = {
        'registry.a2a.yaml': {
            'schema': 'agent-memory-root-registry/v1', 'version': 1,
            'areas': [{'id': area, 'summary': 'Using static evidence from Code Atlas.',
                       'path': f'{area}/registry.a2a.yaml'}],
        },
        f'{area}/registry.a2a.yaml': {
            'schema': 'agent-memory-area-registry/v1', 'version': 1, 'area': area,
            'kinds': [{'kind': kind, 'summary': 'Reading snapshot evidence.',
                       'path': f'{kind}/registry.a2a.yaml'}],
        },
        f'{area}/{kind}/registry.a2a.yaml': {
            'schema': 'agent-memory-kind-registry/v1', 'version': 1, 'area': area, 'kind': kind,
            'objects': [metadata | {'path': name}],
        },
        f'{area}/{kind}/{name}': {
            'schema': 'agent-memory-object/v1', 'area': area, 'kind': kind, **metadata,
            'sources': [{'kind': 'document', 'ref': MEMORY_SOURCE}],
            'payload': {
                'statement': 'A Code Atlas map describes static evidence captured in a particular snapshot.',
                'consequence': 'Use the matching snapshot ID and verify important conclusions against source. '
                               'Incomplete evidence leaves a dependency unresolved.',
            },
        },
    }
    # JSON is a YAML subset, allowing export without a YAML dependency.
    return {f'{MEMORY_ROOT}/{path}': (json.dumps(data, indent=2) + '\n').encode('utf-8')
            for path, data in files.items()}


def public_git_metadata(name: str, data: bytes) -> bytes:
    """Let normal Git staging and archives include the seed in the exported copy."""
    private_rule = {'.gitignore': b'/docs/agent-memory/',
                    '.gitattributes': b'/docs/agent-memory export-ignore'}.get(name)
    if private_rule is None:
        return data
    return b''.join(line for line in data.splitlines(keepends=True) if line.strip() != private_rule)
