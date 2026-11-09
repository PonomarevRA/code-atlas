"""Create bounded static engine requests from selected graph-owned original ASTs."""
from dataclasses import asdict
from pathlib import Path

from .method_flow_chunks import DEFERRED, _select
from .semantic_models import SourceSpan
from .semantic_sources import declaration_bindings, parse_graph_source

MAX_QUERIES = 64
CALLS = {'invocation_expression', 'call_expression', 'method_invocation', 'call',
         'function_call_expression', 'member_call_expression', 'scoped_call_expression'}
EXTENSIONS = {'csharp': {'.cs'}, 'python': {'.py'}, 'typescript': {'.ts', '.tsx'},
              'javascript': {'.js', '.jsx', '.mjs', '.cjs'}, 'java': {'.java'},
              'go': {'.go'}, 'cpp': {'.cpp', '.cc', '.cxx', '.h', '.hpp', '.hh', '.hxx'},
              'kotlin': {'.kt'}, 'php': {'.php'}, 'rust': {'.rs'}, 'swift': {'.swift'}}


def project_identity(node):
    """Use captured build/project ownership; absent ownership never invents a compilation target."""
    return node.get('codeProjectId') or (node.get('codeProjectFacts') or {}).get('id') or ''


def _utf16_span(node, path, content):
    """Translate original Tree-sitter byte columns to the engine protocol's UTF-16 positions."""
    lines = content.split(b'\n')
    points = []
    for point in (node.start_point, node.end_point):
        prefix = lines[point.row][:point.column].decode('utf-8')
        points.append((point.row, len(prefix.encode('utf-16-le')) // 2))
    return SourceSpan(path, *points[0], *points[1], 'utf-16')


def _callee(node):
    """Locate a terminal static callee token without resolving its type or executing a receiver."""
    current = (node.child_by_field_name('function') or node.child_by_field_name('name')
               or node.child_by_field_name('method') or node.named_child(0))
    for _ in range(24):
        if current is None:
            return node
        nested = (current.child_by_field_name('property') or current.child_by_field_name('name')
                  or current.child_by_field_name('field'))
        if nested is None:
            break
        current = nested
    return current if current is not None else node


def build_semantic_request(root, graph, node_ids, language, target_id, context):
    """Stage selected target files and at most 64 exact lexical callsites for one optional adapter.

    Only files captured under the same target are included. An unclassified target
    is limited to selected files and marked unqualified, preventing accidental
    binding against unrelated projects in a multilingual repository.
    """
    nodes = {node['id']: node for node in graph.get('nodes', [])}
    selected = [nodes[identifier] for identifier in node_ids]
    paths = []
    for node in selected:
        filename = node.get('file') or node.get('source_file')
        if filename in context.source_hashes and filename not in paths:
            paths.append(filename)
    if target_id:
        siblings = sorted({node.get('file') or node.get('source_file') for node in nodes.values()
                           if project_identity(node) == target_id and
                           Path(node.get('file') or node.get('source_file') or '').suffix.lower() in EXTENSIONS[language]})
        for path in siblings:
            if path in context.source_hashes and path not in paths:
                paths.append(path)
                if len(paths) >= 16:
                    break
    paths = paths[:16]
    contents, bindings = declaration_bindings(root, graph, paths, context.snapshot_id, context.source_hashes)
    queries, reasons, visited = [], set(), 0
    for path in paths:
        content = contents.get(path)
        if content is None:
            reasons.add('source-unavailable-or-stale')
            continue
        representative = {'memberFacts': {'language': language}}
        for raw in selected:
            if (raw.get('file') or raw.get('source_file')) == path:
                representative = raw
                break
        tree, _ = parse_graph_source(content, path, representative)
        if tree is None:
            reasons.add('parser-unavailable')
            continue
        for raw in selected:
            if (raw.get('file') or raw.get('source_file')) != path:
                continue
            declaration, reason = _select(tree.root_node, raw, content)
            if declaration is None:
                reasons.add(reason)
                continue
            source_span = asdict(_utf16_span(declaration, path, content))
            pending = [declaration]
            while pending and visited < 20_000 and len(queries) < MAX_QUERIES:
                current = pending.pop()
                visited += 1
                if current != declaration and current.type in DEFERRED:
                    reasons.add('deferred-bodies-excluded')
                    continue
                if current.type in CALLS:
                    callee = _callee(current)
                    point = _utf16_span(callee, path, content)
                    queries.append({'sourceNodeId': raw['id'], 'path': path,
                        'line': point.start_line, 'column': point.start_column,
                        'endLine': point.end_line, 'endColumn': point.end_column,
                        'kind': 'call', 'label': content[callee.start_byte:callee.end_byte].decode('utf-8')[:160],
                        'sourceSpan': source_span, 'callsite': asdict(_utf16_span(current, path, content))})
                pending.extend(reversed(current.named_children))
            if pending:
                reasons.add('query-ast-or-count-budget')
    if not target_id:
        reasons.add('compilation-target-unqualified')
    captured_cohort_complete = set(contents) == set(context.source_hashes)
    if not captured_cohort_complete:
        reasons.add('captured-source-cohort-incomplete')
    files = [{'path': path, 'text': content.decode('utf-8'), 'sourceHash': context.source_hashes[path]}
             for path, content in contents.items()]
    return {'files': files, 'queries': queries, 'scope': {'targetId': target_id or None,
            'qualified': bool(target_id) and captured_cohort_complete,
            'capturedCohortComplete': captured_cohort_complete,
            'complete': False, 'reasons': sorted(reasons)}}, contents, bindings
