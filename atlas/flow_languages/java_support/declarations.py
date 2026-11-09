"""Точное сопоставление Java-объявлений и их собственной документации."""
import re
from ..parsed_source import parsed_tree
from ...node_documentation import empty_documentation

DECLARATIONS = {'method_declaration', 'constructor_declaration', 'compact_constructor_declaration'}
TYPES = {'class_declaration', 'interface_declaration', 'record_declaration', 'enum_declaration'}
MAX_AST_NODES = 20000


def text(node, content):
    """Читает ограниченный UTF-8 фрагмент; координаты остаются байтовыми Tree-sitter."""
    return content[node.start_byte:node.end_byte].decode('utf-8')[:400] if node else ''


def locate(content, node, *, source=None):
    """Выбирает имя, строку и параметрический список без соседнего метода при stale line."""
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_java
    except ImportError:
        return None, 'Локальная грамматика Tree-sitter Java недоступна.', True
    try:
        supplied = source if source is not None else {'text': content.decode('utf-8')}
        root = parsed_tree(supplied, 'java', lambda data: Parser(Language(tree_sitter_java.language())).parse(data)).root_node
    except (TypeError, ValueError) as error:
        return None, 'Локальная грамматика Java несовместима: ' + type(error).__name__, True
    candidates, pending, count = [], [root], 0
    facts = node.get('memberFacts') or {}
    callable_facts = node.get('callableFacts') or {}
    name = facts.get('name') or str(node.get('label') or '').strip('.').split('(', 1)[0]
    line = node.get('line')
    while pending:
        item = pending.pop()
        count += 1
        if count > MAX_AST_NODES:
            return None, 'Java AST превышает лимит 20000 узлов.', True
        if item.type in DECLARATIONS and text(item.child_by_field_name('name'), content) == name:
            matches_line = not isinstance(line, int) or item.start_point.row + 1 <= line <= item.end_point.row + 1
            parameters = item.child_by_field_name('parameters')
            parameter_list = content[parameters.start_byte:parameters.end_byte].decode('utf-8') if parameters else ''
            matches_parameters = not callable_facts.get('parameterList') or callable_facts['parameterList'] == parameter_list
            owners, ancestor = [], item.parent
            while ancestor is not None:
                if ancestor.type in TYPES:
                    owners.append(text(ancestor.child_by_field_name('name'), content))
                ancestor = ancestor.parent
            owner_name = '.'.join(reversed(owners))
            matches_owner = not callable_facts.get('declaringType') or callable_facts['declaringType'] == owner_name
            if matches_line and matches_parameters and matches_owner:
                candidates.append(item)
        pending.extend(reversed(item.named_children))
    if len(candidates) != 1:
        return None, 'Точное Java-объявление не найдено или неоднозначно; соседние методы исключены.', False
    declaration = candidates[0]
    if declaration.has_error:
        return None, 'Выбранное Java-объявление содержит parse error; дерево не построено.', False
    return declaration, '', False


def documentation(declaration, content):
    """Берёт только примыкающие комментарии метода, ограничивая весь текст 8000 символами."""
    result = empty_documentation()
    previous, row, pieces, size = declaration.prev_named_sibling, declaration.start_point.row, [], 0
    while previous is not None and previous.type in {'block_comment', 'line_comment'} and previous.end_point.row >= row - 1:
        raw = content[previous.start_byte:previous.end_byte].decode('utf-8')
        remaining = max(0, 8000 - size)
        pieces.append(raw[:remaining])
        size += len(raw)
        result['truncated'] |= size > 8000
        row, previous = previous.start_point.row, previous.prev_named_sibling
        if size >= 8000:
            break
    cleaned = []
    for raw in reversed(pieces):
        raw = re.sub(r'^/\*\*?|\*/$', '', raw.strip())
        cleaned.extend(re.sub(r'^\s*(\*|//)\s?', '', line).strip() for line in raw.splitlines())
    result['summary'] = '\n'.join(cleaned).strip()[:8000]
    return result


def declaration_facts(declaration, content):
    """Извлекает сигнатуру, формальные параметры и собственные комментарии Java."""
    body = declaration.child_by_field_name('body')
    end = body.start_byte if body else declaration.end_byte
    signature = content[declaration.start_byte:end].decode('utf-8').strip()[:400]
    parameters = []
    parameter_list = declaration.child_by_field_name('parameters')
    for parameter in parameter_list.named_children if parameter_list else []:
        name = parameter.child_by_field_name('name')
        declared_type = parameter.child_by_field_name('type')
        if parameter.type == 'spread_parameter':
            declarator = next((c for c in parameter.named_children if c.type == 'variable_declarator'), None)
            name = declarator.child_by_field_name('name') if declarator else name
            declared_type = next((c for c in parameter.named_children if c.type not in {'modifiers', 'variable_declarator'}), None)
        parameter_name = text(name, content)
        parameter_type = text(declared_type, content)
        if parameter.type == 'receiver_parameter':
            parameter_name = 'this'
            parameter_type = text(parameter.named_children[0], content)
        parameter_type += text(parameter.child_by_field_name('dimensions'), content)
        if parameter.type == 'spread_parameter':
            parameter_type += '...'
        parameters.append({'name': parameter_name, 'type': parameter_type[:400]})
    return {'kind': 'constructor' if declaration.type != 'method_declaration' else 'method',
            'line': declaration.start_point.row + 1, 'endLine': declaration.end_point.row + 1,
            'signature': signature, 'parameters': parameters,
            'returnType': (text(declaration.child_by_field_name('type'), content) + text(declaration.child_by_field_name('dimensions'), content))[:400],
            'documentation': documentation(declaration, content),
            'declarationTruncated': len(content[declaration.start_byte:end].decode('utf-8').strip()) > 400}
