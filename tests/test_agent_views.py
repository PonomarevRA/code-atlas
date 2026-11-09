"""Неизменяемые виды: исходные факты, ограниченная область и атомарное сохранение."""
from copy import deepcopy
import json
from pathlib import Path
import os
import pytest
from atlas.agent_views import AgentViewStore, AgentViewError, build_view, search_nodes
from agent_view_fixture import view_graph, view_request


def test_search_keeps_stable_id_order_counts_and_exclusions():
    """Selective filtering must preserve stable ties, total counts and pagination truncation."""
    graph = {'metadata': {'snapshotId': 'fixture'}, 'nodes': [
        {'id': 'z', 'label': 'needle last', 'kind': 'method'},
        {'id': 'a', 'label': 'needle first duplicate', 'kind': 'method'},
        {'id': 'a', 'label': 'needle second duplicate', 'kind': 'method'},
        {'id': 'b', 'label': 'needle excluded', 'kind': 'DTO'},
        {'id': 'c', 'label': 'unrelated', 'kind': 'method'},
    ]}
    original = deepcopy(graph)
    result = search_nodes(graph, 'NEEDLE', 2, ['dto'])
    assert [node['label'] for node in result['nodes']] == ['needle first duplicate', 'needle second duplicate']
    assert result['total'] == 3 and result['truncated']
    assert graph == original


def test_saved_view_preserves_facts_direction_duplicates_and_input(tmp_path):
    """Окрестность включает входящие связи, но не превращает их в исходящие и не теряет дубли."""
    graph = view_graph()
    before = deepcopy(graph)
    request = view_request(annotations=[{'id':'note','title':'Why','text':'<script>unsafe</script>','nodeIds':['b','c']}])
    view = AgentViewStore(tmp_path).create(tmp_path, graph, request)
    assert graph == before
    assert {node['id'] for node in view['nodes']} == {'a','b','c'}
    expected = [edge for edge in graph['edges'] if edge['source'] in {'a','b','c'} and edge['target'] in {'a','b','c'}]
    assert sorted(map(lambda e: json.dumps(e,sort_keys=True),view['edges'])) == sorted(map(lambda e: json.dumps(e,sort_keys=True),expected))
    assert len(view['edges']) == 4
    assert view['annotations'][0]['text'] == '<script>unsafe</script>'
    assert len(view['annotationEdges']) == 2
    assert all(edge['kind']=='explanation' for edge in view['annotationEdges'])
    assert view['metadata']['selection']['neighborDirection'] == 'both'
    for node in view['nodes']:
        original = next(item for item in graph['nodes'] if item['id']==node['id'])
        assert all(node[key]==value for key,value in original.items())
    assert AgentViewStore(tmp_path).get(view['id']) == view
    graph['nodes'].clear()
    view['nodes'].clear()
    assert len(AgentViewStore(tmp_path).get(view['id'])['nodes']) == 3


def test_saved_view_selection_depth_caps_search_and_stable_layout(tmp_path):
    """Нулевая глубина, cap и исключения имеют наблюдаемую семантику; перестановка фактов её не меняет."""
    graph = view_graph()
    zero = build_view(tmp_path,graph,view_request(depth=0))
    assert [node['id'] for node in zero['nodes']] == ['b']
    limited = build_view(tmp_path,graph,view_request(maxNodes=2))
    assert [node['id'] for node in limited['nodes']] == ['b','a']
    assert limited['metadata']['truncated'] is True
    first = build_view(tmp_path,graph,view_request())
    graph['nodes'].reverse(); graph['edges'].reverse()
    second = build_view(tmp_path,graph,view_request())
    assert first['layout'] == second['layout']
    assert first['edges'] == second['edges']
    searched = build_view(tmp_path,graph,view_request(nodeIds=[],search='REPOSITORY',depth=0))
    assert [node['id'] for node in searched['nodes']] == ['c']
    assert search_nodes(graph,'order',120,['DTO'])['total'] == 1


