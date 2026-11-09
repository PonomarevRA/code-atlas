"""Выбор точного PHP-объявления и его собственной документации."""
import re

from ...node_documentation import empty_documentation

MAX_AST_NODES = 20_000
DECLARATIONS = {'function_definition', 'method_declaration'}
TYPES = {'class_declaration', 'interface_declaration', 'trait_declaration', 'enum_declaration'}
DEFERRED = DECLARATIONS | TYPES | {'anonymous_function', 'arrow_function', 'anonymous_class'}


def text(node, content):
    """Читает полный UTF-8 диапазон; лимиты применяются при публикации."""
    return content[node.start_byte:node.end_byte].decode('utf-8') if node is not None else ''


def site(node):
    """Возвращает 1-based байтовые координаты Tree-sitter, конец исключающий."""
    return {'line': node.start_point.row + 1, 'column': node.start_point.column + 1,
            'endLine': node.end_point.row + 1, 'endColumn': node.end_point.column + 1}


def bounded_nodes(root):
    """Не создаёт частичный индекс объявлений при превышении бюджета AST."""
    nodes, pending = [], [root]
    while pending:
        item = pending.pop()
        nodes.append(item)
        if len(nodes) > MAX_AST_NODES:
            return None
        pending.extend(reversed(item.children))
    return nodes


def owner_name(declaration, content):
    """Сопоставляет тип с форматом qualified names общего member_graph."""
    parts, parent = [], declaration.parent
    while parent is not None:
        if parent.type in TYPES:
            parts.append(text(parent.child_by_field_name('name'), content))
        parent = parent.parent
    return '.'.join(reversed(parts))


def select_declaration(nodes, content, node):
    """Имя, владелец и точная строка должны согласоваться, даже для одного кандидата."""
    member = node.get('memberFacts') or {}
    captured = node.get('callableFacts') or {}
    name = member.get('name') or str(node.get('label', '')).strip('.').split('(', 1)[0]
    candidates = []
    for declaration in nodes:
        if declaration.type not in DECLARATIONS:
            continue
        if text(declaration.child_by_field_name('name'), content) != name:
            continue
        line = node.get('line')
        if isinstance(line, int) and not (declaration.start_point.row + 1 <= line <= declaration.end_point.row + 1):
            continue
        owner = owner_name(declaration, content)
        expected_owner = captured.get('declaringType')
        if expected_owner and owner != expected_owner:
            filename = node.get('file') or node.get('source_file')
            if owner or captured.get('declaringTypeId') or expected_owner != filename:
                continue
        parameters = captured.get('parameterList')
        if parameters and text(declaration.child_by_field_name('parameters'), content) != parameters:
            continue
        candidates.append(declaration)
    return candidates[0] if len(candidates) == 1 else None


def documentation(declaration, content):
    """Извлекает только прилегающие комментарии; PHPDoc теги остаются текстовыми фактами."""
    result = empty_documentation()
    comment, row, remaining = declaration.prev_named_sibling, declaration.start_point.row, 8000
    comments = []
    while comment is not None and comment.type == 'comment' and comment.end_point.row >= row - 1:
        raw = text(comment, content)
        clipped = raw[:remaining]
        comments.append({'text': clipped, 'line': comment.start_point.row + 1})
        remaining -= len(clipped)
        if len(raw) > len(clipped):
            result['truncated'] = True
            break
        row, comment = comment.start_point.row, comment.prev_named_sibling
    comments.reverse()
    result['comments'] = comments
    summary = []
    for comment in comments:
        for line in comment['text'].splitlines():
            line = re.sub(r'^\s*(/\*\*?|\*/|\*|//|#)\s?', '', line).removesuffix('*/').strip()
            if line.startswith('@param '):
                match = re.match(r'@param\s+\S+\s+(\$\w+)\s*(.*)', line)
                if match:
                    result['params'].append({'name': match[1], 'text': match[2]})
            elif line.startswith('@return '):
                result['returns'] = line[8:]
            elif line.startswith('@'):
                result['remarks'] += line + '\n'
            elif line:
                summary.append(line)
    result['summary'] = '\n'.join(summary)
    return result


def declaration_facts(declaration, content):
    """Отделяет сигнатуру и документацию метода от контекста содержащего типа."""
    body = declaration.child_by_field_name('body')
    end = body.start_byte if body is not None else declaration.end_byte
    signature = content[declaration.start_byte:end].decode('utf-8').strip()
    parameters, parameter_list = [], declaration.child_by_field_name('parameters')
    if parameter_list is not None:
        for parameter in parameter_list.named_children[:40]:
            parameters.append({'name': text(parameter.child_by_field_name('name'), content)[:400],
                               'type': text(parameter.child_by_field_name('type'), content)[:400]})
    context, parent = None, declaration.parent
    while parent is not None:
        if parent.type in TYPES:
            context = {'name': text(parent.child_by_field_name('name'), content)[:400],
                       'kind': parent.type, 'line': parent.start_point.row + 1,
                       'documentation': documentation(parent, content)}
            break
        parent = parent.parent
    return {'signature': signature[:2000], 'parameters': parameters,
            'returnType': text(declaration.child_by_field_name('return_type'), content)[:400],
            'documentation': documentation(declaration, content), 'typeContext': context,
            'declarationTruncated': len(signature) > 2000 or (parameter_list is not None and len(parameter_list.named_children) > 40)}


def contains_owned(body, kinds):
    """Находит syntax hazard только своего тела, исключая вложенные отложенные области."""
    pending = [body]
    while pending:
        node = pending.pop()
        if node.type in DEFERRED:
            continue
        if node.type in kinds:
            return True
        pending.extend(node.named_children)
    return False
