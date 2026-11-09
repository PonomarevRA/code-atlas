"""Real enrichment stages keep Java test details off lightweight map/catalog payloads."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from atlas.enrichment_pipeline import enrich_snapshot
from atlas.entrypoints import build_entrypoints
from atlas.java_annotations import METHODS, parse_java
from atlas.map_payload import serialize_graph
from atlas.mod_analysis import active_analysis_rules, assert_analysis_rules_current
from atlas.mod_store import ModStore


@pytest.fixture
def java_test_snapshot(tmp_path):
    """Activate real bundled packages and enrich one tiny Gradle/JUnit/Allure fixture."""
    bundled = Path(__file__).resolve().parents[1] / "mods"
    mods = ModStore(tmp_path / "state", bundled)
    for identifier in ("language-java", "build-gradle", "framework-junit5", "framework-allure"):
        item = next(item for item in mods.catalog()["mods"] if item["id"] == identifier)
        mods.enable(identifier, item["fingerprint"], True)
    rules = active_analysis_rules(mods)
    root = tmp_path / "repository"
    root.mkdir()
    (root / "build.gradle").write_text("plugins { id 'java' }\ntest { useJUnitPlatform() }\n")
    (root / "settings.gradle").write_text("rootProject.name = 'qa-portfolio-fixture'\n")
    sources = {
        "src/test/java/sample/tests/PortfolioTests.java": '''package sample.tests;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import io.qameta.allure.Feature;
import sample.steps.BaseTest;
@Feature("Portfolio validation")
class PortfolioTests extends BaseTest {
 @DisplayName("Проверка невалидного ClientId")
 @ParameterizedTest(name="{0}")
 @MethodSource("sample.test_data.PortfolioRows#invalidClients")
 void validateClient(String name, Object request) { callApi(request); }
}''',
        "src/test/java/sample/test_data/PortfolioRows.java": '''package sample.test_data;
import java.util.stream.Stream;
import org.junit.jupiter.params.provider.Arguments;
class PortfolioRows {
 static Stream<Arguments> invalidClients() {
  return Stream.of(Arguments.of("missing", Map.of()),
                   Arguments.of("invalid", Map.of("clientId", "not-a-uuid")),
                   Arguments.of("empty", Map.of("clientId", "")));
 }
}''',
        "src/test/java/sample/steps/BaseTest.java": '''package sample.steps;
import org.junit.jupiter.api.BeforeAll;
import io.qameta.allure.Step;
class BaseTest {
 @BeforeAll static void prepare() { importFromSqlFile(db, "sql/securities_all.sql"); }
 @Step("Call the portfolio API") void callApi(Object request) {}
}''',
    }
    raw_nodes = []
    for filename, content in sources.items():
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        unit = parse_java(content.encode(), filename, set())
        for index, declaration in enumerate(unit.declarations):
            method = declaration["kind"] in METHODS
            name_ast = declaration["_ast"].child_by_field_name("name")
            raw_nodes.append({"id": f"raw:{filename}:{index}", "file": filename,
                              "label": declaration["name"] + ("()" if method else ""),
                              "kind": "method" if method else "class",
                              "line": name_ast.start_point.row + 1})
    test_node = next(node for node in raw_nodes if node["label"] == "validateClient()")
    step_node = next(node for node in raw_nodes if node["label"] == "callApi()")
    raw = {"nodes": raw_nodes,
           "edges": [{"source": test_node["id"], "target": step_node["id"],
                      "relation": "calls", "confidence": "EXTRACTED"}],
           "metadata": {"rawFixture": "gradle-junit-allure"}}
    original = deepcopy(raw)
    enriched = enrich_snapshot(raw, root, analysis_rules=rules)
    assert_analysis_rules_current(mods, rules)
    return root, original, raw, enriched, test_node["id"]


def _full_test(snapshot, handler_id):
    """Find full declaration evidence on the real handler, not the synthetic ingress ID."""
    node = next(node for node in snapshot["nodes"] if node["id"] == handler_id)
    return node["testFacts"]


def test_pipeline_binds_provider_to_raw_id_and_preserves_source_graph(java_test_snapshot):
    root, original, raw, snapshot, handler = java_test_snapshot
    assert raw == original
    assert all(node["id"] in {item["id"] for item in snapshot["nodes"]} for node in raw["nodes"])
    assert raw["edges"][0] in snapshot["edges"]
    test = _full_test(snapshot, handler)
    assert test["handlerNodeIds"] == [handler]
    assert test["label"] == "Проверка невалидного ClientId"
    assert test["displayTemplate"] == "{0}" and test["runtimeStatus"] == "unknown"
    source, = test["dataSources"]
    provider_id, = source["nodeIds"]
    assert provider_id in {node["id"] for node in raw["nodes"]}
    provider = next(node for node in snapshot["nodes"] if node["id"] == provider_id)
    assert provider["label"] == "invalidClients()"
    assert source["status"] == "declared" and source["declaredRowCount"] == 3
    assert len(source["rows"]) == 3
    fixture, = test["fixtures"]
    assert fixture["inherited"] and fixture["resources"][0]["label"] == "sql/securities_all.sql"
    assert fixture["effectiveOrder"] == "unknown"
    assert test["annotations"]
    assert test["codeProjectId"].startswith("logical-")
    assert test["codeProjectFacts"]["buildManifest"] == "build.gradle"
    assert len(snapshot["metadata"]["testDiscovery"]["entries"]) == 1
    assert snapshot["metadata"]["analysisMods"]["fingerprint"]
    # Allure contributes occurrence evidence without creating a runnable helper entry.
    assert any(node.get("qualifiedName") == "io.qameta.allure.Step" for node in snapshot["nodes"] if node.get("kind") == "attribute")


def test_entrypoint_catalog_is_compact_while_snapshot_keeps_full_details(java_test_snapshot):
    root, original, raw, snapshot, handler = java_test_snapshot
    before = deepcopy(snapshot)
    catalog = build_entrypoints(root, snapshot, "snapshot-fixture")
    entry, = [entry for entry in catalog["entries"] if entry["kind"] == "test"]
    full = _full_test(snapshot, handler)
    assert entry["handlerNodeIds"] == [handler]
    assert entry["id"] == full["id"]
    assert entry["detailsAvailable"] is True
    assert entry["annotationCount"] == len(full["annotations"])
    assert entry["fixtureCount"] == len(full["fixtures"])
    assert "annotations" not in entry and "fixtures" not in entry
    source, = entry["dataSources"]
    assert source["declaredRowCount"] == 3 and source["nodeIds"] == full["dataSources"][0]["nodeIds"]
    assert "rows" not in source
    assert source['rowsTruncated'] == full['dataSources'][0]['rowsTruncated']
    assert "entries" not in catalog["testDiscovery"]
    assert full["dataSources"][0]["rows"] and full["fixtures"] and full["annotations"]
    assert snapshot == before and raw == original


def test_map_projection_omits_test_details_without_mutating_full_snapshot(java_test_snapshot):
    root, original, raw, snapshot, handler = java_test_snapshot
    before = deepcopy(snapshot)
    full = json.loads(serialize_graph(snapshot))
    projected = json.loads(serialize_graph(snapshot, "map"))
    assert full == before and snapshot == before and raw == original
    assert _full_test(full, handler)["dataSources"][0]["declaredRowCount"] == 3
    assert full["metadata"]["testDiscovery"]["entries"]
    assert "entries" not in projected["metadata"]["testDiscovery"]
    assert all("testFacts" not in node and "attributeFacts" not in node for node in projected["nodes"])
    assert projected["edges"] == full["edges"]
    assert [node["id"] for node in projected["nodes"]] == [node["id"] for node in full["nodes"]]
    assert projected["metadata"]["testDiscovery"]["coverage"] == full["metadata"]["testDiscovery"]["coverage"]
    assert len(serialize_graph(snapshot, "map")) < len(serialize_graph(snapshot))


def test_entrypoints_http_marks_mod_freshness_without_rewriting_capture(tmp_path):
    """Activating new analysis rules warns readers while preserving the immutable old map."""
    from fastapi.testclient import TestClient
    from atlas.server import create_app
    from test_grouping_cache import legacy_store
    service, project_id, path, graph = legacy_store(tmp_path)
    service.close()
    before = path.read_bytes()
    with TestClient(create_app(tmp_path / 'state'), base_url='http://127.0.0.1:8765') as client:
        endpoint = f'/api/projects/{project_id}/entrypoints?snapshotId=first'
        result = client.get(endpoint)
        assert result.status_code == 200
        assert result.json()['analysisModsCurrent'] is False
        assert 'обновите карту' in result.json()['warning']
    assert path.read_bytes() == before
