"""Native presentation shapes, groups and existing notes with explicit z-order."""

from pathlib import Path

from .models import TreeBuilder
from .office_package import OfficePackage, provider_fingerprint


class PptxProvider:
    """Preserve slide and shape identities without claiming semantic reading order."""

    id = "document-pptx"
    version = "1"
    capabilities = {"groups": True, "notes": True, "readOnly": True, "semanticReadingOrder": False}

    @property
    def runtime_fingerprint(self):
        return provider_fingerprint(__file__, "python-pptx")

    def parse(self, path: Path, options: dict, context: dict):
        """Inspect bounded OOXML and traverse only existing presentation parts."""
        from pptx import Presentation

        builder = TreeBuilder(path.name, max_nodes=int(options.get("max_nodes", 100000)), max_text_bytes=int(options.get("max_text_bytes", 16 * 1024 * 1024)))
        with OfficePackage(path, options):
            presentation = Presentation(path)
            for index, slide in enumerate(presentation.slides):
                locator = {"type": "pptx", "precision": "structural", "slideId": slide.slide_id, "slideIndex": index, "indexBasis": 0}
                node = builder.add("slide", builder.root_id, f"Slide {index + 1}", source_locator=locator)
                self._shapes(slide.shapes, node, builder, locator, [])
                # Accessing notes_slide can create a part; has_notes_slide is read-only.
                if slide.has_notes_slide:
                    frame = slide.notes_slide.notes_text_frame
                    if frame is not None:
                        builder.add("text", node, "Notes", frame.text, {**locator, "part": "notes"}, {"role": "notes"})
        return builder.build({"status": "partial", "reasonCodes": ["pptx_z_order_is_not_semantic_reading_order", "pptx_smartart_embedded_objects_not_extracted"], "orderMethod": "package_z_order"})

    def _shapes(self, shapes, parent, builder, slide_locator, groups):
        """Recurse through groups and retain exact shape IDs and geometry."""
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        for index, shape in enumerate(shapes):
            locator = {**slide_locator, "shapeId": shape.shape_id, "groupPath": groups}
            attributes = {"shapeType": int(shape.shape_type), "zOrder": index, "geometryUnit": "EMU", "geometryFrame": "parent_group" if groups else "slide", "left": shape.left, "top": shape.top, "width": shape.width, "height": shape.height, "rotation": shape.rotation}
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                node = builder.add("section", parent, shape.name, source_locator=locator, attributes={**attributes, "role": "group"})
                self._shapes(shape.shapes, node, builder, slide_locator, groups + [shape.shape_id])
            elif shape.has_table:
                self._table(shape.table, parent, builder, locator, attributes)
            else:
                text = shape.text_frame.text if shape.has_text_frame else ""
                builder.add("text" if shape.has_text_frame else "reference", parent, shape.name, text, locator, attributes)

    @staticmethod
    def _table(table, parent, builder, locator, attributes):
        """Keep table coordinates and merge-origin flags, including spanned cells."""
        node = builder.add("table", parent, "Table", source_locator=locator, attributes={**attributes, "rowCount": len(table.rows), "columnCount": len(table.columns)})
        for row_index, row in enumerate(table.rows):
            row_node = builder.add("row", node, str(row_index + 1), attributes={"row": row_index})
            for column_index, cell in enumerate(row.cells):
                cell_locator = {**locator, "row": row_index, "column": column_index}
                builder.add("cell", row_node, f"R{row_index + 1}C{column_index + 1}", cell.text, cell_locator, {"row": row_index, "column": column_index, "rowSpan": cell.span_height, "columnSpan": cell.span_width, "mergeOrigin": cell.is_merge_origin, "spanned": cell.is_spanned})
