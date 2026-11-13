"""HTML5 element tree и CSS-навигация без нативного C-парсера и исполнения JS."""
from dataclasses import dataclass, field
from tinycss2.serializer import serialize_identifier

from .html_dom_builder import LocatedParser
from .html_preview_policy import DOCUMENT_TEXT_LIMIT, TEXT_LIMIT, preview_text, previewable
from .html_source_ranges import SourceRanges, HtmlLimit, normalize_newlines, VOID

BLOCKS = {'header', 'footer', 'nav', 'main', 'section', 'article', 'aside', 'form'}


@dataclass
class Element:
    """HTML5 DOM-элемент; source range отсутствует у неявно созданного элемента."""
    tag: str
    attrs: list
    start: int | None
    line: int | None
    parent: int | None
    end: int | None = None
    end_line: int | None = None
    size: int = 1
    children: list[int] = field(default_factory=list)
    namespace: str = ''
    range_status: str = 'generated'
    selector: str = ''
    text: bool = False
    tail: bool = False
    preview_text: str = ''
    script_range: tuple[int, int] | None = None


class MarkupParser:
    """Возвращает исправленное HTML5-дерево и независимые исходные диапазоны."""
    def __init__(self, text: str, max_elements: int = 5000):
        """До 1 MiB UTF-8, 5000 узлов и глубины 128; один объект на документ."""
        self.text, self.max_elements = text, max_elements
        self.elements, self.warnings, self.title = [], [], []
        self.limited, self.duplicate_safe = False, True
        self.engine = 'html5lib-1.1'
        self.unsupported = []

    def parse(self):
        """Не исполняет HTML/JS; исчерпанный бюджет не выдаёт partial DOM за полный."""
        if len(self.text.encode('utf-8')) > 1024 * 1024:
            self.limited = True
            self.warnings = ['HTML превышает бюджет 1 MiB.']
            return self
        normalized, boundaries = normalize_newlines(self.text)
        ranges = SourceRanges(normalized, boundaries, self.max_elements)
        try:
            ranges.feed(normalized)
            ranges.close()
            parser = LocatedParser(ranges, self.max_elements)
            root = parser.parse(normalized, scripting=False)
            has_template = parser.saw_template
            self._collect(root, parser.tree.locations, capture_preview=not has_template)
            errors = [error for error in parser.errors if not error[1].startswith('expected-doctype')]
            self.warnings = list(dict.fromkeys('HTML5 recovery: ' + error[1] for error in errors))[:6]
            if ranges.template:
                self.warnings.append('Обнаружен неподдержанный язык шаблона.')
            self.duplicate_safe = not errors and not ranges.template
            if has_template:
                # html5lib 1.1 не моделирует отдельный template.content fragment.
                # Не выдаём его обычных детей за DOM браузера или CSS-совпадения.
                self.unsupported = ['template-content']
                self.warnings.append('template.content не поддержан html5lib 1.1; CSS-поиск этого файла недоступен.')
                self.duplicate_safe = False
        except (HtmlLimit, RecursionError):
            self.elements.clear()
            self.limited, self.duplicate_safe = True, False
            self.warnings = ['Исчерпан бюджет узлов или глубины HTML; дерево не захвачено.']
        return self

    @staticmethod
    def _preview_label(node):
        """Keep direct text before and after children, including after omitted icons."""
        parts = []
        remaining = TEXT_LIMIT * 4
        own_text = (node.text or '')[:remaining]
        parts.append(own_text)
        remaining -= len(own_text)
        for child in node:
            if not remaining:
                break
            tail = (child.tail or '')[:remaining]
            if tail:
                parts.append(tail)
                remaining -= len(tail)
        return preview_text(' '.join(parts))

    def _collect(self, root, locations, *, capture_preview):
        """Обходит element tree итеративно; comments/text не получают выдуманных тегов."""
        stack = [(root, None, '', 1, 1, capture_preview)]
        remaining_text = DOCUMENT_TEXT_LIMIT
        while stack:
            node, parent, prefix, position, depth, visible_parent = stack.pop()
            if depth > 128:
                raise HtmlLimit('HTML5 tree depth exceeds 128')
            if not isinstance(node.tag, str):
                continue
            namespace, _, tag = node.tag[1:].partition('}') if node.tag.startswith('{') else ('', '', node.tag)
            source = dict(locations.get(node, {}))
            if source.get('rangeStatus') == 'self-closing-token':
                if namespace == 'http://www.w3.org/1999/xhtml' and tag not in VOID:
                    # <div/> не закрывает div в HTML, но <svg><circle/> закрывает circle.
                    source.update(end=None, endLine=None, rangeStatus='start-tag-only')
                else:
                    source['rangeStatus'] = 'exact'
            selector = prefix + (' > ' if prefix else '') + serialize_identifier(tag) + ':nth-of-type(' + str(position) + ')'
            if len(selector) > 8192:
                raise HtmlLimit('CSS path exceeds bounded depth')
            attributes = list(node.attrib.items())
            visible = visible_parent and previewable(tag, namespace, attributes)
            sketch_text = self._preview_label(node)[:remaining_text] if visible and tag != 'textarea' and remaining_text else ''
            remaining_text -= len(sketch_text)
            item = Element(tag, attributes, source.get('start'), source.get('line'), parent,
                source.get('end'), source.get('endLine'), namespace=namespace,
                range_status=source.get('rangeStatus', 'generated'), selector=selector,
                text=bool(node.text), tail=bool(node.tail), preview_text=sketch_text)
            if tag == 'script' and source.get('rangeStatus') == 'exact' and 'endTagStart' in source:
                start, end = source['startTagEnd'], source['endTagStart']
                # Require the source lexer and HTML5 raw-text parser to agree on the body.
                if self.text[start:end].replace('\r\n', '\n').replace('\r', '\n') == (node.text or ''):
                    item.script_range = (start, end)
            index = len(self.elements)
            self.elements.append(item)
            if parent is not None:
                self.elements[parent].children.append(index)
            if tag == 'title':
                self.title = [''.join(node.itertext())[:400]]
            children, counts = [], {}
            for child in node:
                if not isinstance(child.tag, str):
                    item.text |= bool(child.tail)
                    continue
                counts[child.tag] = counts.get(child.tag, 0) + 1
                children.append((child, index, selector, counts[child.tag], depth + 1, visible))
            stack.extend(reversed(children))
        for item in reversed(self.elements):
            item.size += sum(self.elements[child].size for child in item.children)
