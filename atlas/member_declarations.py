"""Лексические декларации полей/методов Java, Python и других зарегистрированных языков."""
from .interface_source import text, walk
from .member_schema import member_facts, initializer_data
from .documentation_ownership import comment_anchor


def modifiers(ast, source):
    """Берёт только syntax modifiers, не слова из комментариев/строк/тела метода."""
    result = []
    allowed = {'public', 'private', 'protected', 'internal', 'fileprivate', 'pub', 'final', 'static', 'const', 'readonly', 'val', 'let'}
    wrappers = {'modifiers','modifier','accessibility_modifier','visibility_modifier','storage_class_specifier','type_qualifier','value_binding_pattern','mutability_modifier','property_modifier','member_modifier'}
    pending = [child for child in ast.children if child.type in wrappers or not child.is_named and child.type in allowed]
    while pending:
        child = pending.pop()
        value = text(child, source)
        if child.type in wrappers and value in allowed:
            result.append(value)
        elif not child.is_named and child.type in allowed:
            result.append(child.type)
        elif child.type in wrappers:
            pending.extend(n for n in child.children if n.type in wrappers or not n.is_named)
    return list(dict.fromkeys(result))


def declaration_name(ast, source):
    """Возвращает syntax name; C++ берёт declarator, а не возвращаемый тип."""
    if ast.type == 'secondary_constructor':
        return 'constructor'
    name = ast.child_by_field_name('name') or ast.child_by_field_name('property')
    if name is not None and name.type == 'pattern':
        name = name.child_by_field_name('bound_identifier')
    if name is not None:
        return text(name, source).lstrip('$')
    declarator = ast.child_by_field_name('declarator')
    if declarator:
        names = [n for n in walk(declarator) if n.type in {'identifier', 'field_identifier'}]
        return text(names[0], source) if names else ''
    direct = next((n for n in ast.named_children if n.type in {'simple_identifier', 'identifier', 'variable_name'}), None)
    return text(direct, source)


def callable_initializer(initializer):
    """Recognize a function-valued binding by syntax, never by evaluating its initializer."""
    wrappers = {'parenthesized_expression','as_expression','satisfies_expression','type_assertion','non_null_expression'}
    node = initializer
    for _ in range(12):
        if node is None or node.type not in wrappers:
            break
        node = node.child_by_field_name('expression') or next(
            (child for child in node.named_children if child.type in wrappers | {'arrow_function','function_expression','generator_function'}), None)
    return node if node is not None and node.type in {'arrow_function','function_expression','generator_function'} else None


def method_parameters(ast, source):
    """Сигнатура перегрузки хранится буквально; типы/значения аргументов не вычисляются."""
    parameters = ast.child_by_field_name('parameters') or next((n for n in ast.named_children if n.type in {'function_value_parameters', 'formal_parameters', 'parameter_list', 'parameters'}), None)
    if parameters is None and ast.child_by_field_name('declarator'):
        parameters = ast.child_by_field_name('declarator').child_by_field_name('parameters')
    return text(parameters, source)


