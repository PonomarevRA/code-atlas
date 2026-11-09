"""Навыки и agent-memory попадают на карту и не ломают граф кода."""

from pathlib import Path

import pytest

from atlas.grouping import enrich_graph
from atlas.map_modules import attach_map_modules, contribute_memory, contribute_skills


def _graph():
    return {
        "nodes": [{"id": "fn", "label": "run", "kind": "function", "file": "service.py", "line": 1, "community": None, "description": ""}],
        "edges": [],
        "metadata": {"nodeCount": 1, "edgeCount": 0},
    }


def _skill(path: Path, name: str, description: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: \"{description}\"\n---\n# {name}\n", encoding="utf-8")


def test_skills_and_memory_link_existing_files(tmp_path: Path):
    """Оба модуля добавляют узел, описание и одну связь documents к существующему файлу."""
    _skill(tmp_path / "skills" / "demo" / "SKILL.md", "demo", "Смотри service.py перед правкой")
    memory = tmp_path / "docs" / "agent-memory" / "area" / "note.a2a.yaml"
    memory.parent.mkdir(parents=True)
    memory.write_text(
        "schema: agent-memory-object/v1\n"
        "id: area.fact.note\n"
        "title: Заметка\n"
        "summary: Кратко о сервисе.\n"
        "sources:\n"
        "  - kind: code\n"
        "    ref: service.py\n"
        "payload:\n"
        "  statement: Сервис удваивает значение.\n",
        encoding="utf-8",
    )
    graph = _graph()
    attach_map_modules(graph, tmp_path)
    enriched = enrich_graph(graph)
    kinds = {node["kind"]: node for node in enriched["nodes"]}
    assert kinds["skill"]["id"] == "skill:skills/demo/SKILL.md"
    assert kinds["skill"]["label"] == "demo"
    assert "service.py" in kinds["skill"]["description"]
    assert kinds["memory"]["description"].startswith("Кратко о сервисе.")
    assert "удваивает" in kinds["memory"]["description"]
    assert isinstance(kinds["skill"]["community"], str)
    documents = [edge for edge in enriched["edges"] if edge["relation"] == "documents"]
    assert {edge["source"] for edge in documents} == {kinds["skill"]["id"], kinds["memory"]["id"]}
    assert {edge["target"] for edge in documents} == {"fn"}
    assert [item["status"] for item in enriched["metadata"]["mapModules"]] == ["ok", "ok"]
    assert enriched["metadata"]["nodeCount"] == 3


