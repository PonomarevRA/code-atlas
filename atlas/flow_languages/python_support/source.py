"""Bounded Python declaration lookup and UTF-8 source coordinates; never imports project code."""
import ast
import io
import tokenize

DECLARATIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
MAX_AST_NODES = 20_000


class Source:
    """Read ast byte offsets against the original UTF-8 lines without normalizing source."""

    def __init__(self, text):
        """Cache encoded source once; all later fragments use original byte offsets."""
        self.text = text
        self.data = text.encode('utf-8')
        self.lines = self.data.splitlines(keepends=True)
        self.offsets = [0]
        for line in self.lines:
            self.offsets.append(self.offsets[-1] + len(line))

    def span(self, node):
        """Match the host's one-based byte columns with exclusive end coordinates."""
        return {'line': node.lineno, 'column': node.col_offset + 1,
                'endLine': node.end_lineno, 'endColumn': node.end_col_offset + 1}

    def fragment(self, node, limit=400):
        """Slice only the requested bounded prefix, including multiline expressions."""
        if node is None:
            return ''
        start = self.offsets[node.lineno - 1] + node.col_offset
        end = self.offsets[node.end_lineno - 1] + node.end_col_offset
        # Four UTF-8 bytes per character suffice even for non-ASCII source labels.
        data = self.data[start:min(end, start + limit * 4)]
        return data.decode('utf-8', errors='ignore')[:limit]

    def header(self, declaration):
        """Keep original defaults, separators and return annotation before the first body statement."""
        first = declaration.body[0]
        start = self.offsets[declaration.lineno - 1] + declaration.col_offset
        end = self.offsets[first.lineno - 1] + first.col_offset
        # Ignore colons inside strings/comments, including comments between the
        # header and first body node. No tokens from the body are inspected.
        prefix = self.data[start:end].decode('utf-8')
        delimiter = None
        for token in tokenize.generate_tokens(io.StringIO(prefix).readline):
            if token.type == tokenize.OP and token.string == ':':
                delimiter = token.start
        lines = prefix.splitlines(keepends=True)
        row, column = delimiter
        header = (''.join(lines[:row - 1]) + lines[row - 1][:column]).rstrip()
        return header[:400], len(header) > 400

    def through(self, first, last):
        """Read a bounded expression prefix ending at the chosen comparison operand."""
        start = self.offsets[first.lineno - 1] + first.col_offset
        end = self.offsets[last.end_lineno - 1] + last.end_col_offset
        return self.data[start:min(end, start + 1600)].decode('utf-8', errors='ignore')[:400]


def declarations(tree):
    """Collect declarations with lexical type/function ownership using a bounded iterative walk."""
    found = []
    pending = [(tree, ())]
    count = 0
    while pending:
        item, owners = pending.pop()
        count += 1
        if count > MAX_AST_NODES:
            return [], True
        child_owners = owners
        if isinstance(item, DECLARATIONS):
            found.append((item, owners))
            child_owners = (*owners, item)
        for child in reversed(list(ast.iter_child_nodes(item))):
            pending.append((child, child_owners))
    return found, False


def resolve(found, node):
    """Require exact declaration/decoration start when supplied; never choose a nearby sibling."""
    facts = node.get('memberFacts') or {}
    name = facts.get('name') or str(node.get('label') or '').strip('.').split('(', 1)[0]
    matches = [(item, owners) for item, owners in found if item.name == name]
    if node.get('_callable_class') or node.get('kind') in {'class', 'interface', 'struct'}:
        matches = [(item, owners) for item, owners in matches if isinstance(item, ast.ClassDef)]
    elif facts.get('kind') in {'method', 'constructor', 'function'} or '(' in str(node.get('label', '')):
        matches = [(item, owners) for item, owners in matches if not isinstance(item, ast.ClassDef)]
    line = node.get('line')
    if isinstance(line, int):
        matches = [(item, owners) for item, owners in matches
                   if line in {item.lineno, min([item.lineno, *[d.lineno for d in item.decorator_list]])}]
    declaring_type = (node.get('callableFacts') or {}).get('declaringType')
    if declaring_type and not declaring_type.endswith('.py'):
        matches = [(item, owners) for item, owners in matches
                   if '.'.join(o.name for o in owners if isinstance(o, ast.ClassDef)) == declaring_type]
    if len(matches) != 1:
        return None
    return matches[0]


def has_yield(declaration):
    """Detect suspension only in this callable, excluding nested closures and classes."""
    pending = list(declaration.body)
    while pending:
        item = pending.pop()
        if isinstance(item, (ast.Yield, ast.YieldFrom)):
            return True
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # Defaults belong to creation in the enclosing callable, even when
            # the newly declared function's body is deferred.
            pending.extend(default for default in [*item.args.defaults, *item.args.kw_defaults] if default)
        elif not isinstance(item, ast.ClassDef):
            pending.extend(ast.iter_child_nodes(item))
    return False
