"""
Real provider parity and batch-boundary contracts, independent of map layout.
"""

from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from atlas.graphify_adapter import GraphifyError, build_graph
from atlas.integrations.graphify.chunking import read_chunk_report
from atlas.integrations.graphify.runtime import GraphifyRuntime
from atlas.member_graph import enrich_member_graph
from atlas.attribute_graph import enrich_attribute_graph


def worker_module():
    """
    Load the shipped standalone script without executing its CLI entrypoint.
    """
    path = Path(__file__).parents[1] / 'resources/integrations/graphify/chunk_worker.py'
    spec = importlib.util.spec_from_file_location('tested_graphify_chunk_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def graph_facts(graph):
    """
    Compare complete fact multisets, including evidence and parallel edges.
    """
    return tuple(Counter(json.dumps(item, ensure_ascii=False, sort_keys=True)
                         for item in graph[section]) for section in ('nodes', 'edges'))


SOURCES = {
    'python': {
        'a.py': 'from b import work as perform\n\ndef run():\n    return perform()\n',
        'b.py': 'from c import value\n\ndef work():\n    return value()\n',
        'c.py': 'def value():\n    return 42\n',
    },
    'csharp': {
        'Contract.cs': 'namespace Demo; public interface IWorker { int Run(); }\n',
        'Worker.cs': 'namespace Demo; public class Worker : IWorker { public int Run() { return Value(); } private int Value() { return 42; } }\n',
        'Entry.cs': 'namespace Demo; public class Entry { public int Execute(IWorker worker) { return worker.Run(); } }\n',
    },
    'typescript': {
        'entry.ts': "import {work} from './barrel'; export function run(){ return work(); }\n",
        'barrel.ts': "export {work} from './worker';\n",
        'worker.ts': 'export function work(){ return 42; }\n',
    },
}


@pytest.mark.parametrize('language', SOURCES)
def test_real_chunks_preserve_complete_monolithic_facts(tmp_path, language):
    """
    Cross-file aliases, dispatch and reexports survive the authoritative pass.
    """
    root = tmp_path / 'source'
    root.mkdir()
    for filename, content in SOURCES[language].items():
        (root / filename).write_text(content)
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    monolithic = build_graph(root, tmp_path / 'monolithic', chunked=False)
    chunked = build_graph(root, tmp_path / 'chunked', chunk_max_files=1)
    assert graph_facts(chunked) == graph_facts(monolithic)
    report = chunked['metadata']['chunkAnalysis']
    assert report['chunkCount'] == report['fileCount'] == 3
    assert report['strategy'] == 'bounded-extraction-global-resolution'
    assert report['globalResolution'] == 'full-corpus'
    assert sorted(filename for chunk in report['chunks'] for filename in chunk['files']) == sorted(before)
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before
    if language == 'typescript':
        assert report['cacheBypassFiles'] == 3
        assert report['fileExtractorCalls'] == {'chunks': 3, 'global': 3}
    else:
        assert report['fileExtractorCalls'] == {'chunks': 3, 'global': 0}
    warm = build_graph(root, tmp_path / 'warm', chunk_max_files=1)
    assert graph_facts(warm) == graph_facts(monolithic)
    if language != 'typescript':
        assert warm['metadata']['chunkAnalysis']['fileExtractorCalls'] == {'chunks': 0, 'global': 0}


def test_changed_and_deleted_files_do_not_reappear_from_chunk_context(tmp_path):
    """
    A later snapshot uses only its live corpus and records a new source identity.
    """
    root = tmp_path / 'source'
    root.mkdir()
    for filename, content in SOURCES['python'].items():
        (root / filename).write_text(content)
    first = build_graph(root, tmp_path / 'first', chunk_max_files=1)
    (root / 'a.py').write_text('def run():\n    return 7\n')
    (root / 'b.py').unlink()
    (root / 'c.py').unlink()
    second = build_graph(root, tmp_path / 'second', chunk_max_files=1)
    reference = build_graph(root, tmp_path / 'reference', chunked=False)
    assert graph_facts(second) == graph_facts(reference)
    assert second['metadata']['chunkAnalysis']['fileCount'] == 1
    assert first['metadata']['chunkAnalysis']['sourceFingerprint'] != second['metadata']['chunkAnalysis']['sourceFingerprint']
    assert all(node['file'] not in {'b.py', 'c.py'} for node in second['nodes'])


def test_warm_rebuild_reuses_unchanged_extraction_and_rebinds_modified_file(tmp_path):
    """
    Compressed replay skips unchanged file extractors; a changed body is re-extracted.
    """
    root = tmp_path / 'source'
    root.mkdir()
    for filename, content in SOURCES['python'].items():
        (root / filename).write_text(content)
    cold = build_graph(root, tmp_path / 'cold', chunk_max_files=1)
    warm = build_graph(root, tmp_path / 'warm', chunk_max_files=1)
    assert graph_facts(cold) == graph_facts(warm)
    assert warm['metadata']['chunkAnalysis']['fileExtractorCalls'] == {'chunks': 0, 'global': 0}
    assert warm['metadata']['chunkAnalysis']['persistentCache']['restoredEntries'] == 3
    (root / 'c.py').write_text('def value():\n    return 43\n')
    changed = build_graph(root, tmp_path / 'changed', chunk_max_files=1)
    reference = build_graph(root, tmp_path / 'reference', chunked=False)
    assert graph_facts(changed) == graph_facts(reference)
    assert changed['metadata']['chunkAnalysis']['fileExtractorCalls'] == {'chunks': 1, 'global': 0}
    assert changed['metadata']['chunkAnalysis']['persistentCache']['restoredEntries'] == 2


def test_resolver_config_change_invalidates_persistent_namespace(tmp_path):
    """
    A config change cannot reuse final or local facts under the previous context.
    """
    root = tmp_path / 'source'
    root.mkdir()
    for filename, content in SOURCES['python'].items():
        (root / filename).write_text(content)
    (root / 'tsconfig.json').write_text('{"compilerOptions":{"baseUrl":"."}}')
    first = build_graph(root, tmp_path / 'first', chunk_max_files=1)
    (root / 'tsconfig.json').write_text('{"compilerOptions":{"baseUrl":"./src"}}')
    changed = build_graph(root, tmp_path / 'changed', chunk_max_files=1)
    assert changed['metadata']['chunkAnalysis']['fileExtractorCalls']['chunks'] == changed['metadata']['chunkAnalysis']['fileCount']
    assert changed['metadata']['chunkAnalysis']['persistentCache']['restoredEntries'] == 0
    assert first['metadata']['chunkAnalysis']['configurationFingerprint'] != changed['metadata']['chunkAnalysis']['configurationFingerprint']


def test_unchanged_caller_does_not_keep_removed_target_from_warm_cache(tmp_path):
    """
    Renaming a callee inside an existing file does not preserve stale call edges.
    """
    root = tmp_path / 'source'
    root.mkdir()
    for filename, content in SOURCES['python'].items():
        (root / filename).write_text(content)
    build_graph(root, tmp_path / 'before', chunk_max_files=1)
    (root / 'b.py').write_text('def renamed():\n    return 42\n')
    warm = build_graph(root, tmp_path / 'warm', chunk_max_files=1)
    cold = build_graph(root, tmp_path / 'cold', chunked=False)
    assert graph_facts(warm) == graph_facts(cold)
    assert not any(edge['target'] == 'b_work' and edge['relation'] == 'calls' for edge in warm['edges'])


def test_xaml_peer_content_is_not_replayed_from_file_local_persistent_cache(tmp_path):
    """
    A code-behind handler rename cannot resurrect stale XAML-derived symbols or edges.
    """
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'View.xaml').write_text('<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" x:Class="Demo.View"><Button Click="OnClick" /></Window>')
    behind = root / 'View.xaml.cs'
    behind.write_text('namespace Demo { public partial class View { private void OnClick(object sender, object e) {} } }')
    build_graph(root, tmp_path / 'before', chunk_max_files=1)
    behind.write_text('namespace Demo { public partial class View { private void Renamed(object sender, object e) {} } }')
    warm = build_graph(root, tmp_path / 'warm', chunk_max_files=1)
    reference = build_graph(root, tmp_path / 'reference', chunked=False)
    assert graph_facts(warm) == graph_facts(reference)
    assert warm['metadata']['chunkAnalysis']['persistentContextBypassFiles'] >= 1
    assert not any('onclick' in node['id'] for node in warm['nodes'])


