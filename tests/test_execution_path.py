"""Статический путь следует вызовам и точным методам, сохраняя неопределённость и лимиты."""
import json
from pathlib import Path
import pytest

from atlas.execution_path import build_execution_path, ExecutionPathError


@pytest.fixture
def method_project(tmp_path):
    """Создаёт исполняемо выглядящий исходник, который анализатор обязан только прочитать."""
    project = tmp_path / 'project'
    project.mkdir()
    text = '''namespace Demo;
public class Service
{
    public void Start()
    {
        // Comment FakeCall() must stay a comment.
        logger.LogInformation("begin FakeCall()");
        using var span = tracing.StartActivity("start");
        if (ready) Next();
        var text = @"Pretend(); logger.LogError(""string-only"")";
    }
    public void Neighbor() { logger.LogError("ADJACENT_METHOD_SECRET"); }
    public void Next()
    {
        Start();
    }
}
'''
    (project / 'Service.cs').write_text(text)
    graph = {'nodes': [
        {'id': 'start', 'label': 'Start', 'kind': 'method', 'file': 'Service.cs', 'line': 4},
        {'id': 'next', 'label': 'Next', 'kind': 'method', 'file': 'Service.cs', 'line': 13},
        {'id': 'neighbor', 'label': 'Neighbor', 'kind': 'method', 'file': 'Service.cs', 'line': 12},
        {'id': 'service', 'label': 'Service', 'kind': 'class', 'file': 'Service.cs', 'line': 2},
    ], 'edges': [
        {'source': 'start', 'target': 'next', 'relation': 'calls'},
        {'source': 'next', 'target': 'start', 'relation': 'calls'},
        {'source': 'start', 'target': 'neighbor', 'relation': 'references'},
        {'source': 'service', 'target': 'start', 'relation': 'method'},
        {'source': 'service', 'target': 'neighbor', 'relation': 'method'},
    ], 'metadata': {}}
    return project, graph, text


def test_execution_path_exact_method_events_and_directed_calls(method_project):
    """В соседний метод не входит; строки/комментарии не становятся вызовами."""
    project, graph, text = method_project
    before = json.dumps(graph, sort_keys=True)
    result = build_execution_path(project, graph, 'start', 's')
    assert result['snapshotId'] == 's'
    assert result['rootNodeId'] == 'start'
    steps = {step['nodeId']: step for step in result['steps']}
    assert set(steps) == {'start', 'next'}
    assert steps['start']['sourceStatus'] == 'parsed'
    assert steps['start']['line'] == 4
    assert steps['start']['endLine'] == 11
    events = steps['start']['events']
    assert [(event['line'], event['column']) for event in events] == sorted((event['line'], event['column']) for event in events)
    assert any(event['kind'] == 'comment' and 'FakeCall' in event['text'] for event in events)
    assert any(event['kind'] == 'log' for event in events)
    assert any(event['kind'] == 'span' for event in events)
    calls = [event for event in events if event['kind'] == 'call']
    assert any('next' in event['targetNodeIds'] and event['conditional'] for event in calls)
    assert not any(event.get('callee') in {'FakeCall', 'Pretend', 'LogError'} for event in calls)
    assert 'ADJACENT_METHOD_SECRET' not in json.dumps(result)
    assert {(edge['source'], edge['target']) for edge in result['edges']} == {('start', 'next'), ('next', 'start')}
    assert result['cycles']
    assert result['warning']
    assert (project / 'Service.cs').read_text() == text
    assert json.dumps(graph, sort_keys=True) == before


def test_execution_root_class_is_unordered_candidates(method_project):
    """Члены класса — варианты входа, а не доказанный последовательный вызов всех методов."""
    project, graph, _ = method_project
    result = build_execution_path(project, graph, 'service', 's')
    member_edges = [edge for edge in result['edges'] if edge['source'] == 'service']
    assert {edge['target'] for edge in member_edges} == {'start', 'neighbor'}
    assert all(edge['confidence'] == 'candidate' for edge in member_edges)


