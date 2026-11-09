"""Opt-in CI comparison of exported health evidence; does not scan or execute a project."""

import argparse
import json
from pathlib import Path
from .code_health_comparison import compare_assessments


def main(argv=None) -> int:
    """Advisory by default; only explicit flags fail CI on REVIEW or UNKNOWN."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--fail-on-review', action='store_true')
    parser.add_argument('--fail-on-unknown', action='store_true')
    args = parser.parse_args(argv)
    try:
        def read(path):
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError('Health record exceeds 2 MiB')
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError('Health record must be an object')
            return value
        result = compare_assessments(read(args.before), read(args.after), plan=read(args.plan) if args.plan else None)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'verdict': 'UNKNOWN', 'error': str(error)}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result['verdict'] == 'REVIEW' and args.fail_on_review or result['verdict'] == 'UNKNOWN' and args.fail_on_unknown else 0


if __name__ == '__main__':
    raise SystemExit(main())
