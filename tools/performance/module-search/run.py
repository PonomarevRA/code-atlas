"""
Offline comparison на frozen фактах: equal-budget GA/random/local и отдельные providers.
"""

import argparse
from pathlib import Path
import json
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from atlas.module_analysis import compare_partitions, project_modules
from atlas.module_analysis.models import digest
from atlas.module_search import search_partitions


def run_case(path, seeds, budget):
    """
    Сравнивает три search arms на одном immutable scope; timings providers не бесплатны.
    """
    fixture = json.loads(path.read_text(encoding='utf-8'))
    graph = project_modules(fixture['graph'], **fixture.get('projection', {}))
    current = [[unit['id']] for unit in graph['units']]
    observations = []
    for seed in seeds:
        providers = compare_partitions(graph, algorithms=['sarf-simple-directed', 'leiden'], seed=seed)
        baselines, provider_facts = {}, []
        for proposal in providers['proposals']:
            record = {key: proposal[key] for key in ('algorithm', 'status', 'elapsedSeconds')}
            if proposal['status'] == 'available':
                name = 'sarf' if proposal['algorithm'] == 'sarf-simple-directed' else 'leiden'
                baselines[name] = proposal['partition']
                record['preprocessing'] = proposal['preprocessing']
            else:
                record['reason'] = proposal['reason']
            provider_facts.append(record)
        results, times = [], []
        for _ in range(2):
            start = time.perf_counter()
            result = search_partitions(graph, current, baselines=baselines, seed=seed, evaluations_per_method=budget)
            times.append(time.perf_counter() - start)
            results.append(result)
        observations.append({'seed': seed, 'resultHash': digest(results[0]),
                             'deterministicRepeat': results[0] == results[1],
                             'elapsedSeconds': times, 'providers': provider_facts, 'result': results[0]})
    return {'fixture': path.name, 'fixtureHash': digest(fixture), 'source': fixture.get('source'),
            'projectionHash': graph['projectionHash'], 'unitCount': len(graph['units']),
            'sourceCoverage': graph['coverage'], 'observations': observations}


def main():
    """
    Пишет inspectable локальный отчёт, не запуская исследуемый код или модель.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget', type=int, default=64)
    parser.add_argument('--seeds', type=int, nargs='+', default=[0, 1, 7, 19, 42])
    parser.add_argument('--fixtures', type=Path, default=ROOT / 'tools/performance/module-analysis/fixtures')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.budget <= 256 or not 1 <= len(args.seeds) <= 20 or any(not 0 <= seed < 2 ** 32 for seed in args.seeds):
        parser.error('budget1..256; seeds1..20 unsigned integers')
    report = {'version': 'module-search-benchmark-v1', 'python': platform.python_version(),
              'cases': [run_case(path, args.seeds, args.budget) for path in sorted(args.fixtures.glob('*.json'))],
              'limitations': ['Finite small corpus, no functional module labels or consumer/default qualification.',
                              'GA/random/local share equal evaluation/attempt caps and current initialization.',
                              'Random categorical sampler is not uniform over all partitions.',
                              'SArF/Leiden upstream construction is separate, timed and NOT equal-budget search competition.',
                              'SArF/Leiden partitions are rescored on Atlas incident MQ/matching objectives; original preprocessing differs.',
                              'Captured actual Atlas fixtures are partial static facts, not complete call graphs or ground truth.',
                              'Wall times are local source measurements, not capacity/RSS/native/UI claims.']}
    if not report['cases']:
        parser.error('No fixtures selected')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    observations = [item for case in report['cases'] for item in case['observations']]
    print(json.dumps({'cases': len(report['cases']), 'observations': len(observations),
                      'deterministic': all(item['deterministicRepeat'] for item in observations)}))


if __name__ == '__main__':
    main()