@pytest.mark.parametrize('change',[
    {'nodeIds':['missing']}, {'nodeIds':['d']}, {'nodeIds':[]},
    {'maxNodes':0}, {'maxNodes':121}, {'depth':4}, {'depth':-1},
    {'annotations':[{'id':'b','title':'Note','text':'body','nodeIds':['b']}]},
    {'annotations':[{'id':'note','title':'Note','text':'body','nodeIds':['z']}]},
    {'annotations':[{'id':'note','title':'Note','text':'body','nodeIds':[]}]},
])
def test_invalid_saved_view_never_leaves_artifact(tmp_path,change):
    """Неверные seeds, limits и ссылки заметок не создают частичный JSON."""
    with pytest.raises(AgentViewError):
        AgentViewStore(tmp_path).create(tmp_path,view_graph(),view_request(**change))
    assert not list(tmp_path.rglob('*.json'))


def test_saved_view_layout_contains_every_card_and_note(tmp_path):
    """PNG и SVG получают одну ограниченную раскладку без потери узлов или пояснений."""
    view = build_view(tmp_path,view_graph(),view_request(annotations=[{'id':'n','title':'Long title','text':'text '*240,'nodeIds':['b']}]))
    layout = view['layout']
    assert set(layout['nodes']) == {node['id'] for node in view['nodes']}
    assert set(layout['annotations']) == {'n'}
    boxes = {box['id']:box for box in layout['projects']}
    for node in view['nodes']:
        box=layout['nodes'][node['id']]; owner=boxes[node['codeProjectId']]
        assert owner['x'] <= box['x'] and owner['y'] <= box['y']
        assert box['x']+box['width'] <= owner['x']+owner['width']
        assert box['y']+box['height'] <= owner['y']+owner['height']
    for box in [*layout['projects'],*layout['annotations'].values()]:
        assert 0 <= box['x'] and 0 <= box['y']
        assert box['x']+box['width'] <= layout['width']
        assert box['y']+box['height'] <= layout['height']
    assert layout['width']*layout['height'] <= 32_000_000


def test_saved_view_path_validation_and_atomic_failure(tmp_path,monkeypatch):
    """Чтение не принимает путь; сбой переименования не оставляет доступный полуснимок."""
    store=AgentViewStore(tmp_path)
    for identifier in ['../outside','a/b','A'*32,'0'*31]:
        with pytest.raises(AgentViewError) as failure:
            store.get(identifier)
        assert failure.value.status==404
    def failed_replace(self,target):
        """Моделирует отказ финального атомарного шага."""
        raise OSError('disk unavailable')
    monkeypatch.setattr(os,'link',failed_replace)
    with pytest.raises(AgentViewError) as failure:
        store.create(tmp_path,view_graph(),view_request())
    assert failure.value.status==500
    assert not list(tmp_path.rglob('*.json'))
    assert not list(tmp_path.rglob('*.tmp'))


def test_uuid_collision_never_overwrites_saved_view(tmp_path,monkeypatch):
    """Даже повторный UUID не изменяет ранее опубликованный immutable JSON."""
    from types import SimpleNamespace
    monkeypatch.setattr('atlas.agent_views.uuid4',lambda:SimpleNamespace(hex='a'*32))
    store=AgentViewStore(tmp_path)
    first=store.create(tmp_path,view_graph(),view_request())
    with pytest.raises(AgentViewError):
        store.create(tmp_path,view_graph(),view_request(title='replacement'))
    assert store.get(first['id'])==first
    assert len(store.list()['views'])==1


def test_view_rejects_stale_direct_snapshot_and_omitted_note_target(tmp_path):
    """Даже прямой builder не смешивает token; заметки не ссылаются на узел за cap."""
    with pytest.raises(AgentViewError) as stale:
        build_view(tmp_path,view_graph(),view_request(snapshotId='old'))
    assert stale.value.status==409
    with pytest.raises(AgentViewError):
        build_view(tmp_path,view_graph(),view_request(maxNodes=2,annotations=[{'id':'n','title':'N','text':'body','nodeIds':['c']}]))


