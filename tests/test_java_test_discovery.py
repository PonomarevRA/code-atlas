"""Java test navigation uses source declarations, never an executed project or provider."""
from copy import deepcopy
import json

import pytest

from atlas.java_annotations import METHODS, TYPES, parse_java
from atlas.test_discovery import enrich_test_discovery


def rules():
    """Supply the same exact-name rules as optional analysis mods, without host defaults."""
    annotations = [
        ("org.junit.jupiter.api.Test", "test"),
        ("org.junit.jupiter.params.ParameterizedTest", "test"),
        ("org.junit.jupiter.api.DisplayName", "metadata"),
        ("org.junit.jupiter.api.Tag", "metadata"),
        ("org.junit.jupiter.api.Disabled", "disabled"),
        ("org.junit.jupiter.api.BeforeAll", "lifecycle"),
        ("org.junit.jupiter.api.AfterEach", "lifecycle"),
        ("org.junit.jupiter.params.provider.MethodSource", "parameter-source"),
        ("org.junit.jupiter.params.provider.CsvSource", "parameter-source"),
        ("org.junit.jupiter.params.provider.ValueSource", "parameter-source"),
    ]
    return {"fingerprint": "rules-v1", "testFrameworks": [
        {"id": "junit5", "language": "java", "modId": "test-junit5", "supportsComposed": True,
         "annotationRules": [{"name": name, "role": role, **({"datasetStrategy": {"MethodSource": "junit-literal-provider-v1", "CsvSource": "junit-positional-csv-v1", "ValueSource": "junit-single-values-v1"}.get(name.rsplit(".", 1)[-1], "unresolved-v1")} if role == "parameter-source" else {})} for name, role in annotations]},
        {"id": "allure", "language": "java", "modId": "test-allure", "supportsComposed": True,
         "annotationRules": [{"name": "io.qameta.allure.Step", "role": "step"},
                             {"name": "io.qameta.allure.Feature", "role": "metadata"}]},
    ]}


def project(tmp_path, sources):
    """Build a raw fixture graph from exact declarations; analysis reads only these paths."""
    nodes, edges = [], []
    for filename, source in sources.items():
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        unit = parse_java(source.encode(), filename, set())
        for index, item in enumerate(unit.declarations):
            nodes.append({"id": f"raw:{filename}:{index}", "label": item["name"] + ("()" if item["kind"] in METHODS else ""),
                          "kind": "method" if item["kind"] in METHODS else "class",
                          "file": filename, "line": item["_ast"].child_by_field_name("name").start_point.row + 1,
                          "codeProjectId": "project-fixture", "codeProjectFacts": {"kind": "package-area"}})
    if len(nodes) >= 2:
        edges.append({"source": nodes[0]["id"], "target": nodes[1]["id"], "relation": "contains"})
    return {"nodes": nodes, "edges": edges, "metadata": {"unrelated": "preserved"}}


def entries(graph):
    """Read the additive ingress inventory rather than treating every method as a test."""
    return graph["metadata"]["testDiscovery"]["entries"]


def test_csv_cards_parameter_scope_and_comment_disabled_are_honest(tmp_path):
    graph = project(tmp_path, {"src/test/java/p/Sorting.java": '''package p;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import custom.Database.DB;
class Sorting {
  //@Disabled("old issue")
  @DisplayName("Сортировка портфеля")
  @ParameterizedTest(name="sortBy: {0}, order: {1}")
  @CsvSource({"securityName, Asc", "value, Asc", "securityName, Desc", "value, Desc"})
  void sorting(String by, String order, @DB(HISTORIC_PORTFOLIO_DB) Object db) {}
}'''})
    original = deepcopy(graph)
    result = enrich_test_discovery(graph, tmp_path, rules())
    entry, = entries(result)
    assert graph == original
    assert entry["label"] == "Сортировка портфеля"
    assert entry["declaredStatus"] == "enabled" and entry["runtimeStatus"] == "unknown"
    assert entry["dataSources"][0]["declaredRowCount"] == 4
    assert entry["dataSources"][0]["rows"][0]["expression"] == '"securityName, Asc"'
    assert not any(item["name"] == "Disabled" for item in entry["annotations"])
    parameter, = [item for item in entry["annotations"] if item["scope"] == "parameter"]
    assert parameter["qualifiedName"] == "custom.Database.DB" and parameter["parameterName"] == "db"
    attributes = [node for node in result["nodes"] if node["kind"] == "attribute"]
    db, = [node for node in attributes if node["name"] == "DB"]
    assert db["ownerNodeId"] is None and db["declarationNodeId"] == entry["handlerNodeIds"][0]
    assert db["owner"]["position"] == 3
    assert all(item["codeProjectId"] == "project-fixture" for item in attributes)
    assert result["edges"][:len(graph["edges"])] == graph["edges"]
    assert result["metadata"]["unrelated"] == "preserved"
    json.dumps(result)


