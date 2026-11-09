"""Qualify real Graphify snapshots through selected local engines without installing dependencies."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

import pytest

from atlas.semantic_enrichment import SemanticEnrichment
from atlas.semantic_provider_registry import create_semantic_provider
from atlas.store import ProjectStore


REPOSITORY = Path(__file__).resolve().parents[1]
TYPESCRIPT = Path(os.environ.get('ATLAS_TEST_TYPESCRIPT_PATH',
    '/usr/local/lib/node_modules/n8n/node_modules/typescript/lib/typescript.js'))
SOURCES = {
    'dotnet/Caller.cs': 'class Caller {\n public static int Run() { return Worker.Work(1); }\n}\n',
    'dotnet/Worker.cs': 'class Worker {\n public static int Work(int value) => value;\n}\n',
    'typescript/main.ts': 'import { Work } from "./helper";\nexport function Run() { return Work(1); }\n',
    'typescript/helper.ts': 'export function Work(value: number): number { return value; }\n',
    'javascript/main.js': 'import { Work } from "./helper";\nexport function Run() { return Work(1); }\n',
    'javascript/helper.js': '/** @param {number} value */\nexport function Work(value) { return value; }\n',
    'python/main.py': 'from helper import Work\ndef Run():\n    return Work(1)\n',
    'python/helper.py': 'def Work(value):\n    return value + 1\n',
}
CALLERS = {'csharp': 'dotnet/Caller.cs', 'typescript': 'typescript/main.ts',
           'javascript': 'javascript/main.js', 'python': 'python/main.py'}


@pytest.fixture(scope='module')
def extracted_project(tmp_path_factory):
    """
    Build one production snapshot from literal source files and observe real provider requests.

    Args:
        tmp_path_factory: Pytest-owned directories for source and application state.

    Yields the store, service, graph, selected callers and trace. The fixture
    never writes graph nodes by hand or executes source/build/package scripts.
    """
    temporary = tmp_path_factory.mktemp('semantic-graphify')
    root = temporary / 'subject'
    for filename, text in SOURCES.items():
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    (root / 'dotnet/app.csproj').write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework>'
        '</PropertyGroup></Project>')
    for language in ('typescript', 'javascript'):
        (root / language / 'package.json').write_text(
            '{"private":true,"scripts":{"postinstall":"exit 1"}}')
    (root / 'python/pyproject.toml').write_text('[project]\nname="source-only-fixture"\nversion="0.0.0"\n')
    source_hashes = {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
                     for p in root.rglob('*') if p.is_file()}
    state = ProjectStore(temporary / 'state')
    service = None
    try:
        added = state.add(str(root))
        state.executor.submit(lambda: None).result(timeout=120)
        job = state.jobs[added['job']['id']]
        assert job['status'] == 'completed', job
        identifier = added['project']['id']
        graph = state.get_graph(identifier)
        snapshot = graph['metadata']['snapshotId']
        path = state._snapshot_path(identifier, '_graph')
        baseline = {p: p.read_bytes() for p in [path, state.raw_graph(identifier)]}
        selected = {}
        for language, filename in CALLERS.items():
            candidates = [n for n in graph['nodes'] if n.get('file') == filename
                          and n.get('label', '').lstrip('.').split('(')[0].rsplit('.', 1)[-1] == 'Run']
            assert len(candidates) == 1, (language, candidates)
            selected[language] = candidates[0]['id']
        trace = []

        class TracedProvider:
            """Delegate to the actual installed provider while retaining request evidence."""

            def __init__(self, declaration, paths):
                """
                Create the declared provider with isolated fixture settings.

                declaration is the host-validated rule; paths contains only explicit
                local settings chosen by this qualification fixture.
                """
                self.provider = create_semantic_provider(declaration, paths)
                self.provider_id = self.provider.provider_id
                self.language = declaration['language']

            def probe(self, context, cancel):
                """
                Probe pinned context under cancellation and return actual availability.

                The observed result comes from the real provider, including missing
                dependency and compatibility reasons. It is never synthesized.
                """
                result = self.provider.probe(context, cancel)
                trace.append({'stage': 'probe', 'language': self.language, 'context': context,
                              'availability': result.availability})
                return result

            def analyze(self, context, probe, request, cancel):
                """
                Analyze the captured request under its probe and cancellation fence.

                Keep a detached request for provenance assertions, then return the
                unchanged real result so production projection owns all binding.
                """
                result = self.provider.analyze(context, probe, request, cancel)
                trace.append({'stage': 'analyze', 'language': self.language,
                              'request': deepcopy(request), 'result': result})
                return result

        service = SemanticEnrichment(state, provider_factory=TracedProvider)
        if TYPESCRIPT.is_file():
            for language in ('typescript', 'javascript'):
                service.settings.save('semantic-' + language, 'typescript-local-v1',
                                      {'enginePath': str(TYPESCRIPT)})
        dotnet = Path('/usr/local/share/dotnet/dotnet')
        sdk = Path('/usr/local/share/dotnet/sdk/8.0.403')
        worker = REPOSITORY / 'build/semantic-csharp/Atlas.Semantic.Roslyn.dll'
        if dotnet.is_file() and sdk.is_dir() and worker.is_file():
            service.settings.save('semantic-csharp', 'roslyn-local-v1',
                {'dotnetPath': str(dotnet), 'sdkPath': str(sdk), 'workerPath': str(worker)})
        # This pins the interpreter under test; a missing Jedi remains a measured
        # dependency failure, never a reason to install or manufacture success.
        service.settings.save('semantic-python', 'jedi-local-v1', {'pythonPath': sys.executable})
        yield state, service, identifier, snapshot, graph, selected, trace
        assert source_hashes == {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
                                 for p in root.rglob('*') if p.is_file()}
        assert all(p.read_bytes() == content for p, content in baseline.items())
        assert not list(root.rglob('obj')) and not list(root.rglob('bin'))
        assert not list(root.rglob('node_modules')) and not list(root.rglob('__pycache__'))
    finally:
        if service is not None:
            service.close()
        state.close()


def test_real_graphify_engine_projection_and_mcp(extracted_project):
    """
    Verify selected language, actual worker evidence and accepted facts through a real snapshot.

    extracted_project supplies the unmodified production extraction and traced
    providers. Missing tools remain explicit; available tools must analyze their
    own selected source and expose accepted facts in the effective MCP graph.
    """
    state, service, identifier, snapshot, graph, selected, trace = extracted_project
    service.use(identifier, snapshot, list(selected.values()), force=True)
    service.executor.submit(lambda: None).result(timeout=65)
    status = service.status(identifier, snapshot)
    assert status['enrichment'] != 'failed', status
    providers = {item['language']: item for item in status['providers']}
    assert set(selected) <= set(providers), providers
    overlay_path = state._snapshot_path(identifier, '_graph').with_name('semantic-overlay.json')
    overlay = json.loads(overlay_path.read_text()) if overlay_path.is_file() else {'facts': {}}
    report = {'snapshotId': snapshot, 'provider': graph['metadata']['provider'], 'languages': {}}
    available = []
    for language, node_id in selected.items():
        provider = providers[language]
        analyzed = [row for row in trace if row['stage'] == 'analyze' and row['language'] == language]
        accepted = overlay['facts'].get(node_id, [])
        report['languages'][language] = {'availability': provider['availability'],
            'enrichment': provider['enrichment'], 'outcome': provider.get('outcome'),
            'analyzeCalls': len(analyzed), 'acceptedFacts': len(accepted)}
        if provider['availability']['status'] != 'available':
            assert not analyzed and not accepted, provider
            continue
        available.append(language)
        assert len(analyzed) == 1, (language, analyzed, status)
        request = analyzed[0]['request']
        assert request['queries'], (language, request)
        assert {q['sourceNodeId'] for q in request['queries']} == {node_id}
        assert all(q['sourceSpan']['path'] == CALLERS[language] for q in request['queries'])
        assert provider['enrichment'] == 'applied' and accepted, (language, status, overlay)
        assert all(fact['runtimeSelectionKnown'] is False for fact in accepted)
    if not available:
        write_qualification_report(report)
        pytest.skip('No compatible local engines: availability checks passed; execution remains unqualified')
    mcp = json.loads(state.published_mcp_graph(identifier).read_text())
    projected = {node['id']: node for node in mcp['nodes']}
    assert set(projected) == {node['id'] for node in graph['nodes']}
    assert mcp['graph']['metadata']['semanticEnrichment']['revision'] == overlay['revision']
    for language in available:
        assert projected[selected[language]]['semanticFacts']
    report['mcpProjectionVerified'] = True
    write_qualification_report(report)


def write_qualification_report(report):
    """
    Save bounded fixture evidence only when explicitly requested by the test runner.

    report contains measured availability and projection counts. Missing engines
    are recorded before pytest skips execution qualification; failures never gain
    a successful MCP projection marker. The caller owns the destination directory.
    """
    destination = os.environ.get('ATLAS_ENGINE_AUDIT_REPORT')
    if destination:
        Path(destination).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
