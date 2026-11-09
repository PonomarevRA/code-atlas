"""Baseline/CI не принимают новые нарушения автоматически и не маскируют unknown."""
import json
from pathlib import Path
import subprocess
import sys
import pytest
from atlas.architecture_baseline import Baseline, create_baseline, compare_baseline
from atlas.architecture_cli import exit_code
from test_architecture_engine import config, fixture, run


def test_line_shift_existing_new_resolved_and_unconfirmed(tmp_path):
    fixture(tmp_path)
    rules = config()
    baseline = create_baseline(run(tmp_path, rules))
    (tmp_path / 'a/main.py').write_text('\n\nfrom b.service import work\n')
    result = run(tmp_path, rules)
    comparison = compare_baseline(result, baseline)
    assert len(comparison['existing']) == 1 and not comparison['new']
    result['baseline'] = comparison
    assert result['status'] == 'violated' and exit_code(result) == 0
    (tmp_path / 'a/second.py').write_text('import b.service\n')
    result = run(tmp_path, rules); result['baseline'] = compare_baseline(result, baseline)
    assert len(result['baseline']['new']) == 1 and exit_code(result) == 1
    (tmp_path / 'a/main.py').write_text('pass\n'); (tmp_path / 'a/second.py').write_text('pass\n')
    result = run(tmp_path, rules)
    assert len(compare_baseline(result, baseline)['resolved']) == 1
    (tmp_path / 'a/second.py').write_text('invalid ! python')
    result = run(tmp_path, rules); result['baseline'] = compare_baseline(result, baseline)
    assert len(result['baseline']['unconfirmed']) == 1 and not result['baseline']['resolved']
    assert exit_code(result) == 2 and exit_code(result, 'warn') == 0
    assert result['status'] == 'unknown'


def test_baseline_cannot_accept_unknown_or_incompatible_config(tmp_path):
    fixture(tmp_path); rules = config()
    baseline = create_baseline(run(tmp_path, rules))
    rules.rules[0].id = 'changed'
    with pytest.raises(ValueError, match='incompatible'):
        compare_baseline(run(tmp_path, rules), baseline)
    (tmp_path / 'a/main.py').write_text('invalid ! python')
    with pytest.raises(ValueError, match='complete'):
        create_baseline(run(tmp_path, rules))
    invalid = baseline.model_dump(); invalid['findings'] *= 2
    with pytest.raises(ValueError, match='Duplicate'):
        Baseline.model_validate(invalid)


def invoke(root, rules, *args):
    """Настоящий headless CLI с отдельным процессом и стандартными stdout/exit codes."""
    return subprocess.run([sys.executable, '-m', 'atlas.architecture_cli', *args, '--root', str(root),
                           '--rules', str(rules), '--format', 'json'], capture_output=True, text=True, timeout=20)


def test_real_cli_exit_codes_and_explicit_baseline(tmp_path):
    root = tmp_path / 'repo'; root.mkdir(); fixture(root)
    rules = tmp_path / 'rules.json'; rules.write_text(config().model_dump_json())
    path = tmp_path / 'baseline.json'
    violation = invoke(root, rules, 'check')
    assert violation.returncode == 1 and json.loads(violation.stdout)['status'] == 'violated'
    first = invoke(root, rules, 'baseline', '--write', str(path))
    assert first.returncode == 0 and path.exists()
    original = path.read_bytes()
    assert invoke(root, rules, 'baseline', '--write', str(path)).returncode == 3
    assert path.read_bytes() == original
    result = invoke(root, rules, 'check', '--baseline', str(path))
    assert result.returncode == 0 and len(json.loads(result.stdout)['baseline']['existing']) == 1
    (root / 'a/main.py').write_text('invalid ! python')
    assert invoke(root, rules, 'check').returncode == 2
    assert invoke(root, rules, 'check', '--unknown', 'warn').returncode == 0
    assert invoke(root, rules, 'baseline', '--write', str(path), '--replace').returncode == 3
    assert path.read_bytes() == original
    rules.write_text('{invalid')
    assert invoke(root, rules, 'check').returncode == 3