def test_exact_exclusions_block_bridge_search_and_annotation_targets(tmp_path):
    """Исключённый raw symbol нельзя вернуть поиском, заметкой или обходом через него."""
    graph=view_graph()
    graph['nodes'][3]['kind']='symbol'
    isolated=build_view(tmp_path,graph,view_request(nodeIds=['a'],excludeNodeIds=['b'],depth=3))
    assert [node['id'] for node in isolated['nodes']]==['a']
    assert isolated['metadata']['selection']['excludeNodeIds']==['b']
    assert isolated['metadata']['kindFilterWarning']
    for changes in [{'excludeNodeIds':['unknown']},{'excludeNodeIds':['b']}, {'nodeIds':['a'],'excludeNodeIds':['b'],'annotations':[{'id':'n','title':'N','text':'text','nodeIds':['b']}]}]:
        with pytest.raises(AgentViewError):
            build_view(tmp_path,graph,view_request(**changes))
    searched=build_view(tmp_path,graph,view_request(nodeIds=[],search='Handler',excludeNodeIds=['b'],depth=3))
    assert [node['id'] for node in searched['nodes']]==['a']


def test_exact_exclusions_apply_before_search_result_cap(tmp_path):
    """120 исключённых совпадений не скрывают следующее допустимое совпадение поиска."""
    graph={'nodes':[{'id':f'n{i:03}','label':'Match','kind':'symbol'} for i in range(121)],'edges':[],'metadata':{'snapshotId':'first'}}
    view=build_view(tmp_path,graph,view_request(nodeIds=[],search='Match',excludeNodeIds=[f'n{i:03}' for i in range(120)],depth=0))
    assert [node['id'] for node in view['nodes']]==['n120']


def test_small_view_copies_only_selected_facts_and_preserves_full_project_counts(tmp_path):
    """Unrelated heavy facts are never copied; selected nested facts and scope counts stay correct."""
    class UnselectedFacts:
        def __deepcopy__(self, memo):
            """Fail if a bounded saved view attempts to copy an unrelated payload."""
            raise AssertionError('Unselected facts must remain in the published snapshot')

    graph = view_graph()
    graph['nodes'][-1]['testFacts'] = UnselectedFacts()
    graph['nodes'][3]['testFacts'] = {'dataSources': [{'rows': [{'value': 'captured'}]}]}
    graph['edges'].append({'source': 'b', 'target': 'b', 'relation': 'calls'})
    view = build_view(tmp_path, graph, view_request(depth=0))
    assert [node['id'] for node in view['nodes']] == ['b']
    assert view['edges'] == [{'source': 'b', 'target': 'b', 'relation': 'calls'}]
    assert len(view['codeProjects']) == 1
    assert view['codeProjects'][0]['nodeCount'] == 4  # A manifest + handler + method + DTO.
    view['nodes'][0]['testFacts']['dataSources'][0]['rows'][0]['value'] = 'changed'
    assert graph['nodes'][3]['testFacts']['dataSources'][0]['rows'][0]['value'] == 'captured'
    assert 'codeProjectId' not in graph['nodes'][3]


def test_saved_view_isolates_nested_captured_project_descriptors(tmp_path):
    """Gradle's nested language metadata must not alias the immutable published snapshot."""
    from hashlib import sha256
    manifest = 'src/build.gradle'
    facts = {'id': 'project-' + sha256(manifest.encode()).hexdigest()[:16],
             'label': 'Gradle project', 'path': 'src', 'manifest': manifest,
             'kind': 'build-project', 'discoveryVersion': 'project-discovery-v1',
             'sourceKind': 'snapshot-captured-source', 'sourceHash': 'a' * 64,
             'modId': 'build-gradle', 'ruleId': 'gradle-projects', 'modFingerprint': 'b' * 64,
             'languages': ['java', 'kotlin']}
    graph = {'nodes': [{'id': 'run', 'kind': 'method', 'file': 'src/Run.java',
                        'codeProjectFacts': facts}], 'edges': [], 'metadata': {'snapshotId': 'first'}}
    view = build_view(tmp_path, graph, view_request(nodeIds=['run'], depth=0))
    view['codeProjects'][0]['languages'].append('changed')
    view['nodes'][0]['codeProjectFacts']['languages'].append('also changed')
    assert facts['languages'] == ['java', 'kotlin']


