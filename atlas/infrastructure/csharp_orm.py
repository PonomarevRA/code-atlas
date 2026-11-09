"""Typed C# context receivers prevent ordinary snapshot collections becoming database facts."""
import re
from .syntax import text


def expand_contexts(syntaxes, sets):
    """Carry explicit DbSet declarations through a bounded inheritance closure, preserving evidence."""
    bases = {}
    for syntax in syntaxes:
        if syntax.language != 'csharp':
            continue
        for cls in syntax.nodes:
            if cls.type not in {'class_declaration', 'interface_declaration'}:
                continue
            parent = next((n for n in cls.named_children if n.type == 'base_list'), None)
            if parent:
                name = text(cls.child_by_field_name('name'), syntax.content)
                bases.setdefault(name, set()).update(re.findall(r'\b\w+\b', text(parent, syntax.content)))
    for name, records in sets.items():
        original = list(records)
        for owner in bases:
            ancestors, pending = set(), [owner]
            for _ in range(8):
                following = set()
                for item in pending:
                    following.update(bases.get(item, set()) - ancestors)
                ancestors.update(following)
                pending = following
            records.extend({**record, 'owner': owner} for record in original if record['owner'] in ancestors)


def receiver_types(syntax, member):
    """Resolve explicit fields/parameters and CreateDbContext factory locals within their owning class/method."""
    owner, method = member.parent, None
    while owner and owner.type not in {'class_declaration', 'struct_declaration', 'record_declaration'}:
        if owner.type in {'method_declaration', 'constructor_declaration'}:
            method = owner
        owner = owner.parent
    if owner is None:
        return {}
    declared, variables = {}, []
    for node in syntax.nodes:
        if not owner.start_byte <= node.start_byte < owner.end_byte:
            continue
        container = node.parent
        enclosing_method = None
        while container and container != owner:
            if container.type in {'method_declaration', 'constructor_declaration'}:
                enclosing_method = container
                break
            container = container.parent
        if enclosing_method is not None and enclosing_method != method:
            continue
        if node.type == 'parameter':
            declared[text(node.child_by_field_name('name'), syntax.content)] = text(node.child_by_field_name('type'), syntax.content)
        elif node.type == 'variable_declaration':
            scope = node.parent
            while scope and scope.type not in {'block', 'class_declaration', 'struct_declaration', 'record_declaration', 'lambda_expression'}:
                scope = scope.parent
            if enclosing_method is not None and (node.start_byte > member.start_byte or scope is not None
                    and not (scope.start_byte <= member.start_byte < scope.end_byte)):
                continue
            value_type = text(node.child_by_field_name('type'), syntax.content)
            for variable in node.named_children:
                if variable.type == 'variable_declarator':
                    name = text(variable.child_by_field_name('name'), syntax.content)
                    declared[name] = value_type
                    variables.append((name, text(variable, syntax.content)))
    for name, expression in variables:
        factory = re.search(r'=\s*(?:await\s+)?(\w+)\.CreateDbContext(?:Async)?\s*\(', expression)
        if factory:
            target = re.fullmatch(r'[\w.]*IDbContextFactory\s*<\s*([\w.]+)\s*>', declared.get(factory[1], ''))
            if target:
                declared[name] = target[1]
        adapter = re.search(r'=\s*(\w+)\.CreateLinqToDBContext\s*\(', expression)
        if adapter and declared.get(adapter[1]) not in {None, 'var'}:
            declared[name] = declared[adapter[1]]
    return {name: kind.rsplit('.', 1)[-1] for name, kind in declared.items()}


def generic_bindings(syntaxes, tables, context_types):
    """Collect concrete generic arguments only for classes whose body explicitly uses EF Set<T>."""
    slots, bindings = {}, {}
    for syntax in syntaxes:
        if syntax.language != 'csharp':
            continue
        for cls in syntax.nodes:
            if cls.type != 'class_declaration':
                continue
            header = text(cls, syntax.content).split('{', 1)[0]
            generic = re.search(r'\bclass\s+(\w+)\s*<([^>]+)>', header)
            if not generic:
                continue
            parameters = [p.strip() for p in generic[2].split(',')]
            for call, callee, _ in syntax.calls():
                callee = re.sub(r'\s+', '', callee)
                match = re.fullmatch(r'(\w+)\.Set\s*<\s*(\w+)\s*>', callee)
                if cls.start_byte <= call.start_byte < cls.end_byte and match and match[2] in parameters:
                    declared = receiver_types(syntax, call).get(match[1], '')
                    if declared in context_types:
                        slots[(generic[1], match[2])] = parameters.index(match[2])
    for syntax in syntaxes:
        if syntax.language != 'csharp':
            continue
        for node in syntax.nodes:
            if node.type != 'object_creation_expression':
                continue
            kind = text(node.child_by_field_name('type'), syntax.content)
            match = re.fullmatch(r'[\w.]*?(\w+)<([^<>]+)>', kind)
            if not match:
                continue
            arguments = [p.strip().rsplit('.', 1)[-1] for p in match[2].split(',')]
            for (owner, parameter), slot in slots.items():
                if match[1] == owner and slot < len(arguments):
                    for table in tables.get(arguments[slot], []):
                        bindings.setdefault((owner, parameter), []).append({**table,
                            'bindingFile': syntax.source['file'], 'bindingLine': node.start_point.row + 1})
    return bindings


def query_facts(syntax, tables, bindings, context_types):
    """Resolve typed EF/LinqToDB table calls; generic instantiations remain explicitly possible tables."""
    for node, callee, _ in syntax.calls():
        callee = re.sub(r'\s+', '', callee)
        match = re.fullmatch(r'(\w+)\.(Set|GetTable)\s*<\s*([\w.]+)\s*>', callee)
        if not match:
            continue
        context = receiver_types(syntax, node).get(match[1], '')
        if context not in context_types:
            continue
        entity, owner = match[3].rsplit('.', 1)[-1], node.parent
        while owner and owner.type != 'class_declaration':
            owner = owner.parent
        owner_name = text(owner.child_by_field_name('name'), syntax.content) if owner else ''
        concrete = tables.get(entity, [])
        candidates = concrete or bindings.get((owner_name, entity), [])
        seen = set()
        for table in candidates:
            if table['name'] in seen:
                continue
            seen.add(table['name'])
            evidence = [{'file': table['file'], 'line': table['line']}]
            if table.get('bindingFile'):
                evidence.append({'file': table['bindingFile'], 'line': table['bindingLine']})
            yield {'kind': 'database', 'resourceKind': 'table', 'operation': 'uses', 'expression': repr(table['name']),
                'line': node.start_point.row + 1, 'language': 'csharp', 'provider': 'ef-query-v1',
                'basis': 'typed-table-call' if concrete else 'generic-instantiation-candidate',
                'entity': entity, 'contextType': context, 'evidence': evidence, 'bindingCandidate': not bool(concrete)}
