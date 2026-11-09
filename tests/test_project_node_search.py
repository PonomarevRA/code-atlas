"""Bounded snapshot search avoids full graph copies while preserving atomic revision and isolation."""

from concurrent.futures import ThreadPoolExecutor
import pytest

from atlas import store
from atlas.agent_views import search_nodes
from test_grouping_cache import legacy_store


def test_project_search_reads_only_preview_and_preserves_cache_and_source(tmp_path, monkeypatch):
    """Large irrelevant facts are never deep-copied and result edits cannot mutate the cache."""
    state, project, path, graph = legacy_store(tmp_path)
    graph["nodes"] = [
        {"id": "z", "label": "Calculate", "kind": "field", "file": "Calculator.cs", "privateHeavy": {"rows": ["ignored"] * 10000}},
        {"id": "a", "label": "Calculate", "kind": "method", "file": "Calculator.cs", "line": 12,
         "comment": "Считает значение.", "commentSource": {"sourceKind": "snapshot-captured-source", "nested": {"value": 3}},
         "complexity": {"score": 4, "status": "complete"}, "privateHeavy": {"rows": ["ignored"] * 10000}},
    ]
    graph["metadata"]["snapshotId"] = "first"
    store.write_json(path, graph)
    original = path.read_bytes()
    original_copy = store.deepcopy
    copied_heavy = []

    def guarded_copy(value):
        """Fail on full graph copying, including a node's unused heavy payload."""
        if isinstance(value, dict) and ("edges" in value or "privateHeavy" in value):
            copied_heavy.append(True)
            pytest.fail("Search copied the full graph or a heavy unselected node field")
        return original_copy(value)

    monkeypatch.setattr(store, "deepcopy", guarded_copy)
    try:
        expected = search_nodes(graph, "CALCULATE", 1, ["FIELD"])
        result = state.search_project_nodes(project, "first", "CALCULATE", 1, ["FIELD"])
        assert result == expected and [node["id"] for node in result["nodes"]] == ["a"]
        result["nodes"][0]["commentSource"]["nested"]["value"] = 90
        result["nodes"][0]["complexity"]["score"] = 200
        again = state.search_project_nodes(project, "first", "CALCULATE", 1, ["FIELD"])
        assert again == expected and not copied_heavy
        assert path.read_bytes() == original
        assert state.search_project_nodes(project, None, "no match")["total"] == 0
        assert state.search_project_nodes(project, None, "", 1)["truncated"] is True
    finally:
        state.close()


def test_project_search_rejects_stale_before_loading_and_refreshes_rewritten_stamp(tmp_path, monkeypatch):
    """A new snapshot and a rewritten same-revision graph never reuse obsolete cached results."""
    state, project, path, graph = legacy_store(tmp_path)
    reads = []
    original = state._read_graph

    def counted_read(source):
        reads.append(source)
        return original(source)

    monkeypatch.setattr(state, "_read_graph", counted_read)
    try:
        with pytest.raises(store.StoreError) as caught:
            state.search_project_nodes(project, "stale", "")
        assert caught.value.status == 409 and not reads
        state.search_project_nodes(project, "first", "")
        state.search_project_nodes(project, "first", "")
        assert len(reads) == 1
        graph["nodes"].append({"id": "new", "label": "New symbol", "kind": "method"})
        store.write_json(path, graph)
        assert state.search_project_nodes(project, "first", "New symbol")["total"] == 1
        assert len(reads) == 2
        next_path = path.parent.parent / "next" / path.name
        store.write_json(next_path, graph)
        with state.lock:
            state.projects[project]["_graph"] = str(next_path)
        with pytest.raises(store.StoreError) as caught:
            state.search_project_nodes(project, "first", "")
        assert caught.value.status == 409
        assert state.search_project_nodes(project, "next", "")["snapshotId"] == "next"
    finally:
        state.close()


def test_concurrent_search_results_are_independent(tmp_path):
    """Parallel readers retain deterministic ordering and isolated nested previews."""
    state, project, path, graph = legacy_store(tmp_path)
    graph["nodes"].append({"id": "nested", "label": "Nested", "complexity": {"score": 3}})
    store.write_json(path, graph)
    try:
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _: state.search_project_nodes(project, "first", "Nested"), range(8)))
        assert all(result == results[0] for result in results)
        results[0]["nodes"][0]["complexity"]["score"] = 90
        assert results[1]["nodes"][0]["complexity"]["score"] == 3
    finally:
        state.close()


def test_fact_index_refreshes_after_same_revision_fact_rewrite(tmp_path):
    """Changed annotation facts invalidate a warm index even when node names and snapshot IDs stay fixed."""
    state, project, path, graph = legacy_store(tmp_path)
    graph['nodes'][0]['testFacts'] = {'tags': ['former-token']}
    store.write_json(path, graph)
    try:
        assert state.search_project_nodes(project, 'first', 'former-token')['total'] == 1
        old_index = state._search_facts
        graph['nodes'][0]['testFacts'] = {'tags': ['replacement-token']}
        store.write_json(path, graph)
        assert state.search_project_nodes(project, 'first', 'former-token')['total'] == 0
        assert state.search_project_nodes(project, 'first', 'replacement-token')['total'] == 1
        assert state._search_facts is not old_index
    finally:
        state.close()