def test_execution_limits_are_explicit(method_project):
    """Обрезанный путь не выдаётся за полный; глубина и число методов ограничены отдельно."""
    project, graph, _ = method_project
    graph['edges'].append({'source': 'next', 'target': 'neighbor', 'relation': 'calls'})
    count = build_execution_path(project, graph, 'start', 's', max_nodes=1)
    assert len(count['steps']) == 1
    assert count['truncation']['nodes']
    depth = build_execution_path(project, graph, 'start', 's', max_depth=1)
    assert 'neighbor' not in {step['nodeId'] for step in depth['steps']}
    assert depth['truncation']['depth']


def test_execution_diamond_is_not_a_cycle(method_project):
    """Два пути к одному методу не являются рекурсией."""
    project, graph, _ = method_project
    graph['edges'] = [
        {'source': 'start', 'target': 'next', 'relation': 'calls'},
        {'source': 'start', 'target': 'neighbor', 'relation': 'calls'},
        {'source': 'next', 'target': 'neighbor', 'relation': 'calls'},
    ]
    result = build_execution_path(project, graph, 'start', 's')
    assert not result['cycles']
    assert len(result['steps']) == 3


@pytest.mark.parametrize('filename', ['../outside.cs', 'link.cs'])
def test_execution_cannot_read_external_source(method_project, tmp_path, filename):
    """Недоступный источник отмечается явно без утечки данных из соседнего каталога."""
    project, graph, _ = method_project
    outside = tmp_path / 'outside.cs'
    outside.write_text('public void Start() { logger.LogError("OUTSIDE_SECRET"); }')
    (project / 'link.cs').symlink_to(outside)
    graph['nodes'][0]['file'] = filename
    result = build_execution_path(project, graph, 'start', 's')
    root = next(step for step in result['steps'] if step['nodeId'] == 'start')
    assert root['sourceStatus'] == 'unavailable'
    assert root['unresolved']
    assert 'OUTSIDE_SECRET' not in json.dumps(result)


def test_execution_unknown_node_rejected(method_project):
    """Синтетический или отсутствующий узел не превращается в произвольный путь."""
    project, graph, _ = method_project
    with pytest.raises(ExecutionPathError) as error:
        build_execution_path(project, graph, 'entry-synthetic', 's')
    assert error.value.status == 404


def test_execution_interface_dispatch_candidates_are_uncertain(method_project):
    """Несколько реализаций интерфейса сохраняются как варианты, без выбора runtime-типа."""
    project, graph, text = method_project
    (project / 'Service.cs').write_text(text.replace('if (ready) Next();', 'if (ready) Work();'))
    (project / 'Workers.cs').write_text('''public interface IWorker { void Work(); }
public class WorkerA { public void Work() { logger.LogInformation("A"); } }
public class WorkerB { public void Work() { logger.LogInformation("B"); } }
''')
    graph['nodes'].extend([
        {'id': 'iface', 'label': 'Work', 'kind': 'method', 'file': 'Workers.cs', 'line': 1},
        {'id': 'impl-a', 'label': 'Work', 'kind': 'method', 'file': 'Workers.cs', 'line': 2},
        {'id': 'impl-b', 'label': 'Work', 'kind': 'method', 'file': 'Workers.cs', 'line': 3},
    ])
    graph['edges'] = [
        {'source': 'start', 'target': 'iface', 'relation': 'calls'},
        {'source': 'iface', 'target': 'impl-a', 'relation': 'dispatches_to'},
        {'source': 'iface', 'target': 'impl-b', 'relation': 'dispatches_to'},
    ]
    result = build_execution_path(project, graph, 'start', 's')
    assert {step['nodeId'] for step in result['steps']} == {'start', 'iface', 'impl-a', 'impl-b'}
    candidates = [edge for edge in result['edges'] if edge['source'] == 'iface']
    assert len(candidates) == 2
    assert all(edge['confidence'] == 'candidate' for edge in candidates)


