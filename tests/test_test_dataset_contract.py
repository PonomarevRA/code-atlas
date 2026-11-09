"""Declared Java dataset facts remain exact, bounded and independent of runtime execution."""
import pytest

from atlas.test_discovery import enrich_test_discovery
from tests.test_java_test_discovery import entries, project, rules


def configured_rules():
    """Use the reviewed JSON-selected JUnit source strategies in an isolated fixture."""
    configured = rules()
    configured["testFrameworks"][0]["annotationRules"].append({
        "name": "org.junit.jupiter.params.provider.EnumSource", "role": "parameter-source",
        "datasetStrategy": "junit-enum-constants-v1"})
    return configured


def capture(tmp_path, body, support="", imports=""):
    """Create test declarations with stable raw identities; analyzed providers are never run."""
    source = f'''package p;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;
import java.util.Map;
import java.util.stream.Stream;
{imports}
class Case {{ {body} }}
{support}
'''
    graph = project(tmp_path, {"Case.java": source})
    return enrich_test_discovery(graph, tmp_path, configured_rules())


def test_method_rows_named_map_fields_and_source_edges(tmp_path):
    result = capture(tmp_path, '''
 @ParameterizedTest @MethodSource("rows") void scenario(String scenarioName, Map<String,String> queryParams) {}
 static Stream<Arguments> rows() { return Stream.of(
   Arguments.of("missing", Map.of("Period", "All")),
   Arguments.of("invalid", Map.of("ClientId", "not-a-uuid", "Period", "All")),
   Arguments.of("empty", Map.of("ClientId", "", "Period", "All"))); }
''')
    entry, = entries(result)
    source, = entry["dataSources"]
    assert [parameter["name"] for parameter in entry["parameters"]] == ["scenarioName", "queryParams"]
    assert entry["parameters"][1]["type"] == "Map<String,String>"
    assert all(parameter["role"] == "indexed" for parameter in entry["parameters"])
    fields = []
    for row in source["rows"]:
        value = row["bindings"][1]["value"]
        assert value["kind"] == "map" and value["completeness"] == "complete"
        fields.append({item["key"]["value"]: item["value"]["value"] for item in value["entries"]})
        assert row["sourceLocation"]["nodeId"] == source["nodeIds"][0]
        assert row["sourceHash"] and row["id"]
    assert "ClientId" not in fields[0]
    assert fields[1]["ClientId"] == "not-a-uuid"
    assert fields[2]["ClientId"] == ""
    relation, = [edge for edge in result["edges"] if edge["relation"] == "uses_test_data"]
    assert relation["source"] == entry["handlerNodeIds"][0] and relation["target"] == source["nodeIds"][0]
    assert relation["runtimeStatus"] == "unknown" and relation["sourceHash"]
    assert relation["dataSourceId"] == source["id"]
    repeated = enrich_test_discovery(result, tmp_path, configured_rules())
    assert repeated == result


def test_values_bind_only_indexed_prefix_not_injected_context(tmp_path):
    entry, = entries(capture(tmp_path, '''
 @ParameterizedTest @ValueSource(ints={1,10,30})
 void scenario(int daysBefore, Steps steps, @custom.DB Object db) {}
'''))
    assert [item["role"] for item in entry["parameters"]] == ["indexed", "context", "context"]
    assert [item["name"] for item in entry["context"]] == ["steps", "db"]
    rows = entry["dataSources"][0]["rows"]
    assert [row["bindings"][0]["value"]["value"] for row in rows] == ["1", "10", "30"]
    assert all(len(row["bindings"]) == 1 for row in rows)
    assert all(item["status"] == "unresolved" for item in entry["context"])


def test_csv_currency_columns_exclude_context(tmp_path):
    entry, = entries(capture(tmp_path, '''
 @ParameterizedTest @CsvSource({"RUB, RUB", "USD, RUB", "RUB, EUR"})
 void scenario(String faceCurrency, String tradeCurrency, Steps steps, @custom.DB Object db) {}
'''))
    rows = entry["dataSources"][0]["rows"]
    assert [[binding["name"] for binding in row["bindings"]] for row in rows] == [["faceCurrency", "tradeCurrency"]] * 3
    assert [[binding["value"]["value"] for binding in row["bindings"]] for row in rows] == [["RUB", "RUB"], ["USD", "RUB"], ["RUB", "EUR"]]
    assert [item["role"] for item in entry["parameters"]] == ["indexed", "indexed", "context", "context"]


