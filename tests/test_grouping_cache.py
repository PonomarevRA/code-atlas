"""Совместимость сохранённых карт и однократный пересчёт производной группировки."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

from atlas import store
from atlas.grouping import enrich_graph
from test_jobs import fake_build


def legacy_store(tmp_path: Path) -> tuple[store.ProjectStore, str, Path, dict]:
    """Создаёт старый опубликованный снимок без группировки и корректный реестр."""
    state = store.ProjectStore(tmp_path / "state")
    project_id = "legacy-fixture"
    output = state.data_dir / project_id / "first"
    graph = fake_build(tmp_path, output)
    graph_path = output / "atlas-graph.json"
    store.write_json(graph_path, graph)
    state.projects[project_id] = {
        "id": project_id, "name": "Legacy", "path": str(tmp_path), "status": "completed",
        "_graph": str(graph_path), "_rawGraph": graph["metadata"]["rawGraphPath"],
    }
    store.write_json(state.registry, state.projects)
    return state, project_id, graph_path, graph


def test_legacy_graph_is_grouped_once_across_concurrent_reads_and_restart(tmp_path, monkeypatch):
    """Параллельные чтения и перезапуск используют один устойчивый кэш группировки."""
    state, project_id, source_path, source = legacy_store(tmp_path)
    original_bytes = source_path.read_bytes()
    raw_path = Path(source["metadata"]["rawGraphPath"])
    raw_bytes = raw_path.read_bytes()
    calls = []

    def counted_grouping(graph, algorithm="louvain"):
        """Считает реальные вычисления без изменения результата алгоритма."""
        calls.append(True)
        return enrich_graph(graph, algorithm)

    monkeypatch.setattr(store, "enrich_graph", counted_grouping)
    try:
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _: state.get_graph(project_id), range(4)))
        assert len(calls) == 1
        assert all(result == results[0] for result in results)
        assert results[0]["communities"]
        assert source_path.read_bytes() == original_bytes
        assert raw_path.read_bytes() == raw_bytes
        assert state.description_snapshot(project_id, "first") == raw_path
        assert len(calls) == 1
    finally:
        state.close()
    reopened = store.ProjectStore(tmp_path / "state")
    try:
        assert reopened.get_graph(project_id) == results[0]
        assert len(calls) == 1
    finally:
        reopened.close()


def test_changed_legacy_source_invalidates_derived_cache(tmp_path, monkeypatch):
    """Новая версия исходного снимка не получает устаревшие группы из sidecar."""
    state, project_id, source_path, source = legacy_store(tmp_path)
    calls = []

    def counted_grouping(graph, algorithm="louvain"):
        """Считает построения для двух разных версий снимка."""
        calls.append(True)
        return enrich_graph(graph, algorithm)

    monkeypatch.setattr(store, "enrich_graph", counted_grouping)
    try:
        first = state.get_graph(project_id)
        updated = deepcopy(source)
        updated["nodes"].append({"id": "second", "label": "Second", "kind": "class", "file": "two.py"})
        store.write_json(source_path, updated)
        second = state.get_graph(project_id)
        assert len(first["nodes"]) == 1
        assert len(second["nodes"]) == 2
        assert sum(group["nodeCount"] for group in second["communities"]) == 2
        assert len(calls) == 2
    finally:
        state.close()


def test_mcp_snapshot_does_not_trigger_grouping(tmp_path, monkeypatch):
    """Описание узла старой карты не ждёт вычисления визуальной раскладки."""
    state, project_id, _, source = legacy_store(tmp_path)

    def forbidden_grouping(graph, algorithm="louvain"):
        """Выявляет случайное включение тяжёлого анализа в путь MCP."""
        raise AssertionError("MCP must not compute visual communities")

    monkeypatch.setattr(store, "enrich_graph", forbidden_grouping)
    try:
        assert state.description_snapshot(project_id, "first") == Path(source["metadata"]["rawGraphPath"])
    finally:
        state.close()


def test_warm_grouping_cache_skips_raw_parse_but_recovers_corruption(tmp_path,monkeypatch):
    """Готовый sidecar не разбирает raw снова; повреждённый восстанавливается из памяти или, если её нет, из raw."""
    state,project_id,source_path,_=legacy_store(tmp_path)
    try:
        expected=state.get_graph(project_id,'louvain')
        reads=[]
        original=state._read_graph
        def counted(path):
            """Измеряет именно чтение исходного графа, не чтение sidecar."""
            reads.append(path)
            return original(path)
        monkeypatch.setattr(state,'_read_graph',counted)
        assert state.get_graph(project_id,'louvain')==expected
        assert reads==[]
        cache=next(source_path.parent.glob('atlas-grouped-*-louvain.json'))
        cache.write_text('{broken',encoding='utf-8')
        # Повреждённый sidecar восстанавливается из уже разобранного снимка в памяти.
        assert state.get_graph(project_id,'louvain')==expected
        assert reads==[]
        cache.write_text('{broken',encoding='utf-8')
        state._published_graph.clear()
        assert state.get_graph(project_id,'louvain')==expected
        assert reads==[source_path]
    finally:
        state.close()


def test_warm_grouping_cache_cannot_hide_invalid_node_shapes(tmp_path):
    """Корректный envelope с повреждённым graph не принимается как валидный снимок."""
    import json
    state,project_id,source_path,_=legacy_store(tmp_path)
    try:
        expected=state.get_graph(project_id,'louvain')
        cache=next(source_path.parent.glob('atlas-grouped-*-louvain.json'))
        payload=json.loads(cache.read_text())
        payload['graph']['nodes']=[{'id':42}]
        cache.write_text(json.dumps(payload),encoding='utf-8')
        actual=state.get_graph(project_id,'louvain')
        assert actual==expected
    finally:
        state.close()


def test_cache_metadata_provider_and_version_cannot_substitute_requested_algorithm(tmp_path):
    """Имя sidecar не заменяет проверку provider/version внутри файла."""
    import json
    state,project_id,source_path,_=legacy_store(tmp_path)
    try:
        expected=state.get_graph(project_id,'louvain')
        cache=next(source_path.parent.glob('atlas-grouped-*-louvain.json'))
        for field,value in [('groupingProvider','leiden'),('groupingVersion','old-version')]:
            payload=json.loads(cache.read_text())
            payload['graph']['metadata'][field]=value
            payload['graph']['nodes']=[]
            cache.write_text(json.dumps(payload),encoding='utf-8')
            assert state.get_graph(project_id,'louvain')==expected
    finally:
        state.close()