def test_independent_runtime_uses_same_chunk_protocol(tmp_path):
    """
    The external interpreter runs the standalone worker with no Atlas import.
    """
    root = tmp_path / 'source'
    root.mkdir()
    for filename, content in SOURCES['python'].items():
        (root / filename).write_text(content)
    runtime = GraphifyRuntime(tmp_path / 'runtime')
    runtime.set(str(Path(sys.executable).absolute()))
    with runtime.activate():
        graph = build_graph(root, tmp_path / 'external', chunk_max_files=1)
    assert graph['metadata']['integration']['mode'] == 'external'
    assert graph['metadata']['chunkAnalysis']['chunkCount'] == 3
    assert any(edge['relation'] == 'calls' for edge in graph['edges'])


def test_chunk_planner_keeps_large_file_atomic_and_hashes_content(tmp_path):
    """
    Byte targets split adjacent files without cutting source or losing input order.
    """
    worker = worker_module()
    paths = []
    for name, count in [('b.py', 3), ('a.py', 12), ('c.py', 2)]:
        path = tmp_path / name
        path.write_bytes(b'x' * count)
        paths.append(path)
    batches, records = worker.plan_chunks(paths, tmp_path, max_files=2, target_bytes=8)
    assert [[path.name for path in batch] for batch in batches] == [['b.py'], ['a.py'], ['c.py']]
    assert [record['bytes'] for record in records] == [3, 12, 2]
    (tmp_path / 'b.py').write_bytes(b'yyy')
    assert worker.plan_chunks(paths, tmp_path)[1][0]['sha256'] != records[0]['sha256']


