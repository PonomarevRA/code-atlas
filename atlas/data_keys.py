"""Рейтинг ключей и ограниченные пути «поле → значение → записи» из снимка."""

from collections import deque
import json

from .data_graph import neighbors
from .data_models import MAX_RESPONSE_BYTES, require
from .data_queries import compact, cursor, offset, page, record, scope_key
from .data_storage import encode, identity


def available_keys(metadata):
    """Предлагает только индексированные поля; ID закреплён за коллекцией и полем."""
    keys = []
    for collection in metadata['collections'].values():
        for field in collection['fields']:
            if field['indexed']:
                keys.append({'id': collection['id'] + ':key:' + identity(field['name']),
                             'kind': 'data-key', 'label': field['name'], 'field': field['name'],
                             'collectionId': collection['id'], 'file': collection['file'], 'column': 0})
    return keys


def selected_key(metadata, query):
    """Не позволяет подставить произвольное поле или ключ другой карты."""
    key = next((key for key in available_keys(metadata) if key['id'] == query.keyId), None)
    require(key is not None, 'KEY_MISSING', 'Индексированный ключ не найден в этом снимке.', 404)
    return key


def public_key(key):
    """Большие имена полей не обходят байтовый бюджет; выбор работает по устойчивому ID."""
    return compact(key, 300)


def matched_records(connection, metadata, key):
    """Максимум по одному правилу не удваивает счётчик при нескольких похожих отношениях."""
    maximum = 0
    for rule in metadata['relations']['items']:
        selected = (key['collectionId'], key['field'])
        if selected == (rule['sourceCollection'], rule['sourceField']):
            maximum = max(maximum, rule['coverage']['matched'])
        elif selected == (rule['targetCollection'], rule['targetField']):
            comparison = 't.type=s.type AND t.value=s.value' if rule['compareAs'] == 'typed' else 't.raw=s.raw'
            count = connection.execute(
                'SELECT COUNT(*) FROM cells s WHERE s.collection=? AND s.field=? AND s.type!=\'null\' '
                'AND EXISTS (SELECT 1 FROM cells t WHERE t.collection=? AND t.field=? AND t.type!=\'null\' AND '
                + comparison + ')', (key['collectionId'], key['field'], rule['sourceCollection'], rule['sourceField'])).fetchone()[0]
            maximum = max(maximum, count)
    return maximum


def rank_keys(connection, metadata, map_id, query):
    """Сортирует по лишним повторениям и межфайловым совпадениям; null не повышает рейтинг."""
    keys = available_keys(metadata)
    if query.collectionId:
        require(query.collectionId in metadata['collections'], 'COLLECTION_MISSING', 'Коллекция не найдена.', 404)
        keys = [key for key in keys if key['collectionId'] == query.collectionId]
    for key in keys:
        stats = connection.execute(
            'SELECT COALESCE(SUM(n-1),0) repeats, COALESCE(MAX(n),0) frequency, COUNT(*) distinct_values '
            'FROM (SELECT COUNT(*) n FROM cells WHERE collection=? AND field=? AND type!=\'null\' GROUP BY type,value)',
            (key['collectionId'], key['field'])).fetchone()
        matched = matched_records(connection, metadata, key)
        key.update(repeatOccurrences=stats['repeats'], maxFrequency=stats['frequency'],
                   distinctValues=stats['distinct_values'], matchedRecords=matched,
                   score=stats['repeats'] + matched)
    keys.sort(key=lambda key: (-key['score'], -key['maxFrequency'], key['file'], key['field'], key['id']))
    result = page([public_key(key) for key in keys], map_id, query, 'keys')
    result['ranking'] = 'score = repeatOccurrences + matchedRecords; null excluded; typed repeats; matches use relation.compareAs'
    return result


def value_node(key, representative, value_type, label, count):
    """Представитель заменяет передачу большого значения; типы и ведущие нули сохраняются."""
    shortened = len(label) >= 180
    if value_type == 'null':
        label = 'null'
    elif value_type == 'boolean':
        label = label.lower()
    elif label == '':
        label = '«» (пустая строка)'
    if shortened:
        label += '…'
    return {'id': key['id'] + ':value:' + identity(representative), 'kind': 'data-value',
            'label': label, 'valueType': value_type, 'count': count, 'owner': key['id'], 'column': 1,
            'keyId': key['id'], 'valueRecordId': representative, 'contentTruncated': shortened}


def key_values(connection, metadata, map_id, query):
    """Группирует точные typed-значения в SQLite; возвращает страницу по убыванию частоты."""
    key = selected_key(metadata, query)
    scope = scope_key(map_id, query, 'key-values')
    start = offset(query.cursor, scope)
    rows = connection.execute(
        'SELECT type, substr(MIN(raw),1,180) label, MIN(record) representative, COUNT(*) n '
        'FROM cells WHERE collection=? AND field=? GROUP BY type,value '
        'ORDER BY n DESC,type,value LIMIT ? OFFSET ?',
        (key['collectionId'], key['field'], query.limit + 1, start)).fetchall()
    items = [value_node(key, row['representative'], row['type'], row['label'], row['n']) for row in rows[:query.limit]]
    more = len(rows) > query.limit
    return {'mapId': map_id, 'snapshotId': query.snapshotId, 'key': public_key(key), 'items': items,
            'truncated': more, 'nextCursor': cursor(scope, start + len(items)) if more else None}


