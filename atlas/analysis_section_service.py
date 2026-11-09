"""Source and snapshot fences around task-resident syntax and description fields."""

from functools import lru_cache
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path

from .analysis_contexts import AnalysisContextError, ast_units
from .analysis_sections import section_page
from .source_viewer import SourceError, read_node_source


@lru_cache(maxsize=32)
def parser_identity(filename):
    """Pin the grammar and runtime installed for this process; restart observes package upgrades."""
    from .method_flow_chunks import GRAMMARS, VERSION
    grammar = GRAMMARS.get(Path(filename).suffix.lower())
    packages = ['tree-sitter']
    if grammar is not None:
        packages.append('tree-sitter-' + grammar[0].replace('_', '-'))
    identities = [VERSION, str(grammar)]
    for package in packages:
        try:
            identities.append(package + ':' + version(package))
        except PackageNotFoundError:
            identities.append(package + ':unavailable')
    return tuple(identities)


def cached_parser(contexts, context_id, project, snapshot, settings):
    """Create an immutable per-file syntax cache after the safe reader has checked exact bytes."""
    from .method_flow_chunks import _parse

    def parse(identity, content, filename):
        """Reuse syntax across methods in one file; a changed source hash selects a new entry."""
        key = ('syntax', filename, identity['sourceByteHash'], parser_identity(filename))

        def charge(parsed):
            """Failed parsers remain retryable; native node counts bound retained tree complexity."""
            if parsed[0] is None:
                return contexts.max_context_bytes + 1, 0
            return len(content), ast_units(parsed[0], contexts.max_units)

        return contexts.value(context_id, project, snapshot, settings, key,
                              lambda: _parse(content, filename), charge)
    return parse


def read_section(store, contexts, project, body, builder, settings_for):
    """
    Read one revision-bound description field and validate after potentially slow work.

    Source bytes are checked on every access. Descriptions can depend on other
    working files (handlers, imports and configuration), so they are recomputed
    instead of being cached under only the selected file hash. Content revisions
    fence continuations; only the separately proven file-local syntax is cached.
    """
    root, graph = store.source_node_snapshot(project, body.nodeId, body.snapshotId)
    store.assert_snapshot(project, body.snapshotId)
    settings = settings_for(root)
    contexts.check(body.contextId, project, body.snapshotId, settings)
    raw = next((node for node in graph.get('nodes', []) if node.get('id') == body.nodeId), None)
    if raw is None:
        raise SourceError('Узел не найден в выбранном снимке.', 404)
    source = read_node_source(root, raw, body.snapshotId, include_syntax=False)
    source_hash = source['sourceByteHash']
    semantic_revision = graph.get('metadata', {}).get('semanticRevision', '')
    def describe():
        """Acquire current context-sensitive facts without claiming single-file cache validity."""
        full_root, full_graph = store.comparison_snapshot(project)
        current_revision = (full_graph.get('metadata', {}).get('semanticEnrichment') or {}).get('revision', '')
        if current_revision != semantic_revision:
            raise AnalysisContextError('Semantic revision changed during analysis', 409)
        store.assert_snapshot(project, body.snapshotId)
        return builder(full_root, full_graph, body.nodeId, body.snapshotId)

    description = describe()
    # A file can change while a multi-file description is being constructed.
    current = read_node_source(root, raw, body.snapshotId, include_syntax=False)
    if current['sourceByteHash'] != source_hash:
        raise AnalysisContextError('Source changed during analysis; restart the section', 409)
    _, after = store.source_node_snapshot(project, body.nodeId, body.snapshotId)
    if after.get('metadata', {}).get('semanticRevision', '') != semantic_revision:
        raise AnalysisContextError('Semantic revision changed during analysis', 409)
    provenance = description.get('sourceProvenance') or {}
    if provenance.get('status') == 'changed' or provenance.get('reason') == 'captured-hash-conflict':
        raise AnalysisContextError('Snapshot source differs from the working file', 409)
    if body.section not in description:
        raise AnalysisContextError('Requested description section is unavailable', 404)
    # A handler/configuration change can alter a section while this file and the
    # snapshot stay identical. Its old offset must not continue into new facts.
    fact_revision = sha256(json.dumps(description[body.section], ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()
    scope = {'contextId': body.contextId, 'projectId': project, 'snapshotId': body.snapshotId,
             'nodeId': body.nodeId, 'section': body.section, 'sourceHash': source_hash,
             'semanticRevision': semantic_revision, 'factRevision': fact_revision}
    result = section_page(description[body.section], scope, body.sectionRef, body.limit)
    result['coverage'] = {'truncation': description.get('truncation', {}),
                          'sectionTruncated': bool(isinstance(description[body.section], dict)
                                                   and description[body.section].get('truncated')),
                          'analysisLimitsUnchanged': True}
    store.assert_snapshot(project, body.snapshotId)
    contexts.touch(body.contextId, project, body.snapshotId, settings)
    return result
