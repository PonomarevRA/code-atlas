"""Byte evidence for a declaration captured during an existing AST pass."""

from hashlib import sha256


def capture_review_declaration(node, declaration, source, filename, source_hash, identity):
    """Keep the exact declaration and Python decorator range without copying source into the graph."""
    if declaration.parent is not None and declaration.parent.type == 'decorated_definition':
        declaration = declaration.parent
    if declaration.has_error:
        return
    facts = {
        'identity': identity, 'file': filename,
        'sourceHash': source_hash, 'sourceKind': 'snapshot-captured-source',
        'line': declaration.start_point.row + 1, 'endLine': declaration.end_point.row + 1,
        'column': declaration.start_point.column + 1,
        'declarationHash': sha256(source[declaration.start_byte:declaration.end_byte]).hexdigest(),
    }
    existing = node.get('reviewDeclaration')
    if existing is None:
        node['reviewDeclaration'] = facts
    elif existing != facts:
        # Some provider nodes collapse multiple declarations into the same raw ID.
        # Keep the affected identities out of both sides of the comparison.
        affected = set(existing.get('ambiguousIdentities', ()))
        affected.update((existing['identity'], identity))
        existing['ambiguousIdentities'] = sorted(affected)
