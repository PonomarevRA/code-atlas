"""Synthetic Office/PDF fixtures prove coordinates and honest extraction limits."""

import hashlib
import socket
import zipfile

import pytest

from atlas.documents.models import DocumentError
from atlas.documents.office_package import OfficePackage
from atlas.documents.provider_docx import DocxProvider
from atlas.documents.provider_pdf import PdfProvider
from atlas.documents.provider_pptx import PptxProvider
from atlas.documents.provider_xlsx import XlsxProvider


def _rewrite_package(path, changes):
    """Replace selected XML parts without modifying the caller's unrelated files."""
    with zipfile.ZipFile(path) as archive:
        members = {item.filename: archive.read(item) for item in archive.infolist()}
    for name, change in changes.items():
        members[name] = change(members[name]) if callable(change) else change
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


def test_docx_block_order_nested_tables_merges_headers_and_no_pages(tmp_path):
    """Heading ownership and physical merge cells survive without fictitious pages."""
    from docx import Document

    path = tmp_path / "rules.docx"
    document = Document()
    document.add_heading("Правила", 1)
    document.add_paragraph("До таблицы")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 0).merge(table.cell(1, 0))
    table.cell(0, 1).add_table(rows=1, cols=1).cell(0, 0).text = "Nested"
    document.add_paragraph("После таблицы")
    document.sections[0].header.paragraphs[0].text = "Header"
    document.save(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    tree = DocxProvider().parse(path, {}, {})
    nodes = tree["nodes"]
    heading = next(node for node in nodes if node["label"] == "Правила")
    main_table = next(node for node in nodes if node["kind"] == "table")
    assert main_table["parentId"] == heading["id"]
    assert next(node for node in nodes if node["text"] == "До таблицы")["order"] < main_table["order"]
    assert next(node for node in nodes if node["text"] == "После таблицы")["order"] > main_table["order"]
    assert len([node for node in nodes if node["kind"] == "table"]) == 2
    anchor = next(node for node in nodes if node["attributes"].get("verticalMerge") == "restart")
    continuation = next(node for node in nodes if node["attributes"].get("verticalMerge") == "continue")
    assert anchor["attributes"]["rowSpan"] == 2
    assert continuation["attributes"]["mergeAnchor"] == {"row": 0, "column": 0}
    assert continuation["attributes"]["mergeAnchorBasis"] == "table_zero_based_coordinates"
    assert any(node["text"] == "Header" and node["sourceLocator"]["part"].startswith("word/header") for node in nodes)
    assert all("page" not in node["sourceLocator"] for node in nodes)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_docx_tracked_changes_and_textboxes_are_explicitly_partial(tmp_path):
    """Unsupported revision/textbox content never disappears under complete coverage."""
    from docx import Document

    path = tmp_path / "tracked.docx"
    document = Document()
    document.add_paragraph("Visible")
    document.save(path)
    insertion = b'<w:ins w:id="1"><w:p><w:r><w:t>Hidden revision</w:t></w:r></w:p></w:ins><w:p><w:r><w:txbxContent><w:p><w:r><w:t>Textbox</w:t></w:r></w:p></w:txbxContent></w:r></w:p>'
    _rewrite_package(path, {"word/document.xml": lambda value: value.replace(b"<w:sectPr", insertion + b"<w:sectPr", 1)})
    tree = DocxProvider().parse(path, {}, {})
    assert tree["coverage"]["status"] == "partial"
    assert {"docx_ins_not_fully_extracted", "docx_txbxContent_not_fully_extracted"} <= set(tree["coverage"]["reasonCodes"])
    assert "Hidden revision" not in " ".join(node["text"] for node in tree["nodes"])


def test_docx_inherited_heading_and_list_styles(tmp_path):
    """A custom style based on Heading 2 retains its inherited outline semantics."""
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE

    path = tmp_path / "styles.docx"
    document = Document()
    custom = document.styles.add_style("Calculation section", WD_STYLE_TYPE.PARAGRAPH)
    custom.base_style = document.styles["Heading 2"]
    document.add_paragraph("Inherited", custom)
    document.add_paragraph("Numbered", "List Number")
    document.save(path)
    tree = DocxProvider().parse(path, {}, {})
    heading = next(node for node in tree["nodes"] if node["text"] == "Inherited")
    item = next(node for node in tree["nodes"] if node["text"] == "Numbered")
    assert heading["kind"] == "section" and heading["attributes"]["outlineLevel"] == 1
    assert item["kind"] == "item" and item["parentId"] == heading["id"]


@pytest.mark.parametrize("payload, options, error", [
    (b'<!DOCTYPE x [<!ENTITY e "test">]><x>&e;</x>', {}, "entities"),
    (b"<x><x><x/></x></x>", {"max_depth": 2}, "budget"),
    (b"<x>" + b"a" * 10000 + b"</x>", {"max_expanded_bytes": 100}, "expanded"),
    (b"<x>" + b"a" * 10000 + b"</x>", {"max_compression_ratio": 2}, "ratio"),
])
def test_office_rejects_xml_entities_depth_and_zip_expansion(tmp_path, payload, options, error):
    """Budget/security failures occur before native Office parsers are called."""
    path = tmp_path / "unsafe.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", payload)
    with pytest.raises(ValueError, match=error):
        with OfficePackage(path, options):
            pass


