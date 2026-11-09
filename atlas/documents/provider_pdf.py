"""Native PDF text, page layout and bookmarks; no OCR or model execution."""

from pathlib import Path

from .models import TreeBuilder
from .office_package import provider_fingerprint


class PdfProvider:
    """Extract basic PDF evidence and mark unsupported semantic reconstruction."""

    id = "document-pdf-basic"
    version = "1"
    capabilities = {"nativeText": True, "pageLayout": True, "bookmarks": True, "ocr": False, "taggedSemanticTree": False}

    @property
    def runtime_fingerprint(self):
        return provider_fingerprint(__file__, "pypdf", "pdfplumber")

    def parse(self, path: Path, options: dict, context: dict):
        """Parse bounded native pages; scans receive needs_ocr without processing."""
        import pdfplumber
        from pypdf import PdfReader

        if path.stat().st_size > int(options.get("max_source_bytes", 50 * 1024 * 1024)):
            raise ValueError("pdf_source_byte_limit")
        builder = TreeBuilder(path.name, max_nodes=int(options.get("max_nodes", 100000)), max_text_bytes=int(options.get("max_text_bytes", 16 * 1024 * 1024)))
        reasons = ["pdf_tagged_semantic_tree_not_extracted", "pdf_reading_order_heuristic"]
        with path.open("rb") as source:
            reader = PdfReader(source, strict=True)
            if reader.is_encrypted:
                return builder.build({"status": "unsupported", "reasonCodes": ["pdf_encrypted"]})
            object_count = sum(len(section) for section in reader.xref.values()) + len(reader.xref_objStm)
            if object_count > int(options.get("max_pdf_objects", 100000)):
                raise ValueError("pdf_object_limit")
            if len(reader.pages) > int(options.get("max_pdf_pages", 300)):
                raise ValueError("pdf_page_limit")
            with pdfplumber.open(path) as layout:
                for index, page in enumerate(reader.pages):
                    self._check_streams(page, options)
                    text = page.extract_text() or ""
                    locator = {"type": "pdf", "precision": "structural", "pageIndex": index, "indexBasis": 0, "rotation": int(page.rotation), "coordinateSystem": "pdfplumber_top_left_points"}
                    layout_page = layout.pages[index]
                    attributes = {"pageNumber": index + 1, "width": float(layout_page.width), "height": float(layout_page.height), "mediaBox": [float(value) for value in page.mediabox], "extractionMethod": "pypdf_native"}
                    raster = self._contains_image(page.get("/Resources", {}), options)
                    if not text.strip() and raster:
                        attributes["status"] = "needs_ocr"
                        reasons.append("needs_ocr")
                    elif not text.strip():
                        attributes["status"] = "no_native_text"
                    else:
                        attributes["status"] = "native_text"
                        if raster:
                            attributes["rasterContentInterpreted"] = False
                            reasons.append("pdf_mixed_raster_content_not_interpreted")
                    node = builder.add("page", builder.root_id, f"Page {index + 1}", text, locator, attributes)
                    if text.strip():
                        self._layout(layout_page, node, builder, locator, options)
            self._bookmarks(reader.outline, reader, builder.root_id, builder, 0, int(options.get("max_depth", 128)))
            catalog = reader.trailer["/Root"]
            builder.nodes[0]["attributes"]["taggedStructurePresent"] = "/StructTreeRoot" in catalog
        return builder.build({"status": "partial", "reasonCodes": sorted(set(reasons)), "ocrExecuted": False, "taggedSemanticTree": "unsupported", "readingOrder": "approximate"})

    @staticmethod
    def _check_streams(page, options):
        """Bound expanded page content before text/layout extraction."""
        streams = page.get("/Contents")
        if streams is None:
            return
        streams = streams.get_object()
        if not isinstance(streams, list):
            streams = [streams]
        total = 0
        for stream in streams:
            total += len(stream.get_object().get_data())
            if total > int(options.get("max_pdf_content_bytes", 16 * 1024 * 1024)):
                raise ValueError("pdf_content_stream_limit")

    @staticmethod
    def _contains_image(resources, options, depth=0):
        """Inspect image/form resource dictionaries without decoding image pixels."""
        if depth > int(options.get("max_depth", 128)):
            raise ValueError("pdf_resource_depth_limit")
        resources = resources.get_object() if hasattr(resources, "get_object") else resources
        objects = resources.get("/XObject", {})
        objects = objects.get_object() if hasattr(objects, "get_object") else objects
        for reference in objects.values():
            item = reference.get_object()
            if item.get("/Subtype") == "/Image":
                return True
            nested = item.get("/Resources")
            if nested is not None and PdfProvider._contains_image(nested, options, depth + 1):
                return True
        return False

    @staticmethod
    def _layout(page, parent, builder, locator, options):
        """Store approximate word boxes and optional geometric table recovery."""
        words = page.extract_words()
        for index, word in enumerate(words):
            box = [float(word[key]) for key in ("x0", "top", "x1", "bottom")]
            builder.add("text", parent, word["text"][:160], word["text"], {**locator, "precision": "approximate", "bbox": box}, {"extractionMethod": "pdfplumber_words", "readingOrder": "geometric_heuristic", "wordIndex": index})
        if options.get("extract_tables", False):
            for table in page.find_tables():
                table_locator = {**locator, "precision": "approximate", "bbox": list(table.bbox)}
                table_node = builder.add("table", parent, "Geometric table", source_locator=table_locator, attributes={"extractionMethod": "pdfplumber_lines", "headerPolicy": "unavailable"})
                for row_index, row in enumerate(table.extract()):
                    row_locator = {**table_locator, "tableRow": row_index}
                    row_node = builder.add("row", table_node, str(row_index + 1), source_locator=row_locator, attributes={"row": row_index})
                    for column, value in enumerate(row):
                        cell_locator = {**row_locator, "tableColumn": column}
                        box = table.rows[row_index].cells[column]
                        if box is not None:
                            cell_locator["bbox"] = list(box)
                        builder.add("cell", row_node, f"R{row_index + 1}C{column + 1}", value or "", source_locator=cell_locator, attributes={"row": row_index, "column": column, "valueStatus": "unavailable" if value is None else "present"})

    def _bookmarks(self, items, reader, parent, builder, depth, maximum):
        """Preserve outline hierarchy separately from page text and its headings."""
        if depth > maximum:
            raise ValueError("pdf_outline_depth_limit")
        previous = parent
        for item in items:
            if isinstance(item, list):
                self._bookmarks(item, reader, previous, builder, depth + 1, maximum)
            else:
                page = reader.get_destination_page_number(item)
                previous = builder.add("reference", parent, str(item.title), source_locator={"type": "pdf", "precision": "structural", "pageIndex": page, "indexBasis": 0}, attributes={"role": "bookmark", "isPageHeading": False})
