"""Лексические чтения/записи членов с проверкой владельца и локального затенения."""
from .interface_source import text

ACCESSORS = {'member_access_expression': ('expression', 'name'), 'field_access': ('object', 'field'),
             'attribute': ('object', 'attribute'), 'member_expression': ('object', 'property'),
             'selector_expression': ('operand', 'field'), 'field_expression': ('value', 'field')}
DEFERRED = {'lambda', 'lambda_expression', 'anonymous_function', 'function_expression', 'arrow_function'}
IDENTIFIERS = {'identifier', 'field_identifier', 'property_identifier', 'private_property_identifier', 'simple_identifier', 'name'}


def access_relations(ast):
    """Присваивание самого поля — write; изменение его коллекции остаётся чтением receiver."""
    node = ast
    parent = node.parent
    while parent and parent.type in {'parenthesized_expression', 'parenthesized_lvalue', 'directly_assignable_expression', 'expression_list'}:
        node, parent = parent, parent.parent
    if parent and parent.type in {'assignment_expression', 'assignment', 'augmented_assignment', 'assignment_statement', 'compound_assignment_expr'} and (parent.child_by_field_name('left') or parent.child_by_field_name('target')) == node:
        operator = parent.child_by_field_name('operator')
        compound = parent.type in {'augmented_assignment', 'compound_assignment_expr'} or any(child.type.endswith('=') and child.type != '=' for child in parent.children if not child.is_named)
        return ['reads', 'writes'] if compound else ['writes']
    if parent and parent.type in {'update_expression', 'prefix_unary_expression', 'postfix_unary_expression', 'inc_statement', 'dec_statement'}:
        if parent.type in {'prefix_unary_expression', 'postfix_unary_expression'} and not any(child.type in {'++', '--'} for child in parent.children):
            return ['reads']
        return ['reads', 'writes']
    return ['reads']


def member_accesses(ast, source, language, owner, fields, module_fields, classes, boundaries):
    """Разрешает self/this, точный локальный тип и поля модуля; чужой/неизвестный receiver остаётся неизвестным."""
    shadowed = set()
    receivers = {'self', 'cls'} if language.name == 'python' else {'self'} if language.name == 'rust' else {'$this'} if language.name == 'php' else {'this'}
    boundaries = boundaries | DEFERRED
    if language.name == 'go':
        receiver = ast.child_by_field_name('receiver')
        for parameter in receiver.named_children if receiver else []:
            name = text(parameter.child_by_field_name('name'), source)
            if name:
                receivers.add(name)
    pending = list(reversed(ast.named_children))
    while pending:
        child = pending.pop()
        if child.type in boundaries:
            continue
        if child.type in {'parameter', 'formal_parameter', 'variable_declarator', 'parameter_declaration', 'function_value_parameter', 'value_parameter', 'variable_declaration', 'let_declaration', 'default_parameter', 'typed_parameter'}:
            name_node = child.child_by_field_name('name') or child.child_by_field_name('pattern') or next((n for n in child.named_children if n.type in IDENTIFIERS), None)
            name = text(name_node, source)
            if name:
                shadowed.add(name)
        elif child.type in {'parameters', 'formal_parameters'}:
            shadowed.update(text(n, source) for n in child.named_children if n.type == 'identifier')
        elif language.name == 'go' and child.type == 'short_var_declaration':
            left = child.child_by_field_name('left')
            shadowed.update(text(n, source) for n in left.named_children if n.type == 'identifier')
        elif language.name == 'python' and child.type in {'assignment', 'augmented_assignment'}:
            left = child.child_by_field_name('left')
            if left and left.type == 'identifier':
                shadowed.add(text(left, source))
                receivers.discard(text(left, source))
        pending.extend(reversed(child.named_children))
    pending = list(reversed(ast.named_children))
    while pending:
        child = pending.pop()
        if child.type in boundaries:
            continue
        target, reference = None, child
        if child.type in ACCESSORS:
            receiver_key, name_key = ACCESSORS[child.type]
            if language.name == 'php' and child.type == 'member_access_expression':
                receiver_key = 'object'
            receiver = text(child.child_by_field_name(receiver_key), source)
            name = text(child.child_by_field_name(name_key), source).lstrip('$')
            if receiver in receivers and owner:
                target = fields.get((owner['id'], name))
            elif receiver not in shadowed:
                matches = classes.get(receiver, [])
                if len(matches) == 1:
                    target = fields.get((matches[0]['id'], name))
        elif child.type in IDENTIFIERS:
            name = text(child, source)
            parent = child.parent
            accessor_receiver = parent and parent.type in ACCESSORS and parent.child_by_field_name(ACCESSORS[parent.type][0]) == child
            name_position = parent and parent.child_by_field_name('name') == child
            standalone = (not name_position) and (not parent or parent.type not in set(ACCESSORS) | {'variable_declarator', 'parameter', 'formal_parameter', 'parameter_declaration'})
            if name not in shadowed and (accessor_receiver or standalone):
                target = module_fields.get(name) if language.name == 'python' else fields.get((owner['id'], name)) if owner else module_fields.get(name)
                target = target or module_fields.get(name)
        if target:
            for relation in access_relations(reference):
                yield target, relation, child.start_point.row + 1
        pending.extend(reversed(child.named_children))
