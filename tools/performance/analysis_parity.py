"""Export exact numeric health evidence for comparison across two Atlas trees.

Unlike the timing receipt's aggregate inventory, this checks every declaration's
known components and findings. New coverage/version fields are deliberately
outside this compatibility check and have their own contract tests.
"""

import argparse
import json
import sys
from pathlib import Path

from analysis_runner import digest, source_identity


def health_rows(filename, content):
    """Measure real grammar facts and retain stable scores, identities and rules."""
    from atlas.code_health import enrich_code_health
    from atlas.code_health_source import parse_health_source

    parsed = parse_health_source(filename, content)
    if parsed['truncated'] or not parsed['methods']:
        raise AssertionError('Parity fixture was not completely parsed: ' + filename)
    graph = enrich_code_health({'nodes': parsed['methods'], 'edges': [], 'metadata': {}})
    rows = []
    for node in graph['nodes']:
        health = node['health']
        if health['score'] is None:
            raise AssertionError('Fully-known fixture became unknown: ' + filename)
        rows.append({
            'identity': node['healthIdentity'], 'file': filename,
            'components': health['components'],
            'scores': {key: health.get(key) for key in (
                'score', 'structuralScore', 'readabilityScore', 'documentationScore',
                'organizationScore', 'transparencyScore')},
            'verdict': health['verdict'],
            'findings': sorted(item['rule'] for item in health['findings']),
        })
    return rows


def organization_cases():
    """Cover nonzero size, fragmentation and cycle penalties with explicit facts."""
    from atlas.code_organization import organization_profiles

    cases = {}
    for name, count, statements, cycle in (
            ('concentrated', 40, 30, False), ('fragments', 8, 1, False),
            ('cycle', 3, 5, True)):
        nodes = []
        for index in range(count):
            filename = 'main.py' if name == 'concentrated' else f'area{index}/part.py'
            nodes.append({'id': str(index), 'file': filename,
                          'complexity': {'status': 'complete', 'statementCount': statements,
                                         'branchingCount': 0}})
        edges = [{'source': str(index), 'target': str(index + 1), 'relation': 'calls'}
                 for index in range(count - 1)]
        if cycle:
            edges.append({'source': str(count - 1), 'target': '0', 'relation': 'calls'})
        dependencies = {node['id']: {'status': 'complete'} for node in nodes}
        profiles = organization_profiles(nodes, edges, dependencies)
        cases[name] = {filename: {'score': row['score'], 'metrics': row['metrics'],
                                 'findings': sorted(item['rule'] for item in row['findings'])}
                       for filename, row in profiles.items()}
        if any(row['score'] is None for row in profiles.values()):
            raise AssertionError('Explicit organization fixture became unknown')
    return cases


def main():
    """Write portable evidence; comparison is a separate strict JSON equality check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    rows = health_rows('Cases.cs', args.corpus.read_bytes())
    examples = {
        'python.py': b'def doubled(x):\n    """Return twice the input x."""\n    return x * 2\n',
        'javascript.js': b'function doubled(x) { return x * 2; }',
        'typescript.ts': b'function doubled(x: number): number { return x * 2; }',
        'java.java': b'class Amount { int doubled(int x) { return x * 2; } }',
        'go.go': b'package amount\nfunc Double(x int) int { return x * 2 }',
        'nested.cs': b'class Amount { int Run(int x) { if(x>0) { if(x>1) { if(x>2) { return 3; } } } return 0; } }',
    }
    for filename, content in examples.items():
        rows.extend(health_rows(filename, content))
    result = {'schema': 'atlas-analysis-parity-v1', 'corpusHash': digest(args.corpus),
              'harnessHash': digest(Path(__file__)),
              'sourceFingerprint': source_identity(args.source_root),
              'evidence': {'declarations': rows, 'organization': organization_cases()}}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'declarations': len(rows), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
