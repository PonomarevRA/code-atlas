"""Byte-bounded страницы каталогов с cursor, привязанным к scope и списку."""
import base64
import json

from .documents.models import identity, require


def catalog_page(items, field, *, cursor=None, limit=100, scope=''):
    """Изменение списка отклоняет старый cursor вместо пропуска или дублирования строк."""
    require(type(limit) is int and 1 <= limit <= 100, 'INVALID_LIMIT', 'Размер страницы: 1..100.')
    stamp = identity(scope, items)
    offset = 0
    if cursor:
        try:
            token = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            offset = token['offset']
            require(token['stamp'] == stamp and type(offset) is int and 0 <= offset < len(items),
                    'STALE_CURSOR', 'Каталог изменился; начните с первой страницы.', 409)
        except (ValueError, KeyError, TypeError, UnicodeError):
            require(False, 'INVALID_CURSOR', 'Некорректный cursor.')
    page = []
    for item in items[offset:offset + limit]:
        if len(json.dumps(page + [item], ensure_ascii=False).encode()) > 54000:
            break
        page.append(item)
    require(bool(page) or not items, 'ITEM_LIMIT', 'Один элемент превышает предел ответа.')
    next_offset = offset + len(page)
    next_cursor = None
    if next_offset < len(items):
        next_cursor = base64.urlsafe_b64encode(json.dumps({'stamp': stamp, 'offset': next_offset}).encode()).decode()
    return {field: page, 'nextCursor': next_cursor, 'total': len(items), 'truncated': False}