@pytest.mark.parametrize('maximum,target', [(True, 1), (0, 1), (1025, 1), (1, False), (1, 0)])
def test_invalid_batch_limits_are_rejected(tmp_path, maximum, target):
    """
    Bad limits never start a provider job, even for an empty corpus.
    """
    with pytest.raises(ValueError):
        worker_module().plan_chunks([], tmp_path, max_files=maximum, target_bytes=target)
    with pytest.raises(GraphifyError):
        build_graph(tmp_path, tmp_path.parent / ('bad-' + str(maximum) + '-' + str(target)),
                    chunk_max_files=maximum, chunk_target_bytes=target)


def test_unsupported_provider_version_fails_before_extracting(tmp_path, monkeypatch):
    """
    A different provider cannot silently execute an unqualified cache protocol.
    """
    worker = worker_module()
    monkeypatch.setattr(worker, 'version', lambda _: '999.0')
    with pytest.raises(RuntimeError, match='requires graphifyy'):
        worker.run_chunked(tmp_path, tmp_path.parent / 'output')


def test_changed_source_during_provider_work_is_not_published(tmp_path, monkeypatch):
    """
    The source fence rejects facts assembled from different file revisions.
    """
    import graphify.extract as provider
    worker = worker_module()
    root, output = tmp_path / 'source', tmp_path / 'output'
    root.mkdir()
    output.mkdir()
    source = root / 'a.py'
    source.write_text('def work(): return 1\n')
    calls = []

    def extract(paths, **kwargs):
        """
        Change source during the authoritative pass, after the batch was hashed.
        """
        calls.append(list(paths))
        if len(calls) == 2:
            source.write_text('def work(): return 2\n')
        return {'nodes': [], 'edges': [], 'failed_sources': []}

    def run_cli(*args, **kwargs):
        """
        Stand in only for CLI orchestration; execute the real chunk interceptor.
        """
        return provider.extract([source], root=root, cache_root=output)

    monkeypatch.setattr(provider, 'extract', extract)
    monkeypatch.setattr(worker.runpy, 'run_module', run_cli)
    with pytest.raises(RuntimeError, match='Source changed'):
        worker.run_chunked(root, output)
    assert provider.extract is extract
    assert not (output / 'graphify-out/atlas-chunks.json').exists()


def test_configuration_mutation_during_global_resolution_is_rejected(tmp_path, monkeypatch):
    """
    The report and persistent cache are published only for one configuration revision.
    """
    import graphify.extract as provider
    worker = worker_module()
    root, output = tmp_path / 'source', tmp_path / 'output'
    root.mkdir()
    source, config = root / 'a.py', root / 'tsconfig.json'
    source.write_text('def work(): return 1\n')
    config.write_text('{"compilerOptions":{}}')
    calls = []

    def extract(paths, **kwargs):
        """
        Simulate a settings edit after local extraction has completed.
        """
        calls.append(list(paths))
        if len(calls) == 2:
            config.write_text('{"compilerOptions":{"strict":true}}')
        return {'nodes': [], 'edges': [], 'failed_sources': []}

    def run_cli(*args, **kwargs):
        """
        Exercise the actual interceptor without replacing its configuration fence.
        """
        return provider.extract([source], root=root, cache_root=output)

    monkeypatch.setattr(provider, 'extract', extract)
    monkeypatch.setattr(worker.runpy, 'run_module', run_cli)
    with pytest.raises(RuntimeError, match='configuration changed'):
        worker.run_chunked(root, output)
    assert not (output / 'graphify-out/atlas-chunks.json').exists()


