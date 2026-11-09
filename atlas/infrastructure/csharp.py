"""C# ORM and literal resource declarations feed the common infrastructure index."""
import re
from .syntax import text, literal, arguments
from .csharp_orm import receiver_types


def _owner(node, kinds):
    """Find the nearest actual declaration owner, without scanning comments or other classes."""
    current = node.parent
    while current and current.type not in kinds:
        current = current.parent
    return current


def _table_mappings(syntax, declaration):
    """Read explicit ToTable names from one IEntityTypeConfiguration declaration."""
    header = text(declaration, syntax.content).split('{', 1)[0]
    entity = re.search(r'IEntityTypeConfiguration\s*<\s*([\w.]+)\s*>', header)
    if not entity:
        return []
    result = []
    for call, callee, values in syntax.calls():
        if not declaration.start_byte <= call.start_byte < declaration.end_byte:
            continue
        value = literal(values[0]) if values and callee.endswith('.ToTable') else None
        if value:
            result.append((entity[1].split('.')[-1], {'name': value,
                'file': syntax.source['file'], 'line': call.start_point.row + 1}))
    return result


def _dbset(syntax, declaration):
    """Read a DbSet property with its declared owner and source evidence."""
    match = re.search(r'\bDbSet\s*<\s*([\w.]+)\s*>\s+(\w+)', text(declaration, syntax.content))
    owner = _owner(declaration, {'class_declaration', 'interface_declaration'})
    if not match or owner is None:
        return None
    return match[2], {'entity': match[1].split('.')[-1],
        'owner': text(owner.child_by_field_name('name'), syntax.content),
        'file': syntax.source['file'], 'line': declaration.start_point.row + 1}


def _static_value(syntax, declaration, value):
    """Accept a literal or an explicit RabbitMQQueue/Exchange name argument; do not evaluate constructors."""
    if value.type not in {'object_creation_expression', 'implicit_object_creation_expression'}:
        return literal(text(value, syntax.content))
    header = text(declaration.parent, syntax.content).split('=', 1)[0]
    if 'RabbitMQQueue' not in header and 'RabbitMQExchange' not in header:
        return None
    args = value.child_by_field_name('arguments')
    if args is None:
        args = next((n for n in value.named_children if n.type == 'argument_list'), None)
    named, positional = arguments([text(n, syntax.content) for n in args.named_children] if args else [])
    return literal(named.get('name') or (positional[0] if positional else ''))


def _static_symbol(syntax, declaration, namespace):
    """Read one named static literal, retaining its namespace for visibility checks."""
    name = text(declaration.child_by_field_name('name'), syntax.content)
    value = declaration.child_by_field_name('value')
    if value is None and declaration.type == 'variable_declarator' and len(declaration.named_children) > 1:
        value = declaration.named_children[-1]
    if value is None:
        arrow = next((n for n in declaration.named_children if n.type == 'arrow_expression_clause'), None)
        value = arrow.named_children[-1] if arrow is not None and arrow.named_children else None
    if not name or value is None:
        return None
    container = declaration if declaration.type == 'property_declaration' else declaration.parent.parent
    header = text(container, syntax.content).split('=', 1)[0]
    if 'static' not in header and 'const' not in header:
        return None
    owner = _owner(declaration, {'class_declaration', 'struct_declaration', 'record_declaration'})
    resource = _static_value(syntax, declaration, value)
    if owner is None or not resource:
        return None
    owner_name = text(owner.child_by_field_name('name'), syntax.content)
    return {'name': f'{owner_name}.{name}', 'qualified': f'{namespace}.{owner_name}.{name}',
            'value': resource, 'file': syntax.source['file'], 'line': declaration.start_point.row + 1}


def declarations(syntax):
    """Collect independent table mappings, typed DbSets and literal static resource names from one AST."""
    symbols, tables, sets = [], {}, {}
    namespace = re.search(r'\bnamespace\s+([\w.]+)', syntax.source['text'])
    namespace = namespace[1] if namespace else ''
    for node in syntax.nodes:
        if node.type == 'class_declaration':
            for entity, table in _table_mappings(syntax, node):
                tables.setdefault(entity, []).append(table)
        if node.type not in {'property_declaration', 'variable_declarator'}:
            continue
        dbset = _dbset(syntax, node)
        if dbset:
            sets.setdefault(dbset[0], []).append(dbset[1])
        symbol = _static_symbol(syntax, node, namespace)
        if symbol:
            symbols.append(symbol)
    return symbols, tables, sets


def orm_facts(syntax, sets, tables):
    """Match explicit DbSet member access to declared table candidates; naming conventions are never guessed."""
    seen = set()
    for node in syntax.nodes:
        if node.type != 'member_access_expression':
            continue
        name = text(node.child_by_field_name('name'), syntax.content)
        if name not in sets:
            continue
        receiver = text(node.child_by_field_name('expression'), syntax.content).removeprefix('this.')
        if not re.fullmatch(r'\w+', receiver):
            continue
        # A snapshot.Securities collection is not a database access. Require the receiver's
        # declared DbSet owner type, retaining all explicit table mappings as candidates.
        receiver_type = receiver_types(syntax, node).get(receiver)
        line = node.start_point.row + 1
        for dbset in sets[name]:
            if dbset['owner'] != receiver_type:
                continue
            entity = dbset['entity']
            for table in tables.get(entity, ()):
                key = (line, table['name'])
                if key in seen:
                    continue
                seen.add(key)
                yield {'kind': 'database', 'resourceKind': 'table', 'operation': 'uses', 'expression': repr(table['name']),
                       'line': line, 'language': 'csharp', 'provider': 'ef-dbset-v1', 'basis': 'dbset-entity-table',
                       'entity': entity, 'dbSet': name, 'contextType': dbset['owner'],
                       'evidence': [{k: item[k] for k in ('file', 'line')} for item in (dbset, table)]}
