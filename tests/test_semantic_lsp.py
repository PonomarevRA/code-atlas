"""LSP framing, read-only requests and real optional clangd qualification."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
from threading import Event

import pytest

from atlas.semantic_lsp import LspSession
from atlas.semantic_lsp_providers import (ClangdProvider, _safe_compile_flags, _request_sources,
                                          _definition_facts, _cpp_callable_targets,
                                          _cpp_include_closure_complete)
from atlas.semantic_models import SemanticContext, SourceSpan
from atlas.semantic_process import SemanticProcessError

HASH = 'a' * 64


def test_cpp_missing_include_overload_is_candidate(cpp_context):
    """An uncaptured header may introduce a better overload; diagnostics timing cannot prove closure."""
    context, request = cpp_context
    root = Path(context.workspace_root)
    (root / 'extra.h').write_text('inline int helper(double value) { return 7; }\n')
    caller = '#include "helper.h"\n#include "extra.h"\nint caller() { return helper(1.0); }\n'
    item = next(item for item in request['files'] if item['path'] == 'caller.cpp')
    item.update(text=caller, sourceHash=sha256(caller.encode()).hexdigest())
    context.source_hashes['caller.cpp'] = item['sourceHash']
    query = request['queries'][0]
    query.update(line=2)
    query['sourceSpan'].update(start_line=2, end_line=2, end_column=36)
    query['callsite'].update(start_line=2, end_line=2, end_column=33)
    provider = ClangdProvider('cpp')
    probe = provider.probe(context, Event())
    if probe.availability.status != 'available':
        pytest.skip('No compatible installed clangd')
    result = provider.analyze(context, probe, request, Event())
    assert not any(fact.confidence == 'confirmed-static' for fact in result.facts)
    assert result.coverage['calls'].analyzed == 0
    assert 'include-closure-not-proven' in result.coverage['calls'].reasons


def test_cpp_include_closure_rejects_macro_and_missing_search_location(tmp_path):
    compilation = [{'arguments': ['clang++', '-I', str(tmp_path)]}]
    assert _cpp_include_closure_complete({'main.cpp': '#include "helper.h"\n',
                                         'helper.h': 'void helper();'}, compilation, tmp_path)
    assert not _cpp_include_closure_complete({'main.cpp': '#define H "helper.h"\n#include H\n',
                                             'helper.h': 'void helper();'}, compilation, tmp_path)
    assert not _cpp_include_closure_complete({'main.cpp': '#include <missing.h>\n'}, compilation, tmp_path)


@pytest.fixture
def cpp_context(tmp_path):
    """Two captured files, one safe compile target and a selected cross-file call."""
    contents = {'helper.h': 'inline int helper(int value) { return value + 1; }\n',
                'caller.cpp': '#include "helper.h"\nint caller() { return helper(1); }\n'}
    for path, text in contents.items():
        (tmp_path / path).write_text(text, encoding='utf-8')
    (tmp_path / 'compile_commands.json').write_text(json.dumps([{'directory': str(tmp_path),
        'file': 'caller.cpp', 'arguments': ['clang++', '-std=c++17', '-I', str(tmp_path), '-c', 'caller.cpp']}]))
    hashes = {path: sha256(text.encode()).hexdigest() for path, text in contents.items()}
    context = SemanticContext('snapshot', 'workspace', str(tmp_path), 'default', HASH, hashes, HASH)
    request = {'files': [{'path': path, 'text': text, 'sourceHash': hashes[path]} for path, text in contents.items()],
        'queries': [{'path': 'caller.cpp', 'sourceNodeId': 'caller', 'kind': 'call', 'label': 'caller',
                     'line': 1, 'column': 22,
                     'sourceSpan': {'path': 'caller.cpp', 'start_line': 1, 'start_column': 0,
                                    'end_line': 1, 'end_column': 34},
                     'callsite': {'path': 'caller.cpp', 'start_line': 1, 'start_column': 22,
                                  'end_line': 1, 'end_column': 31}}]}
    return context, request


def fake_server(tmp_path, *, malicious=False):
    """A protocol fixture only; its synthetic definitions do not qualify a real language engine."""
    path = tmp_path / 'fixture_lsp.py'
    path.write_text('''import sys,json
def read():
    length=0
    while True:
        line=sys.stdin.buffer.readline()
        if not line: return None
        if line in (b"\\r\\n",b"\\n"): break
        if line.lower().startswith(b"content-length:"): length=int(line.split(b":",1)[1])
    return json.loads(sys.stdin.buffer.read(length))
def send(value):
    payload=json.dumps(dict(jsonrpc="2.0",**value)).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\\r\\n\\r\\n".encode()+payload);sys.stdout.buffer.flush()
while True:
    value=read()
    if value is None: break
    method=value.get("method")
    if method=="initialize":
        send({"id":value["id"],"result":{"capabilities":{"definitionProvider":True,"positionEncoding":"utf-16"}}})
    elif method=="textDocument/definition":
        send({"id":200,"method":"workspace/executeCommand","params":{"command":"must-not-run"}})
        rejected=read()
        if rejected.get("error",{}).get("code")!=-32601: raise RuntimeError("unsafe command accepted")
        send({"id":value["id"],"result":None})
    elif method=="shutdown": send({"id":value["id"],"result":None})
    elif method=="exit": break
''')
    return [sys.executable, '-I', str(path)]


def test_lsp_read_only_handshake_and_server_command_rejection(tmp_path):
    with LspSession(fake_server(tmp_path), tmp_path, Event()) as session:
        session.initialize(tmp_path)
        assert session.request('textDocument/definition', {}) is None
    assert session.process.poll() is not None


def test_malformed_framing_and_stalled_input_are_bounded(tmp_path):
    with pytest.raises(SemanticProcessError, match='lsp-(message-limit|eof)'):
        with LspSession([sys.executable, '-I', '-c',
                         'import sys,time;sys.stdout.write("Content-Length: 999999999\\r\\n\\r\\n");'
                         'sys.stdout.flush();time.sleep(10)'], tmp_path, Event(), timeout=1) as session:
            session.request('initialize', {})
    with pytest.raises(SemanticProcessError, match='lsp-timeout'):
        with LspSession([sys.executable, '-I', '-c', 'import time;time.sleep(10)'],
                        tmp_path, Event(), timeout=.1) as session:
            session.notify('textDocument/didOpen', {'text': 'x' * (1024 * 1024)})


def test_cpp_context_missing_and_explicit_missing_engine_fall_back(cpp_context):
    context, request = cpp_context
    provider = ClangdProvider('cpp', engine_path=Path(context.workspace_root) / 'absent')
    assert provider.probe(context, Event()).availability.status == 'missing'
    provider = ClangdProvider('cpp', engine_path=sys.executable)
    (Path(context.workspace_root) / 'compile_commands.json').unlink()
    outcome = provider.probe(context, Event())
    assert outcome.availability.status == 'incompatible'
    assert 'context-missing' in outcome.availability.reason


def test_cpp_rejects_executable_compiler_flags_and_escaping_includes(tmp_path):
    for flags in (['clang++', '-Xclang', '-load', 'evil.so'], ['clang++', '@response'],
                  ['clang++', '-I', '/outside'], ['clang++', '-fplugin=evil.so']):
        with pytest.raises((ValueError, IndexError)):
            _safe_compile_flags(tmp_path, flags, tmp_path, tmp_path / 'captured')
    flags = _safe_compile_flags(tmp_path, ['clang++', '-std=c++17', '-DVALUE=1', '-c', 'file.cpp'],
                                tmp_path, tmp_path / 'captured')
    assert flags == ['-std=c++17', '-DVALUE=1']


def test_source_hash_and_utf16_position_fences(cpp_context):
    context, request = cpp_context
    request['files'][0]['text'] += ' changed'
    with pytest.raises(ValueError, match='captured bytes'):
        _request_sources(context, request)


def test_ambiguous_engine_locations_never_choose_one_same_named_target(cpp_context):
    context, request = cpp_context
    contents, queries = _request_sources(context, request)
    root = Path(context.workspace_root)

    class AmbiguousSession:
        """Protocol response fixture; this does not qualify an installed language engine."""
        def request(self, method, parameters):
            return [{'uri': (root / path).as_uri(), 'range': {
                'start': {'line': line, 'character': left},
                'end': {'line': line, 'character': right}}}
                for path, line, left, right in [('helper.h', 0, 11, 17), ('caller.cpp', 1, 4, 10)]]

    facts, resolved, reasons = _definition_facts(AmbiguousSession(), root, context, 'cpp',
        contents, queries, callable_target=_cpp_callable_targets(contents))
    assert resolved == 0 and len(facts) == 2
    assert all(fact.confidence == 'candidate' for fact in facts)
    assert 'ambiguous-definition' in reasons


def test_duplicate_compile_targets_are_incompatible(cpp_context):
    context, _ = cpp_context
    database = Path(context.workspace_root) / 'compile_commands.json'
    rows = json.loads(database.read_text())
    database.write_text(json.dumps(rows * 2))
    outcome = ClangdProvider('cpp', engine_path=sys.executable).probe(context, Event())
    assert outcome.availability.status == 'incompatible'
    assert 'compile-target' in outcome.availability.reason


def test_bundled_cpp_mod_adds_only_semantic_profile(tmp_path):
    from atlas.mod_store import ModStore
    from atlas.mod_analysis import active_semantic_rules
    store = ModStore(tmp_path)
    inventory = {'files': ['caller.cpp'], 'truncated': False, 'version': 'test', 'mode': 'all', 'warnings': []}
    rules = active_semantic_rules(store, inventory=inventory)
    selected = [item for item in rules['semanticProviders'] if item['modId'] == 'semantic-cpp']
    assert selected and selected[0]['language'] == 'cpp'
    assert selected[0]['strategy'] == 'clangd-local-v1'


def test_real_installed_clangd_returns_source_anchored_cross_file_binding(cpp_context):
    context, request = cpp_context
    # Conditional qualification uses only an existing engine; never install one.
    provider = ClangdProvider('semantic-cpp/clangd')
    outcome = provider.probe(context, Event())
    if outcome.availability.status != 'available':
        pytest.skip('No compatible local clangd/context: ' + outcome.availability.reason)
    before = {path.name: path.read_bytes() for path in Path(context.workspace_root).iterdir() if path.is_file()}
    result = provider.analyze(context, outcome, request, Event())
    confirmed = [fact for fact in result.facts if fact.confidence == 'confirmed-static']
    assert len(confirmed) == 1
    assert confirmed[0].source.source_span.path == 'caller.cpp'
    assert confirmed[0].target.source_span.path == 'helper.h'
    assert result.status == 'partial' and result.coverage['calls'].status == 'partial'
    assert {path.name: path.read_bytes() for path in Path(context.workspace_root).iterdir() if path.is_file()} == before


def test_real_clangd_function_pointer_shadowing_does_not_confirm_header_call(cpp_context):
    context, request = cpp_context
    text = '#include "helper.h"\nint caller(int (*helper)(int)) { return helper(1); }\n'
    (Path(context.workspace_root) / 'caller.cpp').write_text(text)
    digest = sha256(text.encode()).hexdigest()
    context = replace(context, source_hashes={**context.source_hashes, 'caller.cpp': digest})
    request['files'][1].update(text=text, sourceHash=digest)
    query = request['queries'][0]
    query['column'] = text.splitlines()[1].index('return helper') + len('return ')
    query['sourceSpan']['end_column'] = len(text.splitlines()[1])
    query['callsite'].update(start_column=query['column'], end_column=query['column'] + len('helper(1)'))
    provider = ClangdProvider('semantic-cpp/clangd')
    probe = provider.probe(context, Event())
    if probe.availability.status != 'available':
        pytest.skip('No compatible local clangd: ' + probe.availability.reason)
    result = provider.analyze(context, probe, request, Event())
    assert not any(fact.confidence == 'confirmed-static' and fact.kind == 'call' for fact in result.facts)
    assert result.facts[0].confidence == 'candidate'
    assert result.facts[0].target.kind == 'local-symbol'
    assert result.facts[0].target.source_span is None


def test_real_clangd_facts_reach_effective_store_and_mcp(cpp_context, tmp_path):
    """A real engine binding reaches production consumers while captured Graphify stays immutable."""
    from atlas.mod_analysis import active_semantic_rules
    from atlas.semantic_sources import declaration_bindings
    from atlas.semantic_overlay import make_overlay, write_overlay
    from atlas.store import write_json
    from test_grouping_cache import legacy_store
    context, request = cpp_context
    root = Path(context.workspace_root)
    state, project_id, snapshot, _ = legacy_store(tmp_path / 'map-store')
    try:
        rules = active_semantic_rules(state.mods, inventory={'files': list(context.source_hashes), 'truncated': False})
        declaration = next(item for item in rules['semanticProviders'] if item['modId'] == 'semantic-cpp')
        context = replace(context, snapshot_id=snapshot.parent.name, workspace_id=project_id,
                          provider_fingerprint=declaration['modFingerprint'])
        provider = ClangdProvider('semantic-cpp/clangd')
        probe = provider.probe(context, Event())
        if probe.availability.status != 'available':
            pytest.skip('No compatible local clangd: ' + probe.availability.reason)
        result = provider.analyze(context, probe, request, Event())
        nodes = [{'id': identifier, 'label': identifier + '()', 'kind': 'method', 'file': path,
                  'line': line, 'memberFacts': {'name': identifier, 'language': 'cpp'},
                  'commentSource': {'sourceKind': 'snapshot-captured-source',
                                    'sourceHash': context.source_hashes[path]}}
                 for identifier, path, line in [('caller', 'caller.cpp', 2), ('helper', 'helper.h', 1)]]
        graph = {'nodes': nodes, 'edges': [], 'metadata': {'snapshotId': snapshot.parent.name}}
        write_json(snapshot, graph)
        state.projects[project_id]['path'] = str(root)
        original, raw = snapshot.read_bytes(), state.raw_graph(project_id).read_bytes()
        contents, bindings = declaration_bindings(root, graph, context.source_hashes,
            snapshot.parent.name, context.source_hashes)
        delta = make_overlay(snapshot, graph, root, [result], bindings, contents, rules)
        write_overlay(snapshot, delta)
        effective = state.get_graph(project_id)
        assert any(edge['source'] == 'caller' and edge['target'] == 'helper' for edge in effective['edges'])
        selected = state.source_snapshot(project_id, 'caller', snapshot.parent.name)[1]
        assert selected['semanticFacts'][0]['runtimeSelectionKnown'] is False
        mcp = json.loads(state.published_mcp_graph(project_id).read_text())
        assert any(link['source'] == 'caller' and link['target'] == 'helper' for link in mcp['links'])
        assert snapshot.read_bytes() == original and state.raw_graph(project_id).read_bytes() == raw
        item = next(item for item in state.mods.catalog()['mods'] if item['id'] == 'semantic-cpp')
        state.mods.enable(item['id'], item['fingerprint'], False)
        assert state.get_graph(project_id)['edges'] == []
    finally:
        state.close()
