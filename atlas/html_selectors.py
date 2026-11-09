"""CSS-запросы к captured HTML5 element tree без чтения исходника или запуска сайта."""
from xml.etree import ElementTree

import cssselect2
import tinycss2

NAMESPACES = {'html': 'http://www.w3.org/1999/xhtml', 'svg': 'http://www.w3.org/2000/svg',
              'math': 'http://www.w3.org/1998/Math/MathML', 'xlink': 'http://www.w3.org/1999/xlink'}
DYNAMIC = {'hover', 'active', 'focus', 'focus-within', 'focus-visible', 'visited', 'target',
           'target-within', 'playing', 'paused', 'current', 'past', 'future', 'fullscreen', 'modal',
           'seeking', 'buffering', 'stalled', 'muted', 'volume-locked', 'user-valid', 'user-invalid'}


class HtmlElementWrapper(cssselect2.ElementWrapper):
    """Смешанный HTML/SVG/MathML документ сохраняет регистр foreign tag names."""
    def __init__(self, *args, **kwargs):
        """CSS HTML case folding применим к HTML namespace, включая возврат из SVG."""
        super().__init__(*args, **kwargs)
        # cssselect2 0.8 передаёт один HTML flag всему дереву. Для foreignObject
        # это ошибочно: его имя case-sensitive, а вложенный HTML div снова нет.
        self.in_html_document = self.namespace_url == NAMESPACES['html']


class HtmlQueryError(ValueError):
    """Некорректный/неподдержанный запрос отличается от корректного пустого результата."""
    def __init__(self, message, status=422):
        """Сохраняет HTTP-статус для общего HTTP/MCP контракта."""
        super().__init__(message)
        self.status = status


def compile_selector(selector):
    """Ограничивает грамматику и стоимость до компиляции cssselect2, без eval входа."""
    if not isinstance(selector, str) or not selector.strip() or len(selector) > 8192:
        raise HtmlQueryError('CSS-селектор должен содержать от 1 до 8192 символов.')
    try:
        tokens = tinycss2.parse_component_value_list(selector)
    except (ValueError, RecursionError) as error:
        # Например, тысячи цифр в :nth-child превышают integer guard Python.
        # Нельзя отключать этот guard или превращать неверный CSS в HTTP 500.
        raise HtmlQueryError('CSS-селектор превышает бюджет чисел или вложенности.') from error
    pending = [(tokens, 0)]
    count, has_count = 0, 0
    while pending:
        group, depth = pending.pop()
        if depth > 8:
            raise HtmlQueryError('Слишком глубокая вложенность CSS-селектора.')
        for index, token in enumerate(group):
            count += 1
            if count > 1024:
                raise HtmlQueryError('CSS-селектор превышает бюджет 1024 токенов.')
            if token.type == 'error':
                raise HtmlQueryError('Синтаксическая ошибка CSS-селектора.')
            # CSS strings/attribute values are data, not pseudo-class names.
            if token.type in {'ident', 'function'} and index and group[index - 1].type == 'literal' and group[index - 1].value == ':':
                name = token.lower_name if token.type == 'function' else token.lower_value
                if name in DYNAMIC:
                    raise HtmlQueryError('Псевдокласс :' + name + ' требует состояния браузера, которого нет в статическом дереве.')
                has_count += name == 'has'
            if has_count > 1:
                raise HtmlQueryError('Допускается один :has() на запрос; сузьте селектор.')
            if token.type == 'function':
                pending.append((token.arguments, depth + 1))
            elif token.type in {'[] block', '() block', '{} block'}:
                pending.append((token.content, depth + 1))
    try:
        compiled = cssselect2.compile_selector_list(tokens, namespaces=NAMESPACES)
    except (cssselect2.SelectorError, RecursionError, NotImplementedError, ValueError, SyntaxError) as error:
        raise HtmlQueryError('Некорректный или неподдержанный CSS-селектор: ' + str(error)[:200]) from error
    if len(compiled) > 16 or any(item.pseudo_element for item in compiled):
        raise HtmlQueryError('До 16 CSS-селекторов; псевдоэлементы не являются DOM-элементами.')
    return compiled, has_count