def test_external_method_source_three_declared_rows_and_raw_link(tmp_path):
    graph = project(tmp_path, {
        "checks/Validation.java": '''package p;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
class Validation {
 @DisplayName("Проверка невалидного ClientId")
 @ParameterizedTest(name="{0}")
 @MethodSource("p.Data#invalidClientIdParams")
 void validate(String scenario, Object request) {}
}''',
        "support/Data.java": '''package p;
import java.util.stream.Stream;
import org.junit.jupiter.params.provider.Arguments;
class Data {
 static Stream<Arguments> invalidClientIdParams() {
  return Stream.of(
   Arguments.of("missing", Map.of("period", "All")),
   Arguments.of("invalid", Map.of("clientId", "not-a-uuid")),
   Arguments.of("empty", Map.of("clientId", "")));
 }
}''',
    })
    result = enrich_test_discovery(graph, tmp_path, rules())
    source, = entries(result)[0]["dataSources"]
    assert source["status"] == "declared" and source["declaredRowCount"] == 3
    assert source["file"] == "support/Data.java" and len(source["nodeIds"]) == 1
    provider = next(node for node in result["nodes"] if node["id"] == source["nodeIds"][0])
    assert provider["label"] == "invalidClientIdParams()"
    assert 'not-a-uuid' in source["rows"][1]["expression"]


def test_composed_tags_class_disabled_and_inherited_fixture_resources(tmp_path):
    graph = project(tmp_path, {
        "Base.java": '''package p;
import org.junit.jupiter.api.BeforeAll;
class Base {
 @BeforeAll static void prepare() { importFromSqlFile(db, "sql/securities_all.sql"); }
}''',
        "Smoke.java": '''package p;
import org.junit.jupiter.api.Tag;
@Tag("SmokeRegression") @interface Smoke {}
''',
        "Case.java": '''package p;
import org.junit.jupiter.api.*;
import io.qameta.allure.Step;
@Disabled("feature refactoring") @Smoke
class Case extends Base {
 @Test void scenario() {}
 @Step("Call the API") void call() {}
}''',
    })
    result = enrich_test_discovery(graph, tmp_path, rules())
    entry, = entries(result)
    assert entry["declaredStatus"] == "disabled"
    assert entry["disabledEvidence"][0]["scope"] == "type"
    assert entry["tags"] == ["SmokeRegression"]
    fixture, = entry["fixtures"]
    assert fixture["inherited"] and fixture["effectiveOrder"] == "unknown"
    assert fixture["resources"][0]["label"] == "sql/securities_all.sql"
    assert fixture["nodeIds"] and fixture["status"] == "declared"
    assert not any(item["label"] == "call" for item in entries(result))


def test_custom_same_name_and_step_cannot_impersonate_junit(tmp_path):
    graph = project(tmp_path, {"Case.java": '''package p;
import org.junit.jupiter.api.*;
import io.qameta.allure.Step;
@interface Test {}
class Case {
 @Test void fake() {}
 @Step("not a test") void helper() {}
 @org.junit.jupiter.api.Test void real() {}
}'''})
    result = enrich_test_discovery(graph, tmp_path, rules())
    assert [item["label"] for item in entries(result)] == ["real"]


def test_ambiguous_overload_and_dynamic_provider_remain_unresolved(tmp_path):
    graph = project(tmp_path, {"Case.java": '''package p;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
class Case {
 @ParameterizedTest @MethodSource("rows") void scenario(String input) {}
 static Object rows() { return fetchFromDatabase(); }
 static Object rows(int count) { return null; }
}'''})
    result = enrich_test_discovery(graph, tmp_path, rules())
    source, = entries(result)[0]["dataSources"]
    assert source["status"] == "unresolved" and source["nodeIds"] == []
    assert "overloaded" in source["reason"]


