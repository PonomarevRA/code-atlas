"""Принадлежность подпроектам выводится только из манифестов сохранённого графа."""

from copy import deepcopy

from atlas.grouping import enrich_graph
from atlas.project_scopes import UNASSIGNED_PROJECT, assign_code_projects


def test_nested_projects_exact_path_boundaries_and_unassigned_nodes():
    """Вложенный проект имеет приоритет; префикс имени каталога не создаёт принадлежность."""
    nodes = [
        {"id": "root", "file": "src/App/App.csproj"},
        {"id": "root-code", "file": "src/App/Service.cs"},
        {"id": "nested", "file": "src/App/Child/Child.csproj"},
        {"id": "nested-code", "file": "src/App/Child/Service.cs"},
        {"id": "lookalike", "file": "src/Application/Service.cs"},
        {"id": "external", "kind": "external", "file": "src/App/System.cs"},
        {"id": "empty", "file": ""},
        {"id": "traversal", "file": "src/App/../Other.cs"},
        {"id": "windows", "file": "src\\App\\Windows.cs"},
    ]
    projects = assign_code_projects(nodes)
    by_node = {node["id"]: node["codeProjectId"] for node in nodes}
    assert by_node["root"] == by_node["root-code"] == by_node["windows"]
    assert by_node["nested"] == by_node["nested-code"]
    assert by_node["root"] != by_node["nested"]
    for identifier in ("lookalike", "external", "empty", "traversal"):
        assert by_node[identifier] == UNASSIGNED_PROJECT
    assert sum(project["nodeCount"] for project in projects) == len(nodes)
    assert next(project for project in projects if project["id"] == UNASSIGNED_PROJECT)["nodeCount"] == 4


def test_multiple_manifests_in_one_directory_do_not_invent_ownership():
    """Неоднозначный обычный файл не приписывается произвольному проекту."""
    nodes = [
        {"id": "one", "file": "src/One.csproj"},
        {"id": "two", "file": "src/Two.csproj"},
        {"id": "shared", "file": "src/Shared.cs"},
    ]
    assign_code_projects(nodes)
    assert nodes[0]["codeProjectId"] != nodes[1]["codeProjectId"]
    assert nodes[2]["codeProjectId"] == UNASSIGNED_PROJECT


def test_enrichment_adds_project_catalog_without_mutating_source():
    """Обогащение не меняет исходные узлы и стабильно назначает ID подпроектов."""
    source = {"nodes": [{"id": "manifest", "file": "package.json"}, {"id": "code", "file": "src/a.js"}], "edges": [], "metadata": {}}
    before = deepcopy(source)
    first = enrich_graph(source)
    assert source == before
    second = enrich_graph({**source, "nodes": list(reversed(source["nodes"]))})
    assert first["codeProjects"] == second["codeProjects"]
    assert len(first["codeProjects"]) == 2
    assert first["nodes"][0]["codeProjectId"] == first["nodes"][1]["codeProjectId"]