def test_execution_reaches_interface_implementations_without_dispatch_edge(method_project):
    """Методы реализаций видны, даже если Graphify не построил dispatches_to."""
    project, graph, _ = method_project
    graph['nodes'].extend([
        {'id': 'iface', 'label': 'Work', 'kind': 'method', 'file': 'Workers.cs', 'line': 1},
        {'id': 'iworker', 'label': 'IWorker', 'kind': 'interface', 'file': 'Workers.cs', 'line': 1},
        {'id': 'worker-a', 'label': 'WorkerA', 'kind': 'class', 'file': 'Workers.cs', 'line': 2},
        {'id': 'worker-b', 'label': 'WorkerB', 'kind': 'class', 'file': 'Workers.cs', 'line': 4},
        {'id': 'impl-a', 'label': 'Work', 'kind': 'method', 'file': 'Workers.cs', 'line': 3},
        {'id': 'impl-b', 'label': 'WorkerB.Work()', 'kind': 'method', 'file': 'Workers.cs', 'line': 5},
        {'id': 'helper', 'label': 'Save', 'kind': 'method', 'file': 'Workers.cs', 'line': 6},
    ])
    graph['edges'] = [
        {'source': 'start', 'target': 'iface', 'relation': 'calls'},
        {'source': 'iworker', 'target': 'iface', 'relation': 'method'},
        {'source': 'worker-a', 'target': 'impl-a', 'relation': 'method'},
        {'source': 'worker-b', 'target': 'impl-b', 'relation': 'method'},
        {'source': 'worker-a', 'target': 'iworker', 'relation': 'implements'},
        {'source': 'worker-b', 'target': 'iworker', 'relation': 'implements'},
        {'source': 'impl-a', 'target': 'helper', 'relation': 'calls'},
    ]
    result = build_execution_path(project, graph, 'start', 's')
    assert {'iface', 'impl-a', 'impl-b', 'helper'} <= {step['nodeId'] for step in result['steps']}
    realized = [edge for edge in result['edges'] if edge['source'] == 'iface']
    assert {edge['target'] for edge in realized} == {'impl-a', 'impl-b'}
    assert all(edge['confidence'] == 'candidate' for edge in realized)


def test_execution_event_budget_and_nested_declarations(method_project):
    """Ограничение событий явно, неисполненное вложенное объявление не смешивается с телом."""
    project, graph, _ = method_project
    (project / 'Service.cs').write_text('class Service { public void Start() { void Local() { logger.LogError("HIDDEN"); }\n' + '\n'.join(f'logger.LogInformation("item{i}");' for i in range(170)) + '\n} }')
    result = build_execution_path(project, graph, 'start', 's', max_nodes=1)
    assert len(result['steps'][0]['events']) == 160
    assert result['truncation']['events'] is True
    assert 'HIDDEN' not in json.dumps(result)


@pytest.mark.parametrize('content', [b'\x00binary', b'x' * (1024 * 1024 + 1)])
def test_execution_unreadable_source_is_explicit(method_project, content):
    """Бинарный/огромный файл не читается как метод и не ломает сохранённый call-граф."""
    project, graph, _ = method_project
    (project / 'Service.cs').write_bytes(content)
    result = build_execution_path(project, graph, 'start', 's')
    assert {step['nodeId'] for step in result['steps']} == {'start', 'next'}
    assert all(step['sourceStatus'] == 'unavailable' and step['unresolved'] for step in result['steps'])


def test_execution_api_snapshot_validation_and_race(method_project, tmp_path, monkeypatch):
    """HTTP отклоняет stale token до анализа и публикацию нового снимка во время анализа."""
    from fastapi.testclient import TestClient
    from atlas import server, store
    from test_grouping_cache import legacy_store
    project, graph, _ = method_project
    state, project_id, snapshot, _ = legacy_store(tmp_path)
    store.write_json(snapshot, graph)
    state.projects[project_id]['path'] = str(project)
    store.write_json(state.registry, state.projects)
    state.close()
    with TestClient(server.create_app(tmp_path / 'state'), base_url='http://127.0.0.1:8765') as client:
        route = f'/api/projects/{project_id}/execution-path'
        body = {'nodeId': 'start', 'snapshotId': snapshot.parent.name}
        response = client.post(route, json=body)
        assert response.status_code == 200, response.text
        assert response.json()['rootNodeId'] == 'start'
        assert response.json()['snapshotId'] == body['snapshotId']
        assert client.post(route, json={'nodeId': 'start'}).status_code == 422
        assert client.post(route, json={**body, 'nodeId': 'ingress:synthetic'}).status_code == 404
        for field, value in [('maxDepth', 0), ('maxDepth', 31), ('maxNodes', 0), ('maxNodes', 2001)]:
            assert client.post(route, json={**body, field: value}).status_code == 422
        original = server.build_execution_path
        def forbidden(*args, **kwargs):
            """Устаревший запрос не должен дойти до чтения исходников."""
            raise AssertionError('stale request performed analysis')
        monkeypatch.setattr(server, 'build_execution_path', forbidden)
        assert client.post(route, json={**body, 'snapshotId': 'stale'}).status_code == 409
        def publish_during_analysis(*args, **kwargs):
            """Имитирует публикацию другого снимка после завершения локального анализа."""
            result = original(*args, **kwargs)
            newer = snapshot.parent.parent / 'newer' / 'atlas-graph.json'
            store.write_json(newer, graph)
            with client.app.state.store.lock:
                client.app.state.store.projects[project_id]['_graph'] = str(newer)
            return result
        monkeypatch.setattr(server, 'build_execution_path', publish_during_analysis)
        assert client.post(route, json=body).status_code == 409