def test_csv_quotes_empty_null_and_literal_null_are_distinct(tmp_path):
    entry, = entries(capture(tmp_path, '''
 @ParameterizedTest @CsvSource({"'a,b', '', , null"})
 void scenario(String comma, String empty, String absent, String literalNull) {}
'''))
    cells = [item["value"] for item in entry["dataSources"][0]["rows"][0]["bindings"]]
    assert [(item["kind"], item["value"]) for item in cells] == [
        ("string", "a,b"), ("string", ""), ("null", None), ("string", "null")]


def test_csv_explicit_options_and_custom_null(tmp_path):
    entry, = entries(capture(tmp_path, '''
 @ParameterizedTest @CsvSource(value={"'a||b'||NIL||''"}, delimiterString="||", nullValues={"NIL"}, emptyValue="EMPTY")
 void scenario(String one, String two, String three) {}
'''))
    cells = entry["dataSources"][0]["rows"][0]["valueFacts"]
    assert [item["value"] for item in cells] == ["a||b", None, "EMPTY"]


@pytest.mark.parametrize("annotation", [
    '@CsvSource(value={"a,b"}, useHeadersInDisplayName=true)',
    '@CsvSource(value={"a,b"}, delimiter=DELIMITER)',
    '@CsvSource({"a,\'unterminated"})',
    '@CsvSource(value={DYNAMIC})',
    '@CsvSource(value={"\'\'"}, emptyValue="NIL", nullValues={"NIL"})',
])
def test_unsupported_csv_semantics_fail_closed(tmp_path, annotation):
    entry, = entries(capture(tmp_path, f'@ParameterizedTest {annotation} void scenario(String one, String two) {{}}'))
    source, = entry["dataSources"]
    assert source["status"] == "unresolved" and source["reason"]
    assert not source.get("rows")


def test_enum_constants_and_include_exclude_preserve_source(tmp_path):
    result = capture(tmp_path, '''
 @ParameterizedTest @EnumSource(value=Period.class, names={"Day", "All"}, mode=EnumSource.Mode.EXCLUDE)
 void scenario(Period period) {}
''', 'enum Period { Day, Week, Month, All }')
    source, = entries(result)[0]["dataSources"]
    assert source["status"] == "declared" and source["declaredRowCount"] == 2
    assert [row["expression"] for row in source["rows"]] == ["Period.Week", "Period.Month"]
    assert len(source["nodeIds"]) == 1
    assert all(row["sourceLocation"]["nodeId"] == source["nodeIds"][0] for row in source["rows"])


def test_enum_unknown_regex_filter_is_not_guessed(tmp_path):
    entry, = entries(capture(tmp_path, '''
 @ParameterizedTest @EnumSource(value=Period.class, names={".*"}, mode=EnumSource.Mode.MATCH_ALL)
 void scenario(Period period) {}
''', 'enum Period { Day, All }'))
    source, = entry["dataSources"]
    assert source["status"] == "unresolved" and "regex" in source["reason"]


@pytest.mark.parametrize("shadow", ["class Stream {}", "class Arguments {}"])
def test_shadowed_provider_constructors_do_not_invent_rows(tmp_path, shadow):
    entry, = entries(capture(tmp_path, '''
 @ParameterizedTest @MethodSource("rows") void scenario(String value) {}
 static Object rows() { return Stream.of(Arguments.of("x")); }
''', shadow))
    source, = entry["dataSources"]
    assert source["status"] == "unresolved" and not source.get("rows")


