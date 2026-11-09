"""Сравнивает frozen module fixtures; не исполняет код анализируемого проекта."""

import argparse
from pathlib import Path
import json
import platform
import statistics
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from atlas.module_analysis import compare_partitions, project_modules
from atlas.module_analysis.models import digest


def benchmark_fixture(filename, repeats):
    """
    Измеряет отдельные uncached вызовы на одном frozen входе и peak Python memory.

    Замеры Python allocations не равны RSS, повторные вызовы не являются
    доказательством эффекта кэша или интерактивной скорости приложения.
    """
    fixture = json.loads(filename.read_text(encoding='utf-8'))
    observations, elapsed, peaks = [], [], []
    for _ in range(repeats):
        tracemalloc.start()
        start = time.perf_counter()
        projection = project_modules(fixture['graph'], **fixture.get('projection', {}))
        result = compare_partitions(projection, algorithms=['current', 'louvain', 'leiden', 'sarf-simple-directed'])
        elapsed.append(time.perf_counter() - start)
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
        stable = []
        for item in result['proposals']:
            stable.append({key: value for key, value in item.items() if key != 'elapsedSeconds'})
        observations.append(digest(stable))
    return {'fixture': filename.name, 'fixtureHash': digest(fixture), 'source': fixture.get('source'),
            'projectionHash': projection['projectionHash'], 'coverage': projection['coverage'],
            'unitCount': len(projection['units']), 'repeats': repeats,
            'deterministicAcrossRepeats': len(set(observations)) == 1,
            'elapsedSeconds': {'first': elapsed[0], 'median': statistics.median(elapsed),
                               'min': min(elapsed), 'max': max(elapsed)},
            'peakPythonBytes': {'median': statistics.median(peaks), 'max': max(peaks)},
            'environment': result['environment'], 'proposals': result['proposals']}


def main():
    """Пишет локальный inspectable JSON-отчёт с hash/version, не меняя fixtures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error('--repeats must be between 1 and 20')
    directory = Path(__file__).parent / 'fixtures'
    report = {'version': 'module-analysis-benchmark-v1', 'python': platform.python_version(),
              'cases': [benchmark_fixture(path, args.repeats) for path in sorted(directory.glob('*.json'))],
              'limitations': ['Small frozen corpus; not a capacity or runtime UI qualification.',
                              'Timing is wall-clock perf_counter duration of projection/assessment/comparison, including Python tracing overhead; not CPU time.',
                              'No cache is exercised; first and subsequent calls are separately reported.',
                              'Peak Python allocations do not measure native allocations or process RSS.',
                              'SArF and Leiden objectives/preprocessing differ and are reported per proposal.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'cases': len(report['cases']), 'deterministic': all(case['deterministicAcrossRepeats'] for case in report['cases'])}))


if __name__ == '__main__':
    main()