def test_office_rejects_duplicate_members_and_parent_paths(tmp_path):
    """Ambiguous ZIP names and traversal members fail closed without extraction."""
    path = tmp_path / "unsafe.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../outside.xml", b"<x/>")
    with pytest.raises(ValueError, match="unsafe_member"):
        OfficePackage(path, {})


def test_xlsx_sparse_extreme_dimension_cache_evidence_and_explicit_table(tmp_path):
    """A far cell creates no dense grid; cache presence remains separate from freshness."""
    from openpyxl import Workbook
    from openpyxl.worksheet.table import Table
    from openpyxl.workbook.defined_name import DefinedName

    path = tmp_path / "rates.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Rates"
    sheet.append(["Name", "Value"])
    sheet.append(["A", 42])
    sheet["C2"] = "=B2*2"
    sheet["D2"] = "=B2*3"
    sheet["XFD1048576"] = "Far"
    sheet.merge_cells("F2:G3")
    sheet["F2"] = "Merged"
    sheet.add_table(Table(displayName="RatesTable", ref="A1:B2"))
    book.defined_names.add(DefinedName("SelectedRate", attr_text="'Rates'!$B$2"))
    hidden = book.create_sheet("Hidden")
    hidden.sheet_state = "hidden"
    book.save(path)
    _rewrite_package(path, {"xl/worksheets/sheet1.xml": lambda xml: xml.replace(b"<f>B2*2</f><v></v>", b"<f>B2*2</f><v>84</v>")})
    tree = XlsxProvider().parse(path, {}, {})
    cells = {node["label"]: node for node in tree["nodes"] if node["kind"] == "cell"}
    assert len(cells) < 20
    assert cells["XFD1048576"]["attributes"]["row"] == 1048575
    assert cells["C2"]["attributes"]["cachedValue"] == "84"
    assert cells["C2"]["attributes"]["cachedValueStatus"] == "present"
    assert cells["D2"]["attributes"]["cachedValueStatus"] == "missing"
    assert cells["D2"]["attributes"]["valueStatus"] == "unevaluated_formula"
    assert cells["C2"]["attributes"]["value"] is None
    assert cells["C2"]["attributes"]["cacheFreshness"] == cells["D2"]["attributes"]["cacheFreshness"] == "unknown"
    assert cells["D2"]["attributes"]["formula"] == "=B2*3"
    assert cells["F2"]["attributes"]["rowSpan"] == cells["F2"]["attributes"]["columnSpan"] == 2
    assert any(node["label"] == "RatesTable" and node["attributes"]["headerPolicy"] == "explicit" for node in tree["nodes"])
    assert any(node["label"] == "SelectedRate" for node in tree["nodes"])
    assert any(node["label"] == "Hidden" and node["attributes"]["state"] == "hidden" for node in tree["nodes"])
    assert tree["coverage"]["formulasEvaluated"] is False


def test_office_node_budget_is_enforced_during_parsing(tmp_path):
    """Many cells cannot grow an unbounded tree before post-parse validation."""
    from openpyxl import Workbook

    path = tmp_path / "budget.xlsx"
    book = Workbook()
    book.active.append(list(range(10)))
    book.save(path)
    with pytest.raises(DocumentError, match="узлов"):
        XlsxProvider().parse(path, {"max_nodes": 5}, {})


