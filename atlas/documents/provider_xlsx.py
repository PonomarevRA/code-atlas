"""Sparse Excel structure with independent formula and saved-cache evidence."""

from pathlib import Path

from .models import TreeBuilder
from .office_package import OfficePackage, provider_fingerprint

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class XlsxProvider:
    """Read only cells present in OOXML; never evaluate formulas or update links."""

    id = "document-xlsx"
    version = "1"
    capabilities = {"sparseCells": True, "formulasEvaluated": False, "readOnly": True}

    @property
    def runtime_fingerprint(self):
        return provider_fingerprint(__file__, "openpyxl")

    def parse(self, path: Path, options: dict, context: dict):
        """Expose workbook metadata and sparse coordinates under validated ZIP limits."""
        from openpyxl import load_workbook

        builder = TreeBuilder(path.name, max_nodes=int(options.get("max_nodes", 100000)), max_text_bytes=int(options.get("max_text_bytes", 16 * 1024 * 1024)))
        with OfficePackage(path, options) as package:
            workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
            try:
                root = package.xml("xl/workbook.xml")
                if root is None:
                    raise ValueError("xlsx_workbook_missing")
                relationships = package.related_parts("xl/workbook.xml")
                shared = self._shared_strings(package)
                for index, sheet in enumerate(root.findall(S + "sheets/" + S + "sheet")):
                    sheet_id = sheet.get("sheetId")
                    part = relationships.get(sheet.get(R + "id"))
                    node = builder.add("sheet", builder.root_id, sheet.get("name", ""), source_locator=self._locator(sheet_id), attributes={"sheetId": sheet_id, "ordinal": index, "state": sheet.get("state", "visible")})
                    sheet_root = package.xml(part)
                    if sheet_root is None:
                        builder.warnings.append("Missing worksheet part: " + str(part))
                        continue
                    self._sheet(sheet_root, node, sheet_id, part, package, shared, builder, options)
                names = root.find(S + "definedNames")
                if names is not None:
                    for name in names:
                        builder.add("reference", builder.root_id, name.get("name", ""), name.text or "", {"type": "xlsx", "precision": "structural", "part": "xl/workbook.xml", "definedName": name.get("name")}, {"localSheetId": name.get("localSheetId"), "evaluated": False})
            finally:
                workbook.close()
        return builder.build({"status": "partial", "reasonCodes": ["xlsx_noncell_objects_not_extracted"], "formulasEvaluated": False, "cacheFreshness": "unknown", "sparse": True})

    @staticmethod
    def _locator(sheet_id, address=None):
        """Use workbook sheet identity and exact A1 addresses without dense windows."""
        locator = {"type": "xlsx", "precision": "exact", "sheetId": sheet_id, "basis": "A1"}
        if address is not None:
            locator["range"] = address
        return locator

    @staticmethod
    def _shared_strings(package):
        """Read rich shared strings in package order without normalizing whitespace."""
        root = package.xml("xl/sharedStrings.xml")
        result = []
        if root is not None:
            for item in root:
                result.append("".join(text.text or "" for text in item.iter(S + "t")))
        return result

    @staticmethod
    def _value(cell, shared):
        """Retain raw lexemes and distinguish explicit empty from missing content."""
        value = cell.find(S + "v")
        cell_type = cell.get("t", "n")
        if cell_type == "inlineStr":
            inline = cell.find(S + "is")
            return "" if inline is None else "".join(item.text or "" for item in inline.iter(S + "t"))
        raw = value.text if value is not None else None
        if cell_type == "s" and raw is not None:
            index = int(raw)
            if not 0 <= index < len(shared):
                raise ValueError("xlsx_invalid_shared_string")
            return shared[index]
        return raw

    def _sheet(self, root, parent, sheet_id, part, package, shared, builder, options):
        """Extract actual sparse row/cell elements, merges and explicit table metadata."""
        from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries

        merges = root.find(S + "mergeCells")
        merged_ranges = [] if merges is None else [item.get("ref") for item in merges]
        merge_anchors = {}
        for merged_range in merged_ranges:
            first_column, first_row, last_column, last_row = range_boundaries(merged_range)
            merge_anchors[merged_range.split(":")[0]] = {"mergeAnchor": merged_range.split(":")[0], "mergeAnchorBasis": "A1", "rowSpan": last_row - first_row + 1, "columnSpan": last_column - first_column + 1}
        builder.nodes[int(parent)]["attributes"]["mergedRanges"] = merged_ranges
        dimension = root.find(S + "dimension")
        builder.nodes[int(parent)]["attributes"]["declaredDimension"] = dimension.get("ref") if dimension is not None else None
        count = 0
        data = root.find(S + "sheetData")
        for row in (() if data is None else data):
            row_number = int(row.get("r", "1")) - 1
            row_node = builder.add("row", parent, str(row_number + 1), attributes={"row": row_number})
            for cell in row.findall(S + "c"):
                count += 1
                if count > int(options.get("max_cells", 100000)):
                    raise ValueError("xlsx_cell_limit")
                address = cell.get("r")
                row_index, column_index = coordinate_to_tuple(address)
                if not 1 <= row_index <= 1048576 or not 1 <= column_index <= 16384:
                    raise ValueError("xlsx_invalid_cell_coordinate")
                formula = cell.find(S + "f")
                value = self._value(cell, shared)
                attributes = {"row": row_index - 1, "column": column_index - 1, "cellType": cell.get("t", "n"), "value": value, "valueStatus": "empty" if value is None else "present", "styleIndex": cell.get("s")}
                attributes.update(merge_anchors.get(address, {}))
                if formula is not None:
                    cache = cell.find(S + "v")
                    present = cache is not None and (cache.text is not None or cell.get("t") == "str")
                    formula_text = "=" + formula.text if formula.text is not None else None
                    attributes.update({"value": None, "valueStatus": "unevaluated_formula", "formula": formula_text, "formulaStatus": "present" if formula_text is not None else "unresolved_shared_or_data_table", "formulaAttributes": dict(formula.attrib), "cachedValue": value if present else None, "cachedValueStatus": "present" if present else "missing", "cacheFreshness": "unknown", "formulasEvaluated": False})
                text = (attributes.get("formula") or "") if formula is not None else (value or "")
                builder.add("cell", row_node, address, text, self._locator(sheet_id, address), attributes)
        relationships = package.related_parts(part)
        tables = root.find(S + "tableParts")
        if tables is not None:
            for table_part in tables:
                table = package.xml(relationships.get(table_part.get(R + "id")))
                if table is not None:
                    first_column, first_row, last_column, last_row = range_boundaries(table.get("ref"))
                    cell_range = {"rowStart": first_row - 1, "rowEndExclusive": last_row, "columnStart": first_column - 1, "columnEndExclusive": last_column}
                    builder.add("table", parent, table.get("displayName", "Table"), source_locator=self._locator(sheet_id, table.get("ref")), attributes={"range": table.get("ref"), "cellContainerId": parent, "cellRange": cell_range, "headerRowCount": int(table.get("headerRowCount", "1")), "headerPolicy": "explicit", "columns": [dict(column.attrib) for column in table.findall(S + "tableColumns/" + S + "tableColumn")]})
