"""Java source field candidates preserve raw facts and stop at ambiguous language/runtime boundaries."""
from copy import deepcopy
import hashlib

import pytest

from atlas.java_test_calls import enrich_java_test_calls, ORIGIN
from atlas.member_graph import enrich_member_graph
from atlas.execution_path import build_execution_path


RULES = {'fingerprint': 'java-rules', 'testFrameworks': [{'id': 'junit5', 'language': 'java'}]}


def graph_fixture(tmp_path, *, body='apiSteps.go("x", 1);', parameters='', methods=None, field=None):
    """Capture real Java members using the existing language provider, without compiling or running them."""
    sources = {
        'Base.java': 'package p; import p.steps.ApiSteps; class Base { '
                     + (field or 'protected ApiSteps apiSteps = new ApiSteps();') + ' }',
        'Case.java': f'package p; class Case extends Base {{ void scenario({parameters}) {{ {body} }} }}',
        'steps/ApiSteps.java': 'package p.steps; public class ApiSteps { '
                              + (methods or 'public int go(String value, int code) { return code; }') + ' }',
    }
    nodes = []
    for name, source in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        nodes.append({'id': 'file:' + name, 'label': name, 'kind': 'file', 'file': name, 'line': 1})
    return enrich_member_graph({'nodes': nodes, 'edges': [], 'metadata': {'snapshotId': 'fixture'}}, tmp_path)


def bound_edges(graph):
    """Select only this additive source-binding stage, excluding upstream extractor facts."""
    return [edge for edge in graph['edges'] if edge.get('_origin') == ORIGIN]


def method(graph, name):
    """Choose one actual captured method identity for path assertions."""
    return next(node for node in graph['nodes'] if (node.get('memberFacts') or {}).get('name') == name)


def test_inherited_typed_field_opens_real_method_in_common_path(tmp_path):
    graph = graph_fixture(tmp_path)
    original = deepcopy(graph)
    result = enrich_java_test_calls(graph, tmp_path, RULES)
    edge, = bound_edges(result)
    assert graph == original
    assert result['nodes'] == graph['nodes']
    assert result['edges'][:len(graph['edges'])] == graph['edges']
    assert edge['source'] == method(graph, 'scenario')['id']
    assert edge['target'] == method(graph, 'go')['id']
    assert edge['confidence'] == 'INFERRED' and edge['evidence']['runtimeSelectionKnown'] is False
    assert edge['evidence']['bindingFile'] == 'Base.java' and edge['evidence']['receiverType'] == 'ApiSteps'
    assert enrich_java_test_calls(result, tmp_path, RULES) == result
    path = build_execution_path(tmp_path, result, edge['source'], 'fixture')
    assert edge['target'] in {step['nodeId'] for step in path['steps']}
    root = next(step for step in path['steps'] if step['nodeId'] == edge['source'])
    event = next(event for event in root['events'] if event.get('callee') == 'go')
    assert event['targetNodeIds'] == [edge['target']] and event['confidence'] == 'candidate'


@pytest.mark.parametrize('body,parameters', [
    ('apiSteps.go("x", 1);', 'p.steps.ApiSteps apiSteps'),
    ('p.steps.ApiSteps apiSteps = new p.steps.ApiSteps(); apiSteps.go("x", 1);', ''),
    ('Runnable later = () -> apiSteps.go("x", 1);', ''),
    ('class Local { void later() { apiSteps.go("x", 1); } }', ''),
])
def test_local_or_parameter_shadows_and_deferred_bodies_are_not_field_calls(tmp_path, body, parameters):
    graph = graph_fixture(tmp_path, body=body, parameters=parameters)
    assert bound_edges(enrich_java_test_calls(graph, tmp_path, RULES)) == []


@pytest.mark.parametrize('body', [
    'if (o instanceof p.steps.ApiSteps apiSteps) { apiSteps.go("x", 1); }',
    'if (o instanceof Row(p.steps.ApiSteps apiSteps)) { apiSteps.go("x", 1); }',
    'switch (o) { case p.steps.ApiSteps apiSteps -> apiSteps.go("x", 1); default -> {} }',
])
def test_java_pattern_variables_shadow_an_inherited_field_receiver(tmp_path, body):
    """Java 21 type/record patterns are local bindings, rather than references to the Base field."""
    graph = graph_fixture(tmp_path, body=body, parameters='Object o')
    assert bound_edges(enrich_java_test_calls(graph, tmp_path, RULES)) == []