def test_shadowed_map_is_expression_not_literal_structure(tmp_path):
    entry, = entries(capture(tmp_path, '''
 @ParameterizedTest @MethodSource("rows") void scenario(Object data) {}
 static Object rows() { return Stream.of(Arguments.of(Map.of("key", "value"))); }
''', 'class Map {}'))
    value = entry["dataSources"][0]["rows"][0]["bindings"][0]["value"]
    assert value["kind"] == "expression" and "entries" not in value


def test_aggregator_and_converter_do_not_claim_runtime_binding(tmp_path):
    entry, = entries(capture(tmp_path, '''
 @ParameterizedTest @CsvSource({"a,b"})
 void scenario(@AggregateWith(Mapper.class) Object aggregate) {}
''', imports='import org.junit.jupiter.params.aggregator.AggregateWith;'))
    source, = entry["dataSources"]
    assert entry["parameters"][0]["role"] == "aggregator"
    assert source["bindingStatus"] == "unresolved" and source["rows"][0]["bindings"] == []
    second = tmp_path / "converted"
    second.mkdir()
    converted, = entries(capture(second, '''
 @ParameterizedTest @ValueSource(strings={"abc"}) void scenario(@ConvertWith(Converter.class) Object input) {}
''', imports='import org.junit.jupiter.params.converter.ConvertWith;'))
    assert converted["dataSources"][0]["rows"][0]["bindings"][0]["resolution"] == "unknown"


def test_large_sources_keep_captured_bindings_and_explicit_truncation(tmp_path):
    values = ",".join(str(index) for index in range(45))
    entry, = entries(capture(tmp_path, f'@ParameterizedTest @ValueSource(ints={{{values}}}) void scenario(int value) {{}}'))
    source, = entry["dataSources"]
    assert source["declaredRowCount"] == 45 and source["rowsTruncated"]
    assert len(source["rows"]) == 30 and source["completeness"] == "partial"
    assert all(len(row["bindings"]) == 1 for row in source["rows"])


def test_one_line_nested_and_adjacent_classes_have_exact_containers(tmp_path):
    graph = project(tmp_path, {"Case.java": '''package p; import org.junit.jupiter.api.Test;
class Outer { class Inner { @Test void nested() {} } @Test void outer() {} } class Other { @Test void adjacent() {} }
'''})
    result = enrich_test_discovery(graph, tmp_path, configured_rules())
    names = {node["id"]: node["label"] for node in graph["nodes"]}
    assert {item["label"]: names[item["containerNodeId"]] for item in entries(result)} == {
        "nested": "Inner", "outer": "Outer", "adjacent": "Other"}
    disabled = enrich_test_discovery(result, tmp_path, {"testFrameworks": []})
    assert not any(node.get("testContainerFacts") for node in disabled["nodes"])
    assert not any(edge["relation"] == "uses_test_data" for edge in disabled["edges"])


@pytest.mark.parametrize("shadow", ["static class Stream {}", "static class Arguments {}"])
def test_member_type_shadow_does_not_impersonate_library_provider(tmp_path, shadow):
    entry, = entries(capture(tmp_path, f'''
 {shadow}
 @ParameterizedTest @MethodSource("rows") void scenario(String value) {{}}
 static Object rows() {{ return Stream.of(Arguments.of("x")); }}
'''))
    assert entry["dataSources"][0]["status"] == "unresolved"


@pytest.mark.parametrize("name", ["Stream", "Arguments"])
def test_value_receiver_shadow_does_not_impersonate_library_provider(tmp_path, name):
    entry, = entries(capture(tmp_path, f'''
 static Custom {name};
 @ParameterizedTest @MethodSource("rows") void scenario(String value) {{}}
 static Object rows() {{ return Stream.of(Arguments.of("x")); }}
'''))
    assert entry["dataSources"][0]["status"] == "unresolved"


def test_member_map_and_value_map_shadow_stay_expressions(tmp_path):
    entry, = entries(capture(tmp_path, '''
 static class Map {}
 @ParameterizedTest @MethodSource("rows") void scenario(Object value) {}
 static Object rows() { return Stream.of(Arguments.of(Map.of("key", "value"))); }
'''))
    assert entry["dataSources"][0]["rows"][0]["valueFacts"][0]["kind"] == "expression"