def test_saved_views_combine_layouts_without_duplicate_ids(tmp_path):
    """Legacy views remain readable; canonical UUID wins even when legacy mtime is newer."""
    import json
    import shutil
    import os
    store = AgentViewStore(tmp_path)
    current = store.create(tmp_path, view_graph(), view_request(title='canonical'))
    canonical = tmp_path / 'maps/views' / (current['id'] + '.json')
    legacy = tmp_path / 'views'
    legacy.mkdir()
    old = {**current, 'title': 'legacy duplicate'}
    (legacy / canonical.name).write_text(json.dumps(old))
    os.utime(legacy / canonical.name, (2_000_000_000, 2_000_000_000))
    legacy_only = {**current, 'id': 'b' * 32, 'title': 'legacy only'}
    (legacy / ('b' * 32 + '.json')).write_text(json.dumps(legacy_only))
    assert store.get(current['id'])['title'] == 'canonical'
    assert store.get('b' * 32)['title'] == 'legacy only'
    assert {row['title'] for row in store.list()['views']} == {'canonical', 'legacy only'}
    assert len(store.list()['views']) == 2
    shutil.rmtree(tmp_path / 'maps')
    assert store.get(current['id'])['title'] == 'legacy duplicate'
    assert not (tmp_path / 'maps').exists()


def test_view_storage_rejects_links_conflicts_and_preserves_foreign_temporary(tmp_path, monkeypatch):
    """Creation never removes a temporary file it failed to create or traverses a link."""
    from types import SimpleNamespace
    import shutil
    store = AgentViewStore(tmp_path)
    identifier = 'e' * 32
    monkeypatch.setattr('atlas.agent_views.uuid4', lambda: SimpleNamespace(hex=identifier))
    store.directory.mkdir(parents=True)
    temporary = store.directory / (identifier + '.tmp')
    temporary.write_text('foreign staging')
    with pytest.raises(AgentViewError):
        store.create(tmp_path, view_graph(), view_request())
    assert temporary.read_text() == 'foreign staging'
    temporary.unlink()
    shutil.rmtree(tmp_path / 'maps')
    external = tmp_path / 'external'
    external.mkdir()
    (tmp_path / 'maps').symlink_to(external, target_is_directory=True)
    with pytest.raises(AgentViewError) as error:
        store.create(tmp_path, view_graph(), view_request())
    assert error.value.status == 409
    assert not list(external.iterdir())
    with pytest.raises(AgentViewError):
        store.get(identifier)


def test_legacy_view_uuid_collision_cannot_shadow_its_immutable_url(tmp_path, monkeypatch):
    """UUID collisions must preserve identity across both storage layouts."""
    import json
    from types import SimpleNamespace
    store = AgentViewStore(tmp_path)
    original = build_view(tmp_path, view_graph(), view_request(title='legacy original'))
    legacy = tmp_path / 'views' / (original['id'] + '.json')
    legacy.parent.mkdir()
    legacy.write_text(json.dumps(original))
    before = legacy.read_bytes()
    monkeypatch.setattr('atlas.agent_views.uuid4', lambda: SimpleNamespace(hex=original['id']))
    with pytest.raises(AgentViewError) as error:
        store.create(tmp_path, view_graph(), view_request(title='shadow attempt'))
    assert error.value.status == 409
    assert store.get(original['id']) == original
    assert legacy.read_bytes() == before
    assert not (store.directory / legacy.name).exists()
