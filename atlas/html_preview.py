"""A bounded, presentation-only sketch from captured HTML5 facts."""

from .html_preview_policy import preview_text, previewable
from .html_selectors import HtmlQueryError

MAX_PREVIEW_ROWS = 60
MAX_PREVIEW_DEPTH = 5
MAX_CAPTURED_ELEMENTS = 5000

TAG_KINDS = {
    **dict.fromkeys(('h1', 'h2', 'h3', 'h4', 'h5', 'h6'), 'heading'),
    **dict.fromkeys(('p', 'span', 'strong', 'em', 'small', 'label', 'code', 'pre', 'option'), 'text'),
    **dict.fromkeys(('ul', 'ol', 'dl', 'table'), 'list'),
    **dict.fromkeys(('li', 'dt', 'dd', 'tr', 'td', 'th'), 'item'),
    **dict.fromkeys(('button', 'input', 'textarea', 'select'), 'control'),
    **dict.fromkeys(('img', 'picture', 'audio', 'video', 'canvas'), 'media'),
    'a': 'link',
}
KIND_LABELS = {'heading': 'Заголовок', 'text': 'Текст', 'list': 'Список', 'item': 'Элемент',
               'control': 'Поле или кнопка', 'media': 'Медиа', 'link': 'Ссылка', 'container': 'Блок'}
VOID_SKETCHES = {'input', 'textarea', 'img', 'picture', 'audio', 'video', 'canvas'}


def _parent(item, index):
    """Accept only an earlier parent index, matching the immutable preorder contract."""
    parent = item.get('parent')
    if parent is None and index == 0:
        return None
    if type(parent) is not int or not 0 <= parent < index:
        raise HtmlQueryError('Нарушена вложенность сохранённого HTML5-дерева.', 409)
    return parent


def _allowed(item):
    """Project only known passive nodes; no attribute name or value is sent to the client."""
    return previewable(item['tag'], item.get('namespace', ''), item.get('attrs', []))


def _selected_root(document, elements, node_id):
    """Select the file or one captured element, respecting hidden and foreign ancestors."""
    if node_id == document['fileNodeId']:
        index = 0
    else:
        index = next((index for index, item in enumerate(elements) if item.get('nodeId') == node_id), None)
    if index is None:
        raise HtmlQueryError('Элемент отсутствует в сохранённом HTML5-дереве.', 404)
    parent = index
    while parent is not None:
        item = elements[parent]
        if not _allowed(item):
            raise HtmlQueryError('Для этого элемента статический эскиз недоступен.', 422)
        parent = _parent(item, parent)
    return index


def _sketch(elements, root, captured_text):
    """Return parent-indexed passive rows, with a visible flag when a budget omits content."""
    rows = []
    visible = {}
    truncated = False
    for index in range(root, len(elements)):
        item = elements[index]
        parent = _parent(item, index)
        if index != root and (parent is None or parent < root):
            break
        parent_row = visible.get(parent)
        if index != root and parent_row is None:
            continue
        if not _allowed(item):
            continue
        depth = 0 if parent_row is None else rows[parent_row]['depth'] + 1
        if depth > MAX_PREVIEW_DEPTH:
            truncated = True
            continue
        if len(rows) >= MAX_PREVIEW_ROWS:
            truncated = True
            break
        tag = item['tag']
        kind = TAG_KINDS.get(tag, 'container')
        fallback = tag if tag in {'html', 'body', 'main', 'section', 'article', 'header', 'footer', 'nav', 'aside', 'form'} else KIND_LABELS[kind]
        label = '' if tag in {'input', 'textarea'} else preview_text(captured_text.get(item['nodeId']))
        rows.append({'parent': parent_row, 'depth': depth, 'kind': kind, 'label': label or fallback})
        if tag not in VOID_SKETCHES:
            visible[index] = len(rows) - 1
    return rows, truncated


def build_html_preview(graph, snapshot_id, node_id):
    """Read a snapshot DTO only. No source filesystem or browser interaction occurs here."""
    node = next((row for row in graph['nodes'] if row['id'] == node_id), None)
    if not node or (node.get('htmlFacts') or {}).get('role') not in {'page', 'fragment', 'block'}:
        raise HtmlQueryError('HTML-узел не найден в снимке.', 404)
    file = node.get('file')
    metadata = graph.get('metadata', {})
    document = metadata.get('htmlDocuments', {}).get(file)
    if not document or not 0 < len(document.get('elements', [])) <= MAX_CAPTURED_ELEMENTS:
        raise HtmlQueryError('HTML5-дерево недоступно. Обновите карту с HTML-модом.', 409)
    if 'template-content' in (document.get('unsupported') or []):
        raise HtmlQueryError('Эскиз недоступен для файла с template: его содержимое может быть восстановлено неточно.', 422)
    elements = document['elements']
    root = _selected_root(document, elements, node_id)
    rows, truncated = _sketch(elements, root, metadata.get('htmlPreviewText', {}).get(file, {}))
    return {'snapshotId': snapshot_id, 'nodeId': node_id, 'rows': rows,
            'truncated': truncated or bool(document.get('truncated')),
            'sourceKind': 'snapshot-captured-source'}