def test_long_values_are_explicitly_truncated_not_claimed_literals(tmp_path):
    value = "x" * 700
    entry, = entries(capture(tmp_path, f'@ParameterizedTest @ValueSource(strings={{"{value}"}}) void scenario(String value) {{}}'))
    source, = entry["dataSources"]
    row, = source["rows"]
    assert row["truncated"] and row["expressionTruncated"] and source["completeness"] == "partial"
    assert row["bindings"][0]["resolution"] == "unknown"


def test_duplicate_map_keys_are_not_published_as_literal_map(tmp_path):
    entry, = entries(capture(tmp_path, '''
 @ParameterizedTest @MethodSource("rows") void scenario(Object value) {}
 static Object rows() { return Stream.of(Arguments.of(Map.of("key", "one", "key", "two"))); }
'''))
    value = entry["dataSources"][0]["rows"][0]["valueFacts"][0]
    assert value["kind"] == "expression" and "duplicate" in value["reason"]


def test_inherited_method_provider_is_unresolved_without_guessed_graph_edge(tmp_path):
    entry_graph = capture(tmp_path, '''
 @ParameterizedTest @MethodSource("inheritedRows") void scenario(String value) {}
''', 'class Base { static Object inheritedRows() { return null; } }')
    entry, = entries(entry_graph)
    assert entry["dataSources"][0]["nodeIds"] == []
    assert not any(edge["relation"] == "uses_test_data" for edge in entry_graph["edges"])


def test_lifecycle_has_phase_scope_and_explicit_unknown_order(tmp_path):
    graph = project(tmp_path, {"Case.java": '''package p; import org.junit.jupiter.api.*;
class Base { @BeforeAll static void before() {} }
class Case extends Base { @Test void scenario() {} @AfterEach void cleanup() {} }
'''})
    entry, = entries(enrich_test_discovery(graph, tmp_path, configured_rules()))
    assert {(item["phase"], item["scope"], item["inherited"]) for item in entry["fixtures"]} == {
        ("before-all", "inherited-type", True), ("after-each", "type", False)}
    assert all(item["effectiveOrder"] == "unknown" and item["sourceHash"] for item in entry["fixtures"])


def test_composed_aggregator_and_converter_keep_runtime_values_unknown(tmp_path):
    entry, = entries(capture(tmp_path, '''
 @ParameterizedTest @ValueSource(strings={"a"}) void scenario(@CsvToRecord Object record) {}
''', '''@AggregateWith(Mapper.class) @interface CsvToRecord {}''',
        imports='import org.junit.jupiter.params.aggregator.AggregateWith;'))
    assert entry["parameters"][0]["role"] == "aggregator"
    assert entry["dataSources"][0]["rows"][0]["bindings"] == []
    subdir = tmp_path / "converter"
    subdir.mkdir()
    converted, = entries(capture(subdir, '''
 @ParameterizedTest @ValueSource(strings={"a"}) void scenario(@ToDomain Object record) {}
''', '''@ConvertWith(Converter.class) @interface ToDomain {}''',
        imports='import org.junit.jupiter.params.converter.ConvertWith;'))
    assert converted["dataSources"][0]["rows"][0]["bindings"][0]["resolution"] == "unknown"


def test_java_receiver_is_not_an_indexed_argument_or_injected_context(tmp_path):
    """JUnit rows bind actual value parameters, excluding Java's explicit receiver."""
    graph = project(tmp_path, {"Case.java": '''
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
class Case {
 @ParameterizedTest @ValueSource(strings={"x"})
 void scenario(Case this, String input, Object injected) {}
}'''} )
    captured = enrich_test_discovery(graph, tmp_path, rules())
    entry, = entries(captured)
    assert [(item['name'], item['position'], item['role']) for item in entry['parameters']] == [
        ('input', 1, 'indexed'), ('injected', 2, 'context')]
    binding, = entry['dataSources'][0]['rows'][0]['bindings']
    assert binding['name'] == 'input' and binding['index'] == 0 and binding['value']['value'] == 'x'