def test_execution_dynamic_log_does_not_use_later_argument_as_template(method_project):
    """Строковый параметр после вычисляемого шаблона не выдаётся за текст самого лога."""
    project, graph, _ = method_project
    (project / 'Service.cs').write_text('class Service { void Start() { logger.LogInformation(BuildMessage(), "VALUE_NOT_TEMPLATE"); } }')
    result = build_execution_path(project, graph, 'start', 's', max_nodes=1)
    logs = [event for event in result['steps'][0]['events'] if event['kind'] == 'log']
    assert len(logs) == 1
    assert 'VALUE_NOT_TEMPLATE' not in logs[0]['text']
    assert 'вычисляется' in logs[0]['text']


def test_execution_trace_annotations_remain_candidates(method_project):
    """Имена trace-методов без семантики типа явно предполагаемые; соседние тела исключены."""
    project, graph, _ = method_project
    (project / 'Service.cs').write_text('''class Service {
void Start() {
activity.SetTag("count", count);
activity.AddTag("kind", "demo");
activity.AddEvent(new ActivityEvent("event"));
activity.SetStatus(ActivityStatusCode.Error);
var text = "activity.SetTag(fake)";
// activity.AddTag(comment)
}
void Neighbor() { activity.SetTag("ADJACENT_TRACE", 5); }
}''')
    result = build_execution_path(project, graph, 'start', 's', max_nodes=1)
    events = [event for event in result['steps'][0]['events'] if event['kind'] == 'span']
    assert [event['traceOperation'] for event in events] == ['SetTag', 'AddTag', 'AddEvent', 'SetStatus']
    assert all(event['confidence'] == 'candidate' for event in events)
    assert 'ADJACENT_TRACE' not in json.dumps(result)
    assert len(events) == 4


def test_larger_path_budget_keeps_captured_nodes_and_source_limits(method_project):
    """More than600 graph nodes are reachable without claiming unavailable source bodies were parsed."""
    from atlas.api_models import ExecutionPathInput
    from atlas.execution_path_limits import MAX_PATH_NODES

    project, graph, _ = method_project
    for index in range(700):
        target = f'external-{index}'
        graph['nodes'].append({'id': target, 'label': f'External{index}', 'kind': 'method', 'file': 'Missing.cs'})
        graph['edges'].append({'source': 'start', 'target': target, 'relation': 'calls'})
    before = json.dumps(graph, sort_keys=True)
    result = build_execution_path(project, graph, 'start', 's', max_nodes=1200)
    assert result['returnedNodes'] == 702
    assert not result['truncation']['nodes']
    assert result['limits']['maxFiles'] == 240
    assert result['limits']['maxEventsPerMethod'] == 160
    assert next(step for step in result['steps'] if step['nodeId'] == 'external-699')['sourceStatus'] == 'unavailable'
    assert json.dumps(graph, sort_keys=True) == before
    assert ExecutionPathInput(nodeId='start', snapshotId='s').maxNodes == MAX_PATH_NODES == 2000
    browser_limit = (Path(__file__).parents[1] / 'web/execution-path-limits.js').read_text()
    assert f'PATH_NODE_LIMIT = {MAX_PATH_NODES};' in browser_limit
    with pytest.raises(ExecutionPathError):
        build_execution_path(project, graph, 'start', 's', max_nodes=MAX_PATH_NODES + 1)
