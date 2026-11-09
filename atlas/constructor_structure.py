"""C# конструкторы, отсутствующие в Graphify, и их лексические переходы."""
import hashlib

from .interface_source import METHOD_KINDS, TYPE_KINDS, text, walk

MAX_CONSTRUCTORS = 4000


def capture_constructors(model, nodes, by_id, ambiguous_ids, add_edge, definition):
    """Дополняет копию снимка; сохраняет raw IDs и не угадывает перегрузки/отложенные вызовы."""
    count = 0
    limited = False
    for method in model.methods:
        owner = method['owner']
        if not owner['id'] or owner['id'] in ambiguous_ids:
            continue
        identifier = method['id']
        if method['constructor'] and (not identifier or identifier == owner['id']):
            if count >= MAX_CONSTRUCTORS:
                limited = True
                continue
            source = model.files[owner['file']]
            parameters = text(method['ast'].child_by_field_name('parameters'), source)
            identity = f"{owner['file']}:{owner['qualifiedName']}`{len(owner['typeParameters'])}:{'static' if 'static' in method['modifiers'] else 'instance'}:{parameters}"
            identifier = 'csharp-constructor:' + hashlib.sha256(identity.encode()).hexdigest()[:24]
            method['id'] = identifier
            if identifier not in by_id:
                node = {'id': identifier, 'label': owner['name'] + parameters[:600], 'kind': 'constructor',
                        '_callable': True, '_origin': 'csharp-source-structure', 'file': owner['file'],
                        'line': method['ast'].start_point.row + 1}
                nodes.append(node)
                by_id[identifier] = node
            count += 1
        if identifier in by_id and (method['constructor'] or identifier != owner['id']):
            by_id[identifier]['callableFacts'] = {'kind': 'constructor' if method['constructor'] else 'method',
                'declaringTypeId': owner['id'], 'declaringType': owner['qualifiedName'],
                'parameterList': text(method['ast'].child_by_field_name('parameters'), model.files[owner['file']])[:600],
                'modifiers': method['modifiers'], 'sourceKind': 'snapshot-captured-source'}
            if method['constructor']:
                add_edge(owner['id'], identifier, 'method', owner, method['ast'].start_point.row + 1)

    # Индекс готов целиком: base/this могут ссылаться на объявление ниже в файле.
    for method in model.methods:
        if not method['constructor'] or not method['id'] or method['id'] not in by_id:
            continue
        owner = method['owner']
        source = model.files[owner['file']]
        initializer = next((n for n in method['ast'].named_children if n.type == 'constructor_initializer'), None)
        if initializer:
            target_owner = owner if any(n.type == 'this' for n in initializer.children) else None
            if target_owner is None and 'base' in [n.type for n in initializer.children]:
                bases = [definition(model, base, owner) for base in owner['baseTypes']]
                classes = [base for base in bases if base and base['kind'] == 'class']
                target_owner = classes[0] if len(classes) == 1 else None
            arguments = next((n for n in initializer.named_children if n.type == 'argument_list'), None)
            candidates = [m for m in target_owner['methods'] if m['constructor'] and 'static' not in m['modifiers'] and m['id'] in by_id] if target_owner else []
            # У единственного объявления ещё проверяем обязательные и необязательные аргументы.
            if len(candidates) == 1:
                target = candidates[0]
                parameters = target['ast'].child_by_field_name('parameters')
                required = sum(not any(n.type == '=' for n in p.children) for p in parameters.named_children if p.type == 'parameter')
                if arguments is not None and required <= len(arguments.named_children) <= len(target['parameters']):
                    add_edge(method['id'], target['id'], 'calls', owner, initializer.start_point.row + 1,
                             callKind='constructor-initializer', expression=text(initializer, source)[:600])
        shadowed = set(owner['fields']) | {parameter['name'] for parameter in method['parameters']}
        shadowed.update(text(n.child_by_field_name('name'), source) for n in walk(method['ast']) if n.type == 'variable_declarator')
        pending = list(reversed(method['ast'].named_children))
        while pending:
            invocation = pending.pop()
            if invocation.type in TYPE_KINDS | METHOD_KINDS | {'lambda_expression', 'anonymous_method_expression', 'local_function_statement'}:
                continue
            if invocation.type == 'invocation_expression':
                function = invocation.child_by_field_name('function')
                target_owner, name = owner, text(function, source)
                if function.type == 'member_access_expression':
                    receiver = text(function.child_by_field_name('expression'), source)
                    target_owner = owner if receiver == 'this' else None if receiver in shadowed else definition(model, receiver, owner)
                    name = text(function.child_by_field_name('name'), source)
                if function.type == 'identifier' and name in shadowed:
                    target_owner = None
                arguments = invocation.child_by_field_name('arguments')
                candidates = [m for m in target_owner['methods'] if not m['constructor'] and m['name'] == name
                              and m['id'] in by_id] if target_owner and target_owner['id'] not in ambiguous_ids else []
                if len(candidates) == 1 and arguments and len(candidates[0]['parameters']) == len(arguments.named_children):
                    add_edge(method['id'], candidates[0]['id'], 'calls', owner, invocation.start_point.row + 1,
                             callKind='constructor-body', expression=text(invocation, source)[:600])
            pending.extend(reversed(invocation.named_children))
    return count, limited