def test_stale_callee_source_hash_prevents_mixed_revision_binding(tmp_path):
    """A stable caller/field does not make a separately changed callee safe to connect."""
    graph = graph_fixture(tmp_path)
    method(graph, 'go')['callableFacts']['sourceHash'] = 'f' * 64
    assert bound_edges(enrich_java_test_calls(graph, tmp_path, RULES)) == []


def test_same_arity_overloads_are_not_selected_by_name_or_argument_guess(tmp_path):
    graph = graph_fixture(tmp_path, methods='public int go(String value, int code) { return code; } '
                          'public int go(Object value, int code) { return code; }')
    assert bound_edges(enrich_java_test_calls(graph, tmp_path, RULES)) == []


@pytest.mark.parametrize('field', [
    'private ApiSteps apiSteps = new ApiSteps();',
    'protected Object apiSteps = new ApiSteps();',
])
def test_inaccessible_or_unknown_declared_type_does_not_bind_runtime_initializer(tmp_path, field):
    graph = graph_fixture(tmp_path, field=field)
    assert bound_edges(enrich_java_test_calls(graph, tmp_path, RULES)) == []


def test_interface_receiver_is_explicitly_outside_the_concrete_field_binder(tmp_path):
    graph = graph_fixture(tmp_path)
    path = tmp_path / 'steps/ApiSteps.java'
    path.write_text('package p.steps; public interface ApiSteps { int go(String value, int code); }')
    # Rebuild member facts for this fixture rather than mixing mismatched source hashes.
    files = [node for node in graph['nodes'] if node.get('kind') == 'file']
    graph = enrich_member_graph({'nodes': files, 'edges': [], 'metadata': {}}, tmp_path)
    assert bound_edges(enrich_java_test_calls(graph, tmp_path, RULES)) == []


def test_disabled_provider_removes_only_its_derived_edges(tmp_path):
    graph = graph_fixture(tmp_path)
    active = enrich_java_test_calls(graph, tmp_path, RULES)
    disabled = enrich_java_test_calls(active, tmp_path, {'testFrameworks': []})
    assert disabled['edges'] == graph['edges']
    assert disabled['metadata']['javaTestCalls']['status'] == 'disabled'


def test_failed_source_attempts_and_site_limits_are_visible(tmp_path, monkeypatch):
    from atlas import java_test_calls
    graph = graph_fixture(tmp_path)
    monkeypatch.setattr(java_test_calls, 'MAX_FILES', 1)
    result = enrich_java_test_calls(graph, tmp_path, RULES)
    coverage = result['metadata']['javaTestCalls']['coverage']
    assert coverage['attempts'] == 1 and coverage['limited'] and coverage['warnings']
    monkeypatch.setattr(java_test_calls, 'MAX_FILES', 400)
    monkeypatch.setattr(java_test_calls, 'MAX_CALLS', 0)
    result = enrich_java_test_calls(graph, tmp_path, RULES)
    assert bound_edges(result) == [] and result['metadata']['javaTestCalls']['coverage']['limited']


def test_stale_field_source_hash_prevents_mixed_revision_binding(tmp_path):
    graph = graph_fixture(tmp_path)
    field = next(node for node in graph['nodes'] if (node.get('fieldFacts') or {}).get('name') == 'apiSteps')
    field['fieldFacts']['sourceHash'] = 'f' * 64
    assert bound_edges(enrich_java_test_calls(graph, tmp_path, RULES)) == []


def test_private_target_in_same_file_is_not_public_just_because_source_path_matches(tmp_path):
    """Separate top-level classes do not gain private access merely by sharing a Java source file."""
    source = 'package p; class ApiSteps { private void go() {} } '
    source += 'class Case { ApiSteps apiSteps; void scenario() { apiSteps.go(); } }'
    (tmp_path / 'Case.java').write_text(source)
    graph = enrich_member_graph({'nodes': [{'id': 'file', 'kind': 'file', 'label': 'Case.java',
        'file': 'Case.java', 'line': 1}], 'edges': []}, tmp_path)
    assert bound_edges(enrich_java_test_calls(graph, tmp_path, RULES)) == []