def test_xlsx_shared_formula_without_local_text_is_not_fabricated(tmp_path):
    """Shared formula references preserve their source attributes and cache separately."""
    from openpyxl import Workbook

    path = tmp_path / "shared.xlsx"
    book = Workbook()
    book.active["A1"] = "=B1*2"
    book.save(path)
    _rewrite_package(path, {"xl/worksheets/sheet1.xml": lambda xml: xml.replace(b"<f>B1*2</f><v></v>", b'<f t="shared" si="0"/><v>12</v>')})
    tree = XlsxProvider().parse(path, {}, {})
    cell = next(node for node in tree["nodes"] if node["kind"] == "cell")
    assert cell["attributes"]["formula"] is None
    assert cell["attributes"]["formulaStatus"] == "unresolved_shared_or_data_table"
    assert cell["attributes"]["formulaAttributes"] == {"t": "shared", "si": "0"}
    assert cell["attributes"]["cachedValue"] == "12"


def test_pptx_groups_notes_merge_and_missing_notes_are_read_only(tmp_path):
    """Existing notes are captured while slides lacking notes acquire no new part."""
    from pptx import Presentation
    from pptx.util import Inches

    path = tmp_path / "slides.pptx"
    deck = Presentation()
    first = deck.slides.add_slide(deck.slide_layouts[6])
    group = first.shapes.add_group_shape()
    shape = group.shapes.add_textbox(0, 0, Inches(2), Inches(1))
    shape.text = "Grouped"
    table = first.shapes.add_table(2, 2, 0, Inches(2), Inches(4), Inches(1)).table
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(0, 0).text = "Merged"
    first.notes_slide.notes_text_frame.text = "Speaker notes"
    second = deck.slides.add_slide(deck.slide_layouts[6])
    assert not second.has_notes_slide
    deck.save(path)
    before = path.read_bytes()
    tree = PptxProvider().parse(path, {}, {})
    assert any(node["text"] == "Speaker notes" for node in tree["nodes"])
    grouped = next(node for node in tree["nodes"] if node["text"] == "Grouped")
    assert grouped["sourceLocator"]["groupPath"] == [group.shape_id]
    assert grouped["sourceLocator"]["shapeId"] == shape.shape_id
    assert any(node["attributes"].get("mergeOrigin") and node["attributes"]["columnSpan"] == 2 for node in tree["nodes"])
    assert path.read_bytes() == before
    assert not Presentation(path).slides[1].has_notes_slide
    assert tree["coverage"]["orderMethod"] == "package_z_order"


