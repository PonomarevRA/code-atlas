"""Запросы только опубликованных индексов; оригиналы и парсеры не открываются."""

import base64
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from .models import MAX_QUERY_BYTES, encode, identity, require


@contextmanager
def database(path):
    """SQLite открыт в immutable read-only режиме без создания отсутствующего файла."""
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        yield connection
    finally:
        connection.close()


def _offset(cursor, scope):
    """Курсор связан со снимком, операцией и фильтрами и живёт не более суток."""
    if not cursor:
        return 0
    require(isinstance(cursor, str) and len(cursor) <= 2048, 'INVALID_CURSOR', 'Некорректный размер курсора.')
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        valid = value['scope'] == scope and type(value['offset']) is int and value['offset'] >= 0 and 0 <= time.time() - value['issued'] <= 86400
        require(valid, 'INVALID_CURSOR', 'Курсор не относится к запросу или истёк.')
        return value['offset']
    except (ValueError, KeyError, TypeError, UnicodeError):
        require(False, 'INVALID_CURSOR', 'Некорректный курсор.')


def _cursor(scope, offset):
    """Сериализует смещение без произвольных SQL или путей."""
    return base64.urlsafe_b64encode(encode({'scope': scope, 'offset': offset, 'issued': time.time()}).encode()).decode()


def bound_id(node_id, binding):
    """Связывает revision-local ID с документом, чтобы одинаковые blobs не сливали пути."""
    return binding['documentId'][:24] + ':' + node_id


def stored_id(node_id, binding):
    """Чужой document binding не может адресовать узел через совпавшую ревизию."""
    prefix = binding['documentId'][:24] + ':'
    return node_id[len(prefix):] if isinstance(node_id, str) and node_id.startswith(prefix) else None


def project(node, binding, include_text=False):
    """Добавляет provenance binding; выдаёт preview вместо неограниченного body."""
    result = dict(node)
    result['id'] = bound_id(result['id'], binding)
    if result.get('parentId'):
        result['parentId'] = bound_id(result['parentId'], binding)
    if result.get('attributes', {}).get('cellContainerId'):
        result['attributes'] = {**result['attributes'], 'cellContainerId': bound_id(result['attributes']['cellContainerId'], binding)}
    text = result.pop('text', '')
    result['preview'] = text[:1200]
    result['contentRef'] = {'nodeId': result['id'], 'revisionId': result.get('revisionId'), 'unit': 'unicode-codepoints'}
    if include_text:
        result['text'] = text[:12000]
        result['contentTruncated'] = len(text) > 12000
    if result.get('parentId') is None:
        result['label'] = binding['path']
    result['documentId'] = binding['documentId']
    result['sourcePath'] = binding['path']
    result['sourceHash'] = binding['sourceHash']
    return result


def nodes_page(root, manifest, operation, *, parent_id=None, document_id=None, query='', node_id=None, cursor=None, limit=50, **unused):
    """Постранично читает дерево/поиск с лимитами элементов и сериализованных байтов."""
    require(not unused, 'INVALID_QUERY', 'Неизвестные параметры запроса: ' + ', '.join(unused))
    require(type(limit) is int and 1 <= limit <= 50, 'INVALID_LIMIT', 'Размер страницы должен быть 1..50.')
    require(isinstance(query, str) and len(query) <= 500, 'INVALID_QUERY', 'Слишком длинная поисковая строка.')
    scope = identity(manifest['mapId'], manifest['snapshotId'], operation, parent_id, document_id, query, node_id)
    offset = _offset(cursor, scope)
    items, skipped, has_more = [], 0, False
    bindings = manifest['documents']
    for binding in bindings:
        if document_id and binding['documentId'] != document_id:
            continue
        clauses, parameters = [], []
        if operation == 'tree':
            parent = stored_id(parent_id, binding) if parent_id is not None else None
            if parent_id is not None and parent is None:
                continue
            clauses.append('parent_id IS ?')
            parameters.append(parent)
        elif operation == 'search':
            clauses.append("instr(lower(label || ' ' || text), lower(?)) > 0")
            parameters.append(query)
        elif operation == 'node':
            stored = stored_id(node_id, binding)
            if stored is None:
                continue
            clauses.append('id = ?')
            parameters.append(stored)
        sql = 'SELECT payload FROM nodes'
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        sql += ' ORDER BY position, id'
        with database(Path(root) / 'revisions' / binding['revisionId'] / 'index.sqlite') as connection:
            for row in connection.execute(sql, parameters):
                if skipped < offset:
                    skipped += 1
                    continue
                node = project(json.loads(row[0]), binding, include_text=operation == 'node')
                candidate = {'items': items + [node], 'snapshotId': manifest['snapshotId'], 'nextCursor': 'x' * 512}
                if len(items) >= limit or len(encode(candidate).encode()) > MAX_QUERY_BYTES:
                    require(bool(items), 'RESPONSE_LIMIT', 'Один узел превышает предел ответа; запросите меньшую проекцию.')
                    has_more = True
                    break
                items.append(node)
            if has_more:
                break
    return {'items': items, 'snapshotId': manifest['snapshotId'], 'nextCursor': _cursor(scope, offset + len(items)) if has_more else None}


