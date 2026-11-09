"""Сравнение локальных Git-коммитов без изменения рабочего дерева и точности до символов."""

import os
from pathlib import Path
import subprocess

import pytest

from atlas.git_compare import GitCompareError, compare_graph


def git(repo: Path, *arguments: str) -> str:
    """Выполняет Git только в изолированном репозитории теста, без оболочки."""
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "-C", str(repo), *arguments],
        capture_output=True, text=True, check=True, timeout=10,
        env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
    )
    return result.stdout.strip()


def commit(repo: Path, message: str) -> str:
    """Фиксирует исключительно временные файлы и возвращает полный SHA."""
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def comparison_repo(tmp_path):
    """Готовит две ревизии с разными типами файловых изменений и грязным рабочим деревом."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    source = repo / "src"
    source.mkdir()
    (source / "modified.py").write_text("def one(): return 1\ndef two(): return 2\n")
    (source / "deleted.py").write_text("DELETE THIS SOURCE\n")
    (source / "rename_old.py").write_text("RENAME UNIQUE CONTENT\n" * 5)
    (repo / "outside.txt").write_text("outside baseline\n")
    base = commit(repo, "base")
    (source / "modified.py").write_text("def one(): return 99\ndef two(): return 2\n")
    (source / "deleted.py").unlink()
    (source / "rename_old.py").rename(source / "renamed.py")
    (source / "added.py").write_text("ADDED SOURCE\n")
    (source / "Пример с пробелом.py").write_text("UNICODE SOURCE\n")
    (source / "line\nbreak.py").write_text("NEWLINE NAME\n")
    (repo / "outside.txt").write_text("outside target\n")
    target = commit(repo, "target")
    # Сравнение ревизий не должно затрагивать staged/unstaged/untracked состояние.
    (source / "modified.py").write_text("UNCOMMITTED\n")
    (repo / "staged.txt").write_text("STAGED\n")
    git(repo, "add", "staged.txt")
    (repo / "untracked.txt").write_text("UNTRACKED\n")
    graph = {"nodes": [
        {"id": "one", "file": "src/modified.py", "line": 1},
        {"id": "two-unchanged-method", "file": "src/modified.py", "line": 2},
        {"id": "added", "file": "src/added.py"},
        {"id": "renamed", "file": "src/renamed.py"},
        {"id": "unicode", "file": "src/Пример с пробелом.py"},
        {"id": "newline", "file": "src/line\nbreak.py"},
    ], "edges": [], "metadata": {}}
    return repo, base, target, graph


def working_state(repo):
    """Снимает HEAD, индекс и все пользовательские файлы без служебного каталога Git."""
    files = {}
    for path in repo.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(repo).parts:
            files[str(path.relative_to(repo))] = path.read_bytes()
    return git(repo, "rev-parse", "HEAD"), (repo / ".git" / "index").read_bytes(), files


def test_changed_files_cover_all_symbols_and_preserve_worktree(comparison_repo):
    """Даже неизменённый метод помечается, когда изменился его файл; Git остаётся нетронутым."""
    repo, base, target, graph = comparison_repo
    before = working_state(repo)
    result = compare_graph(repo, graph, base, target)
    assert result["precision"] == "file"
    assert result["totalChangedFiles"] == 7
    assert result["matchedNodeCount"] == 6
    assert result["unmatchedFileCount"] == 2
    assert set(result["changedNodeIds"]) == {node["id"] for node in graph["nodes"]}
    assert result["warning"]
    by_path = {item["path"]: item for item in result["files"]}
    assert set(by_path["src/modified.py"]["nodeIds"]) == {"one", "two-unchanged-method"}
    assert by_path["src/renamed.py"]["oldPath"] == "src/rename_old.py"
    assert by_path["src/deleted.py"]["nodeIds"] == []
    assert "src/line\nbreak.py" in by_path
    assert "src/Пример с пробелом.py" in by_path
    assert working_state(repo) == before


def test_identical_commits_have_no_changes(comparison_repo):
    """Одна и та же ревизия не подсвечивает узлы из-за грязного рабочего дерева."""
    repo, base, _, graph = comparison_repo
    result = compare_graph(repo, graph, base, base)
    assert result["files"] == []
    assert result["changedNodeIds"] == []
    assert result["totalChangedFiles"] == result["matchedNodeCount"] == result["unmatchedFileCount"] == 0


def test_indexed_subdirectory_maps_relative_source_paths(comparison_repo):
    """Путь узла относительно индексируемой подпапки сопоставляется с Git-путём от корня."""
    repo, base, target, _ = comparison_repo
    graph = {"nodes": [{"id": "relative", "file": "modified.py"}, {"id": "absolute", "file": str(repo / "src" / "added.py")}], "edges": [], "metadata": {}}
    result = compare_graph(repo / "src", graph, base, target)
    assert set(result["changedNodeIds"]) == {"relative", "absolute"}


@pytest.mark.parametrize("reference", ["missing-ref", "--help", "-c", "HEAD;touch /tmp/code-atlas-should-not-exist", "HEAD\n--all"])
def test_invalid_refs_are_rejected_without_modification(comparison_repo, reference):
    """Неверные и похожие на команды значения не становятся параметрами shell/Git."""
    repo, base, _, graph = comparison_repo
    before = working_state(repo)
    with pytest.raises(GitCompareError):
        compare_graph(repo, graph, base, reference)
    assert working_state(repo) == before


def test_non_repository_has_controlled_error(tmp_path):
    """Обычный каталог не запускает извлечение или поиск удалённого репозитория."""
    with pytest.raises(GitCompareError):
        compare_graph(tmp_path, {"nodes": [], "edges": []}, "main", "HEAD")


def test_direct_and_merge_base_compare_different_commit_ranges(tmp_path):
    """Сравнение двух концов и диапазона от общего предка различает расходящиеся ветки."""
    repo = tmp_path / "diverged"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "base.py").write_text("base\n")
    ancestor = commit(repo, "ancestor")
    git(repo, "checkout", "-qb", "feature")
    (repo / "feature.py").write_text("feature\n")
    target = commit(repo, "feature")
    git(repo, "checkout", "-q", "main")
    (repo / "main.py").write_text("main\n")
    base = commit(repo, "main")
    graph = {"nodes": [], "edges": [], "metadata": {}}
    direct = compare_graph(repo, graph, base, target, "direct")
    merged = compare_graph(repo, graph, base, target, "merge-base")
    assert {item["path"] for item in direct["files"]} == {"main.py", "feature.py"}
    assert {item["path"] for item in merged["files"]} == {"feature.py"}
    assert merged["comparisonBase"] == ancestor
    assert direct["comparisonBase"] == base


def test_compare_api_validation_and_snapshot_identity(comparison_repo, tmp_path, monkeypatch):
    """HTTP сравнивает тот же снимок без пересчёта групп и проверяет поля запроса."""
    from fastapi.testclient import TestClient
    from atlas import store
    from atlas.server import create_app
    from test_grouping_cache import legacy_store

    repo, base, target, graph = comparison_repo
    state, project_id, snapshot, original = legacy_store(tmp_path)
    stored = {**graph, "metadata": original["metadata"]}
    store.write_json(snapshot, stored)
    state.projects[project_id]["path"] = str(repo)
    store.write_json(state.registry, state.projects)
    state.close()
    with TestClient(create_app(tmp_path / "state"), base_url="http://127.0.0.1:8765") as client:
        graph_response = client.get(f"/api/projects/{project_id}/graph")
        assert graph_response.status_code == 200
        token = graph_response.json()["metadata"]["snapshotId"]

        def forbidden_grouping(*args, **kwargs):
            """Не позволяет пути Git неожиданно запускать группировку."""
            raise AssertionError("Git comparison must not regroup a snapshot")

        monkeypatch.setattr(store, "enrich_graph", forbidden_grouping)
        body = {"baseRef": base, "targetRef": target, "mode": "direct"}
        response = client.post(f"/api/projects/{project_id}/compare", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["snapshotId"] == token
        assert response.json()["baseCommit"] == base
        assert response.json()["targetCommit"] == target
        assert response.json()["matchedNodeCount"] == 6
        assert client.post("/api/projects/missing/compare", json=body).status_code == 404
        for invalid in ({"baseRef": "", "targetRef": target}, {**body, "mode": "unknown"}, {"baseRef": base}, {**body, "targetRef": "x" * 513}):
            invalid_response = client.post(f"/api/projects/{project_id}/compare", json=invalid)
            assert invalid_response.status_code == 422
            assert invalid_response.json().get("error")


def test_rename_matches_old_snapshot_filename(comparison_repo):
    """Узел старого снимка сопоставляется через oldPath, даже если файл уже переименован."""
    repo, base, target, _ = comparison_repo
    graph = {"nodes": [{"id": "old-name", "file": "src/rename_old.py"}], "edges": []}
    result = compare_graph(repo, graph, base, target)
    assert result["changedNodeIds"] == ["old-name"]
    renamed = next(item for item in result["files"] if item["path"] == "src/renamed.py")
    assert renamed["nodeIds"] == ["old-name"]


def test_rename_crossing_index_boundary_is_preserved_in_both_directions(tmp_path):
    """Вынос файла за пределы индекса и обратное перемещение не исчезают из сравнения."""
    repo = tmp_path / "crossing"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    source = repo / "src"
    source.mkdir()
    (source / "unit.py").write_text("RENAME ACROSS SCOPE\n" * 5)
    base = commit(repo, "inside")
    (source / "unit.py").rename(repo / "outside.py")
    target = commit(repo, "outside")
    graph = {"nodes": [{"id": "unit", "file": "unit.py"}], "edges": []}
    for first, second in ((base, target), (target, base)):
        result = compare_graph(source, graph, first, second)
        assert result["totalChangedFiles"] == 1
        assert result["changedNodeIds"] == ["unit"]
        assert result["files"][0]["status"].startswith("R")


def test_git_execution_ignores_repository_overrides_and_never_fetches(comparison_repo, monkeypatch):
    """Окружение не подменяет репозиторий, отсутствующий ref не вызывает сетевой fallback."""
    from atlas import git_compare

    repo, base, target, graph = comparison_repo
    real_popen = git_compare.subprocess.Popen
    executions = []

    def observed_popen(command, *args, **kwargs):
        """Записывает реальные локальные команды и делегирует исходному subprocess."""
        executions.append((command, kwargs["env"]))
        return real_popen(command, *args, **kwargs)

    monkeypatch.setenv("GIT_DIR", str(repo / "does-not-exist"))
    monkeypatch.setattr(git_compare.subprocess, "Popen", observed_popen)
    assert compare_graph(repo, graph, base, target)["matchedNodeCount"] == 6
    with pytest.raises(GitCompareError):
        compare_graph(repo, graph, base, "origin/not-fetched")
    for command, environment in executions:
        assert "fetch" not in command
        assert "pull" not in command
        assert "checkout" not in command
        assert "protocol.allow=never" in command
        assert environment["GIT_NO_LAZY_FETCH"] == "1"
        assert "GIT_DIR" not in environment