def _pdf_fixture(path, *, scan=False, blank=False, encrypted=False, table=False):
    """Write minimal native-text/raster pages with no external rendering dependency."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    resources = DictionaryObject()
    if not blank:
        content = DecodedStreamObject()
        if scan:
            image = DecodedStreamObject()
            image.set_data(b"\xff\x00\x00")
            image.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"), NameObject("/Width"): NumberObject(1), NameObject("/Height"): NumberObject(1), NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/ColorSpace"): NameObject("/DeviceRGB")})
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im1"): writer._add_object(image)})
            content.set_data(b"q 100 0 0 100 0 0 cm /Im1 Do Q")
        else:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): writer._add_object(font)})
            if table:
                content.set_data(b"10 20 m 110 20 l S 10 50 m 110 50 l S 10 80 m 110 80 l S 10 20 m 10 80 l S 60 20 m 60 80 l S 110 20 m 110 80 l S BT /F1 10 Tf 20 65 Td (A) Tj 50 0 Td (B) Tj -50 -30 Td (1) Tj 50 0 Td (2) Tj ET")
            else:
                content.set_data(b"BT /F1 12 Tf 20 100 Td (Native PDF text) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(content)
    page[NameObject("/Resources")] = resources
    writer.add_outline_item("Bookmark", 0)
    if encrypted:
        writer.encrypt("password")
    writer.write(path)


def test_pdf_native_text_bookmarks_and_layout_are_distinct(tmp_path):
    """Bookmarks remain references; text boxes identify their geometric method."""
    path = tmp_path / "native.pdf"
    _pdf_fixture(path)
    tree = PdfProvider().parse(path, {}, {})
    assert any("Native PDF text" in node["text"] for node in tree["nodes"])
    bookmark = next(node for node in tree["nodes"] if node["label"] == "Bookmark")
    assert bookmark["kind"] == "reference" and bookmark["attributes"]["isPageHeading"] is False
    boxes = [node for node in tree["nodes"] if "bbox" in node["sourceLocator"]]
    assert boxes and all(node["sourceLocator"]["precision"] == "approximate" for node in boxes)
    assert tree["coverage"]["taggedSemanticTree"] == "unsupported"
    assert tree["coverage"]["ocrExecuted"] is False


@pytest.mark.parametrize("scan, blank, encrypted, expected", [(True, False, False, "needs_ocr"), (False, True, False, "no_native_text"), (False, False, True, "unsupported")])
def test_pdf_scan_blank_and_encrypted_are_not_false_complete(tmp_path, scan, blank, encrypted, expected):
    """A truly blank page is not mislabeled a scan and encryption is unsupported."""
    path = tmp_path / "page.pdf"
    _pdf_fixture(path, scan=scan, blank=blank, encrypted=encrypted)
    tree = PdfProvider().parse(path, {}, {})
    assert tree["coverage"]["status"] != "complete"
    if expected == "unsupported":
        assert tree["coverage"]["status"] == "unsupported"
    else:
        page = next(node for node in tree["nodes"] if node["kind"] == "page")
        assert page["attributes"]["status"] == expected
        assert ("needs_ocr" in tree["coverage"]["reasonCodes"]) is scan


def test_pdf_page_and_content_budgets(tmp_path):
    """Native parsers respect explicit page and expanded content limits."""
    path = tmp_path / "budget.pdf"
    _pdf_fixture(path)
    with pytest.raises(ValueError, match="page_limit"):
        PdfProvider().parse(path, {"max_pdf_pages": 0}, {})
    with pytest.raises(ValueError, match="content_stream_limit"):
        PdfProvider().parse(path, {"max_pdf_content_bytes": 1}, {})


def test_pdf_optional_geometric_table_has_cell_locators(tmp_path):
    """Line-based tables retain approximate cell boxes rather than semantic claims."""
    path = tmp_path / "table.pdf"
    _pdf_fixture(path, table=True)
    tree = PdfProvider().parse(path, {"extract_tables": True}, {})
    table = next(node for node in tree["nodes"] if node["kind"] == "table")
    assert table["attributes"]["extractionMethod"] == "pdfplumber_lines"
    cells = [node for node in tree["nodes"] if node["kind"] == "cell"]
    assert [node["text"] for node in cells] == ["A", "B", "1", "2"]
    assert all(node["sourceLocator"]["precision"] == "approximate" and len(node["sourceLocator"]["bbox"]) == 4 for node in cells)


def test_pdf_rotation_uses_layout_coordinate_frame(tmp_path):
    """Rotated word boxes and page sizes refer to the same top-left point frame."""
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject

    original = tmp_path / "unrotated.pdf"
    path = tmp_path / "rotated.pdf"
    _pdf_fixture(original)
    writer = PdfWriter(clone_from=original)
    writer.pages[0].mediabox = RectangleObject([0, 0, 200, 300])
    writer.pages[0].rotate(90)
    writer.write(path)
    tree = PdfProvider().parse(path, {}, {})
    page = next(node for node in tree["nodes"] if node["kind"] == "page")
    assert page["sourceLocator"]["rotation"] == 90
    assert page["attributes"]["width"] == 300 and page["attributes"]["height"] == 200
    assert page["attributes"]["mediaBox"] == [0, 0, 200, 300]


@pytest.mark.parametrize("kind", ["docx", "xlsx", "pptx", "pdf"])
def test_native_providers_parse_with_network_calls_denied(tmp_path, monkeypatch, kind):
    """Synthetic inputs parse when DNS and socket connection APIs are forbidden."""
    path = tmp_path / ("offline." + kind)
    if kind == "docx":
        from docx import Document
        document = Document()
        document.add_paragraph("Local document")
        document.save(path)
        provider = DocxProvider()
    elif kind == "xlsx":
        from openpyxl import Workbook
        document = Workbook()
        document.active["A1"] = "Local workbook"
        document.save(path)
        provider = XlsxProvider()
    elif kind == "pptx":
        from pptx import Presentation
        document = Presentation()
        document.slides.add_slide(document.slide_layouts[6])
        document.save(path)
        provider = PptxProvider()
    else:
        _pdf_fixture(path)
        provider = PdfProvider()

    def denied(*args, **kwargs):
        """Make unexpected network access fail the fixture immediately."""
        raise AssertionError("network call during native document parse")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    assert provider.parse(path, {}, {})["nodes"]
