"""Field tables use captured declarations, preserve edges, and expose unknown nullability."""
from copy import deepcopy
from atlas.attribute_graph import enrich_attribute_graph
from atlas.mcp_graph import build_mcp_graph
from tests.test_attribute_graph import project


def fields(graph):
    """Index only confirmed field/property facts."""
    return {node["fieldFacts"]["name"]: node["fieldFacts"] for node in graph["nodes"] if "fieldFacts" in node}


def test_fields_properties_docs_top_level_nullable_and_mcp(tmp_path):
    source = """#nullable enable
class C {
/// <summary>Client identifier.</summary>
public string Client {get; init;}
public string? Optional {get;set;}
public List<string?> Rows {get;set;}
public System.Nullable<int> Count;
public int x, y;
void Run() {}
}"""
    graph, _ = project(tmp_path, source)
    graph["edges"].append({"source": graph["nodes"][0]["id"], "target": graph["nodes"][1]["id"], "relation": "uses", "confidence": "EXTRACTED"})
    before = deepcopy(graph)
    captured = enrich_attribute_graph(graph, tmp_path)
    facts = fields(captured)
    assert graph == before
    assert captured["edges"] == graph["edges"]
    assert facts["Client"]["description"] == "Client identifier."
    assert facts["Client"]["nullable"] is False
    assert facts["Optional"]["nullable"] is True
    assert facts["Rows"]["type"] == "List<string?>" and facts["Rows"]["nullable"] is False
    assert facts["Count"]["nullable"] is True
    assert facts["x"]["nullable"] is False and facts["y"]["nullable"] is False
    assert "Run" not in facts
    published = build_mcp_graph(captured)
    raw = next(node for node in published["nodes"] if node.get("fieldFacts", {}).get("name") == "Client")
    assert raw["attributes"]["atlas.field"]["type"] == "string"
    assert raw["fieldFacts"]["sourceKind"] == "snapshot-captured-source"
    (tmp_path / "Types.cs").unlink()
    assert fields(captured) == facts


def test_nullable_unknown_disable_restore_and_project_context(tmp_path):
    (tmp_path / "App.csproj").write_text('<Project><PropertyGroup><Nullable>enable</Nullable></PropertyGroup></Project>')
    graph, _ = project(tmp_path, """class C {
string A;
#nullable disable
string B;
#nullable enable warnings
string Cc;
#nullable restore
string D;
}""")
    facts = fields(enrich_attribute_graph(graph, tmp_path))
    assert facts["A"]["nullable"] is False
    assert facts["B"]["nullable"] is None and facts["Cc"]["nullable"] is None
    assert facts["D"]["nullable"] is False
    (tmp_path / "App.csproj").write_text('<Project><PropertyGroup Condition="x"><Nullable>enable</Nullable></PropertyGroup></Project>')
    assert fields(enrich_attribute_graph(graph, tmp_path))["A"]["nullable"] is None


def test_nullable_is_not_inferred_from_comment_or_nested_generic(tmp_path):
    graph, _ = project(tmp_path, 'class C { /* #nullable enable */ List<string?> Rows; string? S; int N; }')
    facts = fields(enrich_attribute_graph(graph, tmp_path))
    assert facts["Rows"]["nullable"] is None
    assert facts["S"]["nullable"] is True
    assert facts["N"]["nullable"] is False
