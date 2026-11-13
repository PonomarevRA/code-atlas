"""Static JavaScript declarations and same-script call candidates in HTML snapshots."""

from collections import Counter

from .flow_languages.javascript_support.declarations import CALLABLES, name_of, text, walk
from .html_preview_policy import HTML_NAMESPACE

MAX_SCRIPT_NODES = 100_000
MAX_TOTAL_AST_NODES = 500_000
MAX_CALL_EDGES = 16_000
JAVASCRIPT_TYPES = {
    'module', 'text/javascript', 'application/javascript', 'text/ecmascript',
    'application/ecmascript', 'application/x-javascript', 'text/jscript',
    'text/livescript', 'text/x-ecmascript', 'text/x-javascript',
    'text/javascript1.0', 'text/javascript1.1', 'text/javascript1.2',
    'text/javascript1.3', 'text/javascript1.4', 'text/javascript1.5',
}


def is_inline_javascript(element):
    """Accept inline HTML scripts with executable JS MIME types, excluding data and external scripts."""
    if element.tag != 'script' or element.namespace != HTML_NAMESPACE:
        return False
    attrs = dict(element.attrs)
    if 'src' in attrs:
        return False
    kind = attrs.get('type')
    if kind is None:
        language = attrs.get('language', '').strip().lower()
        return not language or 'text/' + language in JAVASCRIPT_TYPES
    kind = kind.strip().lower()
    if kind == 'module':
        return True
    mime = kind.split(';', 1)[0].strip()
    return not kind or (mime != 'module' and mime in JAVASCRIPT_TYPES)


def _script_elements(elements):
    """Yield executable candidates outside inert template and noscript subtrees."""
    inert = set()
    for index, element in enumerate(elements):
        if element.tag in {'template', 'noscript'} or element.parent in inert:
            inert.add(index)
        elif is_inline_javascript(element) and element.text:
            yield index, element


def _body_scope(node):
    """Only publish call edges to direct script/module bindings, not member names."""
    parent = node.parent
    if node.type in {'function_declaration', 'generator_function_declaration'}:
        if parent.type == 'export_statement':
            parent = parent.parent
        return parent.type == 'program'
    if parent.type != 'variable_declarator' or parent.child_by_field_name('value') != node:
        return False
    binding = parent.child_by_field_name('name')
    declaration = parent.parent
    if binding is None or binding.type != 'identifier' or declaration.type != 'lexical_declaration':
        return False
    if declaration.children[0].type != 'const':
        return False
    container = declaration.parent
    if container.type == 'export_statement':
        container = container.parent
    return container.type == 'program'


def _binding_names(node, source):
    """Count potential shadowing conservatively, including destructuring and imports."""
    if node.type in CALLABLES:
        if node.type != 'method_definition':
            binding = node.child_by_field_name('name')
            if binding is not None:
                yield text(binding, source)
        parameters = node.child_by_field_name('parameters') or node.child_by_field_name('parameter')
        roots = [parameters]
    elif node.type in {'variable_declarator', 'class_declaration', 'class'}:
        roots = [node.child_by_field_name('name')]
    elif node.type == 'catch_clause':
        roots = [node.child_by_field_name('parameter')]
    elif node.type == 'import_statement':
        roots = [child for child in node.named_children if child.type == 'import_clause']
    elif node.type == 'for_in_statement':
        roots = [node.child_by_field_name('left')]
    elif node.type in {'assignment_expression', 'augmented_assignment_expression'}:
        target = node.child_by_field_name('left')
        roots = [target] if target is not None and target.type in {'identifier', 'array_pattern', 'object_pattern'} else []
    elif node.type == 'update_expression':
        target = node.child_by_field_name('argument')
        roots = [target] if target is not None and target.type == 'identifier' else []
    else:
        return
    for root in roots:
        if root is not None:
            for child in walk(root):
                if child.type in {'identifier', 'shorthand_property_identifier_pattern'}:
                    yield text(child, source)


def _script_nodes(tree, source):
    """Index declarations and associate each call with its nearest callable body."""
    declarations, calls, bindings = [], [], Counter()
    pending = [(tree, None)]
    while pending:
        node, owner = pending.pop()
        bindings.update(_binding_names(node, source))
        if node.type in CALLABLES:
            declarations.append(node)
            owner = node.id
        if node.type == 'call_expression' and owner is not None:
            function = node.child_by_field_name('function')
            if function is not None and function.type == 'identifier':
                calls.append((owner, text(function, source), function.start_point.row))
        pending.extend((child, owner) for child in reversed(node.named_children))
    return declarations, calls, bindings