def test_inherited_same_arity_overload_is_not_ignored(tmp_path):
    """Inherited overloads require type resolution too; the nearest declared class alone is insufficient."""
    graph = graph_fixture(tmp_path, body='apiSteps.go(null);',
        methods='public void go(String value) {}')
    target = tmp_path / 'steps/ApiSteps.java'
    target.write_text('package p.steps; class Parent { public void go(Integer value) {} } '
                     'public class ApiSteps extends Parent { public void go(String value) {} }')
    files = [node for node in graph['nodes'] if node.get('kind') == 'file']
    graph = enrich_member_graph({'nodes': files, 'edges': [], 'metadata': {}}, tmp_path)
    assert bound_edges(enrich_java_test_calls(graph, tmp_path, RULES)) == []


def test_stale_intermediate_superclass_does_not_create_a_new_inheritance_route(tmp_path):
    """Unchanged caller/field/callee cannot validate a separately changed superclass declaration."""
    graph = graph_fixture(tmp_path)
    (tmp_path / 'Case.java').write_text('package p; class Case extends Mid { void scenario() { apiSteps.go("x", 1); } }')
    mid = tmp_path / 'Mid.java'
    mid.write_text('package p; class Mid extends Base {}')
    files = [node for node in graph['nodes'] if node.get('kind') == 'file']
    files.append({'id': 'mid-file', 'kind': 'file', 'label': 'Mid.java', 'file': 'Mid.java', 'line': 1})
    graph = enrich_member_graph({'nodes': files, 'edges': [], 'metadata': {}}, tmp_path)
    raw = next(node for node in graph['nodes'] if node.get('kind') == 'class' and node.get('label') == 'Mid')
    raw['attributeFacts'] = {'sourceHash': hashlib.sha256(mid.read_bytes()).hexdigest()}
    mid.write_text(mid.read_text() + '\n// changed after annotation capture')
    assert bound_edges(enrich_java_test_calls(graph, tmp_path, RULES)) == []


def test_unresolved_target_superclass_cannot_hide_inherited_overloads(tmp_path):
    """A known target method does not prove uniqueness when its superclass is unavailable."""
    graph = graph_fixture(tmp_path)
    target = tmp_path / 'steps/ApiSteps.java'
    target.write_text('package p.steps; public class ApiSteps extends UnknownParent { '
                     'public int go(String value, int code) { return code; } }')
    files = [node for node in graph['nodes'] if node.get('kind') == 'file']
    graph = enrich_member_graph({'nodes': files, 'edges': [], 'metadata': {}}, tmp_path)
    assert bound_edges(enrich_java_test_calls(graph, tmp_path, RULES)) == []


@pytest.mark.parametrize('inherited', [False, True])
def test_nested_field_type_cannot_be_replaced_by_same_name_import(tmp_path, inherited):
    """A local/member type shadows imports; unsupported nested-type lookup must stay unresolved."""
    graph = graph_fixture(tmp_path)
    source = 'package p; import p.steps.ApiSteps; '
    nested = 'static class ApiSteps { public int go(String value, int code) { return code; } } '
    if inherited:
        source += 'class Parent { ' + nested + '} class Base extends Parent { protected ApiSteps apiSteps; }'
    else:
        source += 'class Base { ' + nested + ' protected ApiSteps apiSteps; }'
    (tmp_path / 'Base.java').write_text(source)
    files = [node for node in graph['nodes'] if node.get('kind') == 'file']
    graph = enrich_member_graph({'nodes': files, 'edges': [], 'metadata': {}}, tmp_path)
    assert bound_edges(enrich_java_test_calls(graph, tmp_path, RULES)) == []


def test_failed_parser_consumes_reserved_ast_budget(tmp_path, monkeypatch):
    """An invalid source may fail after parsing; repeated failures cannot bypass the AST limit."""
    from atlas import java_test_calls
    graph = graph_fixture(tmp_path)
    attempts = []

    def failed_parser(content, filename, names, budget):
        """Simulate a declaration decoding failure after the parser consumed its allowance."""
        attempts.append(filename)
        raise UnicodeError('invalid declaration')

    monkeypatch.setattr(java_test_calls, 'MAX_AST_NODES', 100)
    monkeypatch.setattr(java_test_calls, 'parse_java', failed_parser)
    result = enrich_java_test_calls(graph, tmp_path, RULES)
    assert len(attempts) == 1
    assert result['metadata']['javaTestCalls']['coverage']['astNodes'] == 100
    assert result['metadata']['javaTestCalls']['status'] == 'partial'