def test_duplicate_skill_keeps_primary_path(tmp_path: Path):
    """Повтор имени остаётся одним узлом, предпочтение у каталога skills."""
    _skill(tmp_path / ".cursor" / "skills" / "demo" / "SKILL.md", "demo", "Черновик")
    _skill(tmp_path / "skills" / "demo" / "SKILL.md", "demo", "Основной")
    nodes = contribute_skills(tmp_path)["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["file"] == "skills/demo/SKILL.md"
    assert ".cursor/skills/demo/SKILL.md" in nodes[0]["description"]
    dotted = tmp_path / "only-dots"
    _skill(dotted / ".agents" / "skills" / "demo" / "SKILL.md", "demo", "Копия agents")
    _skill(dotted / ".cursor" / "skills" / "demo" / "SKILL.md", "demo", "Копия cursor")
    preferred = contribute_skills(dotted)["nodes"]
    assert preferred[0]["file"] == ".cursor/skills/demo/SKILL.md"


def test_memory_limits_and_skips_registries(tmp_path: Path):
    """Реестр не становится узлом, описание обрезается, девятая связь и повтор id отбрасываются."""
    registry = tmp_path / "docs" / "agent-memory" / "registry.a2a.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("schema: agent-memory-root-registry/v1\nkind: ignored\n", encoding="utf-8")
    refs = "\n".join(f"  - kind: code\n    ref: f{i}.py" for i in range(9))
    note = tmp_path / "docs" / "agent-memory" / "area" / "a-long.a2a.yaml"
    note.parent.mkdir()
    note.write_text(
        "schema: agent-memory-object/v1\nid: area.fact.long\ntitle: Длинная\nsummary: " + ("я" * 1300) + "\nsources:\n" + refs + "\n",
        encoding="utf-8",
    )
    copy = tmp_path / "docs" / "agent-memory" / "area" / "z-copy.a2a.yaml"
    copy.write_text("schema: agent-memory-object/v1\nid: area.fact.long\ntitle: Копия\nsummary: повтор\n", encoding="utf-8")
    graph = {
        "nodes": [{"id": f"n{i}", "file": f"f{i}.py", "kind": "function", "label": str(i)} for i in range(9)],
        "edges": [],
        "metadata": {},
    }
    attach_map_modules(graph, tmp_path)
    memory_nodes = [node for node in graph["nodes"] if node["kind"] == "memory"]
    assert [node["id"] for node in memory_nodes] == ["memory:area.fact.long"]
    assert len(memory_nodes[0]["description"]) == 1200
    documents = [edge for edge in graph["edges"] if edge["relation"] == "documents"]
    assert len(documents) == 8


def test_module_error_keeps_the_other_module(tmp_path: Path, monkeypatch):
    """Исключение одного сканера не удаляет узлы второго и не роняет сборку модулей."""
    _skill(tmp_path / "skills" / "demo" / "SKILL.md", "demo", "Готово")

    def broken(root):
        raise RuntimeError("x" * 5000)

    monkeypatch.setattr("atlas.map_modules.contribute_memory", broken)
    graph = _graph()
    attach_map_modules(graph, tmp_path)
    assert any(node["kind"] == "skill" for node in graph["nodes"])
    assert all(node["kind"] != "memory" for node in graph["nodes"])
    failed = graph["metadata"]["mapModules"][1]
    assert failed["status"] == "error"
    assert len(failed["error"]) == 4000


def test_unnamed_skill_and_empty_project_do_not_fail(tmp_path: Path):
    """Файл без имени остаётся узлом по каталогу, пустой проект не меняет код."""
    path = tmp_path / "skills" / "plain" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Без шапки\n", encoding="utf-8")
    nodes = contribute_skills(tmp_path)["nodes"]
    assert nodes[0]["label"] == "plain"
    assert nodes[0]["id"] == "skill:skills/plain/SKILL.md"
    graph = _graph()
    before = list(graph["nodes"])
    attach_map_modules({"nodes": [], "edges": [], "metadata": {}}, tmp_path / "empty")
    empty = {"nodes": before, "edges": [], "metadata": {"nodeCount": 1, "edgeCount": 0}}
    attach_map_modules(empty, tmp_path / "empty")
    assert empty["nodes"] == before
    assert empty["metadata"]["nodeCount"] == 1
    assert [item["status"] for item in empty["metadata"]["mapModules"]] == ["ok", "ok"]


def test_kind_filter_keeps_modules_until_a_kind_is_chosen():
    """Пустой фильтр типа оставляет skill и memory, выбранный тип кода их скрывает."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки фильтра нужен Node.js")
    script = """
import { filterNodes } from './web/filters.js';
const graph = { nodes: [
  { id: 'fn', kind: 'function', label: 'run', file: 'service.py' },
  { id: 'skill:skills/demo/SKILL.md', kind: 'skill', label: 'demo', file: 'skills/demo/SKILL.md' },
  { id: 'memory:area.fact.note', kind: 'memory', label: 'note', file: 'docs/agent-memory/note.a2a.yaml' },
], edges: [] };
const all = filterNodes(graph, {}).map(node => node.kind).sort();
const functions = filterNodes(graph, { kind: 'function' }).map(node => node.kind);
if (all.join() !== 'function,memory,skill') process.exit(1);
if (functions.join() !== 'function') process.exit(2);
console.log('kind filter PASS');
"""
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert result.returncode == 0, result.stderr
