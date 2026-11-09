"""Проверки архитектуры не выдают неполноту за успех."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from atlas.architecture_rules import Rules
from atlas.architecture_facts import capture
from atlas.architecture_engine import evaluate, rules_identity
from atlas.architecture_cycles import cycle_witnesses


def config(kind='forbidden', relations=None):
    """Две непересекающиеся области и одно направленное правило."""
    rule = {'id': 'boundary', 'kind': kind, 'layers': ['a'], 'relations': relations or ['python-import']}
    if kind == 'forbidden':
        rule['target'] = ['b']
    else:
        rule['layers'] = ['a', 'b']
    return Rules.model_validate({'version': 'atlas-architecture-v1', 'pythonRoot': '.', 'layers': [
        {'id': 'a', 'paths': ['a/*.py']}, {'id': 'b', 'paths': ['b/*.py']}], 'rules': [rule]})


def fixture(root, cycle=False):
    """Реальные исходники с одной статической связью и необязательным обратным ребром."""
    (root / 'a').mkdir(); (root / 'b').mkdir()
    (root / 'a/main.py').write_text('from b.service import work\n')
    (root / 'b/service.py').write_text('import a.main\n' if cycle else 'def work(): pass\n')


def run(root, rules):
    """Те же capture/evaluate, которые вызывают сервис и CLI."""
    return evaluate(rules, capture(root, {'nodes': [], 'edges': []}, 'snapshot', rules))


def test_forbidden_keeps_source_proof_and_stable_id(tmp_path):
    fixture(tmp_path)
    before = run(tmp_path, config())
    assert before['status'] == 'violated'
    edge = before['findings'][0]['path'][0]
    assert (edge['source'], edge['target'], edge['line']) == ('a/main.py', 'b/service.py', 1)
    assert len(edge['sourceHash']) == 64
    (tmp_path / 'a/main.py').write_text('\n\nfrom b.service import work\n')
    after = run(tmp_path, config())
    assert before['findings'][0]['id'] == after['findings'][0]['id']
    assert before['sourceFingerprint'] != after['sourceFingerprint']
    assert after['findings'][0]['path'][0]['line'] == 3


def test_absence_only_passes_complete_explicit_scope(tmp_path):
    fixture(tmp_path)
    (tmp_path / 'a/main.py').write_text('import os\n')
    assert run(tmp_path, config())['status'] == 'passed'
    (tmp_path / 'a/main.py').write_text('invalid ! python')
    assert run(tmp_path, config())['status'] == 'unknown'
    assert run(tmp_path, config(relations=['code-dependency']))['status'] == 'unknown'


def test_known_violation_survives_unrelated_parse_failure(tmp_path):
    fixture(tmp_path)
    (tmp_path / 'a/broken.py').write_text('invalid ! python')
    result = run(tmp_path, config())
    assert result['status'] == 'violated'
    assert result['rules'][0]['coverage'] == 'unknown'


def test_cycle_has_closed_directed_witness(tmp_path):
    fixture(tmp_path, cycle=True)
    result = run(tmp_path, config('cycles'))
    assert result['status'] == 'violated'
    path = result['findings'][0]['path']
    assert path[0]['source'] == path[-1]['target']
    assert all(left['target'] == right['source'] for left, right in zip(path, path[1:]))
    (tmp_path / 'b/service.py').write_text('pass\n')
    assert run(tmp_path, config('cycles'))['status'] == 'passed'


def test_ambiguous_and_empty_selectors_do_not_pass(tmp_path):
    fixture(tmp_path)
    rules = config(); rules.layers[1].paths = ['a/*.py', 'b/*.py']
    assert run(tmp_path, rules)['status'] == 'unknown'
    rules.layers[1].paths = ['missing/*.py']
    assert run(tmp_path, rules)['status'] == 'unknown'


def test_inventory_and_byte_limits_are_unknown(tmp_path, monkeypatch):
    import atlas.architecture_facts as module
    fixture(tmp_path)
    monkeypatch.setattr(module, 'MAX_FILES', 1)
    assert run(tmp_path, config())['status'] == 'unknown'
    monkeypatch.setattr(module, 'MAX_FILES', 20000)
    monkeypatch.setattr(module, 'MAX_BYTES', 1)
    assert run(tmp_path, config())['status'] == 'unknown'


def test_source_race_never_keeps_stale_proof(tmp_path, monkeypatch):
    import atlas.architecture_facts as module
    fixture(tmp_path)
    original = module.read_node_source
    calls = {}
    def changed(root, node, snapshot, **kwargs):
        calls[node['file']] = calls.get(node['file'], 0) + 1
        if node['file'] == 'a/main.py' and calls[node['file']] == 2:
            (root / node['file']).write_text('pass\n')
        return original(root, node, snapshot, **kwargs)
    monkeypatch.setattr(module, 'read_node_source', changed)
    result = run(tmp_path, config())
    assert result['status'] == 'unknown' and not result['findings']


def test_inferred_edges_and_missing_hash_cannot_prove_violation(tmp_path):
    fixture(tmp_path)
    facts = capture(tmp_path, {'nodes': [], 'edges': []}, 's', config())
    facts['edges'][0]['confidence'] = 'inferred'
    assert evaluate(config(), facts)['status'] == 'unknown'
    facts['edges'][0].pop('sourceHash'); facts['edges'][0]['confidence'] = 'declared'
    assert evaluate(config(), facts)['status'] == 'unknown'


def test_deep_cycles_avoid_recursion_and_order_is_deterministic():
    edges = [{'source': str(i), 'target': str(i + 1)} for i in range(10000)]
    assert cycle_witnesses(edges) == []
    edges.append({'source': '10000', 'target': '0'})
    first = cycle_witnesses(edges)
    assert len(first) == 1 and len(first[0]) == 10001
    assert first == cycle_witnesses(list(reversed(edges)))


def test_versions_and_unknown_fields_are_rejected():
    value = config().model_dump()
    for mutate in (lambda v: v.update(version='v2'), lambda v: v.update(execute='evil'),
                   lambda v: v['layers'][0].update(paths=['../outside']),
                   lambda v: v['rules'][0].update(relations=['owns']),
                   lambda v: v['rules'][0].update(target=['missing'])):
        candidate = deepcopy(value); mutate(candidate)
        with pytest.raises(ValidationError):
            Rules.model_validate(candidate)
    reordered = config(); reordered.layers.reverse()
    assert rules_identity(config()) == rules_identity(reordered)


def test_nested_relative_import_and_package_prefix(tmp_path):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'a/__init__.py').write_text('')
    (tmp_path / 'a/main.py').write_text('def f():\n from . import other\n')
    (tmp_path / 'a/other.py').write_text('pass\n')
    rules = config()
    rules.layers[0].paths = ['a/main.py', 'a/__init__.py']
    rules.layers[1].paths = ['a/other.py']
    assert run(tmp_path, rules)['status'] == 'violated'
    (tmp_path / 'a/main.py').write_text('from ..a import other\n')
    assert run(tmp_path, rules)['status'] == 'unknown'


def test_external_symlink_is_never_read(tmp_path):
    fixture(tmp_path)
    (tmp_path / 'a/link.py').symlink_to('/etc/passwd')
    result = run(tmp_path, config())
    assert result['rules'][0]['coverage'] == 'unknown'
    assert 'root:' not in str(result)


def test_explicit_python_root_resolves_src_layout(tmp_path):
    root = tmp_path / 'src'; root.mkdir()
    fixture(root)
    rules = config()
    rules.pythonRoot = 'src'
    rules.layers[0].paths = ['src/a/*.py']; rules.layers[1].paths = ['src/b/*.py']
    assert run(tmp_path, rules)['status'] == 'violated'


def test_module_collision_is_unknown(tmp_path):
    fixture(tmp_path)
    (tmp_path / 'b/service').mkdir()
    (tmp_path / 'b/service/__init__.py').write_text('')
    assert run(tmp_path, config())['status'] == 'unknown'


def test_analyzer_mismatch_is_unknown(tmp_path):
    fixture(tmp_path)
    facts = capture(tmp_path, {'nodes': [], 'edges': []}, 's', config())
    facts['analyzerVersion'] = 'legacy'
    assert evaluate(config(), facts)['status'] == 'unknown'


def test_manifest_references_are_declarations_not_runtime(tmp_path):
    (tmp_path / 'a').mkdir(); (tmp_path / 'b').mkdir()
    (tmp_path / 'a/A.csproj').write_text('<Project><ProjectReference Include="../b/B.csproj" /></Project>')
    (tmp_path / 'b/B.csproj').write_text('<Project/>')
    rules = config(relations=['project-reference'])
    rules.layers[0].paths = ['a/*.csproj']; rules.layers[1].paths = ['b/*.csproj']
    result = run(tmp_path, rules)
    assert result['status'] == 'violated'
    assert result['findings'][0]['path'][0]['target'] == 'b/B.csproj'
    (tmp_path / 'a/A.csproj').write_text('<Project><ProjectReference Include="$(Other)" /></Project>')
    assert run(tmp_path, rules)['status'] == 'unknown'
    (tmp_path / 'a/A.csproj').write_text('<Project/>')
    assert run(tmp_path, rules)['status'] == 'passed'


def test_cycles_in_multiple_components_and_self_loop():
    edges = [{'source': a, 'target': b} for a, b in [('a','b'), ('b','a'), ('c','c'), ('d','e')]]
    paths = cycle_witnesses(edges)
    assert len(paths) == 2
    assert all(path[0]['source'] == path[-1]['target'] for path in paths)


def test_findings_limit_retains_violated_status(tmp_path, monkeypatch):
    import atlas.architecture_engine as module
    fixture(tmp_path)
    monkeypatch.setattr(module, 'MAX_FINDINGS', 0)
    result = run(tmp_path, config())
    assert result['status'] == 'violated' and not result['findings']
    assert result['findingsTruncated']


def test_validated_snapshot_target_race_and_pure_target_hash(tmp_path, monkeypatch):
    import hashlib
    import atlas.architecture_facts as module
    fixture(tmp_path)
    rules = config(relations=['code-dependency'])
    nodes = [{'id': file, 'file': file, 'sourceByteHash': hashlib.sha256((tmp_path / file).read_bytes()).hexdigest()}
             for file in ['a/main.py', 'b/service.py']]
    graph = {'nodes': nodes, 'edges': [{'source': nodes[0]['id'], 'target': nodes[1]['id'],
                                      'relation': 'calls', 'confidence': 'resolved'}]}
    facts = capture(tmp_path, graph, 's', rules)
    assert evaluate(rules, facts)['status'] == 'violated'
    facts['edges'][0]['targetHash'] = 'wrong'
    assert evaluate(rules, facts)['status'] == 'unknown'
    original = module.read_node_source
    calls = {}
    def change_target(root, node, snapshot, **kwargs):
        calls[node['file']] = calls.get(node['file'], 0) + 1
        if node['file'] == 'b/service.py' and calls[node['file']] == 2:
            (root / node['file']).write_text('changed = True\n')
        return original(root, node, snapshot, **kwargs)
    monkeypatch.setattr(module, 'read_node_source', change_target)
    facts = capture(tmp_path, graph, 's', rules)
    assert 'b/service.py' in facts['errors']
    assert evaluate(rules, facts)['status'] == 'unknown'


def test_python_root_is_required_for_python_contract():
    value = config().model_dump(); value.pop('pythonRoot')
    with pytest.raises(ValidationError):
        Rules.model_validate(value)


def test_package_collision_does_not_prove_descendant_binding(tmp_path):
    fixture(tmp_path)
    (tmp_path / 'b.py').write_text('')
    (tmp_path / 'b/__init__.py').write_text('')
    result = run(tmp_path, config())
    assert result['status'] == 'unknown' and not result['findings']


def test_root_init_relative_import_has_no_package_context(tmp_path):
    (tmp_path / '__init__.py').write_text('from . import b\n')
    (tmp_path / 'b.py').write_text('pass\n')
    rules = config()
    rules.layers[0].paths = ['__init__.py']; rules.layers[1].paths = ['b.py']
    result = run(tmp_path, rules)
    assert result['status'] == 'unknown' and not result['findings']


def test_cycle_identity_covers_internal_topology_not_only_witness(tmp_path):
    for file, text in {'a.py':'import b\n','b.py':'import a\n','c.py':'pass\n'}.items():
        (tmp_path / file).write_text(text)
    rules = Rules.model_validate({'version':'atlas-architecture-v1','pythonRoot':'.',
                                 'layers':[{'id':'all','paths':['*.py']}],
                                 'rules':[{'id':'cycles','kind':'cycles','layers':['all'],'relations':['python-import']}]})
    before = run(tmp_path, rules)['findings'][0]
    (tmp_path / 'b.py').write_text('import a\nimport c\n')
    (tmp_path / 'c.py').write_text('import b\n')
    after = run(tmp_path, rules)['findings'][0]
    topology = lambda finding: [(edge['source'], edge['target'], edge['relation']) for edge in finding['path']]
    assert topology(before) == topology(after)
    assert before['id'] != after['id']


def test_explicit_exclusions_bound_inventory_and_are_scope_identity(tmp_path, monkeypatch):
    import atlas.architecture_facts as module
    fixture(tmp_path)
    (tmp_path / 'generated').mkdir()
    for index in range(10): (tmp_path / f'generated/{index}.json').write_text('{}')
    monkeypatch.setattr(module, 'MAX_FILES', 5)
    rules = config()
    before = capture(tmp_path, {'nodes': [], 'edges': []}, 's', rules)
    assert 'inventory-limit' in before['inventoryReasons']
    original_identity = rules_identity(rules)
    rules.exclude = ['generated']
    facts = capture(tmp_path, {'nodes': [], 'edges': []}, 's', rules)
    assert facts['inventoryReasons'] == []
    assert evaluate(rules, facts)['status'] == 'violated'
    assert rules_identity(rules) != original_identity
    (tmp_path / 'a/main.py').write_text('pass\n')
    assert run(tmp_path, rules)['status'] == 'passed'
    for unsafe in ['../outside', '/outside', 'generated/*', '.', 'a//b']:
        value = rules.model_dump(); value['exclude'] = [unsafe]
        with pytest.raises(ValidationError): Rules.model_validate(value)
