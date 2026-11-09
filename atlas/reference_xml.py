"""XML doc-comments и DocFX Markdown превращаются в текст, без загрузки xref/include."""
from __future__ import annotations

import re
import textwrap
from urllib.parse import parse_qs, unquote

_XREF = re.compile(r"<xref:([^<>\s]+)>")
_INLINE_CODE = re.compile(r"(?<![\w`])(`+)(.*?)\1", re.S)
_LABELS = {"remarks": "Remarks", "example": "Examples", "examples": "Examples", "returns": "Returns",
           "param": "Parameter", "typeparam": "Type parameter", "exception": "Exception",
           "value": "Value", "seealso": "See also", "permission": "Permission"}


def reference_label(identity: str) -> str:
    """Убирает служебный префикс DocId и display query только из видимой подписи."""
    target, _, query = unquote(identity).partition("?")
    label = re.sub(r"^(?:[TMPFEON]:|Overload:)", "", target).rstrip("*")
    display = parse_qs(query).get("displayProperty", [""])[0]
    if display == "name":
        return label.rsplit(".", 1)[-1]
    if display == "nameWithType":
        return ".".join(label.split(".")[-2:])
    return label


def xref_markdown(identity: str, label: str = "") -> str:
    """Сохраняет точный UID в локальной ссылке; адрес внешнего документа не угадывается."""
    caption = label or reference_label(identity)
    caption = caption.replace("[", r"\[").replace("]", r"\]")
    return f"[{caption}](xref:{identity})"


def normalize_xrefs(markdown: str) -> str:
    """Преобразует DocFX xref вне fenced/inline code, сохраняя примеры буквально."""
    lines, fence = [], None
    for line in markdown.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            run = marker.group(1)
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not line[marker.end():].strip():
                fence = None
            lines.append(line)
            continue
        if fence is not None:
            lines.append(line)
            continue
        # Разделяем inline code, чтобы литерал <xref:...> не стал ссылкой в коде.
        parts, start = [], 0
        for match in _INLINE_CODE.finditer(line):
            parts.append(_XREF.sub(lambda item: xref_markdown(item.group(1)), line[start:match.start()]))
            parts.append(match.group(0))
            start = match.end()
        parts.append(_XREF.sub(lambda item: xref_markdown(item.group(1)), line[start:]))
        lines.append("".join(parts))
    return "".join(lines)


def _prose(text: str | None) -> str:
    """XML indentation не создаёт лишние пробелы внутри обычного абзаца."""
    return re.sub(r"\s+", " ", text or "")


def _content(node) -> str:
    """Соединяет mixed content по исходному порядку, включая хвосты дочерних узлов."""
    parts = [_prose(node.text)]
    for child in node:
        parts.append(_element(child))
        parts.append(_prose(child.tail))
    return "".join(parts).strip()


def _table_cell(node) -> str:
    """Литерал | в XML ячейке не становится новой колонкой Markdown таблицы."""
    return _content(node).replace("|", r"\|").replace("\n", " ")


def _element(node) -> str:
    """Сохраняет абзацы, code, ссылки, списки и Markdown CDATA известных XML тегов."""
    tag = node.tag
    if tag == "format" and node.get("type") == "text/markdown":
        return "\n\n" + normalize_xrefs(textwrap.dedent("".join(node.itertext())).strip()) + "\n\n"
    if tag == "code":
        code = textwrap.dedent("".join(node.itertext())).strip("\r\n")
        # Более длинный fence оставляет встроенные backticks частью примера.
        runs = re.findall(r"`+", code)
        fence = "`" * max(3, max((len(run) + 1 for run in runs), default=3))
        language = re.sub(r"[^\w+#.-]", "", node.get("language", node.get("lang", "")))
        return f"\n\n{fence}{language}\n{code}\n{fence}\n\n"
    if tag in {"see", "seealso"}:
        if node.get("cref"):
            return xref_markdown(node.get("cref"), _content(node))
        return node.get("langword") or _content(node) or node.get("href", "")
    if tag in {"paramref", "typeparamref"}:
        return node.get("name", "")
    if tag == "c":
        value = "".join(node.itertext())
        runs = re.findall(r"`+", value)
        fence = "`" * max(1, max((len(run) + 1 for run in runs), default=1))
        return f"{fence} {value} {fence}"
    if tag in {"para", "p", "note"}:
        return "\n\n" + _content(node) + "\n\n"
    if tag == "br":
        return "\n"
    if tag == "list":
        rows = []
        if node.get("type") == "table":
            header = node.find("listheader")
            headings = [_table_cell(part) for part in header] if header is not None else ["Term", "Description"]
            rows.append("| " + " | ".join(headings) + " |")
            rows.append("| " + " | ".join("---" for _ in headings) + " |")
            for item in node.findall("item"):
                cells = []
                for part in item:
                    cells.append(_table_cell(part))
                rows.append("| " + " | ".join(cells) + " |")
            return "\n\n" + "\n".join(rows) + "\n\n"
        for index, item in enumerate(node.findall("item"), 1):
            term, description = item.find("term"), item.find("description")
            body = _content(item) if description is None else _content(description)
            if term is not None and description is not None:
                body = _content(term) + ": " + body
            marker = f"{index}." if node.get("type") == "number" else "-"
            rows.append(f"{marker} {body}")
        return "\n\n" + "\n".join(rows) + "\n\n"
    return _content(node)


def xml_sections(node) -> str:
    """Одинаковые Markdown разделы для compiled NuGet XML и dotnet-api-docs Type/Docs."""
    sections = []
    for section in node:
        if section.tag in {"inheritdoc", "include"}:
            sections.append(f"Unresolved {section.tag}: {section.attrib}")
            continue
        body = _element(section).strip()
        if section.tag == "summary":
            sections.append(body)
            continue
        label = _LABELS.get(section.tag, section.tag)
        detail = section.get("name", section.get("cref", ""))
        heading = label + (" · " + reference_label(detail) if detail else "")
        if not re.match(rf"^##\s+{re.escape(label)}\s*(?:\n|$)", body, re.I):
            body = f"## {heading}\n\n{body}"
        sections.append(body)
    return "\n\n".join(sections)


def xml_summary(node) -> str:
    """Авторский summary для поиска без заголовков; inline ссылки остаются читаемыми."""
    return _content(node) if node is not None else ""
