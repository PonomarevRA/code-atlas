"""Independent JavaScript activation and real shared-backend ESM/CJS/JSDoc/JSX qualification."""
from hashlib import sha256
import json
from pathlib import Path
from threading import Event

import pytest

from atlas.mod_analysis import active_analysis_rules, active_semantic_rules
from atlas.mod_store import ModStore
from atlas.semantic_models import SemanticContext
from atlas.semantic_overlay import make_overlay
from atlas.semantic_providers.typescript import TypeScriptProvider
from atlas.semantic_sources import declaration_bindings
from test_semantic_typescript import engine, HASH, request_for, utf16


def javascript_request(tmp_path, mode='esm'):
    """Capture a common JavaScript project with exact full declaration and callsite anchors."""
    if mode == 'cjs':
        caller = 'const {run: helper} = require("./helper");\nfunction main() { return helper(1); }\nmodule.exports={main};\n'
        helper = '/** @param {number} value */\nfunction run(value) { return value+1; }\nmodule.exports={run};\n'
    else:
        extra = 'const view=<div/>; ' if mode == 'jsx' else ''
        caller = 'import {run as helper} from "./helper";\nexport function main() { ' + extra + 'return helper(1); }\n'
        helper = '/** @param {number} value */\nexport function run(value) { return value+1; }\n'
    name = 'main.jsx' if mode == 'jsx' else 'main.js'
    files = []
    for path, text in ((name, caller), ('helper.js', helper)):
        (tmp_path / path).write_text(text)
        files.append({'path': path, 'text': text, 'sourceHash': sha256(text.encode()).hexdigest()})
    line = caller.splitlines()[1]
    start = line.index('helper(1)')
    source = {'path': name, 'start_line': 1, 'start_column': line.index('function'),
              'end_line': 1, 'end_column': utf16(line), 'coordinate_kind': 'utf-16'}
    site = {**source, 'start_column': utf16(line[:start]), 'end_column': utf16(line[:start + 9])}
    query = {'sourceNodeId': 'main', 'path': name, 'line': 1, 'column': site['start_column'],
             'kind': 'call', 'label': 'main', 'sourceSpan': source, 'callsite': site}
    context = SemanticContext('snapshot', 'workspace', str(tmp_path), 'javascript', HASH,
                               {item['path']: item['sourceHash'] for item in files}, HASH)
    return context, {'files': files, 'queries': [query]}


@pytest.mark.parametrize('mode', ['esm', 'cjs', 'jsx'])
def test_real_javascript_cross_file_and_production_graph(tmp_path, engine, mode):
    """JS/JSDoc candidates retain exact targets without producing confirmed edges."""
    context, request = javascript_request(tmp_path, mode)
    provider = TypeScriptProvider('semantic-javascript/javascript', language='javascript', engine_path=engine)
    probe = provider.probe(context, Event())
    assert probe.availability.status == 'available'
    result = provider.analyze(context, probe, request, Event())
    fact = result.facts[0]
    assert fact.confidence == 'candidate', fact
    assert fact.target.source_span.path == 'helper.js'
    assert fact.target.kind == 'function' and fact.target.label == 'run'
    graph = {'nodes': [
        {'id': 'main', 'file': request['files'][0]['path'], 'line': 2, 'kind': 'method', 'label': 'main()'},
        {'id': 'run', 'file': 'helper.js', 'line': 2, 'kind': 'method', 'label': 'run()'}], 'edges': []}
    snapshot = tmp_path / 'maps' / 'snapshot' / 'atlas-graph.json'
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps(graph))
    contents, bindings = declaration_bindings(tmp_path, graph, context.source_hashes,
                                               context.snapshot_id, context.source_hashes)
    rules = {'semanticProviders': [{'modId': 'semantic-javascript', 'id': 'javascript',
                                    'modFingerprint': HASH}], 'fingerprint': HASH}
    delta = make_overlay(snapshot, graph, tmp_path, [result], bindings, contents, rules)
    assert delta['edges'] == []
    stored = delta['facts']['main'][0]
    assert stored['confidence'] == 'candidate' and stored['target']['graph_node_id'] == 'run'
    assert stored['runtimeSelectionKnown'] is False
    assert result.status == 'partial'