def test_repeat_capture_is_idempotent_and_disabling_mod_drops_test_entries(tmp_path):
    graph = project(tmp_path, {"Case.java": '''package p;
import org.junit.jupiter.api.Test;
class Case { @Test void scenario() {} }'''})
    first = enrich_test_discovery(graph, tmp_path, rules())
    second = enrich_test_discovery(first, tmp_path, rules())
    assert second == first
    disabled = enrich_test_discovery(first, tmp_path, {"fingerprint": "none", "testFrameworks": []})
    assert not entries(disabled)
    assert disabled["metadata"]["testDiscovery"]["status"] == "disabled"
    assert not any(node.get("testFacts") for node in disabled["nodes"])
    assert not any(node.get("_origin") == "java-annotations" for node in disabled["nodes"])


def test_meta_annotation_cycle_and_unreadable_source_do_not_fabricate_entries(tmp_path):
    graph = project(tmp_path, {"Case.java": '''package p;
@B @interface A {} @A @interface B {}
class Case { @A void scenario() {} }'''})
    graph["nodes"].append({"id": "outside", "file": "../Outside.java", "label": "run()", "line": 1})
    result = enrich_test_discovery(graph, tmp_path, rules())
    assert entries(result) == []
    assert result["metadata"]["testDiscovery"]["status"] == "partial"
    assert result["metadata"]["testDiscovery"]["warnings"]


def test_raw_overload_collapse_fails_closed(tmp_path):
    graph = project(tmp_path, {"Case.java": '''package p; import org.junit.jupiter.api.Test;
class Case { @Test void same() {} @Test void same(int value) {} }
'''})
    graph["nodes"] = [node for node in graph["nodes"] if node["id"] != "raw:Case.java:2"]
    result = enrich_test_discovery(graph, tmp_path, rules())
    assert not entries(result)
    assert result["metadata"]["testDiscovery"]["coverage"]["unresolvedDeclarations"] >= 2


def test_symlink_source_is_not_read(tmp_path):
    other = tmp_path / "private-source"
    other.write_text("class Hidden {}")
    (tmp_path / "Alias.java").symlink_to(other)
    graph = {"nodes": [{"id": "alias", "label": "Hidden", "file": "Alias.java", "line": 1}], "edges": []}
    result = enrich_test_discovery(graph, tmp_path, rules())
    assert not entries(result)
    assert result["metadata"]["testDiscovery"]["status"] == "partial"


def test_source_file_budget_is_reported(tmp_path, monkeypatch):
    graph = project(tmp_path, {"Case.java": "class Case {}"})
    monkeypatch.setattr("atlas.test_discovery.MAX_FILES", 0)
    result = enrich_test_discovery(graph, tmp_path, rules())
    assert result["metadata"]["testDiscovery"]["coverage"]["files"] == 0
    assert result["metadata"]["testDiscovery"]["status"] == "partial"


def test_base_disabled_is_context_not_inherited_disabled_verdict(tmp_path):
    graph = project(tmp_path, {"Case.java": '''package p;
import org.junit.jupiter.api.*;
@Disabled("base disabled") class Base {}
class Child extends Base { @Test void scenario() {} }
'''})
    entry, = entries(enrich_test_discovery(graph, tmp_path, rules()))
    assert entry["declaredStatus"] == "enabled"
    assert any(item["scope"] == "inherited-type" and item["name"] == "Disabled" for item in entry["annotations"])


def test_parameterized_name_template_does_not_replace_display_name(tmp_path):
    configured = rules()
    parameterized = next(item for item in configured["testFrameworks"][0]["annotationRules"] if item["name"].endswith("ParameterizedTest"))
    parameterized["displayArgument"] = "name"
    graph = project(tmp_path, {"Case.java": '''package p;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.params.ParameterizedTest;
class Case {
 @DisplayName("Проверка сортировки") @ParameterizedTest(name="case {0}") void scenario(String name) {}
}'''})
    entry, = entries(enrich_test_discovery(graph, tmp_path, configured))
    assert entry["label"] == "Проверка сортировки" and entry["displayTemplate"] == "case {0}"


