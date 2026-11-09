"""Shared source budgets and provenance for independently implemented language adapters."""
from hashlib import sha256
from .registry import language_adapter
from .providers import create_provider
from .parsed_source import ParseBudget
from ..source_viewer import SourceError, read_node_source


def describe_language(analysis, node, result):
    """Route all supported languages through one provider factory and common output contract."""
    filename = node.get('file') or node.get('source_file') or ''
    provider = create_provider(filename, adapter_loader=language_adapter)
    if provider is None:
        return None
    description = provider.describe(analysis, node, result)
    analysis.truncation['events'] |= bool(description.get('eventsTruncated'))
    return description


def read_and_describe(analysis, node, result, adapter):
    """Read through the host safety boundary, merge adapter facts, and invalidate stale snapshot metrics."""
    budget = getattr(analysis, '_flow_parse_budget', None)
    if budget is None:
        budget = analysis.__dict__.setdefault('_flow_parse_budget', ParseBudget())
    # Loading and accounting share the parse lock: concurrent descriptions cannot
    # read the same file twice or exceed request caps before either read is recorded.
    with budget.lock:
        return _read_and_describe(analysis, node, result, adapter, budget)


def _read_and_describe(analysis, node, result, adapter, budget):
    """Load one host-owned source and merge fresh adapter facts while holding its request lock."""
    filename = node.get('file') or node.get('source_file') or ''
    if filename not in analysis.files:
        if len(analysis.files) >= analysis.max_files:
            analysis.truncation['files'] = True
            result['unresolved'].append('Исчерпан лимит файлов для разбора тела.')
            return result
        analysis.files[filename] = None
        try:
            source = read_node_source(analysis.root, node, analysis.snapshot_id, include_syntax=False)
        except SourceError:
            result['unresolved'].append('Текущий исходник недоступен.')
            return result
        size = len(source['text'].encode('utf-8'))
        if analysis.total_bytes + size > analysis.max_bytes:
            analysis.truncation['bytes'] = True
            result['unresolved'].append('Исчерпан лимит байтов для разбора тела.')
            return result
        analysis.total_bytes += size
        # Parse trees live only with this budgeted source DTO, never across requests.
        source['_flow_parse_cache'] = {'_budget': budget}
        analysis.files[filename] = source
    source = analysis.files[filename]
    if source is None:
        result['unresolved'].append('Исходник недоступен в пределах бюджета.')
        return result
    facts = adapter(source, node, max_events=analysis.max_events)
    result.update(facts)
    result.update(file=source['file'], sourceKind='working-tree',
                  sourceHash=sha256(source['text'].encode('utf-8')).hexdigest(),
                  sourceByteHash=source['sourceByteHash'], sourceReadAt=source['sourceReadAt'])
    analysis.truncation['events'] |= bool(facts.get('eventsTruncated'))
    analysis.truncation['ast'] |= (facts.get('methodFlow') or {}).get('reason') in {'selection-ast-budget', 'body-ast-budget'}
    if (result.get('complexity') or {}).get('sourceHash') != result['sourceHash']:
        from ..code_complexity import unavailable_complexity
        result['complexity'] = unavailable_complexity(reason='snapshot metrics differ from current source', source_kind='working-tree', source_hash=result['sourceHash'])
    if result.get('health') and result['health'].get('sourceHash') != result['sourceHash']:
        result['health'] = {'status':'unavailable', 'score':None, 'documentationScore':None, 'sourceKind':'working-tree'}
    return result
