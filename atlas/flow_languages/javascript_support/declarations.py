"""Точное сопоставление JavaScript-объявлений и принадлежащих им комментариев."""
import re

from ...node_documentation import empty_documentation

CALLABLES = {'function_declaration', 'generator_function_declaration', 'function_expression',
             'generator_function', 'arrow_function', 'method_definition'}
CLASSES = {'class_declaration', 'class'}


def text(node, source):
    """Читает UTF-8-фрагмент AST без потери байтовых координат."""
    return source[node.start_byte:node.end_byte].decode('utf-8') if node is not None else ''


def walk(root):
    """Обходит все узлы, включая токены, без рекурсии Python."""
    pending = [root]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.children))


def name_of(declaration, source):
    """Для анонимных функций берёт только явное имя binding/property/assignment."""
    parent = declaration.parent
    if parent and parent.type in {'variable_declarator', 'pair', 'field_definition', 'assignment_expression'}:
        for field in ('name', 'key', 'left', 'property'):
            name = parent.child_by_field_name(field)
            if name is not None:
                return text(name, source).strip('\"\'')
    return text(declaration.child_by_field_name('name'), source).strip('\"\'')


def select(declarations, node, source):
    """Имя и точная строка/столбец/владелец исключают соседние и вложенные методы."""
    name = (node.get('memberFacts') or {}).get('name')
    if not name:
        name = str(node.get('label') or '').lstrip('.').split('(', 1)[0]
    matches = [item for item in declarations if name_of(item, source) == name]
    line = node.get('line') or node.get('source_line')
    if line is not None:
        # A binding's line may precede its function expression in multiline declarations.
        anchored = []
        for item in matches:
            lines = {item.start_point.row + 1}
            parent = item.parent
            if parent is not None and parent.type in {'variable_declarator', 'pair', 'field_definition', 'assignment_expression'}:
                lines.add(parent.start_point.row + 1)
            if line in lines:
                anchored.append(item)
        matches = anchored
    column = node.get('column')
    if column is not None:
        matches = [item for item in matches if item.start_point.column + 1 == column]
    owner = (node.get('callableFacts') or {}).get('declaringType')
    if owner and not any(char in owner for char in '/\\') and not owner.endswith(('.js', '.jsx', '.mjs', '.cjs')):
        owned = []
        for item in matches:
            names, parent = [], item.parent
            while parent is not None:
                if parent.type in CLASSES | CALLABLES:
                    names.append(name_of(parent, source))
                parent = parent.parent
            if '.'.join(reversed(names)) == owner:
                owned.append(item)
        matches = owned
    return matches[0] if len(matches) == 1 else None


def documentation(declaration, source):
    """Берёт прилегающий комментарий функции, включая export и const wrappers."""
    anchor = declaration
    while anchor.parent and anchor.parent.type in {'variable_declarator', 'lexical_declaration',
                                                   'variable_declaration', 'export_statement'}:
        anchor = anchor.parent
    previous, next_row = anchor.prev_named_sibling, anchor.start_point.row
    result, comments, size = empty_documentation(), [], 0
    while previous is not None and previous.type == 'comment' and previous.end_point.row >= next_row - 1:
        raw = text(previous, source)
        remaining = 8000 - size
        comments.append({'text': raw[:remaining], 'line': previous.start_point.row + 1})
        size += len(raw)
        if size > 8000:
            result['truncated'] = True
            break
        next_row, previous = previous.start_point.row, previous.prev_named_sibling
    result['comments'] = list(reversed(comments))
    cleaned = []
    for comment in result['comments']:
        value = re.sub(r'^/\*\*?|\*/$', '', comment['text']).strip()
        for line in value.splitlines():
            line = re.sub(r'^\s*(?://|\*)\s?', '', line).strip()
            if line.startswith('@'):
                break  # Tags remain verbatim in comments; no invented JSDoc type semantics.
            cleaned.append(line)
    result['summary'] = '\n'.join(cleaned).strip()[:8000]
    return result


def parameters(declaration, source):
    """Возвращает буквальные bindings; JavaScript runtime-типы не угадывает."""
    group = declaration.child_by_field_name('parameters')
    single = declaration.child_by_field_name('parameter')
    items = group.named_children if group else ([single] if single else [])
    result = []
    for item in items:
        if item.type == 'comment':
            continue
        binding = item.child_by_field_name('left') if item.type == 'assignment_pattern' else item
        result.append({'name': text(binding, source)[:400], 'type': 'unknown'})
    return result