def field_declarations(ast, source, language, owner, method):
    """Возвращает только поля владельца/модуля и явные Python self-assignments, не локальные переменные."""
    kind = language.name
    if ast.type not in language.fields:
        return []
    if kind == 'python':
        left = ast.child_by_field_name('left')
        instance = left is not None and left.type == 'attribute' and text(left.child_by_field_name('object'), source) in {'self', 'cls'}
        if method and not instance:
            return []
        if left is None or left.type not in {'identifier', 'attribute'} or left.type == 'attribute' and not instance:
            return []
        name = text(left.child_by_field_name('attribute') if instance else left, source)
        return [(name, ast, ast.child_by_field_name('right'), text(ast.child_by_field_name('type'), source), not instance)]
    if method:
        return []
    if kind in {'java', 'cpp', 'javascript', 'typescript'}:
        declarators = [ast] if ast.type in {'variable_declarator', 'field_definition', 'public_field_definition', 'property_signature'} else [n for n in ast.named_children if n.type in {'variable_declarator', 'identifier', 'field_identifier', 'init_declarator', 'pointer_declarator', 'array_declarator'}]
        result = []
        for item in declarators:
            name = declaration_name(item, source) or (text(item, source) if item.type in {'identifier', 'field_identifier'} else '')
            value = item.child_by_field_name('value') or item.child_by_field_name('default_value')
            if kind == 'cpp' and ast.type == 'field_declaration':
                after_declarator = False
                for position, child in enumerate(ast.children):
                    if ast.field_name_for_child(position) == 'declarator':
                        after_declarator = child.id == item.id
                    elif after_declarator and ast.field_name_for_child(position) == 'default_value':
                        value = child
                        break
            type_node = ast.child_by_field_name('type')
            if name:
                result.append((name, item, value, text(type_node, source), False))
        return result
    if kind == 'php':
        result = []
        for item in ast.named_children:
            if item.type in {'property_element', 'const_element'}:
                result.append((declaration_name(item, source), item,
                               item.child_by_field_name('default_value') or item.child_by_field_name('value'),
                               text(ast.child_by_field_name('type'), source), False))
        return result
    if kind == 'kotlin':
        variable = next((n for n in ast.named_children if n.type == 'variable_declaration'), None)
        if variable is None:
            return []
        value = next((n for n in ast.named_children if n.type not in {'modifiers', 'variable_declaration', 'getter', 'setter'}), None)
        declared_type = next((n for n in variable.named_children if n.type != 'identifier'), None)
        return [(declaration_name(variable, source), variable, value, text(declared_type, source), owner is None)]
    value = ast.child_by_field_name('value')
    if kind == 'go' and value and value.type == 'expression_list':
        values = value.named_children
        names = ast.children_by_field_name('name')
        # Multi-name specs preserve the pairing only when the AST gives one value per name.
        return [(text(n, source), n, values[i] if len(values) == len(names) else None,
                 text(ast.child_by_field_name('type'), source), owner is None) for i, n in enumerate(names)]
    name = declaration_name(ast, source)
    declared_type = ast.child_by_field_name('type')
    if kind == 'swift':
        annotation = next((n for n in ast.named_children if n.type == 'type_annotation'), None)
        declared_type = annotation.child_by_field_name('name') if annotation else None
    return [(name, ast, value, text(declared_type, source), owner is None)] if name else []


def declaration_modifiers(ast, source, language, owner):
    """C++ access labels apply until the next label, within the immediate class body only."""
    result = modifiers(ast, source)
    if language.name == 'cpp' and owner:
        parent = ast.parent
        if parent and parent.type == 'field_declaration_list':
            visibility = 'private' if owner['kind'] == 'class' else 'public'
            for sibling in parent.named_children:
                if sibling.id == ast.id:
                    break
                if sibling.type == 'access_specifier':
                    visibility = text(sibling, source)
            result.append(visibility)
    return result


def captured_comment(ast, source):
    """Only adjacent source comments or Python docstrings are captured; no documentation is invented."""
    if ast.type in {'function_item', 'function_signature_item'}:
        from .rust_support import declaration_comment
        return declaration_comment(ast, source)[:1200]
    anchor = comment_anchor(ast)
    pieces = []
    sibling = anchor.prev_named_sibling
    line = anchor.start_point.row
    while sibling and 'comment' in sibling.type and line - sibling.end_point.row <= 1 and len(pieces) < 8:
        pieces.append(text(sibling, source).strip('/ *'))
        line = sibling.start_point.row
        sibling = sibling.prev_named_sibling
    body = ast.child_by_field_name('body')
    if ast.type == 'function_definition' and body and body.named_children:
        first = body.named_children[0]
        if first.type == 'expression_statement' and first.named_children and first.named_children[0].type == 'string':
            pieces.insert(0, text(first.named_children[0], source).strip('\"\''))
    return '\n'.join(reversed(pieces))[:1200]


def field_record(name, ast, initializer, declared_type, static, source, filename, source_hash, language, owner):
    """Публикует видимость, nullability unknown и исходное значение отдельно от runtime объекта."""
    mods = declaration_modifiers(ast, source, language, owner)
    if language.name == 'python' and name.isupper():
        mods.append('const')  # Соглашение имён; visibilityKind явно показывает отсутствие enforce.
    if ast.type in {'const_spec', 'const_item', 'const_declaration'}:
        mods.append('const')
    member = member_facts(language.name, 'field', name, owner['id'] if owner else None, mods,
                          owner_kind=owner['kind'] if owner else 'module', static=static)
    data = initializer_data(initializer, source, language.name, declared_type=declared_type, constant=member['constant'])
    return {'memberFacts': member, 'fieldFacts': {'kind': 'field', 'name': name, 'type': declared_type[:500] or 'unknown',
        'nullable': None, 'nullableEvidence': 'Language type semantics not inferred', 'description': captured_comment(ast, source),
        'file': filename, 'line': ast.start_point.row + 1, 'sourceKind': 'snapshot-captured-source',
        'sourceHash': source_hash, 'truncated': False, 'warning': 'Declared source field; runtime values and dynamic member creation are not evaluated.'},
        **({'dataFacts': data} if data else {}), **({'staticData': data} if data and (member['static'] or member['constant']) else {})}
