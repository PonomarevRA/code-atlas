"""Точные повторы авторских HTML-поддеревьев, без оценки семантической эквивалентности."""
from collections import defaultdict
from hashlib import sha256


def duplicate_groups(documents: list[dict], max_groups: int = 100) -> tuple[list[dict], bool]:
    """Хеширует bounded raw ranges; вложенные дубли уже выбранного блока не дублируют совет."""
    buckets = defaultdict(list)
    hashed_bytes, limited = 0, False
    for document in documents:
        parser = document['parser']
        if not parser.duplicate_safe or parser.limited or document['sourceRole'] != 'authoring':
            continue
        for index, element in enumerate(parser.elements):
            if (element.tag in {'html', 'head', 'body', 'script', 'style'} or element.size < 4
                    or element.range_status != 'exact' or element.start is None or element.end is None):
                continue
            text = parser.text[element.start:element.end]
            if len(text) < 100:
                continue
            encoded = text.encode('utf-8')
            hashed_bytes += len(encoded)
            if hashed_bytes > 16 * 1024 * 1024:
                limited = True
                break
            fingerprint = sha256(encoded).hexdigest()
            buckets[fingerprint].append({'file': document['file'], 'line': element.line,
                'endLine': element.end_line, 'start': element.start, 'end': element.end,
                'elementIndex': index, 'characters': len(text), 'sourceHash': document['sourceHash'],
                'tag': element.tag})
        if limited:
            break
    candidates = [(key, values) for key, values in buckets.items() if len(values) > 1]
    candidates.sort(key=lambda group: (-group[1][0]['characters'], group[0]))
    covered, result = defaultdict(list), []
    for fingerprint, occurrences in candidates:
        remaining = []
        for occurrence in occurrences:
            if not any(start <= occurrence['start'] and occurrence['end'] <= end
                       for start, end in covered[occurrence['file']]):
                remaining.append(occurrence)
        if len(remaining) < 2:
            continue
        if len(result) == max_groups:
            limited = True
            break
        # Один пример не требует загрузки всего повторяющегося HTML в MCP.
        result.append({'id': 'html-duplicate:' + fingerprint, 'fingerprint': fingerprint,
            'tag': remaining[0]['tag'], 'characters': remaining[0]['characters'],
            'occurrenceCount': len(remaining), 'occurrences': remaining[:40],
            'truncated': len(remaining) > 40, 'kind': 'exact-source', 'confidence': 'exact-source',
            'recommendation': 'Выделить общий компонент/layout/partial; использовать существующую сборку. '
                'Если её нет, рассмотреть JS framework со статической генерацией или шаблонизатор. '
                'Это рекомендация, не требование runtime JavaScript или доказательство одинакового поведения.'})
        for occurrence in remaining:
            covered[occurrence['file']].append((occurrence['start'], occurrence['end']))
    return result, limited