def captured_tree(document):
    """Восстанавливает etree только из ограниченной сериализации опубликованного снимка."""
    values = document['elements']
    if not values or len(values) > 5000:
        raise HtmlQueryError('Дерево не захвачено или превышает бюджет.', 409)
    elements = []
    for index, item in enumerate(values):
        tag = '{' + item['namespace'] + '}' + item['tag'] if item['namespace'] else item['tag']
        element = ElementTree.Element(tag, dict(item['attrs']))
        # CSS :empty учитывает даже whitespace; сами строки для CSS не нужны.
        element.text = ' ' if item['text'] else None
        element.tail = ' ' if item['tail'] else None
        parent = item['parent']
        if parent is not None:
            if not isinstance(parent, int) or not 0 <= parent < index:
                raise HtmlQueryError('Нарушен captured HTML tree contract.', 409)
            elements[parent].append(element)
        elif index:
            raise HtmlQueryError('У HTML5-документа должен быть один корень.', 409)
        elements.append(element)
    return elements


def query_html(graph, snapshot_id, node_id, selector='*', offset=0, limit=30):
    """Ищет по всему HTML-файлу выбранного узла; выдаёт ограниченную страницу raw-ID.

    Источник и состояние браузера не читаются. Правильный пустой результат
    отличается от отсутствующего дерева, невалидного CSS и runtime-псевдокласса.
    """
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 5000 or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise HtmlQueryError('Некорректная страница CSS-результатов.')
    nodes = {node['id']: node for node in graph.get('nodes', [])}
    node = nodes.get(node_id)
    if not node or not node.get('htmlFacts'):
        raise HtmlQueryError('HTML-узел не найден в снимке.', 404)
    filename = node.get('file')
    document = graph.get('metadata', {}).get('htmlDocuments', {}).get(filename)
    if not document:
        raise HtmlQueryError('HTML5-дерево не захвачено. Обновите карту с HTML-модом.', 409)
    if document.get('unsupported'):
        raise HtmlQueryError('CSS-поиск недоступен для неподдержанной конструкции: ' + ', '.join(document['unsupported']), 422)
    compiled, has_count = compile_selector(selector)
    if has_count and len(document['elements']) > 1000:
        raise HtmlQueryError(':has() ограничен деревом до 1000 элементов; используйте структурный селектор без :has().')
    elements = captured_tree(document)
    by_element = {element: document['elements'][index] for index, element in enumerate(elements)}
    matches = []
    for wrapper in HtmlElementWrapper.from_html_root(elements[0]).iter_subtree():
        if any(item.test(wrapper) for item in compiled):
            matches.append(by_element[wrapper.etree_element]['nodeId'])
    selected = matches[offset:offset + limit]
    return {'snapshotId': snapshot_id, 'file': filename, 'fileNodeId': document['fileNodeId'],
        'selector': selector, 'scope': 'captured-html-file', 'sourceKind': 'snapshot-captured-source',
        'sourceHash': document['sourceHash'], 'parser': document['parser'], 'runtimeStatus': 'unknown',
        'total': len(matches), 'offset': offset, 'nextOffset': offset + len(selected) if offset + len(selected) < len(matches) else None,
        'captureTruncated': document['truncated'], 'warnings': document['warnings'],
        'nodes': [{'id': identifier, 'label': nodes[identifier]['label'], 'file': filename,
            'line': nodes[identifier].get('line'), 'htmlFacts': {key: nodes[identifier]['htmlFacts'].get(key)
                for key in ('tag', 'cssSelector', 'generated', 'rangeStatus', 'start', 'end', 'namespace')}}
            for identifier in selected]}
