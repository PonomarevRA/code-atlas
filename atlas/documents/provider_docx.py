"""Read-only Word blocks and OOXML coordinates, without invented pagination."""

from pathlib import Path

from .models import TreeBuilder
from .office_package import OfficePackage, provider_fingerprint

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocxProvider:
    """Extract native DOCX structure; tracked revisions remain explicitly partial."""

    id = "document-docx"
    version = "1"
    capabilities = {"structuralLocators": True, "pagination": False, "readOnly": True}

    @property
    def runtime_fingerprint(self):
        return provider_fingerprint(__file__, "python-docx")

    def parse(self, path: Path, options: dict, context: dict):
        """Preserve body block order and raw table cells including merge markers."""
        from docx import Document
        from docx.oxml import parse_xml

        builder = TreeBuilder(path.name, max_nodes=int(options.get("max_nodes", 100000)), max_text_bytes=int(options.get("max_text_bytes", 16 * 1024 * 1024)))
        reasons = []
        with OfficePackage(path, options) as package:
            document = Document(path)
            self._blocks(document.element.body, builder.root_id, builder, "word/document.xml", [], document)
            for name, root in package.roots.items():
                if name.startswith("word/") and name.endswith(".xml"):
                    for tag in ("txbxContent", "ins", "del", "moveFrom", "moveTo", "altChunk", "drawing", "object", "fldSimple", "customXml"):
                        if root.find(".//" + W + tag) is not None:
                            reasons.append("docx_" + tag + "_not_fully_extracted")
                if name.startswith(("word/header", "word/footer", "word/footnotes", "word/endnotes", "word/comments")):
                    part = builder.add("section", builder.root_id, name, source_locator=self._locator(name, []))
                    native_root = parse_xml(package.archive.read(name))
                    self._blocks(native_root, part, builder, name, [], document)
            if any("vbaProject" in member.filename for member in package.archive.infolist()):
                reasons.append("macros_not_executed")
        builder.warnings.append("Word page numbers are unavailable without a layout engine.")
        return builder.build({"status": "partial" if reasons else "complete", "reasonCodes": sorted(set(reasons)), "trackedChangesPolicy": "exclude_revisions", "pagination": "unavailable"})

    @staticmethod
    def _locator(part, blocks):
        """Identify the package part and zero-based block path, never a page."""
        return {"type": "docx", "precision": "structural", "part": part, "blockPath": blocks, "indexBasis": 0}

    def _blocks(self, element, parent, builder, part, path, document):
        """Walk container blocks; recurse into cells and annotation containers."""
        from docx.text.paragraph import Paragraph

        sections = []
        for index, child in enumerate(element):
            block_path = path + [index]
            locator = self._locator(part, block_path)
            if child.tag == W + "p":
                paragraph = Paragraph(child, document)
                text = paragraph.text
                kind, attributes = self._paragraph_details(paragraph, child)
                if kind == "section":
                    level = attributes.get("outlineLevel", 0)
                    while sections and sections[-1][0] >= level:
                        sections.pop()
                    section_parent = sections[-1][1] if sections else parent
                    section = builder.add(kind, section_parent, text[:160], text, locator, attributes)
                    sections.append((level, section))
                else:
                    block_parent = sections[-1][1] if sections else parent
                    builder.add(kind, block_parent, text[:160], text, locator, attributes)
            elif child.tag == W + "tbl":
                block_parent = sections[-1][1] if sections else parent
                self._table(child, block_parent, builder, part, block_path, document)
            elif child.tag in {W + "footnote", W + "endnote", W + "comment"}:
                annotation = builder.add("section", parent, child.tag.split("}")[-1], source_locator=locator, attributes={"annotationId": child.get(W + "id")})
                self._blocks(child, annotation, builder, part, block_path, document)
            elif child.tag == W + "sdt":
                content = child.find(W + "sdtContent")
                if content is not None:
                    self._blocks(content, parent, builder, part, block_path, document)

    @staticmethod
    def _paragraph_details(paragraph, element):
        """Resolve direct and inherited outline/list properties with cycle guards."""
        attributes = {}
        property_sets = [element.find(W + "pPr")]
        style = paragraph.style
        if style is not None:
            attributes["style"] = style.name
        visited = set()
        while style is not None:
            if style.style_id in visited or len(visited) >= 128:
                raise ValueError("docx_style_inheritance_cycle_or_depth")
            visited.add(style.style_id)
            property_sets.append(style.element.find(W + "pPr"))
            style = style.base_style
        for properties in property_sets:
            if properties is None:
                continue
            outline = properties.find(W + "outlineLvl")
            if outline is not None:
                attributes.setdefault("outlineLevel", int(outline.get(W + "val", "0")))
            number = properties.find(W + "numPr")
            if number is not None and "numbering" not in attributes:
                attributes["numbering"] = {item.tag.split("}")[-1]: item.get(W + "val") for item in number}
        if attributes.get("outlineLevel", 9) < 9:
            kind = "section"
        elif "numbering" in attributes:
            kind = "item"
        else:
            kind = "text"
        return kind, attributes

    def _table(self, element, parent, builder, part, path, document):
        """Keep one node per physical cell and explicit horizontal/vertical spans."""
        from docx.text.paragraph import Paragraph

        rows = element.findall(W + "tr")
        table = builder.add("table", parent, "Table", source_locator=self._locator(part, path), attributes={"rowCount": len(rows), "headerPolicy": "explicit_only"})
        active_merges = {}
        for row_index, row in enumerate(rows):
            header = row.find(W + "trPr/" + W + "tblHeader")
            explicit_header = header is not None and header.get(W + "val", "true") not in {"0", "false", "off"}
            row_node = builder.add("row", table, str(row_index + 1), attributes={"row": row_index, "explicitHeader": explicit_header})
            grid_before = row.find(W + "trPr/" + W + "gridBefore")
            column = int(grid_before.get(W + "val", "0")) if grid_before is not None else 0
            next_merges = {}
            for cell_index, cell in enumerate(row.findall(W + "tc")):
                properties = cell.find(W + "tcPr")
                span = properties.find(W + "gridSpan") if properties is not None else None
                column_span = int(span.get(W + "val", "1")) if span is not None else 1
                merge = properties.find(W + "vMerge") if properties is not None else None
                attributes = {"row": row_index, "column": column, "columnSpan": column_span, "rowSpan": 1}
                anchor = None
                if merge is not None:
                    continuing = merge.get(W + "val", "continue") == "continue"
                    anchor = active_merges.get(column) if continuing else None
                    attributes["verticalMerge"] = "continue" if continuing else "restart"
                    if anchor is not None:
                        anchor_attributes = builder.nodes[int(anchor)]["attributes"]
                        attributes["mergeAnchor"] = {"row": anchor_attributes["row"], "column": anchor_attributes["column"]}
                        attributes["mergeAnchorBasis"] = "table_zero_based_coordinates"
                        anchor_attributes["rowSpan"] += 1
                locator = self._locator(part, path + [row_index, cell_index])
                locator.update({"row": row_index, "column": column})
                # A table window needs direct cell text, while nested tables retain
                # their own cells and must not be flattened into the outer value.
                paragraphs = [Paragraph(item, document).text for item in cell if item.tag == W + "p"]
                cell_text = "\n".join(paragraphs)
                cell_node = builder.add("cell", row_node, f"R{row_index + 1}C{column + 1}", cell_text, locator, attributes)
                if merge is not None:
                    next_merges[column] = anchor if anchor is not None else cell_node
                self._blocks(cell, cell_node, builder, part, path + [row_index, cell_index], document)
                column += column_span
            active_merges = next_merges