def record_node(row, metadata, column, fields):
    """Карточка хранит короткие значения и координаты; полная запись доступна отдельным чтением."""
    value = compact(record(row, fields), 180)
    if len(encode(value).encode()) > 2000:
        provenance = json.loads(row['provenance'])
        value = {'id': row['id'], 'collectionId': row['collection'], 'contentTruncated': True,
                 'provenance': {key: compact(item, 180) for key, item in provenance.items()
                                if key in {'file', 'line', 'pointer', 'sourceKind', 'sourceHash'}}}
    source = value['provenance']
    label = f"{metadata['collections'][row['collection']]['label'][:100]} · {row['ordinal']}"
    return {**value, 'kind': 'data-record', 'label': label, 'column': column,
            'description': f"{source.get('file', '')}:{source.get('line', source.get('pointer', ''))}"}


def key_path(connection, metadata, map_id, query):
    """Изолирует одну группу значения и BFS связей без несвязанных строк; все бюджеты явны."""
    key = selected_key(metadata, query)
    require(query.maxNodes >= 3, 'INVALID_GRAPH_LIMIT', 'Для пути нужны минимум три узла.')
    cell = connection.execute('SELECT type,value,substr(raw,1,180) label FROM cells '
                              'WHERE collection=? AND field=? AND record=?',
                              (key['collectionId'], key['field'], query.valueRecordId)).fetchone()
    require(cell is not None, 'VALUE_MISSING', 'Представитель значения не относится к выбранному ключу.', 404)
    scope = scope_key(map_id, query, 'key-path')
    start = offset(query.cursor, scope)
    parameters = (key['collectionId'], key['field'], cell['type'], cell['value'])
    condition = 'collection=? AND field=? AND type=? AND value=?'
    count = connection.execute('SELECT COUNT(*) FROM cells WHERE ' + condition, parameters).fetchone()[0]
    # Сначала резервируем место для страницы корней: соседние данные не вытесняют её.
    root_limit = min(query.limit, query.maxNodes - 2, 16)
    roots = connection.execute('SELECT c.record FROM cells c JOIN records r ON r.id=c.record WHERE '
                               'c.collection=? AND c.field=? AND c.type=? AND c.value=? ORDER BY r.ordinal,c.record LIMIT ? OFFSET ?',
                               (*parameters, root_limit + 1, start)).fetchall()
    value = value_node(key, query.valueRecordId, cell['type'], cell['label'], count)
    nodes = {key['id']: public_key(key), value['id']: value}
    links = [{'source': key['id'], 'target': value['id'], 'relation': 'field_value'}]
    queued = set()
    queue = deque()
    for root in roots[:root_limit]:
        row = connection.execute('SELECT * FROM records WHERE id=?', (root['record'],)).fetchone()
        root_node = record_node(row, metadata, 2, query.fields)
        edge = {'source': value['id'], 'target': root['record'], 'relation': 'value_record'}
        projected = {'nodes': [*nodes.values(), root_node], 'links': links + [edge]}
        if len(encode(projected).encode()) > MAX_RESPONSE_BYTES - 7000:
            break
        queued.add(root['record'])
        queue.append((root['record'], 0))
        nodes[row['id']] = root_node
        links.append(edge)
    consumed_roots = len(queued)
    rules = metadata['relations']['items']
    if query.relationIds:
        require(set(query.relationIds) <= {rule['id'] for rule in rules}, 'RELATION_MISSING', 'Правило не найдено.')
        rules = [rule for rule in rules if rule['id'] in query.relationIds]
    rule_index = {rule['id']: rule for rule in rules}
    truncated = len(roots) > consumed_roots
    seen_edges = set()
    while queue:
        record_id, depth = queue.popleft()
        row = connection.execute('SELECT id,collection,parent FROM records WHERE id=?', (record_id,)).fetchone()
        if depth >= query.maxDepth:
            continue
        for other, relation, source, target in neighbors(connection, row, rules):
            edge_id = (source, target, relation)
            if edge_id in seen_edges:
                continue
            if len(links) >= 200 or (other not in queued and len(queued) >= query.maxNodes - 2):
                truncated = True
                break
            edge = {'source': source, 'target': target, 'relation': relation}
            rule = rule_index.get(relation)
            if rule:
                status = 'кандидат' if rule['status'] == 'candidate' else 'заданное правило'
                edge.update(semanticStatus=rule['semanticStatus'], label='Совпадение значений · ' + status)
            else:
                edge.update(semanticStatus='extracted', label='Вложенные данные')
            other_node = None
            if other not in queued:
                other_row = connection.execute('SELECT * FROM records WHERE id=?', (other,)).fetchone()
                other_node = record_node(other_row, metadata, depth + 3, query.fields)
            projected = list(nodes.values()) + ([other_node] if other_node else [])
            if len(encode({'nodes': projected, 'links': links + [edge]}).encode()) > MAX_RESPONSE_BYTES - 7000:
                truncated = True
                break
            seen_edges.add(edge_id)
            links.append(edge)
            if other_node:
                nodes[other] = other_node
                queued.add(other)
                queue.append((other, depth + 1))
    result = {'mapId': map_id, 'snapshotId': query.snapshotId, 'key': public_key(key),
              'nodes': list(nodes.values()), 'links': links, 'matchedRecords': count, 'runtimeStatus': 'unknown',
              'truncated': truncated, 'nextCursor': cursor(scope, start + consumed_roots) if len(roots) > consumed_roots else None,
              'warning': 'Связи ограничены maxDepth/maxNodes; для продолжения отдельной записи используйте trace_data_links.'}
    return result