def test_http_and_cli_report_same_finding_and_source_fingerprint(tmp_path):
    from atlas.architecture_service import ArchitectureChecks
    from test_architecture_api import Store
    root = tmp_path / 'repo'; root.mkdir(); fixture(root)
    rules = tmp_path / 'rules.json'; rules.write_text(config().model_dump_json())
    service = ArchitectureChecks(Store(root))
    page = service.run('p', 's', config())
    finding = service.page('p', page['runId'], 'findings')['items'][0]
    cli = json.loads(invoke(root, rules, 'check').stdout)
    assert cli['findings'][0]['id'] == finding['id']
    assert cli['sourceFingerprint'] == page['sourceFingerprint']


def test_shipped_atlas_rules_have_coverage_and_enforce_actual_boundaries():
    from atlas.architecture_rules import Rules
    root = Path(__file__).resolve().parents[1]
    rules = Rules.model_validate_json((root / 'architecture-rules.json').read_text())
    result = run(root, rules)
    assert result['status'] == 'passed'
    assert result['counts']['passed'] == 4
    assert all(rule['selectedFiles'] > 0 and rule['coverage'] == 'complete' for rule in result['rules'])


def test_existing_violation_with_partial_coverage_still_fails_ci(tmp_path):
    fixture(tmp_path)
    rules = config()
    baseline = create_baseline(run(tmp_path, rules))
    (tmp_path / 'a/broken.py').write_text('invalid ! python')
    result = run(tmp_path, rules); result['baseline'] = compare_baseline(result, baseline)
    assert result['status'] == 'violated' and result['rules'][0]['coverage'] == 'unknown'
    assert result['baseline']['existing'] and exit_code(result) == 2
    result['findingsTruncated'] = True
    assert exit_code(result) == 2
    with pytest.raises(ValueError): create_baseline(result)


def test_cycle_id_survives_line_changes(tmp_path):
    fixture(tmp_path, cycle=True)
    rules = config('cycles')
    baseline = create_baseline(run(tmp_path, rules))
    (tmp_path / 'b/service.py').write_text('\n\nimport a.main\n')
    comparison = compare_baseline(run(tmp_path, rules), baseline)
    assert len(comparison['existing']) == 1 and not comparison['new']


def cycle_fixture(root):
    """Один путь a↔b, плюс файл для расширения той же SCC."""
    for file, text in {'a.py':'import b\n','b.py':'import a\n','c.py':'pass\n'}.items():
        (root / file).write_text(text)
    from atlas.architecture_rules import Rules
    return Rules.model_validate({'version':'atlas-architecture-v1','pythonRoot':'.',
                                 'layers':[{'id':'all','paths':['*.py']}],
                                 'rules':[{'id':'cycles','kind':'cycles','layers':['all'],'relations':['python-import']}]})


def test_added_cycle_inside_existing_scc_fails_baseline(tmp_path):
    rules = cycle_fixture(tmp_path)
    baseline = create_baseline(run(tmp_path, rules))
    (tmp_path / 'b.py').write_text('import a\nimport c\n')
    (tmp_path / 'c.py').write_text('import b\n')
    result = run(tmp_path, rules); result['baseline'] = compare_baseline(result, baseline)
    assert len(result['baseline']['new']) == 1 and exit_code(result) == 1
    assert result['baseline']['unconfirmed'] and not result['baseline']['resolved']


def test_different_witness_does_not_resolve_existing_cycle(tmp_path):
    rules = cycle_fixture(tmp_path)
    baseline = create_baseline(run(tmp_path, rules))
    (tmp_path / 'aa.py').write_text('import a\n')
    (tmp_path / 'a.py').write_text('import b\nimport aa\n')
    result = run(tmp_path, rules)
    comparison = compare_baseline(result, baseline)
    assert comparison['new'] and comparison['unconfirmed'] and not comparison['resolved']
    (tmp_path / 'a.py').write_text('pass\n')
    (tmp_path / 'aa.py').write_text('pass\n')
    (tmp_path / 'b.py').write_text('pass\n')
    comparison = compare_baseline(run(tmp_path, rules), baseline)
    assert comparison['resolved'] and not comparison['unconfirmed']


def test_exclusions_change_baseline_compatibility(tmp_path):
    fixture(tmp_path)
    rules = config()
    baseline = create_baseline(run(tmp_path, rules))
    rules.exclude = ['generated']
    with pytest.raises(ValueError, match='incompatible'):
        compare_baseline(run(tmp_path, rules), baseline)
