"""Чтение текущего исходника строго из проекта и согласованного снимка графа."""
import os
from pathlib import Path
import subprocess
import sys
import pytest
from atlas.source_viewer import SourceError, read_node_source


def test_fact_reader_skips_syntax_without_changing_source_checks(tmp_path, monkeypatch):
    """Сбор фактов сохраняет исходник и хеш, не расходуя бюджет на токены подсветки."""
    import atlas.source_syntax as syntax
    (tmp_path / 'fact.py').write_bytes(b'\xef\xbb\xbfdef value():\r\n    return 1\r\n')
    node = {'file': 'fact.py', 'line': 2}
    displayed = read_node_source(tmp_path, node, 'snapshot')

    def unexpected_highlight(*args, **kwargs):
        raise AssertionError('Fact collection must not tokenize the file')

    monkeypatch.setattr(syntax, 'highlight_source', unexpected_highlight)
    captured = read_node_source(tmp_path, node, 'snapshot', include_syntax=False)
    assert 'syntax' not in captured
    for key in ('text', 'file', 'line', 'totalLines', 'snapshotId', 'sourceKind', 'sourceByteHash', 'warning'):
        assert captured[key] == displayed[key]
    (tmp_path / 'fact.py').write_bytes(b'\x00binary')
    with pytest.raises(SourceError) as error:
        read_node_source(tmp_path, node, 'snapshot', include_syntax=False)
    assert error.value.status == 415
    with pytest.raises(SourceError) as error:
        read_node_source(tmp_path, {'file': '../outside.py'}, 'snapshot', include_syntax=False)
    assert error.value.status == 404


