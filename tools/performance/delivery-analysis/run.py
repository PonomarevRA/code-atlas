"""Изолированное сравнение синтетических комплектаций без запуска исходного ПО."""

import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from atlas.delivery_analysis import DeliveryAnalysisError, compare_search, prepare_problem
from atlas.delivery_analysis.model import MAX_INPUT_BYTES


def load_fixture(path):
    """Читает не более128KiB до JSON-разбора; logical IDs не открывают другие файлы."""
    with path.open('rb') as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise DeliveryAnalysisError('Fixture exceeds 128 KiB')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise DeliveryAnalysisError('Fixture is not bounded valid JSON') from error
    return prepare_problem(data)


def main():
    """Сохраняет каждую клетку абляции и проигрыш к точному oracle без отбора удач."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evaluations', type=int, default=64)
    parser.add_argument('--seeds', type=int, nargs='+', default=[7, 42, 91])
    args = parser.parse_args()
    if not 1 <= len(args.seeds) <= 10:
        parser.error('Select1..10seeds')
    fixtures = [args.input] if args.input else sorted((Path(__file__).parent / 'fixtures').glob('*.json'))
    cases = []
    try:
        for path in fixtures:
            problem = load_fixture(path)
            runs = []
            for seed in args.seeds:
                started = time.perf_counter()
                result = compare_search(problem, seed=seed, evaluation_budget=args.evaluations)
                result['elapsedSeconds'] = time.perf_counter() - started
                result['timingKind'] = 'wall-clock-perf-counter'
                result['timingScope'] = 'entire compare_search: exact oracles, baselines and all four search cells'
                runs.append(result)
            cases.append({'fixture': path.name, 'problem': problem.as_dict(),
                          'problemHash': problem.problem_hash, 'runs': runs})
    except DeliveryAnalysisError as error:
        parser.error(str(error))
    lost = []
    for case in cases:
        for run in case['runs']:
            for row in run['outcomes']:
                if row['gapToExact'] is not None and row['gapToExact'] > 0:
                    lost.append({'fixture': case['fixture'], 'seed': row['seed'],
                                 'condensed': row['condensed'], 'operator': row['operator'],
                                 'gapToExact': row['gapToExact']})
    report = {'version': 'atlas-delivery-experiment/v1', 'source': 'declared-synthetic-only',
              'python': platform.python_version(), 'networkx': importlib.metadata.version('networkx'),
              'cases': cases, 'lostCases': lost, 'paperReproduction': False,
              'limitations': ['TC-inspired full-completion is not the published COST/CONSISTENT operator.',
                              'Direction convention and strict fixed-point delivery are explicit model adaptations.',
                              'SCC/file encoding also changes the search space; no pure causal effect is established.',
                              'Exact oracle is offline and never used by genetic selection; its cost is outside the GA budget.',
                              'Timing is wall-clock duration of the entire compare_search, including exact oracles, baselines and all four search cells; not CPU time or per-operator timing.',
                              'Fixture loading and report serialization are outside that timing; native/UI/RSS and production performance remain unqualified.',
                              'No real requirements, configurations or source project code were executed.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'cases': len(cases), 'runs': sum(len(case['runs']) for case in cases), 'lostCases': len(lost)}))


if __name__ == '__main__':
    main()
