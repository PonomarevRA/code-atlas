"""Лексические правила Rust: области impl, операторы и собственные rustdoc-комментарии."""

import re

BRANCHES = {'match_expression'}
LOOPS = {'for_expression', 'while_expression', 'loop_expression'}
DEFERRED = {'function_item', 'closure_expression', 'async_block', 'const_block',
            'struct_item', 'enum_item', 'trait_item', 'impl_item', 'mod_item'}
SCOPES = {'struct_item', 'enum_item', 'trait_item', 'impl_item', 'mod_item', 'function_item'}
CONTROLS = BRANCHES | LOOPS | {'if_expression'}


def scope_name(node, source: bytes) -> str:
    """Полный заголовок impl различает методы разных типов, трейтов и generic-ограничений."""
    if node.type == 'impl_item':
        body = node.child_by_field_name('body')
        end = body.start_byte if body is not None else node.end_byte
        value = source[node.start_byte:end].decode('utf-8')
    else:
        name = node.child_by_field_name('name')
        value = source[name.start_byte:name.end_byte].decode('utf-8') if name is not None else ''
        parameters = node.child_by_field_name('type_parameters')
        if parameters is not None:
            value += source[parameters.start_byte:parameters.end_byte].decode('utf-8')
    return re.sub(r'\s+', '', value)


def statement_sample(node, control: bool) -> bool:
    """Считает операторы и хвостовые выражения; оболочка expression_statement не дублирует if/loop."""
    if control or node.type == 'let_declaration':
        return True
    if node.type == 'expression_statement':
        return not any(child.type in CONTROLS for child in node.named_children)
    return bool(node.parent and node.parent.type == 'block' and node.type not in DEFERRED
                and node.type not in {'block', 'empty_statement', 'attribute_item'} and 'comment' not in node.type)


def declaration_comment(declaration, source: bytes) -> str:
    """Читает прилегающие комментарии через атрибуты; внутренние //! и /*! не принадлежат функции."""
    pieces = []
    sibling, line = declaration.prev_named_sibling, declaration.start_point.row
    inspected = 0
    while sibling is not None and line - sibling.end_point.row <= 1 and inspected < 80:
        inspected += 1
        value = source[sibling.start_byte:sibling.end_byte].decode('utf-8')
        if sibling.type == 'attribute_item':
            pass  # #[inline]/#[cfg] не меняет владельца предшествующего rustdoc.
        elif 'comment' in sibling.type and not value.lstrip().startswith(('//!', '/*!')):
            pieces.append(value)
        else:
            break
        line, sibling = sibling.start_point.row, sibling.prev_named_sibling
    lines = []
    for value in reversed(pieces):
        value = re.sub(r'^/\*+|\*/$', '', value.strip())
        lines.extend(re.sub(r'^\s*(?:///|//|\*)\s?', '', line) for line in value.splitlines())
    return '\n'.join(lines).strip()


def documentation_sections(value: str) -> str:
    """Нормализует rustdoc Markdown для общего счётчика; код примеров не становится описанием контракта."""
    result, in_code, skip_section = [], False, False
    headings = {'arguments': 'Arguments', 'parameters': 'Parameters', 'returns': 'Returns',
                'errors': 'Exceptions', 'panics': 'Exceptions'}
    for line in value.splitlines():
        stripped = line.strip()
        if stripped.startswith(('```', '~~~')):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = re.fullmatch(r'#+\s+(.+?)\s*#*', stripped)
        if heading:
            section = headings.get(heading[1].casefold())
            skip_section = section is None
            if section:
                result.append(section)
            continue
        if skip_section:
            continue
        parameter = re.match(r'\s*[-*]\s+`([A-Za-z_]\w*)`\s*[-:]\s*(.+)', line)
        result.append(f'{parameter[1]}: {parameter[2]}' if parameter else line)
    return '\n'.join(result)
