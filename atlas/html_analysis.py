"""HTML-мод дополняет снимок страницами, блоками, ссылками и рекомендациями о дублях."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from .html_markup import MarkupParser
from .html_duplicates import duplicate_groups
from .source_viewer import _read_regular_file, SourceError

VERSION = 'html-dom-v2'


def _selection(root: Path, inventory: dict) -> dict:
    """Явный atlas-html.json задаёт authoring/generated roots; config не исполняется."""
    defaults = {'sourceRoots': ['.'], 'generatedRoots': []}
    if 'atlas-html.json' not in inventory['files']:
        return defaults
    value = json.loads(_read_regular_file(root, Path('atlas-html.json'), 16384))
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError('Unknown HTML selection fields')
    for key, fallback in defaults.items():
        paths = value.get(key, fallback)
        if not isinstance(paths, list) or len(paths) > 16 or (key == 'sourceRoots' and not paths):
            raise ValueError('Invalid HTML source roots')
        for path in paths:
            if not isinstance(path, str) or not path or '\\' in path or ':' in path:
                raise ValueError('Invalid HTML source root')
            relative = PurePosixPath(path)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('HTML roots must be project relative')
        defaults[key] = paths
    return defaults


def _under(filename: str, roots: list[str]) -> bool:
    """Сопоставляет полные сегменты относительного пути, а не строковый префикс."""
    path = PurePosixPath(filename)
    return any(path.is_relative_to(PurePosixPath(root)) for root in roots)


def _documents(root: Path, inventory: dict, metadata: dict) -> list[dict]:
    """Читает до 200 HTML-файлов/16 MiB; unknown output не участвует в выводе о дублях."""
    documents, size, element_count, attempts = [], 0, 0, 0
    selection = _selection(root, inventory)
    metadata['selection'] = selection
    for filename in inventory['files']:
        if PurePosixPath(filename).suffix.lower() not in {'.html', '.htm'}:
            continue
        if not _under(filename, selection['sourceRoots']) or _under(filename, selection['generatedRoots']):
            metadata['excludedFiles'] += 1
            continue
        if attempts >= 200 or size >= 16 * 1024 * 1024 or element_count >= 20000:
            metadata['truncated'] = True
            break
        attempts += 1
        try:
            payload = _read_regular_file(root, Path(filename), min(1024 * 1024, 16 * 1024 * 1024 - size))
            size += len(payload)
            parser = MarkupParser(payload.decode('utf-8'), min(5000, 20000 - element_count)).parse()
            element_count += len(parser.elements)
            metadata['truncated'] |= parser.limited
            suspicious = any(part in {'dist', 'build', 'out', '_site'} for part in PurePosixPath(filename).parts[:-1])
            role = 'unknown-output' if suspicious and selection['sourceRoots'] == ['.'] else 'authoring'
            documents.append({'file': filename, 'sourceHash': sha256(payload).hexdigest(),
                              'parser': parser, 'sourceRole': role})
        except (SourceError, OSError, UnicodeError, AssertionError, ValueError):
            metadata['unavailableFiles'].append(filename)
            metadata['truncated'] = True
    metadata.update(bytesRead=size, elementsParsed=element_count)
    return documents


def _identity(kind: str, value: str) -> str:
    """Раздельное пространство derived-ID не заменяет идентификаторы Graphify."""
    return 'atlas-html:' + kind + ':' + sha256(value.encode()).hexdigest()[:24]


def _facts(document: dict, provider: dict) -> dict:
    """Общий provenance принадлежит захваченному исходнику, не текущему DOM."""
    return {'version': VERSION, 'language': 'html', 'file': document['file'],
        'sourceHash': document['sourceHash'], 'sourceKind': 'snapshot-captured-source',
        'sourceRole': document['sourceRole'], 'runtimeStatus': 'unknown',
        'modId': provider['modId'], 'modFingerprint': provider['modFingerprint']}


def _reference(filename: str, value: str, files: set[str]) -> str | None:
    """Разрешает только буквальные локальные пути в inventory; URL не загружаются."""
    if len(value) > 1000 or any(marker in value for marker in ('{{', '{%', '<%')):
        return None
    try:
        url = urlsplit(value)
        path = unquote(url.path)
    except ValueError:
        return None
    if url.scheme or url.netloc or not path or '\\' in path:
        return None
    target = PurePosixPath(path.lstrip('/')) if path.startswith('/') else PurePosixPath(filename).parent / path
    if '..' in target.parts or ':' in str(target):
        return None
    return str(target) if str(target) in files else None


class _Capture:
    """Владеет добавлением derived-узлов и связей, не переписывая исходные факты."""
    def __init__(self, graph: dict, provider: dict, metadata: dict):
        """Копирует изменяемую поверхность снимка и индексирует существующие file-ID."""
        self.result = {**graph, 'nodes': [dict(node) for node in graph.get('nodes', [])],
            'edges': list(graph.get('edges', [])), 'metadata': deepcopy(graph.get('metadata', {}))}
        self.provider, self.metadata = provider, metadata
        self.result['metadata']['htmlAnalysis'] = metadata
        self.file_nodes = {node.get('file'): node for node in self.result['nodes'] if node.get('kind') == 'file'}
        self.used_ids = {node['id'] for node in self.result['nodes']}
        self.element_nodes, self.pages = {}, {}
        self.initial = len(self.result['nodes'])

    def add(self, node):
        """Не допускает collision и незаметного превышения объёма derived-слоя."""
        if len(self.result['nodes']) - self.initial >= 5000:
            self.metadata['truncated'] = True
            return False
        if node['id'] in self.used_ids:
            raise ValueError('HTML derived ID collides with existing graph')
        self.used_ids.add(node['id'])
        self.result['nodes'].append(node)
        return True

    def document(self, document, groups):
        """Регистрирует одну страницу, её выбранные блоки и containment, не вызовы."""
        parser, filename = document['parser'], document['file']
        facts = _facts(document, self.provider)
        facts.update(role='page' if any(item.tag == 'html' and item.start is not None for item in parser.elements) else 'fragment',
            title=''.join(parser.title)[:400], status='partial' if parser.warnings or parser.limited else 'parsed',
            warnings=parser.warnings, elementCount=len(parser.elements), truncated=parser.limited,
            parser=parser.engine, treeKind='html5-element-tree', scripting=False, unsupported=parser.unsupported)
        node = self.file_nodes.get(filename)
        if node is None:
            node = {'id': _identity('file', filename), 'kind': 'file', 'file': filename,
                    'line': 1, 'label': PurePosixPath(filename).name}
            if not self.add(node):
                return
        node['htmlFacts'] = facts
        node.setdefault('comment', f"HTML {facts['role']}: {facts['title'] or filename}. Статическая структура; runtime не проверен.")
        self.pages[filename] = node
        selected = range(len(parser.elements))
        for index in selected:
            item = parser.elements[index]
            attrs = dict(item.attrs)
            label = item.tag + (('#' + attrs['id'][:80]) if attrs.get('id') else '')
            identity = filename + ':' + str(item.start) if item.start is not None else filename + ':dom:' + str(index)
            block = {'id': _identity('block', identity), 'kind': 'markup',
                'label': label, 'file': filename, 'line': item.line, 'endLine': item.end_line or item.line,
                'htmlFacts': {**_facts(document, self.provider), 'role': 'block', 'tag': item.tag,
                    'start': item.start, 'end': item.end, 'offsetUnit': 'unicode-codepoints',
                    'rangeStatus': item.range_status, 'generated': item.start is None,
                    'cssSelector': item.selector, 'parentElementIndex': item.parent,
                    'elementIndex': index, 'namespace': item.namespace, 'parser': parser.engine,
                    'attributes': [{'name': key, 'value': (value or '')[:300]} for key, value in item.attrs[:20]],
                    'truncated': len(item.attrs) > 20}, 'comment': 'Статический HTML-блок; callback и DOM не исполнялись.'}
            if not self.add(block):
                break
            self.element_nodes[(filename, index)] = block
        if all((filename, index) in self.element_nodes for index in selected):
            # Полное element tree хранится отдельно от кратких карточек. Query
            # использует captured attrs/текстовые признаки, а не текущий файл.
            self.result['metadata'].setdefault('htmlDocuments', {})[filename] = {
                'fileNodeId': node['id'], 'sourceHash': document['sourceHash'],
                'parser': parser.engine, 'truncated': parser.limited, 'warnings': parser.warnings,
                'unsupported': parser.unsupported,
                'elements': [{'nodeId': self.element_nodes[(filename, index)]['id'],
                    'tag': item.tag, 'namespace': item.namespace, 'attrs': item.attrs,
                    'parent': item.parent, 'text': item.text, 'tail': item.tail}
                    for index, item in enumerate(parser.elements)]}
            preview_texts = {self.element_nodes[(filename, index)]['id']: item.preview_text
                             for index, item in enumerate(parser.elements) if item.preview_text}
            if preview_texts:
                self.result['metadata'].setdefault('htmlPreviewText', {})[filename] = preview_texts
            facts['treeCaptured'] = True
        else:
            facts.update(treeCaptured=False, truncated=True)
        for index in selected:
            block = self.element_nodes.get((filename, index))
            if block is None:
                continue
            parent = parser.elements[index].parent
            while parent is not None and (filename, parent) not in self.element_nodes:
                parent = parser.elements[parent].parent
            owner = self.element_nodes.get((filename, parent), node)
            block['htmlFacts']['parentNodeId'] = owner['id']
            owner['htmlFacts'].setdefault('childNodeIds', []).append(block['id'])
            self.result['edges'].append({'source': owner['id'], 'target': block['id'],
                'relation': 'contains', 'confidence': 'EXTRACTED', 'sourceKind': 'snapshot-captured-source'})

    def duplicate(self, group):
        """Публикует один совет и его проверяемые диапазоны; не меняет health."""
        occurrences = []
        for item in group['occurrences']:
            occurrence = {key: value for key, value in item.items() if key != 'elementIndex'}
            block = self.element_nodes.get((item['file'], item['elementIndex']))
            if block:
                occurrence['nodeId'] = block['id']
            occurrences.append(occurrence)
        duplicate = {'id': group['id'], 'kind': 'markup_duplicate', 'label': 'Повтор HTML: ' + group['tag'],
            'file': occurrences[0]['file'], 'line': occurrences[0]['line'],
            'comment': group['recommendation'], 'htmlFacts': {'version': VERSION, 'role': 'duplicate-group',
                'sourceKind': 'snapshot-captured-source', 'runtimeStatus': 'unknown', 'modId': self.provider['modId'],
                'modFingerprint': self.provider['modFingerprint'],
                **{key: value for key, value in group.items() if key != 'occurrences'}, 'occurrences': occurrences}}
        if not self.add(duplicate):
            return
        for occurrence in occurrences:
            if occurrence.get('nodeId'):
                self.result['edges'].append({'source': occurrence['nodeId'], 'target': duplicate['id'],
                    'relation': 'duplicates', 'confidence': 'EXTRACTED'})
            page = self.pages.get(occurrence['file'])
            if page:
                ids = page['htmlFacts'].setdefault('duplicateGroupIds', [])
                if group['id'] not in ids:
                    ids.append(group['id'])

    def references(self, document, available_files):
        """Разрешает буквальные ссылки на известные страницы без загрузки URL."""
        page = self.pages.get(document['file'])
        if not page:
            return
        references = []
        for item in document['parser'].elements:
            for key, value in item.attrs:
                if key not in {'href', 'src', 'action'} or not value:
                    continue
                target = _reference(document['file'], value, available_files)
                if len(references) >= 40:
                    page['htmlFacts']['referencesTruncated'] = True
                    break
                references.append({'attribute': key, 'value': value[:300], 'line': item.line,
                                   'targetFile': target, 'status': 'resolved-file' if target else 'unresolved'})
                if target in self.pages:
                    self.result['edges'].append({'source': page['id'], 'target': self.pages[target]['id'],
                        'relation': 'references', 'confidence': 'EXTRACTED'})
        page['htmlFacts']['references'] = references


def capture_html(graph: dict, root: Path, rules: dict, inventory: dict) -> dict:
    """Координирует чтение, поиск дублей и публикацию bounded HTML-слоя."""
    providers = rules.get('markupProviders', [])
    if not providers:
        return graph
    metadata = {'version': VERSION, 'truncated': bool(inventory['truncated']),
                'unavailableFiles': [], 'excludedFiles': 0, 'warnings': []}
    capture = _Capture(graph, providers[0], metadata)
    try:
        documents = _documents(root, inventory, metadata)
    except (ValueError, SourceError, OSError):
        metadata.update(truncated=True, warnings=['atlas-html.json недоступен или некорректен; HTML-анализ пропущен.'])
        return capture.result
    groups, limited = duplicate_groups(documents)
    metadata['truncated'] |= limited
    for document in documents:
        capture.document(document, groups)
    for group in groups:
        capture.duplicate(group)
    files = set(inventory['files'])
    for document in documents:
        capture.references(document, files)
    partial = metadata['truncated'] or any(item['parser'].warnings or item['sourceRole'] != 'authoring' for item in documents)
    metadata.update(fileCount=len(documents), duplicateGroupCount=len(groups),
                    derivedNodeCount=len(capture.result['nodes']) - capture.initial,
                    status='partial' if partial else 'complete')
    return capture.result
