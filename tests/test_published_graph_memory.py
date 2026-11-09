"""Повторное чтение опубликованного снимка не разбирает тот же файл заново."""

from pathlib import Path

import pytest

from atlas import store
from test_grouping_cache import legacy_store


def test_same_revision_is_parsed_once_and_bad_json_stays_409(tmp_path: Path):
    """Тот же snapshotId не вызывает второй read; битый файл не запоминается и снова даёт 409."""
    state, project_id, graph_path, _ = legacy_store(tmp_path)
    reads = []
    original = state._read_graph

    def counted(path: Path):
        """Считает только разбор atlas-graph.json, не чтение реестра."""
        reads.append(path)
        return original(path)

    state._read_graph = counted
    try:
        listed = state.list_projects()["projects"][0]
        assert listed["snapshotId"] == "first"
        assert "path" in listed
        first = state.comparison_snapshot(project_id)[1]
        first["nodes"].append({"id": "extra", "label": "Extra"})
        second = state.comparison_snapshot(project_id)[1]
        assert second["metadata"]["snapshotId"] == "first"
        assert all(node["id"] != "extra" for node in second["nodes"])
        state.description_snapshot(project_id, "first")
        assert len(reads) == 1
        graph_path.write_text("{", encoding="utf-8")
        with pytest.raises(store.StoreError) as broken:
            state.comparison_snapshot(project_id)
        assert broken.value.status == 409
        assert "Не удалось прочитать карту" in str(broken.value)
        with pytest.raises(store.StoreError) as again:
            state.comparison_snapshot(project_id)
        assert again.value.status == 409
        assert len(reads) == 3
    finally:
        state.close()


def test_study_pages_expose_local_icon_and_atlas_description():
    """Вкладка браузера и карточка ведут в Atlas-описание, а не в Graphify MCP."""
    root = Path(__file__).resolve().parents[1] / "web"
    index = (root / "index.html").read_text(encoding="utf-8")
    view = (root / "view.html").read_text(encoding="utf-8")
    assert 'rel="icon"' in index and 'href="/favicon.png"' in index
    assert 'rel="icon"' in view and 'href="/favicon.png"' in view
    assert (root / "favicon.png").is_file()
    assert 'id="node-description-open" class="primary"' in index
    assert 'id="describe" class="primary"' not in index
    assert 'data-path="chain"' in index
    assert 'id="show-details"' not in index
    assert 'id="empty-focus-path"' in index
    assert index.find('id="ingress-catalog"') > index.find('class="graph-options"')
    assert 'id="ingress-catalog" class="ingress-catalog discovery-panel"' in index
    assert 'id="discovery-mode"' in index
    assert '#workspace-left, #workspace-right { position:fixed' in (root / 'styles.css').read_text(encoding='utf-8')