def test_provider_cache_quota_corruption_and_managed_file_boundary(tmp_path):
    """
    LRU evicts only managed compressed entries and bad payloads remain cache misses.
    """
    worker = worker_module()
    cache_class = worker._support('provider_cache.py')['ProviderCache']
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'nodes': [{'id': 'node'}], 'edges': []}))
    cache = cache_class(tmp_path / 'cache', 'context', quota_bytes=100)
    foreign = cache.folder.parent / 'keep.txt'
    foreign.write_text('not a managed entry')
    assert cache.export('a' * 64, source)
    assert cache.export('b' * 64, source)
    entries = list(cache.folder.glob('*.gz'))
    assert len(entries) == 1
    assert sum(path.stat().st_size for path in entries) <= 100
    assert foreign.read_text() == 'not a managed entry'
    assert not cache.restore('a' * 64, tmp_path / 'miss.json')
    assert cache.restore('b' * 64, tmp_path / 'restored.json')
    assert (tmp_path / 'restored.json').read_bytes() == source.read_bytes()
    entries[0].write_bytes(b'corrupt')
    assert not cache.restore('b' * 64, tmp_path / 'bad.json')
    assert cache.export('b' * 64, source)
    assert cache.restore('b' * 64, tmp_path / 'repaired.json')


def test_provider_cache_expiry_and_symlink_boundaries(tmp_path):
    """
    Idle expiry uses saved access times and cache links never target unrelated data.
    """
    worker = worker_module()
    support = worker._support('provider_cache.py')
    clock = [1000]
    cache = support['ProviderCache'](tmp_path / 'cache', 'context', clock=lambda: clock[0])
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'nodes': [{'id': 'node'}], 'edges': []}))
    assert cache.export('a' * 64, source)
    clock[0] += support['MAX_IDLE_SECONDS'] + 1
    assert not cache.restore('a' * 64, tmp_path / 'expired.json')
    outside = tmp_path / 'outside'
    outside.mkdir()
    protected = outside / ('b' * 64 + '.gz')
    protected.write_bytes(b'keep')
    redirected = cache.folder.parent / ('c' * 64)
    redirected.symlink_to(outside, target_is_directory=True)
    assert cache.export('d' * 64, source)
    assert protected.read_bytes() == b'keep'


def test_duplicate_chunk_file_report_is_rejected(tmp_path):
    """
    A malformed report cannot claim two owners for the same source file.
    """
    folder = tmp_path / 'graphify-out'
    folder.mkdir()
    report = {'version': 'atlas-graphify-chunks-v1', 'chunkCount': 2, 'fileCount': 2,
              'strategy': 'bounded-extraction-global-resolution', 'globalResolution': 'full-corpus',
              'chunks': [{'id': 'a' * 64, 'bytes': 1, 'files': ['a.py']},
                         {'id': 'b' * 64, 'bytes': 1, 'files': ['a.py']}]}
    (folder / 'atlas-chunks.json').write_text(json.dumps(report))
    with pytest.raises(ValueError, match='exactly once'):
        read_chunk_report(tmp_path)


def _without_batch_metadata(graph):
    """
    Compare enrichment facts while excluding scheduling-only observations.
    """
    result = deepcopy(graph)
    for section in ('memberGraph', 'attributeGraph'):
        for key in ('executionScope', 'chunkCount', 'chunkMaxFiles', 'capturedAt'):
            result.get('metadata', {}).get(section, {}).pop(key, None)
    return result


def test_local_mod_batch_size_preserves_facts_and_global_budgets(tmp_path):
    """
    Member/attribute batches retain one shared capture and stable ownership IDs.
    """
    root = tmp_path / 'source'
    root.mkdir()
    for filename, content in {**SOURCES['csharp'], **SOURCES['python']}.items():
        (root / filename).write_text(content)
    raw = build_graph(root, tmp_path / 'graph', chunk_max_files=1)
    small = enrich_member_graph(raw, root, chunk_max_files=1)
    large = enrich_member_graph(raw, root, chunk_max_files=32)
    assert _without_batch_metadata(small) == _without_batch_metadata(large)
    assert small['metadata']['memberGraph']['chunkCount'] == 3
    small_attributes = enrich_attribute_graph(small, root, chunk_max_files=1)
    large_attributes = enrich_attribute_graph(large, root, chunk_max_files=32)
    assert _without_batch_metadata(small_attributes) == _without_batch_metadata(large_attributes)
    assert small_attributes['metadata']['attributeGraph']['chunkCount'] == 3