def test_javascript_untyped_shadow_stays_unknown(tmp_path, engine):
    """A dynamic parameter masks the import and does not borrow its static definition."""
    context, request = request_for(tmp_path, language='javascript')
    caller = request['files'][0]['text'].replace('main()', 'main(helper)')
    request['files'][0].update(text=caller, sourceHash=sha256(caller.encode()).hexdigest())
    context.source_hashes['main.js'] = request['files'][0]['sourceHash']
    query = request['queries'][0]
    query['sourceSpan']['end_column'] += 6
    query['callsite']['start_column'] += 6
    query['callsite']['end_column'] += 6
    provider = TypeScriptProvider('javascript', language='javascript', engine_path=engine)
    result = provider.analyze(context, provider.probe(context, Event()), request, Event())
    assert result.facts[0].confidence == 'unknown' and result.facts[0].target is None


def test_javascript_and_typescript_activation_are_independent(tmp_path):
    """Neither profile requires the other mod; switching either Off preserves the source baseline."""
    store = ModStore(tmp_path, Path(__file__).resolve().parents[1] / 'mods')
    inventory = {'files': ['main.ts', 'main.js'], 'truncated': False, 'mode': 'all', 'version': 'test', 'warnings': []}
    baseline = active_analysis_rules(store, inventory=inventory)
    rules = active_semantic_rules(store, inventory=inventory)
    assert {'javascript', 'typescript'} <= {item['language'] for item in rules['semanticProviders']}
    for identifier, remaining in [('semantic-typescript', 'javascript'), ('semantic-javascript', None)]:
        item = next(item for item in store.catalog()['mods'] if item['id'] == identifier)
        assert not item['manifest']['requires']
        store.enable(identifier, item['fingerprint'], False)
        active = {item['language'] for item in active_semantic_rules(store, inventory=inventory)['semanticProviders']}
        assert active == ({remaining} if remaining else set())
        assert active_analysis_rules(store, inventory=inventory) == baseline


def test_javascript_missing_ambient_overload_does_not_confirm(tmp_path, engine):
    """JavaScript also sees global ambient overload changes; incomplete source stays candidate-only."""
    caller = 'function main() { return choose(1); }\n'
    files = [{'path': path, 'text': text, 'sourceHash': sha256(text.encode()).hexdigest()}
             for path, text in [('main.js', caller),
                                ('base.d.ts', 'declare function choose(value: number): number;\n')]]
    extra = 'declare function choose(value: 1): "specific";\n'
    (tmp_path / 'extra.d.ts').write_text(extra)
    source = {'path': 'main.js', 'start_line': 0, 'start_column': 0, 'end_line': 0,
              'end_column': len(caller.rstrip()), 'coordinate_kind': 'utf-16'}
    site = {**source, 'start_column': caller.index('choose'), 'end_column': caller.index('choose') + 9}
    query = {'sourceNodeId': 'main', 'path': 'main.js', 'line': 0, 'column': site['start_column'],
             'kind': 'call', 'label': 'main', 'sourceSpan': source, 'callsite': site}
    context = SemanticContext('snapshot', 'workspace', str(tmp_path), 'javascript', HASH,
                               {item['path']: item['sourceHash'] for item in files}, HASH)
    provider = TypeScriptProvider('javascript', language='javascript', engine_path=engine)
    probe = provider.probe(context, Event())
    request = {'files': files, 'queries': [query], 'scope': {'sourceClosureProven': True}}
    subset = provider.analyze(context, probe, request, Event())
    assert subset.facts[0].target.source_span.path == 'base.d.ts'
    files.append({'path': 'extra.d.ts', 'text': extra, 'sourceHash': sha256(extra.encode()).hexdigest()})
    context.source_hashes['extra.d.ts'] = files[-1]['sourceHash']
    expanded = provider.analyze(context, probe, request, Event())
    assert expanded.facts[0].target.source_span.path == 'extra.d.ts'
    assert subset.facts[0].confidence == expanded.facts[0].confidence == 'candidate'
