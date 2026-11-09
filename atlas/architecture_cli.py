"""Headless CLI архитектурных проверок, использующий те же facts/engine, что HTTP и MCP."""
import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4
from pydantic import ValidationError
from .architecture_rules import Rules
from .architecture_facts import capture
from .architecture_engine import evaluate
from .architecture_baseline import Baseline, create_baseline, compare_baseline

MAX_JSON_BYTES = 1024 * 1024


def read_json(path: Path):
    """Читает ограниченный JSON без исполнения проекта или baseline."""
    with path.open('rb') as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError('JSON input exceeds 1 MiB')
    return json.loads(raw)


def exit_code(result: dict, unknown_policy: str = 'fail') -> int:
    """0 допускается с явно принятым existing baseline; статусы результата не меняются."""
    comparison = result.get('baseline')
    new_violation = bool(comparison['new']) if comparison else bool(result['counts']['violated'])
    # Усечённый список не может доказать отсутствие новых нарушений для CI.
    incomplete = (result['counts']['unknown'] or result['findingsTruncated']
                  or any(item['coverage'] != 'complete' for item in result['rules']))
    if new_violation:
        return 1
    if incomplete and unknown_policy == 'fail':
        return 2
    return 0


def write_baseline(path: Path, baseline: Baseline, replace=False):
    """Записывает атомарно; существующий baseline требует отдельный --replace."""
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            stream.write(baseline.model_dump_json(indent=2) + '\n')
        if replace:
            os.replace(temporary, path)
        else:
            # link не перезаписывает существующий путь даже при конкурентном создании.
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def text_report(result: dict) -> str:
    """Краткий отчёт сохраняет unknown/coverage и позиции доказательств."""
    lines = [f"Architecture: {result['status']}", f"Sources: {result['sourceFingerprint']}"]
    for rule in result['rules']:
        lines.append(f"{rule['ruleId']}: {rule['status']} (coverage={rule['coverage']}, findings={rule['findingCount']})")
        lines.extend('  ? ' + reason for reason in rule['reasons'][:10])
        if rule['reasonsTruncated'] or len(rule['reasons']) > 10:
            lines.append('  ? more reasons omitted; narrow scope')
    for finding in result['findings']:
        lines.append(f"  {finding['ruleId']} {finding['kind']} {finding['id']}")
        for edge in finding['path'][:20]:
            lines.append(f"    {edge['source']} -> {edge['target']} [{edge['relation']}] {edge['file']}:{edge.get('line') or 1}")
        if len(finding['path']) > 20:
            lines.append('    ... path truncated in text; use --format json')
    if result.get('baseline'):
        comparison = result['baseline']
        lines.append('Baseline: ' + ', '.join(f'{key}={len(comparison[key])}' for key in ('new', 'existing', 'resolved', 'unconfirmed')))
    if result['findingsTruncated']:
        lines.append('Finding list truncated; absence of new findings is not proven')
    if result.get('baselineWritten'):
        lines.append('Baseline written: ' + result['baselineWritten'])
    if 'exitCode' in result:
        lines.append(f"Exit code: {result['exitCode']}")
    return '\n'.join(lines)


def parser():
    """Отделяет проверку CI от явного принятия нового baseline."""
    root = argparse.ArgumentParser(description='Static architecture checks; no target code execution')
    commands = root.add_subparsers(dest='command', required=True)
    for command in ('check', 'baseline'):
        sub = commands.add_parser(command)
        sub.add_argument('--root', type=Path, required=True)
        sub.add_argument('--rules', type=Path, required=True)
        sub.add_argument('--format', choices=['text', 'json'], default='text')
        if command == 'check':
            sub.add_argument('--baseline', type=Path)
            sub.add_argument('--unknown', choices=['fail', 'warn'], default='fail')
        else:
            sub.add_argument('--write', type=Path, required=True)
            sub.add_argument('--replace', action='store_true')
    return root


def main(argv=None) -> int:
    """Возвращает0/1/2/3; ошибки конфигурации не выглядят результатом анализа."""
    args = parser().parse_args(argv)
    try:
        if not args.root.is_dir():
            raise ValueError('Root directory does not exist')
        rules = Rules.model_validate(read_json(args.rules))
        facts = capture(args.root, {'nodes': [], 'edges': []}, 'source-only', rules)
        result = evaluate(rules, facts)
        if args.command == 'baseline':
            baseline = create_baseline(result)
            write_baseline(args.write, baseline, args.replace)
            result['baselineWritten'] = str(args.write)
            code = 0
        else:
            if args.baseline:
                baseline = Baseline.model_validate(read_json(args.baseline))
                result['baseline'] = compare_baseline(result, baseline)
            code = exit_code(result, args.unknown)
        result['exitCode'] = code
        print(json.dumps(result, ensure_ascii=False) if args.format == 'json' else text_report(result))
        return code
    except (OSError, ValueError, ValidationError) as error:
        if args.format == 'json':
            print(json.dumps({'status': 'error', 'exitCode': 3, 'error': str(error)}, ensure_ascii=False))
        else:
            print('Architecture error: ' + str(error), file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