def table_page(root, manifest, node_id, *, row_start=0, row_count=20, column_start=0, column_count=20, document_id=None, **unused):
    """Возвращает sparse cell window; отсутствующие клетки не материализуются."""
    require(not unused, 'INVALID_QUERY', 'Неизвестные параметры таблицы: ' + ', '.join(unused))
    require(all(type(value) is int and value >= 0 for value in (row_start, row_count, column_start, column_count)), 'INVALID_WINDOW', 'Ожидаются неотрицательные координаты.')
    require(1 <= row_count * column_count <= 1000, 'CELL_LIMIT', 'Окно не должно превышать 1000 ячеек.')
    cells, table = [], None
    for binding in manifest['documents']:
        if document_id and binding['documentId'] != document_id:
            continue
        stored = stored_id(node_id, binding)
        if stored is None:
            continue
        with database(Path(root) / 'revisions' / binding['revisionId'] / 'index.sqlite') as connection:
            record = connection.execute('SELECT payload FROM nodes WHERE id=?', (stored,)).fetchone()
            if not record:
                continue
            table = project(json.loads(record[0]), binding)
            require(table['kind'] in {'table', 'sheet'}, 'INVALID_TABLE', 'Узел не является таблицей или листом.')
            attributes = json.loads(record[0]).get('attributes', {})
            cell_range = attributes.get('cellRange', {})
            origin_row, origin_column = cell_range.get('rowStart', 0), cell_range.get('columnStart', 0)
            container = attributes.get('cellContainerId', stored)
            # Stop at nested tables: their cells belong to a different table window.
            sql = '''WITH RECURSIVE descendants(id, kind) AS (SELECT id, kind FROM nodes WHERE parent_id=? UNION ALL SELECT n.id, n.kind FROM nodes n JOIN descendants d ON n.parent_id=d.id WHERE d.kind!='table') SELECT payload FROM nodes WHERE id IN (SELECT id FROM descendants) AND kind='cell' ORDER BY position'''
            for row in connection.execute(sql, (container,)):
                cell = json.loads(row[0])
                attributes = cell.get('attributes', {})
                r, c = attributes.get('row', -1), attributes.get('column', -1)
                in_range = origin_row <= r < cell_range.get('rowEndExclusive', 2 ** 63) and origin_column <= c < cell_range.get('columnEndExclusive', 2 ** 63)
                if in_range and origin_row + row_start <= r < origin_row + row_start + row_count and origin_column + column_start <= c < origin_column + column_start + column_count:
                    projected = project(cell, binding, include_text=True)
                    require(len(encode({'table': table, 'cells': cells + [projected]}).encode()) < MAX_QUERY_BYTES - 1000, 'RESPONSE_LIMIT', 'Уменьшите окно таблицы.')
                    cells.append(projected)
            break
    require(table is not None, 'NODE_MISSING', 'Таблица не найдена.', 404)
    return {'snapshotId': manifest['snapshotId'], 'table': table, 'cells': cells, 'window': {'rowStart': row_start, 'rowCount': row_count, 'columnStart': column_start, 'columnCount': column_count, 'coordinateSystem': 'container-relative', 'origin': {'row': origin_row, 'column': origin_column}, 'cellCoordinateBasis': 0}}


def read_content(root, manifest, node_id, *, offset=0, limit=4000, document_id=None):
    """Читает окно текста в Unicode codepoints; конец исключён, bytes всегда <=64k."""
    require(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 12000, 'INVALID_WINDOW', 'Ожидается offset>=0 и limit=1..12000 символов.')
    for binding in manifest['documents']:
        if document_id and binding['documentId'] != document_id:
            continue
        stored = stored_id(node_id, binding)
        if stored is None:
            continue
        with database(Path(root) / 'revisions' / binding['revisionId'] / 'index.sqlite') as connection:
            row = connection.execute('SELECT text FROM nodes WHERE id=?', (stored,)).fetchone()
            if row is None:
                continue
            # Python slicing preserves embedded NUL; SQLite length/substr stop at NUL.
            content = row[0]
            text, total = content[offset:offset + limit], len(content)
            next_offset = offset + len(text)
            return {'snapshotId': manifest['snapshotId'], 'nodeId': node_id, 'documentId': binding['documentId'], 'sourcePath': binding['path'], 'sourceHash': binding['sourceHash'], 'text': text, 'offset': offset, 'unit': 'unicode-codepoints', 'endExclusive': True, 'totalCharacters': total, 'nextOffset': next_offset if next_offset < total else None}
    require(False, 'NODE_MISSING', 'Узел не найден.', 404)
