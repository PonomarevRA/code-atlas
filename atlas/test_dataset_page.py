"""Snapshot-bound, byte-bounded pages of declared test inputs shared by HTTP and MCP."""
from copy import deepcopy
import base64
import hashlib
import json

from .graph_context import GraphContextError
from .test_entry_summary import build_test_entry_summary

MAX_PAGE_BYTES = 48_000


def _token(scope: str, offset: int) -> str:
    """Bind continuation to snapshot, raw test and source without exposing mutable state."""
    return base64.urlsafe_b64encode(f'{scope}:{offset}'.encode()).decode().rstrip('=')


def _offset(cursor: str | None, scope: str) -> int:
    """Reject malformed or cross-source continuations rather than silently restarting."""
    if cursor is None:
        return 0
    try:
        value = base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True).decode()
        bound, number = value.rsplit(':', 1)
        if bound != scope or not number.isdigit() or len(number) > 8:
            raise ValueError('scope')
        return int(number)
    except (ValueError, UnicodeError) as error:
        raise GraphContextError('Курсор не относится к этому снимку, тесту или источнику.', 409) from error


def _size(value: dict) -> int:
    """Measure actual UTF-8 output, including non-ASCII parameter values."""
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())


def read_test_dataset_page(graph: dict, node_id: str, snapshot_id: str,
                           source_id: str | None = None, cursor: str | None = None, limit: int = 20) -> dict:
    """Read captured rows only; never read a provider file or execute test code.

    No source selects a compact source catalogue. Selecting one source pages its
    captured rows; capture truncation stays separate from pagination. IDs and
    expressions are never shortened to make a row fit the response budget.
    """
    if type(limit) is not int or not 1 <= limit <= 30:
        raise GraphContextError('Размер страницы должен быть от 1 до 30.')
    if graph.get('metadata', {}).get('snapshotId') != snapshot_id:
        raise GraphContextError('Карта обновилась. Загрузите актуальный снимок.', 409)
    node = next((item for item in graph.get('nodes', []) if item['id'] == node_id), None)
    if node is None:
        raise GraphContextError('Узел отсутствует в снимке.', 404)
    facts = node.get('testFacts')
    if not facts:
        raise GraphContextError('У узла нет захваченного объявления теста.', 404)
    summary = build_test_entry_summary(facts)
    result = {'version': 'test-dataset-page-v1', 'snapshotId': snapshot_id, 'nodeId': node_id,
              'runtimeStatus': 'unknown', 'sourceKind': 'snapshot-captured-source',
              'sources': summary.get('dataSources', []), 'parameters': deepcopy(facts.get('parameters', [])),
              'context': deepcopy(facts.get('context', [])), 'rows': [], 'nextCursor': None,
              'captureTruncated': False, 'truncated': False}
    if not source_id:
        if cursor:
            raise GraphContextError('Для продолжения выберите sourceId.', 409)
    else:
        sources = facts.get('dataSources', [])
        source = next((item for item in sources if item.get('id') == source_id), None)
        if source is None:
            raise GraphContextError('Источник отсутствует в этом объявлении теста.', 404)
        scope = hashlib.sha256(f'{snapshot_id}\0{node_id}\0{source_id}'.encode()).hexdigest()[:32]
        start = _offset(cursor, scope)
        rows = source.get('rows', [])
        if start > len(rows):
            raise GraphContextError('Курсор выходит за захваченные строки.', 409)
        result.update(sourceId=source_id, source=next(item for item in result['sources'] if item.get('id') == source_id),
                      totalCapturedRows=len(rows), declaredRowCount=source.get('declaredRowCount'),
                      captureTruncated=bool(source.get('rowsTruncated')))
        for row in rows[start:start + limit]:
            candidate = {**result, 'rows': [*result['rows'], row], 'nextCursor': _token(scope, start + len(result['rows']) + 1)}
            if _size(candidate) > MAX_PAGE_BYTES:
                if not result['rows']:
                    raise GraphContextError('Строка превышает бюджет страницы; откройте исходник провайдера.', 413)
                break
            result['rows'].append(deepcopy(row))
        following = start + len(result['rows'])
        if following < len(rows):
            result['nextCursor'] = _token(scope, following)
        result['truncated'] = result['captureTruncated'] or result['nextCursor'] is not None
    if _size(result) > MAX_PAGE_BYTES:
        raise GraphContextError('Метаданные теста превышают бюджет; откройте его исходник.', 413)
    return result
