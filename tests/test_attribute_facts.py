"""Атрибуты подтверждаются выбранным C# AST, с отдельными владельцами и явными границами."""

from copy import deepcopy

import pytest

from atlas import attribute_facts as attributes
from atlas.execution_path import build_execution_path
from atlas.execution_path_source import SourceAnalysis
from atlas.node_description import build_node_description


def _project(tmp_path, text: str, label: str = "Run"):
    """Создаёт ровно один текущий C#-файл и выбранный узел без выдуманных рёбер."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "Handler.cs").write_text(text, encoding="utf-8")
    node = {"id": "run", "label": label, "kind": "method", "file": "Handler.cs"}
    return root, {"nodes": [node], "edges": [], "metadata": {}}


def _facts(root, graph):
    """Читает атрибуты через настоящий безопасный SourceAnalysis, а не mock AST."""
    return SourceAnalysis(root, "snapshot", 1, 1024 * 1024, 160).describe(graph["nodes"][0])["attributeFacts"]


def test_declaration_arguments_targets_and_source_ranges(tmp_path):
    """Запятые внутри выражений сохраняются; name: и Property= имеют разные лексические роли."""
    text = '''using System;
class Handler {
  [Example(
    typeof(Dictionary<int, string>),
    value: nameof(Handler), Property = new [] {1, 2}), N.Qualified, N.Qualified]
  [return: MaybeNull]
  void Run() { }
}'''
    root, graph = _project(tmp_path, text)
    facts = _facts(root, graph)
    group = next(group for group in facts["groups"] if group["scope"] == "declaration")
    assert group["name"] == "Run"
    assert [attribute["name"] for attribute in group["attributes"]] == ["Example", "N.Qualified", "N.Qualified", "MaybeNull"]
    example = group["attributes"][0]
    assert [argument["expression"] for argument in example["arguments"]] == [
        "typeof(Dictionary<int, string>)", "nameof(Handler)", "new [] {1, 2}"]
    assert [argument["position"] for argument in example["arguments"]] == [1, 2, 3]
    assert [argument["nameKind"] for argument in example["arguments"]] == [None, "constructor", "property"]
    assert [argument["name"] for argument in example["arguments"]] == [None, "value", "Property"]
    assert example["file"] == "Handler.cs" and (example["line"], example["endLine"]) == (3, 5)
    assert example["arguments"][0]["line"] == 4
    assert example["text"] == 'Example(\n    typeof(Dictionary<int, string>),\n    value: nameof(Handler), Property = new [] {1, 2})'
    assert group["attributes"][-1]["target"] == "return"
    assert "resolvedType" not in example and "value" not in example["arguments"][1]
    assert facts["status"] == "parsed" and not facts["truncated"]


def test_scopes_keep_containing_types_parameters_and_file_distinct(tmp_path):
    """Типы не наследуют атрибуты методу, а assembly/module не смешиваются с декларацией."""
    root, graph = _project(tmp_path, '''using System;
[assembly: Build("app")]
[module: Build(Name = "module")]
[Outer] class Outer {
  [Inner] class Inner {
    [Method] void Run([FromBody, Bind(Name = "input")] Request @request, int count, [Last] params int[] items) {
      [Nested] void Local([Hidden] int value) { }
    }
    [Neighbour] void Other() { }
  }
}''')
    facts = _facts(root, graph)
    declarations = [group for group in facts["groups"] if group["scope"] == "declaration"]
    assert [[attribute["name"] for attribute in group["attributes"]] for group in declarations] == [["Method"]]
    types = [group for group in facts["groups"] if group["scope"] == "type"]
    assert [group["name"] for group in types] == ["Inner", "Outer"]
    assert [[attribute["name"] for attribute in group["attributes"]] for group in types] == [["Inner"], ["Outer"]]
    parameters = [group for group in facts["groups"] if group["scope"] == "parameter"]
    assert [(group["name"], group["position"]) for group in parameters] == [("request", 1), ("items", 3)]
    assert [[attribute["name"] for attribute in group["attributes"]] for group in parameters] == [["FromBody", "Bind"], ["Last"]]
    file_group = next(group for group in facts["groups"] if group["scope"] == "file")
    assert file_group["name"] == "Handler.cs"
    assert [attribute["target"] for attribute in file_group["attributes"]] == ["assembly", "module"]
    all_names = {attribute["name"] for group in facts["groups"] for attribute in group["attributes"]}
    assert all_names.isdisjoint({"Nested", "Hidden", "Neighbour"})


def test_selected_type_primary_parameters_and_partial_parts_are_local(tmp_path):
    """Выбранный record имеет собственные атрибуты и primary parameters; другие файлы не сканируются."""
    root, graph = _project(tmp_path, '[Direct] partial record Handler([property: JsonName("id")] int Id);', "Handler")
    (root / "Other.cs").write_text('[Elsewhere] partial record Handler;')
    analysis = SourceAnalysis(root, "snapshot", 1, 1024 * 1024, 160)
    facts = analysis.describe(graph["nodes"][0])["attributeFacts"]
    assert [group["scope"] for group in facts["groups"]] == ["declaration", "parameter"]
    assert facts["groups"][0]["attributes"][0]["name"] == "Direct"
    assert facts["groups"][1]["name"] == "Id"
    assert facts["groups"][1]["attributes"][0]["target"] == "property"
    assert list(analysis.files) == ["Handler.cs"]
    assert "partial" in facts["warning"] and "Унаследованные" in facts["warning"]


def test_description_and_execution_path_preserve_graph_and_sources(tmp_path):
    """Оба публичных представления получают одинаковые факты; чтение не меняет граф или файл."""
    root, graph = _project(tmp_path, '[Api] class Handler { [HttpPost("calculate")] void Run() {} }')
    before_graph = deepcopy(graph)
    before_source = (root / "Handler.cs").read_bytes()
    description = build_node_description(root, graph, "run", "snapshot")
    path = build_execution_path(root, graph, "run", "snapshot")
    assert description["attributeFacts"] == path["steps"][0]["attributeFacts"]
    assert description["attributeFacts"]["groups"][0]["attributes"][0]["name"] == "HttpPost"
    assert not description["truncation"]["attributeFacts"]
    assert not path["truncation"]["attributeFacts"]
    assert description["analyzerVersion"] == "csharp-structure-v8-constructor-bodies"
    assert graph == before_graph and (root / "Handler.cs").read_bytes() == before_source


def test_empty_unavailable_unresolved_and_syntax_error_are_distinct(tmp_path):
    """Parsed без атрибутов не равен невозможности чтения, неоднозначности или неполному AST."""
    root, graph = _project(tmp_path, 'class Handler { void Run() {} void Run(int x) {} }')
    facts = _facts(root, graph)
    assert facts["status"] == "unresolved" and not facts["groups"]
    (root / "Handler.cs").write_text('class Handler { void Run() {} }')
    facts = _facts(root, graph)
    assert facts["status"] == "parsed" and all(not group["attributes"] for group in facts["groups"])
    (root / "Handler.cs").write_text('class Handler { [Marker] void Run() { var x = ; } }')
    assert _facts(root, graph)["status"] == "partial"
    (root / "Handler.cs").unlink()
    assert _facts(root, graph)["status"] == "unavailable"
    one, two = attributes.empty_attribute_facts(), attributes.empty_attribute_facts()
    one["groups"].append({})
    assert two["groups"] == []


def test_existing_safe_reader_and_file_byte_budgets_apply(tmp_path):
    """Небезопасный path/symlink не читает атрибуты; helper не открывает дополнительные файлы."""
    root, graph = _project(tmp_path, '[Marker] class Handler { void Run() {} }')
    outside = tmp_path / "Outside.cs"
    outside.write_text('[Secret] class Outside { void Run() {} }')
    (root / "Link.cs").symlink_to(outside)
    for filename in ("../Outside.cs", str(outside), "Link.cs"):
        node = {**graph["nodes"][0], "file": filename}
        result = SourceAnalysis(root, "snapshot", 1, 1024 * 1024, 160).describe(node)
        assert result["attributeFacts"]["status"] == "unavailable"
        assert result["attributeFacts"]["groups"] == []
    for files, byte_budget, truncated in ((0, 1024 * 1024, "files"), (1, 1, "bytes")):
        analysis = SourceAnalysis(root, "snapshot", files, byte_budget, 160)
        assert analysis.describe(graph["nodes"][0])["attributeFacts"]["status"] == "unavailable"
        assert analysis.truncation[truncated]


def test_attribute_argument_text_and_group_limits_are_explicit(tmp_path):
    """Усечение аргументов, текста и списков меняет статус и отражается на attribute/truncation."""
    args = ', '.join(str(index) for index in range(attributes.MAX_ARGUMENTS + 1))
    markers = ', '.join('Marker' for _ in range(attributes.MAX_ATTRIBUTES_PER_GROUP + 1))
    huge = 'x' * (attributes.MAX_EXPRESSION_CHARS + 1)
    root, graph = _project(tmp_path, f'class Handler {{ [Long("{huge}"), Many({args}), {markers}] void Run() {{}} }}')
    result = build_node_description(root, graph, "run", "snapshot")
    facts = result["attributeFacts"]
    items = facts["groups"][0]["attributes"]
    assert len(items) == attributes.MAX_ATTRIBUTES_PER_GROUP
    assert len(items[0]["arguments"][0]["expression"]) == attributes.MAX_EXPRESSION_CHARS
    assert items[0]["arguments"][0]["truncated"] and items[0]["truncated"]
    assert len(items[1]["arguments"]) == attributes.MAX_ARGUMENTS
    assert items[1]["argumentsTruncated"] and items[1]["truncated"]
    assert facts["truncated"] and facts["status"] == "partial"
    assert result["truncation"]["attributeFacts"]
    assert facts["limits"]["maxAttributesPerGroup"] == attributes.MAX_ATTRIBUTES_PER_GROUP


@pytest.mark.parametrize('limit', ['MAX_ATTRIBUTES', 'MAX_INSPECTED_NODES', 'MAX_GROUPS', 'MAX_CONTAINING_TYPES', 'MAX_PARAMETERS', 'MAX_ANCESTORS'])
def test_shared_and_scope_budgets_report_partial_without_crashing(tmp_path, monkeypatch, limit):
    """Каждый независимо исчерпанный бюджет не допускает молчаливую полноту фактов."""
    root, graph = _project(tmp_path, '[Outer] class Outer { [Inner] class Inner { [A, B] void Run([C] int x, [D] int y) {} } }')
    monkeypatch.setattr(attributes, limit, 1)
    facts = _facts(root, graph)
    assert facts["status"] == "partial" and facts["truncated"]
    assert len(facts["groups"]) <= attributes.MAX_GROUPS
    assert sum(len(group["attributes"]) for group in facts["groups"]) <= attributes.MAX_ATTRIBUTES
