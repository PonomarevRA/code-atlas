"""Shared adapter dispatch preserves safe reads, budgets, provenance and raw graph IDs."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from atlas.execution_path_source import SourceAnalysis
from atlas.flow_languages import host, registry
from atlas.node_description import build_node_description


def facts(source, node, *, max_events):
    """A contract fixture, not a substitute for real per-language parser acceptance."""
    return {'kind':'method', 'sourceStatus':'parsed', 'line':1, 'endLine':2,
            'analyzerVersion':'fixture-v1', 'signature':'def start()',
            'events':[{'kind':'call','callee':'helper','text':'helper()','line':2,'column':5,
                       'endLine':2,'endColumn':13,'confidence':'source','targetNodeIds':[],
                       'conditional':False,'awaited':False,'parallel':False,'branchIds':[]}][:max_events],
            'methodFlow':{'status':'parsed','steps':[{'kind':'call','label':'helper()', 'line':2,'column':5,
                           'site':{'line':2,'column':5,'endLine':2,'endColumn':13}}],
                          'truncated':False,'unsupported':[]}}


def test_adapter_through_description_binds_existing_raw_id_and_preserves_source(tmp_path, monkeypatch):
    """The shared description consumes adapter events without rewriting source/snapshot graph."""
    source = tmp_path / 'main.py'
    source.write_text('def start():\n    helper()\n')
    graph = {'nodes':[{'id':'start','label':'start()','file':'main.py','line':1},
                      {'id':'helper','label':'helper()','file':'main.py','line':4}],
             'edges':[{'source':'start','target':'helper','relation':'calls','confidence':'EXTRACTED','source_location':'L2'}]}
    before = deepcopy(graph), source.read_bytes()
    monkeypatch.setattr(host, 'language_adapter', lambda filename: facts)
    result = build_node_description(tmp_path, graph, 'start', 'snapshot')
    assert result['sourceStatus'] == 'parsed' and result['methodFlow']['status'] == 'parsed'
    assert result['events'][0]['targetNodeIds'] == ['helper']
    assert result['sourceHash'] and result['sourceByteHash'] and result['sourceKind'] == 'working-tree'
    assert (graph, source.read_bytes()) == before


def test_foreign_source_shares_file_and_byte_budgets(tmp_path, monkeypatch):
    """Two declarations share one file read; an additional file obeys the common budget."""
    for filename in ['a.py','b.py']:
        (tmp_path / filename).write_text('def start():\n    helper()\n')
    monkeypatch.setattr(host, 'language_adapter', lambda filename: facts)
    original, reads = host.read_node_source, []
    def read(*args, **kwargs):
        reads.append(args[1]['file'])
        return original(*args, **kwargs)
    monkeypatch.setattr(host, 'read_node_source', read)
    analysis = SourceAnalysis(tmp_path, 's', 1, 1000, 3)
    node = {'id':'a','label':'start()','line':1,'file':'a.py'}
    assert analysis.describe(node)['sourceStatus'] == 'parsed'
    assert analysis.describe(node)['sourceStatus'] == 'parsed'
    assert analysis.describe({**node,'file':'b.py'})['sourceStatus'] == 'unavailable'
    assert reads == ['a.py'] and analysis.truncation['files']
    tiny = SourceAnalysis(tmp_path, 's', 2, 1, 3)
    assert tiny.describe(node)['sourceStatus'] == 'unavailable' and tiny.truncation['bytes']


def test_adapter_never_receives_external_file(tmp_path, monkeypatch):
    """A source escape fails before any language parser receives file contents."""
    monkeypatch.setattr(host, 'language_adapter', lambda filename: lambda *args, **kwargs: pytest.fail('unsafe read'))
    result = SourceAnalysis(tmp_path, 's', 2, 1000, 3).describe({'id':'x','file':'../private.py','line':1})
    assert result['sourceStatus'] == 'unavailable'


def test_registry_is_explicit_and_does_not_hide_missing_transitive_dependencies(monkeypatch):
    """An unfinished adapter is optional; a broken adapter import is an actual failure."""
    assert registry.language_adapter('file.cs') is None
    calls = []
    def imported(name):
        calls.append(name)
        return SimpleNamespace(analyze=facts)
    monkeypatch.setattr(registry, 'import_module', imported)
    assert registry.language_adapter('file.TSX') is facts
    assert calls == ['atlas.flow_languages.typescript']
    def missing(name):
        raise ModuleNotFoundError(name=name)
    monkeypatch.setattr(registry, 'import_module', missing)
    assert registry.language_adapter('file.py') is None
    def broken(name):
        raise ModuleNotFoundError(name='required_grammar')
    monkeypatch.setattr(registry, 'import_module', broken)
    with pytest.raises(ModuleNotFoundError):
        registry.language_adapter('file.py')


def test_factory_csharp_and_module_providers_share_contract():
    """Parser choice changes provider identity, not the renderer vocabulary or shape."""
    from atlas.flow_languages.providers import create_provider
    from atlas.flow_languages.contract import VERSION
    base = {'events':[], 'methodFlow':{'status':'parsed','steps':[{'kind':'return','label':'return value','line':1,'column':1}], 'unsupported':[]}}
    legacy = SimpleNamespace(max_events=10, _describe_csharp=lambda node, result: deepcopy(base))
    csharp = create_provider('File.cs').describe(legacy, {}, {})
    from atlas.flow_languages.contract import normalize_description
    python = normalize_description(deepcopy(base), 'python', 10)
    assert csharp['methodFlow'] == python['methodFlow']
    assert csharp['methodFlow']['contractVersion'] == VERSION
    assert create_provider('file.py', adapter_loader=lambda filename: facts).id == 'python'
    assert create_provider('file.unsupported') is None


def test_common_contract_keeps_branches_and_bounds_extensions():
    """Unknown optional metadata remains recoverable; branch bodies never become linear siblings."""
    from atlas.flow_languages.contract import normalize_description
    branch = {'kind':'branch','label':'check','arms':[{'label':'true','steps':[{'kind':'return','label':'return value'}]}]}
    extension = {'id':'rust.macro','version':1,'label':'Макрос не раскрыт','data':{'name':'example'}}
    result = normalize_description({'events':[], 'methodFlow':{'steps':[branch, {'kind':'language-special','label':'special','extensions':[extension, {'bad':True}]}]}}, 'rust', 5)
    tree = result['methodFlow']
    assert len(tree['steps']) == 2 and tree['steps'][0]['arms'][0]['steps'][0]['kind'] == 'return'
    assert tree['steps'][1]['kind'] == 'unknown' and tree['steps'][1]['extensions'] == [dict(extension, detail='')]
    assert tree['unsupported']
    large = normalize_description({'events':[{'kind':'comment','text':str(i)} for i in range(20)], 'methodFlow':{'steps':[{'kind':'operation','label':'x'} for _ in range(500)]}}, 'python', 3)
    assert len(large['events']) == 3 and large['eventsTruncated']
    assert len(large['methodFlow']['steps']) == 180 and large['methodFlow']['truncated']


def test_common_truncation_keeps_only_visible_call_sites():
    """Factory-wide clipping cannot leave ghost links to calls missing from the shared tree."""
    from atlas.flow_languages.contract import normalize_description
    sites = [{'line':i+1,'column':1,'endLine':i+1,'endColumn':4} for i in range(200)]
    result = normalize_description({'events':[{'kind':'call',**site} for site in sites],
              'methodFlow':{'steps':[{'kind':'call','label':'f()','site':site} for site in sites]}}, 'python', 200)
    assert len(result['events']) == len(result['methodFlow']['steps']) == 180
    assert result['eventsTruncated'] and result['methodFlow']['truncated']


def test_python_provider_does_not_require_csharp_parser(tmp_path, monkeypatch):
    """A provider's missing grammar cannot disable another language's source inspection."""
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == 'tree_sitter_c_sharp':
            raise ImportError('C# grammar absent')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(host, 'language_adapter', lambda filename: facts)
    (tmp_path / 'main.py').write_text('def start():\n    helper()\n')
    result = SourceAnalysis(tmp_path, 's', 2, 1000, 10).describe({'id':'start','file':'main.py','line':1,'label':'start()'})
    assert result['sourceStatus'] == 'parsed' and result['flowProvider']['id'] == 'python'
    (tmp_path / 'Code.cs').write_text('class Code { void Run() {} }')
    result = SourceAnalysis(tmp_path, 's', 2, 1000, 10).describe({'id':'run','file':'Code.cs','line':1,'label':'Run()'})
    assert result['sourceStatus'] == 'unavailable' and any('грамматика C#' in text for text in result['unresolved'])