def test_testng_enabled_boolean_and_provider_reference_remain_declared(tmp_path):
    configured = {"testFrameworks": [{"id": "testng", "language": "java", "annotationRules": [
        {"name": "org.testng.annotations.Test", "role": "test", "disabledArgument": "enabled",
         "displayArgument": "description", "sourceArgument": "dataProvider"}]}]}
    graph = project(tmp_path, {"Case.java": '''package p;
import org.testng.annotations.Test;
class Case {
 @Test(description="Disabled case", enabled=false, dataProvider="rows") void disabledCase() {}
 @Test(enabled=ENABLE_FLAG) void unknownCase() {}
}'''})
    found = entries(enrich_test_discovery(graph, tmp_path, configured))
    assert found[0]["label"] == "Disabled case" and found[0]["declaredStatus"] == "disabled"
    assert found[0]["dataSources"][0]["label"] == "rows" and found[0]["dataSources"][0]["status"] == "unresolved"
    assert found[1]["declaredStatus"] == "unknown"


def test_conflicting_explicit_imports_do_not_choose_last_framework(tmp_path):
    graph = project(tmp_path, {"Case.java": '''package p;
import custom.Test;
import org.junit.jupiter.api.Test;
class Case { @Test void scenario() {} }
'''})
    assert not entries(enrich_test_discovery(graph, tmp_path, rules()))


def test_nested_annotation_type_shadows_explicit_junit_import(tmp_path):
    graph = project(tmp_path, {"Sample.java": '''package demo;
import org.junit.jupiter.api.Test;
class Sample {
 @interface Test {}
 @Test void helper() {}
 @org.junit.jupiter.api.Test void real() {}
}'''})
    result = enrich_test_discovery(graph, tmp_path, rules())
    assert [item["label"] for item in entries(result)] == ["real"]
    helper = next(node for node in result["nodes"] if node.get("label") == "helper()")
    annotation = helper["attributeFacts"]["groups"][0]["attributes"][0]
    assert annotation["qualifiedName"] == "demo.Sample.Test" and annotation["resolution"] == "enclosing-type"


def test_inherited_member_annotation_shadow_is_not_guessed_as_import(tmp_path):
    graph = project(tmp_path, {"Case.java": '''package p;
import org.junit.jupiter.api.Test;
class Base { @interface Test {} }
class Child extends Base { @Test void helper() {} }
'''})
    assert not entries(enrich_test_discovery(graph, tmp_path, rules()))


def test_unreadable_source_attempts_consume_the_file_budget(tmp_path, monkeypatch):
    from atlas.source_viewer import SourceError
    attempts = []

    def failed_read(root, path, allowance):
        """Simulate unavailable captured source, without touching an external path."""
        attempts.append(str(path))
        raise SourceError("missing")

    graph = {"nodes": [{"id": name, "label": "Case", "line": 1, "file": name}
                        for name in ("A.java", "B.java")], "edges": []}
    monkeypatch.setattr("atlas.test_discovery.MAX_FILES", 1)
    monkeypatch.setattr("atlas.test_discovery._read_regular_file", failed_read)
    result = enrich_test_discovery(graph, tmp_path, rules())
    assert attempts == ["A.java"]
    assert result["metadata"]["testDiscovery"]["coverage"]["fileAttempts"] == 1
    assert result["metadata"]["testDiscovery"]["status"] == "partial"


def test_invalid_utf_source_still_consumes_bytes_and_warning_budget(tmp_path, monkeypatch):
    payload = b"class \xffInvalid {}"
    graph = {"nodes": [{"id": "bad", "label": "Case", "line": 1, "file": "Bad.java"}], "edges": []}
    monkeypatch.setattr("atlas.test_discovery._read_regular_file", lambda root, path, allowance: payload)
    result = enrich_test_discovery(graph, tmp_path, rules())
    coverage = result["metadata"]["testDiscovery"]["coverage"]
    assert coverage["bytes"] == len(payload) and coverage["fileAttempts"] == 1
    assert result["metadata"]["testDiscovery"]["status"] == "partial"
