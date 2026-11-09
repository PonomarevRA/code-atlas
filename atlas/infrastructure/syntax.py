"""Explicit grammar providers expose the same bounded syntax inventory for every language."""
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
import ast
import re

GRAMMARS = {'.cs': ('csharp', 'tree_sitter_c_sharp', 'language'),
            '.py': ('python', 'tree_sitter_python', 'language'),
            '.js': ('javascript', 'tree_sitter_javascript', 'language'),
            '.ts': ('typescript', 'tree_sitter_typescript', 'language_typescript'),
            '.java': ('java', 'tree_sitter_java', 'language'),
            '.go': ('go', 'tree_sitter_go', 'language'),
            '.rs': ('rust', 'tree_sitter_rust', 'language')}
CALLS = {'invocation_expression', 'call_expression', 'call', 'method_invocation'}
GRAMMARS.update({'.mjs': GRAMMARS['.js'], '.cjs': GRAMMARS['.js'], '.jsx': GRAMMARS['.js'],
                 '.tsx': ('typescript', 'tree_sitter_typescript', 'language_tsx')})
MAX_AST_NODES = 100000


def walk(root):
    """Yield named syntax nodes iteratively, rejecting oversized files rather than claiming completeness."""
    pending, count = [root], 0
    while pending:
        node = pending.pop()
        count += 1
        if count > MAX_AST_NODES:
            raise ValueError('infrastructure AST budget')
        yield node
        pending.extend(reversed(node.named_children))


def text(node, content):
    """Decode a source span; callers publish only whitelisted resource names, never arbitrary arguments."""
    return content[node.start_byte:node.end_byte].decode('utf-8') if node is not None else ''


def literal(value):
    """Read one ordinary string literal, excluding interpolation and arbitrary expression evaluation."""
    value = value.strip()
    if value.startswith('@"') and value.endswith('"'):
        return value[2:-1].replace('""', '"')
    try:
        result = ast.literal_eval(value)
        return result if isinstance(result, str) else None
    except (ValueError, SyntaxError, RecursionError):
        return None


@dataclass
class Syntax:
    """Host-read source plus a common syntax tree; no filesystem access is given to providers."""
    source: dict
    language: str
    content: bytes
    nodes: list

    def calls(self):
        """Yield a uniform callee/arguments/site tuple without treating comments or strings as calls."""
        for node in self.nodes:
            if node.type not in CALLS:
                continue
            function = node.child_by_field_name('function')
            if node.type == 'method_invocation':
                receiver = node.child_by_field_name('object')
                function_name = text(node.child_by_field_name('name'), self.content)
                callee = (text(receiver, self.content) + '.' if receiver else '') + function_name
            else:
                callee = text(function, self.content)
            arguments = node.child_by_field_name('arguments')
            values = [text(child, self.content) for child in arguments.named_children] if arguments else []
            yield node, callee, values


def parse(source):
    """Select a built-in grammar only; unavailable or erroneous source remains explicitly unanalysed."""
    from tree_sitter import Language, Parser
    spec = GRAMMARS.get(Path(source['file']).suffix.lower())
    if spec is None:
        return None
    language, module, factory = spec
    content = source['text'].encode('utf-8')
    tree = Parser(Language(getattr(import_module(module), factory)())).parse(content)
    if tree.root_node.has_error:
        raise ValueError('source contains syntax errors')
    return Syntax(source, language, content, list(walk(tree.root_node)))


def arguments(values):
    """Separate named argument spellings shared by C#, Python and common JS object envelopes."""
    named, positional = {}, []
    for value in values:
        match = re.match(r'^\s*([\w]+)\s*[:=]\s*(.*)$', value, re.S)
        if match:
            named[match[1].lower()] = match[2]
        else:
            positional.append(value)
    return named, positional
