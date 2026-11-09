"""Reviewer regressions cross native providers, published indexes and query scope."""

import json

import pytest

from atlas.documents.models import DocumentError, MAX_RESPONSE_BYTES, encode
from atlas.documents.store import DocumentStore


@pytest.fixture
def document_store(tmp_path):
    """Create an isolated real-worker store whose captured originals are disposable."""
    project = tmp_path / "project"
    project.mkdir()
    store = DocumentStore(tmp_path / "storage", project, "review")
    try:
        yield store, project
    finally:
        store.close()


def _build(store, files):
    """Wait for the explicit import and retain its exact snapshot identity."""
    job = store.ensure("review", files)
    result = store.wait(job["id"], 15)
    assert result["status"] == "completed", json.dumps(result, ensure_ascii=False)
    return result["snapshotId"]


def test_outer_docx_table_window_excludes_nested_table_cells(document_store):
    """A target table owns cells only until the next nested table boundary."""
    from docx import Document

    store, project = document_store
    document = Document()
    outer = document.add_table(rows=1, cols=1)
    outer.cell(0, 0).text = "Outer cell"
    outer.cell(0, 0).add_table(rows=1, cols=1).cell(0, 0).text = "Nested cell"
    document.save(project / "nested.docx")
    snapshot = _build(store, ["nested.docx"])
    root = store.tree("review", snapshot_id=snapshot)["items"][0]
    outer_node = store.tree("review", snapshot_id=snapshot, parent_id=root["id"])["items"][0]
    result = store.table("review", outer_node["id"], snapshot_id=snapshot, row_count=1, column_count=1)
    assert len(result["cells"]) == 1
    assert result["cells"][0]["text"].startswith("Outer cell")
    assert "Nested cell" not in result["cells"][0]["text"]


def test_explicit_xlsx_range_queries_sheet_cells_using_table_relative_window(document_store):
    """B2:C3 windows use zero-based table offsets and retain original A1 evidence."""
    from openpyxl import Workbook
    from openpyxl.worksheet.table import Table

    store, project = document_store
    document = Workbook()
    sheet = document.active
    sheet["A1"] = "Outside"
    for address, value in {"B2": "Name", "C2": "Value", "B3": "A", "C3": 42}.items():
        sheet[address] = value
    sheet.add_table(Table(displayName="RatesTable", ref="B2:C3"))
    document.save(project / "ranges.xlsx")
    snapshot = _build(store, ["ranges.xlsx"])
    table = next(node for node in store.search("review", "RatesTable", snapshot_id=snapshot)["items"] if node["kind"] == "table")
    result = store.table("review", table["id"], snapshot_id=snapshot, row_start=0, column_start=0, row_count=2, column_count=2)
    assert [cell["text"] for cell in result["cells"]] == ["Name", "Value", "A", "42"]
    assert [cell["sourceLocator"]["range"] for cell in result["cells"]] == ["B2", "C2", "B3", "C3"]
    assert result["cells"][0]["attributes"]["row"] == result["cells"][0]["attributes"]["column"] == 1
    clipped = store.table("review", table["id"], snapshot_id=snapshot, row_start=1, column_start=1, row_count=1, column_count=1)
    assert [cell["text"] for cell in clipped["cells"]] == ["42"]


def test_content_document_scope_is_accepted_and_wrong_binding_is_rejected(document_store):
    """Content honors the same explicit documentId exposed by HTTP and MCP tools."""
    from docx import Document

    store, project = document_store
    for name, text in [("one.docx", "First document"), ("two.docx", "Second document")]:
        document = Document()
        document.add_paragraph(text)
        document.save(project / name)
    snapshot = _build(store, ["one.docx", "two.docx"])
    nodes = store.search("review", "First document", snapshot_id=snapshot)["items"]
    node = next(item for item in nodes if item["kind"] == "text")
    other_document = next(item["documentId"] for item in store.tree("review", snapshot_id=snapshot)["items"] if item["documentId"] != node["documentId"])
    result = store.read_content("review", node["id"], snapshot_id=snapshot, document_id=node["documentId"])
    assert result["text"] == "First document"
    with pytest.raises(DocumentError) as error:
        store.read_content("review", node["id"], snapshot_id=snapshot, document_id=other_document)
    assert error.value.code == "NODE_MISSING"


def test_large_log_envelope_and_fields_are_captured_content_not_inline_attributes(document_store):
    """A large JSON field remains reachable through bounded tree and content pages."""
    store, project = document_store
    large = "Ж" * 50000
    (project / "large.jsonl").write_text(json.dumps({"message": "ok", "details": large, "timestamp": large}, ensure_ascii=False) + "\n", encoding="utf-8")
    snapshot = _build(store, ["large.jsonl"])
    root = store.tree("review", snapshot_id=snapshot)["items"][0]
    generation = store.tree("review", snapshot_id=snapshot, parent_id=root["id"])["items"][0]
    event = store.tree("review", snapshot_id=snapshot, parent_id=generation["id"])["items"][0]
    assert event["preview"] == "ok" and "raw" not in event["attributes"]
    assert event["attributes"]["timestampStatus"] == "captured_field"
    children = store.tree("review", snapshot_id=snapshot, parent_id=event["id"])["items"]
    field = next(node for node in children if node["label"] == "details")
    assert field["attributes"]["valueStatus"] == "captured_content"
    content = store.read_content("review", field["id"], snapshot_id=snapshot, document_id=field["documentId"], limit=4000)
    assert content["totalCharacters"] == 50002
    assert content["nextOffset"] == 4000
    assert content["text"].startswith('"ЖЖ')


def test_graph_contains_edges_respect_serialized_response_byte_budget(document_store):
    """Adding containment edges cannot overflow an otherwise bounded node page."""
    from docx import Document

    store, project = document_store
    document = Document()
    for number in range(100):
        document.add_paragraph(str(number) + "x" * 1000)
    document.save(project / "large-graph.docx")
    snapshot = _build(store, ["large-graph.docx"])
    result = store.graph("review", snapshot_id=snapshot)
    assert result["truncated"] is True
    assert len(encode(result).encode("utf-8")) <= MAX_RESPONSE_BYTES