class InlineScriptCapture:
    """Publish bounded code facts under the HTML script block without executing code."""

    def __init__(self, capture, identity):
        """Share the enclosing snapshot's node budget and own a bounded call/AST budget."""
        self.capture, self.identity = capture, identity
        self.report = {'version': 'html-inline-javascript-v1', 'scripts': 0, 'parsed': 0,
                       'functions': 0, 'calls': 0, 'astNodes': 0, 'truncated': False, 'warnings': []}
        self.parser = None

    def warn(self, reason, filename, line):
        """Make unsupported ranges, syntax and budget limits visible without copying code."""
        self.report['truncated'] = True
        if len(self.report['warnings']) < 12:
            self.report['warnings'].append({'file': filename, 'line': line, 'reason': reason})

    def document(self, document):
        """Parse exact raw-text bodies and translate JavaScript points to HTML coordinates."""
        parser, filename = document['parser'], document['file']
        source_text = parser.text
        previous_end, line, column = 0, 1, 0
        scripts = sorted(_script_elements(parser.elements), key=lambda pair: pair[1].start or 0)
        for index, element in scripts:
            self.report['scripts'] += 1
            block = self.capture.element_nodes.get((filename, index))
            if element.script_range is None or block is None:
                self.warn('script-source-range-unavailable', filename, element.line)
                continue
            start, end = element.script_range
            prefix = source_text[previous_end:start].replace('\r\n', '\n').replace('\r', '\n')
            line += prefix.count('\n')
            column = len(prefix.rsplit('\n', 1)[-1].encode()) if '\n' in prefix else column + len(prefix.encode())
            previous_end = start
            body = source_text[start:end].replace('\r\n', '\n').replace('\r', '\n').encode()
            self.script(document, block, body, line, column)

    def script(self, document, block, source, line, column):
        """Capture one script, discarding partial ASTs and keeping only direct lexical calls."""
        filename = document['file']
        if self.parser is None:
            try:
                from tree_sitter import Language, Parser
                import tree_sitter_javascript
                self.parser = Parser(Language(tree_sitter_javascript.language()))
            except (ImportError, TypeError, ValueError):
                self.warn('javascript-grammar-unavailable', filename, line)
                return
        if self.report['astNodes'] >= MAX_TOTAL_AST_NODES:
            self.warn('inline-javascript-ast-budget', filename, line)
            return
        tree = self.parser.parse(source)
        root = tree.root_node
        count = root.descendant_count
        if count > MAX_SCRIPT_NODES or self.report['astNodes'] + count > MAX_TOTAL_AST_NODES:
            self.warn('inline-javascript-ast-budget', filename, line)
            return
        self.report['astNodes'] += count
        if root.has_error:
            self.warn('inline-javascript-parse-error', filename, line)
            return
        self.report['parsed'] += 1
        declarations, calls, bindings = _script_nodes(root, source)
        published, targets = {}, {}
        for declaration in declarations:
            name = name_of(declaration, source)
            node = self.function(document, block, declaration, name, line, column)
            if node is None:
                self.warn('inline-javascript-node-budget', filename, line)
                break
            published[declaration.id] = node['id']
            if name and bindings[name] == 1 and _body_scope(declaration):
                targets[name] = node['id']
        seen = set()
        for owner, name, row in calls:
            source_id, target_id = published.get(owner), targets.get(name)
            key = source_id, target_id, row
            if source_id is None or target_id is None or key in seen:
                continue
            if self.report['calls'] >= MAX_CALL_EDGES:
                self.warn('inline-javascript-call-budget', filename, line + row)
                break
            seen.add(key)
            self.report['calls'] += 1
            self.capture.result['edges'].append({'source': source_id, 'target': target_id,
                'relation': 'calls', 'confidence': 'INFERRED', 'source_file': filename,
                'source_location': f'L{line + row}', 'line': line + row,
                'evidence': {'kind': 'same-script-identifier-call', 'runtimeSelectionKnown': False}})

    def function(self, document, block, declaration, name, line, column):
        """Store real HTML coordinates and whole-file SHA-256 without storing script bodies."""
        start, end = declaration.start_point, declaration.end_point
        identity = f"{block['id']}:{declaration.start_byte}"
        node_id = self.identity('script-function', identity)
        facts = {'kind': 'function', 'sourceKind': 'snapshot-captured-ast',
                 'sourceHash': document['sourceHash'], 'line': line + start.row, 'endLine': line + end.row,
                 'scriptNodeId': block['id'], 'scriptStartByte': declaration.start_byte,
                 'scriptEndByte': declaration.end_byte,
                 'column': start.column + 1 + (column if start.row == 0 else 0),
                 'endColumn': end.column + 1 + (column if end.row == 0 else 0)}
        node = {'id': node_id, 'file': document['file'], 'line': facts['line'], 'endLine': facts['endLine'],
                'label': (name or '<anonymous>') + '()', 'kind': 'function', '_callable': True,
                'memberFacts': {'kind': 'function', 'language': 'javascript', 'name': name,
                                'ownerNodeId': block['id'], 'sourceKind': 'snapshot-captured-ast'},
                'callableFacts': facts}
        if not self.capture.add(node):
            return None
        self.report['functions'] += 1
        self.capture.result['edges'].append({'source': block['id'], 'target': node_id,
            'relation': 'contains', 'confidence': 'EXTRACTED', 'sourceKind': 'snapshot-captured-ast'})
        return node
