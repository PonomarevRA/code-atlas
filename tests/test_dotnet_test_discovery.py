"""C# framework adapters expose source evidence without running tests or data providers."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from atlas.attribute_graph import enrich_attribute_graph
from atlas import dotnet_test_discovery
from atlas.dotnet_test_discovery import enrich_dotnet_test_discovery
from atlas.test_coverage_sources import METHOD_TYPES, TYPE_TYPES, source_text, walk


def rules(*frameworks):
    """Load the shipped optional mod contract; there are no hidden adapter defaults."""
    result = {"fingerprint": "fixture-enabled-mods", "testFrameworks": []}
    for framework in frameworks:
        manifest = Path(__file__).parents[1] / "mods" / f"framework-{framework}" / "mod.json"
        document = json.loads(manifest.read_text())
        for item in document["analysis"]["testFrameworks"]:
            result["testFrameworks"].append({**item, "modId": document["id"]})
    return result


def project(root, sources, *, captured=True):
    """Create exact raw declarations from isolated fixture sources, with optional capture."""
    from tree_sitter import Language, Parser
    import tree_sitter_c_sharp
    parser = Parser(Language(tree_sitter_c_sharp.language()))
    nodes = []
    for filename, source in sources.items():
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        content = source.encode()
        for item in walk(parser.parse(content).root_node):
            if item.type not in METHOD_TYPES | TYPE_TYPES:
                continue
            named = item.child_by_field_name("name")
            name = source_text(named, content)
            method = item.type in METHOD_TYPES
            nodes.append({"id": f"raw:{filename}:{len(nodes)}", "file": filename,
                          "label": f".{name}()" if method else name, "kind": "method" if method else "class",
                          "line": named.start_point.row + 1, "codeProjectId": "test-project",
                          "codeProjectFacts": {"kind": "build-module"}})
    graph = {"nodes": nodes, "edges": [], "metadata": {"snapshotId": "fixture-snapshot", "unrelated": 1}}
    if len(nodes) > 1:
        graph["edges"].append({"source": nodes[0]["id"], "target": nodes[1]["id"], "relation": "contains"})
    return enrich_attribute_graph(graph, root) if captured else graph


def entries(graph):
    """Read additive test entries; unrelated graph nodes are never treated as tests."""
    return graph["metadata"]["testDiscovery"]["entries"]


@pytest.mark.parametrize("framework,source,name", [
    ("xunit", 'using Xunit; class Specs { [Fact] public void X() {} }', "X"),
    ("nunit", 'using NUnit.Framework; class Specs { [Test] public void N() {} }', "N"),
    ("mstest", 'using Microsoft.VisualStudio.TestTools.UnitTesting; [TestClass] class Specs { [TestMethod] public void M() {} }', "M"),
])
def test_enabled_framework_binds_real_raw_method_and_preserves_graph(tmp_path, framework, source, name):
    graph = project(tmp_path, {"checks/Specs.cs": source})
    before = deepcopy(graph)
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules(framework))
    entry, = entries(result)
    assert graph == before
    assert result["edges"] == before["edges"]
    assert [node["id"] for node in result["nodes"]] == [node["id"] for node in before["nodes"]]
    assert entry["framework"] == framework and entry["label"] == name
    assert entry["codeProjectId"] == "test-project"
    assert entry["codeProjectFacts"] == {"kind": "build-module"}
    assert entry["declaredStatus"] == "enabled" and entry["runtimeStatus"] == "unknown"
    assert entry["sourceKind"] == "snapshot-captured-source"
    raw = next(node for node in result["nodes"] if node["id"] == entry["handlerNodeIds"][0])
    assert raw["kind"] == "method" and raw["testFacts"] == entry
    assert entry["evidence"][0]["name"].endswith("Attribute")
    assert result["metadata"]["unrelated"] == 1
    json.dumps(result)


def test_disabled_mod_never_reads_sources_or_retains_its_previous_test_facts(tmp_path, monkeypatch):
    graph = project(tmp_path, {"Specs.cs": 'using Xunit; class Specs { [Fact] void X() {} }'})
    captured = enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit"))
    monkeypatch.setattr("atlas.dotnet_test_discovery.scan_csharp", lambda *_: pytest.fail("inactive mod scanned sources"))
    result = enrich_dotnet_test_discovery(captured, tmp_path, rules())
    assert entries(result) == []
    assert result["metadata"]["testDiscovery"]["dotnet"]["status"] == "disabled"
    assert all(not node.get("testFacts") for node in result["nodes"])


def test_xunit_skip_inline_arguments_and_opaque_member_data(tmp_path):
    graph = project(tmp_path, {"Specs.cs": '''using Xunit;
class Specs {
 [Theory(Skip="legacy", DisplayName="Случай с данными")]
 [InlineData("a,b", 2, typeof(int))]
 [MemberData(nameof(Rows))]
 [Trait("Area", "Assets")]
 void Case(string input, int number, object type) {}
 static object Rows() { System.IO.File.WriteAllText("must-not-run", "bad"); return null; }
}'''})
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit"))
    entry, = entries(result)
    assert entry["label"] == "Случай с данными" and entry["declaredStatus"] == "disabled"
    assert "legacy" in entry["disabledReason"]
    inline, provider = entry["dataSources"]
    assert inline["status"] == "declared" and inline["declaredRowCount"] == 1
    assert [item["expression"] for item in inline["rows"][0]["arguments"]] == ['"a,b"', '2', 'typeof(int)']
    assert provider["status"] == "unresolved" and provider["nodeIds"] == []
    assert "nameof(Rows)" in provider["expression"]
    assert entry["tags"] == ["Area", "Assets"]
    assert not (tmp_path / "must-not-run").exists()


def test_nunit_type_skip_lexical_setup_and_parameter_values_keep_scopes(tmp_path):
    graph = project(tmp_path, {"Specs.cs": '''using NUnit.Framework;
 [Ignore("maintenance")]
 class Specs {
  [SetUp] void Prepare() {}
  [TearDown] void Cleanup() {}
  [TestCase(1, TestName="Первый вариант")]
  [TestCase(2)]
  [Category("Limits")]
  void Case([Values(1, 2)] int number) {}
 }'''})
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules("nunit"))
    entry, = entries(result)
    assert entry["label"] == "Case" and entry["declaredStatus"] == "disabled"
    assert {item["scope"] for item in entry["annotations"]} == {"declaration", "type", "parameter"}
    assert all(item["ownerNodeId"] != entry["handlerNodeIds"][0] for item in entry["annotations"] if item["scope"] != "declaration")
    assert [item["label"] for item in entry["fixtures"]] == ["Prepare", "Cleanup"]
    assert all(item["effectiveOrder"] == "unknown" and not item["inherited"] for item in entry["fixtures"])
    values, = [item for item in entry["dataSources"] if item["kind"] == "parameter-values"]
    assert values["declaredValueCount"] == 2 and values["parameterName"] == "number"
    assert len([item for item in entry["dataSources"] if item["kind"] == "inline-data"]) == 2
    assert entry["dataSources"][0]["rows"][0]["label"] == "Первый вариант"


def test_mstest_rows_ignore_and_dynamic_data_are_declared_not_executed(tmp_path):
    graph = project(tmp_path, {"Specs.cs": '''using Microsoft.VisualStudio.TestTools.UnitTesting;
 [TestClass] class Specs {
  [TestInitialize] void Prepare() {}
  [DataTestMethod, Ignore("refactoring")]
  [DataRow(1, "first")]
  [DynamicData(nameof(Rows))]
  void Case(int number, string text) {}
  static object Rows() => throw new System.Exception("must not execute");
 }'''})
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules("mstest"))
    entry, = entries(result)
    assert entry["declaredStatus"] == "disabled" and entry["runtimeStatus"] == "unknown"
    assert [item["status"] for item in entry["dataSources"]] == ["declared", "unresolved"]
    assert [item["label"] for item in entry["fixtures"]] == ["Prepare"]


def test_one_skipped_nunit_row_does_not_disable_the_whole_parameterized_method(tmp_path):
    graph = project(tmp_path, {"Specs.cs": '''using NUnit.Framework;
class Specs { [TestCase(1, Ignore="legacy row")][TestCase(2)] void Case(int number) {} }'''})
    entry, = entries(enrich_dotnet_test_discovery(graph, tmp_path, rules("nunit")))
    assert entry["declaredStatus"] == "unknown"
    assert "individual parameter rows" in entry["disabledReason"]
    assert [item["rows"][0]["declaredStatus"] for item in entry["dataSources"]] == ["disabled", "enabled"]


def test_fully_qualified_and_alias_attributes_work_but_custom_names_do_not(tmp_path):
    graph = project(tmp_path, {"Specs.cs": '''using NUnit.Framework;
using Case = NUnit.Framework.TestAttribute;
using Fake = Other.TestAttribute;
class TestAttribute : System.Attribute {}
class Specs {
 [Test] void Local() {}
 [Other.Test] void Foreign() {}
 [Fake] void AliasedForeign() {}
 [Case] void AliasedReal() {}
 [global::NUnit.Framework.Test] void Qualified() {}
}'''})
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules("nunit"))
    assert {item["label"] for item in entries(result)} == {"AliasedReal", "Qualified"}


def test_unimported_bare_framework_name_stays_a_candidate_not_an_entry(tmp_path):
    graph = project(tmp_path, {"Specs.cs": 'class Specs { [Fact] void Unknown() {} }'})
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit"))
    assert entries(result) == []


def test_imports_do_not_escape_sibling_namespaces_and_local_shadows_are_rejected(tmp_path):
    graph = project(tmp_path, {
        "Specs.cs": '''namespace Other { using Xunit; class Dummy {} }
namespace Checks { class Specs { [Fact] void NotImported() {} } }
namespace Active { using Xunit; class Specs { [Fact] void Imported() {} } }''',
        "Custom.cs": '''namespace Active { class FactAttribute : System.Attribute {} }''',
    })
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit"))
    assert entries(result) == []


def test_captured_global_usings_and_namespace_aliases_are_supported(tmp_path):
    graph = project(tmp_path, {
        "Specs.cs": '''class Specs { [Fact] void Global() {} }
namespace Local { using Framework = Xunit; class Specs { [Framework.Theory] void Alias() {} } }''',
        "Usings.cs": 'global using Xunit;',
    })
    graph["nodes"].append({"id": "global-using-file", "file": "Usings.cs", "kind": "file", "label": "Usings.cs"})
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit"))
    assert {item["label"] for item in entries(result)} == {"Global", "Alias"}


def test_enabled_mod_rules_control_entry_role_and_standalone_nunit_source(tmp_path):
    graph = project(tmp_path, {"Specs.cs": '''using NUnit.Framework;
class Specs { [TestCaseSource(nameof(Rows))] void Case(int number) {} static object Rows() => null; }'''})
    enabled = rules("nunit")
    for rule in enabled["testFrameworks"][0]["annotationRules"]:
        if rule["name"] == "NUnit.Framework.TestCaseSourceAttribute":
            rule["role"] = "test"
    result = enrich_dotnet_test_discovery(graph, tmp_path, enabled)
    entry, = entries(result)
    assert entry["label"] == "Case" and entry["dataSources"][0]["status"] == "unresolved"
    enabled["testFrameworks"][0]["annotationRules"] = []
    assert entries(enrich_dotnet_test_discovery(graph, tmp_path, enabled)) == []


def test_local_function_test_attributes_are_not_runner_entries(tmp_path):
    graph = project(tmp_path, {"Specs.cs": '''using Xunit;
class Specs { void Outer() { [Fact] void NotARunnerCase() {} } }'''})
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit"))
    assert entries(result) == []


def test_foreign_inline_attribute_does_not_become_a_dataset(tmp_path):
    graph = project(tmp_path, {"Specs.cs": '''using Xunit;
class InlineDataAttribute : System.Attribute { public InlineDataAttribute(int value) {} }
class Specs { [Theory, InlineData(3)] void Case(int value) {} }
'''})
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit"))
    entry, = entries(result)
    assert entry["dataSources"] == []


def test_no_owner_and_ambiguous_raw_symbol_never_fabricate_links(tmp_path):
    graph = project(tmp_path, {"Specs.cs": 'using Xunit; class Specs { [Fact] void Case() {} }'}, captured=False)
    raw = next(node for node in graph["nodes"] if node["kind"] == "method")
    graph["nodes"].append({**raw, "id": "duplicate-raw"})
    assert entries(enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit"))) == []
    graph["nodes"].remove(raw)
    graph["nodes"][-1].pop("line")
    assert entries(enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit"))) == []


def test_repeat_capture_is_idempotent_and_keeps_java_entries(tmp_path):
    graph = project(tmp_path, {"Specs.cs": 'using Xunit; class Specs { [Fact] void Case() {} }'})
    java = {"id": "java-entry", "kind": "test", "framework": "junit5", "language": "java",
            "handlerNodeIds": ["java-raw"], "label": "Java case"}
    graph["metadata"]["testDiscovery"] = {"version": "static-test-discovery-v1", "status": "complete",
        "entries": [java], "warnings": [], "coverage": {"testEntries": 1, "unresolvedDeclarations": 0}}
    first = enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit"))
    second = enrich_dotnet_test_discovery(first, tmp_path, rules("xunit"))
    assert first == second
    assert entries(first)[0] == java and len(entries(first)) == 2


def test_symlink_and_budget_boundaries_are_partial_not_empty_success(tmp_path, monkeypatch):
    target = tmp_path / "hidden.txt"
    target.write_text('using Xunit; class Specs { [Fact] void Case() {} }')
    (tmp_path / "Alias.cs").symlink_to(target)
    graph = {"nodes": [{"id": "raw", "kind": "method", "label": ".Case()", "file": "Alias.cs", "line": 1}], "edges": []}
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit"))
    assert entries(result) == [] and result["metadata"]["testDiscovery"]["status"] == "partial"
    graph = project(tmp_path, {"Specs.cs": 'using Xunit; class Specs { [Fact] void Case() {} }'})
    monkeypatch.setitem(dotnet_test_discovery.DEFAULT_LIMITS, "maxSourceFiles", 0)
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit"))
    assert result["metadata"]["testDiscovery"]["dotnet"]["coverage"]["files"] == 0
    assert result["metadata"]["testDiscovery"]["status"] == "partial"


def test_entry_budget_and_foreign_framework_fixtures_are_explicit(tmp_path, monkeypatch):
    graph = project(tmp_path, {"Specs.cs": '''using Xunit; using NUnit.Framework;
class Specs { [SetUp] void NUnitOnly() {} [Fact] void First() {} [Fact] void Second() {} }'''})
    monkeypatch.setitem(dotnet_test_discovery.DEFAULT_LIMITS, "maxTests", 1)
    result = enrich_dotnet_test_discovery(graph, tmp_path, rules("xunit", "nunit"))
    entry, = entries(result)
    assert entry["fixtures"] == []
    assert result["metadata"]["testDiscovery"]["status"] == "partial"
    assert any("budget" in message for message in result["metadata"]["testDiscovery"]["warnings"])
