"""Bind explicit consumer class registrations to existing graph methods, without inventing graph IDs."""
from .syntax import text


def consumer_handlers(syntaxes, nodes):
    """Index actual class/method declarations; comments and same-name unrelated files cannot create a handler."""
    by_class = {}
    for syntax in syntaxes:
        if syntax.language != 'csharp':
            continue
        namespace = next((n for n in syntax.nodes if n.type in {'file_scoped_namespace_declaration', 'namespace_declaration'}), None)
        namespace_name = text(namespace.child_by_field_name('name'), syntax.content) if namespace else ''
        for method in syntax.nodes:
            if method.type != 'method_declaration' or text(method.child_by_field_name('name'), syntax.content) not in {'Consume', 'ConsumeAsync'}:
                continue
            owner = method.parent
            while owner and owner.type != 'class_declaration':
                owner = owner.parent
            if owner is None:
                continue
            name = text(owner.child_by_field_name('name'), syntax.content)
            label = text(method.child_by_field_name('name'), syntax.content) + '()'
            matches = [n for n in nodes if n.get('file') == syntax.source['file'] and n.get('label', '').rsplit('.', 1)[-1] == label]
            if len(matches) == 1:
                by_class.setdefault((namespace_name, name), []).extend(matches)
    return by_class


def targets(syntax, handler, registry):
    """All visible matches remain candidates; runtime registration conditions are not evaluated."""
    imports = [text(n, syntax.content).removeprefix('using').rstrip(';').strip()
               for n in syntax.nodes if n.type == 'using_directive']
    namespace = next((n for n in syntax.nodes if n.type in {'file_scoped_namespace_declaration', 'namespace_declaration'}), None)
    if namespace:
        imports.append(text(namespace.child_by_field_name('name'), syntax.content))
    qualified, separator, name = handler.rpartition('.')
    return [node for (scope, cls), values in registry.items() if cls == (name if separator else handler)
            and (scope == qualified if separator else scope in imports) for node in values]