def test_unicode_bom_and_current_content(tmp_path):
    """UTF-8 BOM удаляется, путь Unicode сохраняется, исходник не исполняется."""
    path = tmp_path / "Класс с пробелом.py"
    text = 'raise RuntimeError("never execute")\n<script>alert(1)</script>\n'
    path.write_bytes(b'\xef\xbb\xbf' + text.encode())
    before = path.read_bytes()
    result = read_node_source(tmp_path, {"id": "n", "file": path.name, "line": 2}, "snapshot")
    assert result["text"] == text
    assert result["file"] == path.name
    assert result["line"] == 2
    assert result["snapshotId"] == "snapshot"
    assert result["sourceKind"] == "working-tree"
    assert result["warning"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("mode", ["relative", "absolute", "symlink", "parent-symlink"])
def test_outside_paths_are_rejected(tmp_path, mode):
    """Пути графа не дают прочитать соседние файлы напрямую или через symlink."""
    project = tmp_path / "project"
    project.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("outside source")
    if mode == "relative":
        filename = "../outside.txt"
    elif mode == "absolute":
        filename = str(secret)
    elif mode == "symlink":
        (project / "link").symlink_to(secret)
        filename = "link"
    else:
        (project / "directory").symlink_to(tmp_path, target_is_directory=True)
        filename = "directory/outside.txt"
    with pytest.raises(SourceError) as error:
        read_node_source(project, {"id": "n", "file": filename}, "s")
    assert error.value.status == 403


def test_internal_symlink_allowed(tmp_path):
    """Канонический путь внутри проекта можно читать через внутренний symlink."""
    (tmp_path / "actual.py").write_text("pass\n")
    (tmp_path / "link.py").symlink_to(tmp_path / "actual.py")
    assert read_node_source(tmp_path, {"id": "n", "file": "link.py"}, "s")["text"] == "pass\n"


@pytest.mark.parametrize("node", [{"id": "n", "kind": "external", "file": ""}, {"id": "n", "file": "missing.py"}])
def test_missing_sources_are_explicit(tmp_path, node):
    """Внешний узел или удалённый исходник дают управляемое отсутствие источника."""
    with pytest.raises(SourceError) as error:
        read_node_source(tmp_path, node, "s")
    assert error.value.status == 404


@pytest.mark.parametrize("content,status", [(b'\0binary', 415), (b'\xffinvalid', 415), (b'a' * (1024 * 1024 + 1), 413)])
def test_unsupported_files_are_bounded(tmp_path, content, status):
    """Двоичный, неверно закодированный и слишком большой файл отклоняются."""
    (tmp_path / "file").write_bytes(content)
    with pytest.raises(SourceError) as error:
        read_node_source(tmp_path, {"id": "n", "file": "file"}, "s")
    assert error.value.status == status


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO is unavailable on Windows")
def test_fifo_read_never_blocks(tmp_path):
    """FIFO без писателя проверяется в отдельном процессе с коротким watchdog."""
    os.mkfifo(tmp_path / "pipe")
    script = "from pathlib import Path; from atlas.source_viewer import read_node_source,SourceError; import sys\ntry: read_node_source(Path(sys.argv[1]),{'id':'n','file':'pipe'},'s')\nexcept SourceError as error: print(error.status)\n"
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().isdigit()
    assert int(result.stdout.strip()) >= 400


def test_source_endpoint_scopes_snapshot_and_node(tmp_path, monkeypatch):
    """HTTP выбирает только узел текущего снимка, без группировки и произвольного пути."""
    from fastapi.testclient import TestClient
    from atlas import store
    from atlas.server import create_app
    from test_grouping_cache import legacy_store
    state, project_id, snapshot, original = legacy_store(tmp_path)
    project = tmp_path / "source"
    project.mkdir()
    (project / "file.py").write_text("pass\n")
    graph = {"nodes": [{"id": "known", "file": "file.py", "line": 1}], "edges": [], "metadata": original["metadata"]}
    store.write_json(snapshot, graph)
    state.projects[project_id]["path"] = str(project)
    store.write_json(state.registry, state.projects)
    state.close()
    def forbidden_grouping(*args, **kwargs):
        """Чтение кода не должно пересчитывать раскладку графа."""
        raise AssertionError("source must not regroup")
    monkeypatch.setattr(store, "enrich_graph", forbidden_grouping)
    with TestClient(create_app(tmp_path / "state"), base_url="http://127.0.0.1:8765") as client:
        route = f"/api/projects/{project_id}/source"
        body = {"nodeId": "known", "snapshotId": snapshot.parent.name}
        response = client.post(route, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "pass\n"
        assert client.post(route, json={**body, "snapshotId": "stale"}).status_code == 409
        assert client.post(route, json={**body, "nodeId": "missing"}).status_code == 404
        assert client.post("/api/projects/unknown/source", json=body).status_code == 404
        assert client.post(route, json={"file": "/etc/passwd"}).status_code == 422
        assert client.post(route, json={**body, "file": "/etc/passwd"}).status_code == 422


@pytest.mark.parametrize("text,count", [("", 1), ("a\nb\n", 3), ("a\r\nb\r", 3)])
def test_editor_line_count_and_stale_position(tmp_path, text, count):
    """Количество строк совпадает с UI, устаревший номер не подменяется последней строкой."""
    (tmp_path / "source").write_bytes(text.encode())
    result = read_node_source(tmp_path, {"file": "source", "line": 9000}, "s")
    assert result["totalLines"] == count
    assert result["line"] == 9000
    assert result["text"] == text


def test_exact_limit_is_readable(tmp_path):
    """Ровно 1 МиБ поддерживается, ограничение не обрезает допустимый файл."""
    content = "a" * (1024 * 1024)
    (tmp_path / "source").write_text(content)
    assert read_node_source(tmp_path, {"file": "source"}, "s")["text"] == content


def test_symlink_swap_between_resolution_and_open_is_rejected(tmp_path, monkeypatch):
    """Реальная подмена промежуточного каталога после resolve не открывает соседний файл."""
    from atlas import source_viewer
    project = tmp_path / "project"
    project.mkdir()
    directory = project / "inside"
    directory.mkdir()
    (directory / "source").write_text("allowed")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source").write_text("not allowed")
    original_reader = source_viewer._read_regular_file
    def swap_and_read(root, relative):
        """Подменяет настоящий каталог symlink перед защищённым открытием."""
        directory.rename(project / "preserved")
        directory.symlink_to(outside, target_is_directory=True)
        return original_reader(root, relative)
    monkeypatch.setattr(source_viewer, "_read_regular_file", swap_and_read)
    with pytest.raises(SourceError) as error:
        read_node_source(project, {"file": "inside/source"}, "s")
    assert error.value.status == 403
