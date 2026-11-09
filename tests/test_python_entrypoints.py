"""Real host capture and shared readers expose Python registrations without importing code."""
import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.analysis_facts import analysis_preview
from atlas.agent_views import search_nodes
from atlas.captured_entrypoints import captured_entrypoint_catalog
from atlas.map_payload import serialize_graph
from atlas.mod_analysis import active_analysis_rules
from atlas.mod_models import ModManifest
from atlas.mod_store import ModStore
from atlas.project_discovery import capture_project_scopes
from atlas.project_scopes import assign_code_projects
from atlas.python_entrypoints import capture_python_entrypoints


def _rules(tmp_path):
    """Activate real library packages with their exact fingerprints in an isolated store."""
    store = ModStore(tmp_path / 'state', Path(__file__).resolve().parents[1] / 'mods')
    for identifier in ('language-python', 'framework-fastapi', 'framework-fastmcp'):
        item = next(item for item in store.catalog()['mods'] if item['id'] == identifier)
        store.enable(identifier, item['fingerprint'], True)
    return active_analysis_rules(store)


def _graph(root, files):
    """Create realistic source-position nodes while refusing to run the fixture modules."""
    nodes = []
    for filename, source in files.items():
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        for declaration in ast.walk(ast.parse(source)):
            if isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nodes.append({'id': f'{filename}:{declaration.lineno}', 'label': declaration.name + '()',
                              'file': filename, 'line': declaration.lineno, 'kind': 'function'})
    return {'nodes': nodes, 'edges': [], 'metadata': {'snapshotId': 'pinned'}}


def test_real_mods_capture_http_mcp_and_registration_helper(tmp_path):
    """Aliases, nested registrations and cross-file app parameters share the same entry contract."""
    files = {'app.py': '''from fastapi import FastAPI as Application
from mcp.server.fastmcp import FastMCP
from api.routes import register
app = Application()
register(app)
@app.get('/health')
async def health(): return 'ok'
def create_mcp():
    server = FastMCP('local')
    @server.tool(name='find_code')
    def search(query: str): return query
    @server.resource('source://{id}')
    def source(id: str): return id
    @server.prompt()
    def explain(): return 'prompt'
raise RuntimeError('Must never execute analyzed modules')
''', 'api/routes.py': '''def register(api):
    @api.post('/items')
    def save(): return None
'''}
    root = tmp_path / 'repo'; root.mkdir()
    graph = _graph(root, files); original = deepcopy(graph)
    rules = _rules(tmp_path)
    scoped = capture_project_scopes(graph, root, rules); assign_code_projects(scoped['nodes'])
    captured = capture_python_entrypoints(scoped, root, rules)
    entries = captured['metadata']['entrypointDiscovery']['entries']
    assert graph == original and captured['edges'] == graph['edges']
    assert {(entry['kind'], entry['label']) for entry in entries} == {
        ('http', '/health'), ('http', '/items'), ('mcp', 'find_code'), ('mcp', 'source'), ('mcp', 'explain')}
    assert all(entry['handlerNodeIds'] and entry['runtimeStatus'] == 'unknown' for entry in entries)
    assert all(isinstance(entry['evidence'], list) and isinstance(entry['unresolved'], list) for entry in entries)
    assert all(entry['modFingerprint'] and entry['sourceHash'] for entry in entries)
    assert captured['metadata']['entrypointDiscovery']['status'] == 'complete'
    # Snapshot-only catalogue remains useful after the working copy has disappeared.
    for filename in files:
        (root / filename).unlink()
    catalogue = captured_entrypoint_catalog(captured, 'pinned')
    assert len(catalogue['entries']) == 5 and catalogue['coverage']['readFiles'] == 0
    preview = json.loads(serialize_graph(captured, 'map'))
    assert 'entries' not in preview['metadata']['entrypointDiscovery']
    assert all('entrypointFacts' not in node for node in preview['nodes'])
    found = search_nodes(captured, 'find_code', 10)
    assert len(found['nodes']) == 1
    assert found['nodes'][0]['analysisFacts']['entrypoints']['items'][0]['operationName'] == 'find_code'


def test_shadowing_dynamic_paths_and_router_prefix_are_honest(tmp_path):
    """Unknown receivers cannot masquerade as apps, and a router path is not a deployment URL."""
    source = '''from fastapi import FastAPI, APIRouter
app = FastAPI()
router = APIRouter(prefix='/v1')
@router.get('/orders')
def orders(): pass
@app.api_route('/multi', methods=['GET', 'POST'])
def multi(): pass
@app.get(compute_route())
def dynamic(): pass
def unrelated(app):
    @app.get('/fake')
    def fake(): pass
def rebound(app):
    app = object()
    @app.get('/also-fake')
    def fake2(): pass
rebound(app)
'''
    root = tmp_path / 'repo'; root.mkdir()
    captured = capture_python_entrypoints(_graph(root, {'app.py': source}), root, _rules(tmp_path))
    entries = captured['metadata']['entrypointDiscovery']['entries']
    assert len(entries) == 4
    router = next(entry for entry in entries if entry['label'] == '/v1/orders')
    assert router['routeStatus'] == 'router-local-path' and router['unresolved']
    assert {entry['httpMethod'] for entry in entries if entry['label'] == '/multi'} == {'GET', 'POST'}
    dynamic = next(entry for entry in entries if entry['handlerName'] == 'dynamic')
    assert dynamic['route'] is None and dynamic['unresolved']


def test_ambiguous_handlers_budgets_and_disabled_providers(tmp_path, monkeypatch):
    """Missing identities and capped scans remain explicit; disabled mods add no facts."""
    from atlas import python_entrypoints
    root = tmp_path / 'repo'; root.mkdir()
    graph = _graph(root, {'app.py': "from fastapi import FastAPI\napp=FastAPI()\n@app.get('/x')\ndef run(): pass\n"})
    graph['nodes'].append({**graph['nodes'][0], 'id': 'ambiguous'})
    rules = _rules(tmp_path)
    captured = capture_python_entrypoints(graph, root, rules)
    entry = captured['metadata']['entrypointDiscovery']['entries'][0]
    assert entry['handlerNodeIds'] == [] and entry['unresolved']
    assert captured['metadata']['entrypointDiscovery']['status'] == 'partial'
    assert capture_python_entrypoints(graph, root, {}) is graph
    monkeypatch.setattr(python_entrypoints, 'MAX_FILES', 0)
    bounded = capture_python_entrypoints(graph, root, rules)['metadata']['entrypointDiscovery']
    assert bounded['coverage']['limited'] and bounded['status'] == 'partial'


@pytest.mark.parametrize('files,areas', [
    ({'main.py': 'def main(): pass'}, {'.'}),
    ({'atlas/server.py': 'def run(): pass', 'tests/test_api.py': 'def test_api(): pass'}, {'atlas', 'tests'}),
    ({'src/app/api/routes.py': 'def run(): pass', 'src/app/services/calc.py': 'def calc(): pass'}, {'src/app/api', 'src/app/services'}),
])
def test_python_folders_without_manifests_are_logical_areas(tmp_path, files, areas):
    """Flat, repository and src layouts remain navigable without inventing build projects."""
    root = tmp_path / 'repo'; root.mkdir()
    graph = capture_project_scopes(_graph(root, files), root, _rules(tmp_path))
    projects = assign_code_projects(graph['nodes'])
    assert {project['path'] for project in projects if project['id'] != '__unassigned__'} == areas
    assert all(project.get('kind') == 'logical-area' for project in projects if project['nodeCount'])
    assert all(node.get('codeProjectFacts', {}).get('confidence') == 'folder-heuristic' for node in graph['nodes'])


def test_entrypoint_rule_schema_rejects_executable_and_unknown_strategies():
    """A JSON mod selects only reviewed host strategies and cannot inject parser code."""
    for strategy in ('python-file.py', 'exec', '../fastapi'):
        with pytest.raises(ValidationError):
            ModManifest.model_validate({'apiVersion': 1, 'id': 'example', 'name': 'Example', 'version': '1',
                'analysis': {'apiVersion': 1, 'entrypointProviders': [
                    {'id': 'entry', 'language': 'python', 'strategy': strategy}]}})


def test_captured_only_http_route_does_not_rescan_sources(tmp_path, monkeypatch):
    """The real API can expose a stored entry even if every live source scan would fail."""
    from fastapi.testclient import TestClient
    from atlas import server, store
    from test_grouping_cache import legacy_store
    state, project_id, path, graph = legacy_store(tmp_path)
    handler_id = graph['nodes'][0]['id']
    graph['metadata']['entrypointDiscovery'] = {'version': 'python-entrypoints-v1', 'status': 'complete',
        'coverage': {'unmappedHandlers': 0}, 'entries': [{'id': 'captured-http', 'kind': 'http',
            'label': '/health', 'route': '/health', 'handlerNodeIds': [handler_id]}]}
    store.write_json(path, graph)
    state.close()
    def forbidden(*args):
        raise AssertionError('captured catalogue must not scan the working copy')
    monkeypatch.setattr(server, 'build_entrypoints', forbidden)
    monkeypatch.setattr(store.ProjectStore, 'comparison_snapshot', forbidden)
    with TestClient(server.create_app(tmp_path / 'state'), base_url='http://127.0.0.1:8765') as client:
        snapshot_id = client.get(f'/api/projects/{project_id}/graph').json()['metadata']['snapshotId']
        response = client.get(f'/api/projects/{project_id}/entrypoints', params={
            'snapshotId': snapshot_id, 'capturedOnly': True})
        assert response.status_code == 200
        assert response.json()['entries'][0]['handlerNodeIds'] == [handler_id]
        assert response.json()['sourceKind'] == 'snapshot-captured-source'
        assert client.get(f'/api/projects/{project_id}/entrypoints', params={
            'snapshotId': 'stale', 'capturedOnly': True}).status_code == 409


def test_pipeline_description_and_disable_share_one_contract(tmp_path):
    """The actual host publishes entries to map metadata, source description and MCP projections."""
    from atlas.enrichment_pipeline import enrich_snapshot
    from atlas.node_description import build_node_description
    from atlas.graph_context import read_graph_node
    root = tmp_path / 'repo'; root.mkdir()
    source = "from fastapi import FastAPI\napp=FastAPI()\n@app.get('/health')\ndef check(): return 'ok'\n"
    graph = _graph(root, {'api.py': source})
    rules = _rules(tmp_path)
    captured = enrich_snapshot(graph, root, analysis_rules=rules)
    node = captured['nodes'][0]
    description = build_node_description(root, captured, node['id'], 'pinned')
    assert description['entrypointFacts'][0]['route'] == '/health'
    assert description['analysisFacts']['entrypoints']['items'][0]['runtimeStatus'] == 'unknown'
    assert read_graph_node(captured, node['id'])['node']['entrypointFacts'][0]['handlerNodeIds'] == [node['id']]
    disabled = capture_python_entrypoints(captured, root, {})
    assert 'entrypointDiscovery' not in disabled['metadata']
    assert 'entrypointFacts' not in disabled['nodes'][0]
    assert captured['nodes'][0]['entrypointFacts']


def test_symlink_source_is_not_read(tmp_path):
    """Indexed paths cannot make the capture host follow a source symlink outside its root."""
    root = tmp_path / 'repo'; root.mkdir()
    outside = tmp_path / 'secret.py'
    outside.write_text("from fastapi import FastAPI\napp=FastAPI()\n@app.get('/secret')\ndef secret(): pass")
    (root / 'linked.py').symlink_to(outside)
    graph = {'nodes': [{'id': 'n', 'file': 'linked.py', 'label': 'secret()', 'line': 4}], 'edges': []}
    result = capture_python_entrypoints(graph, root, _rules(tmp_path))['metadata']['entrypointDiscovery']
    assert result['entries'] == [] and result['coverage']['unavailableFiles'] == 1


def test_src_layout_absolute_registration_import(tmp_path):
    """Configured source roots align filesystem paths with absolute Python import names."""
    root = tmp_path / 'repo'; root.mkdir()
    graph = _graph(root, {'src/pkg/main.py': '''from fastapi import FastAPI
from pkg.api import register
def create_app():
    app=FastAPI()
    register(api=app)
''', 'src/pkg/api.py': '''def register(api):
    @api.get('/imported')
    def handle(): pass
'''})
    result = capture_python_entrypoints(graph, root, _rules(tmp_path))
    entry = result['metadata']['entrypointDiscovery']['entries'][0]
    assert entry['route'] == '/imported' and entry['handlerNodeIds']


def test_conflicting_entrypoint_mods_are_rejected(tmp_path):
    """Two library mods cannot silently select the same capture strategy by catalogue order."""
    from atlas.mod_store import ModError
    from atlas.mod_analysis import _validate_providers
    with pytest.raises(ModError, match='entrypoint'):
        _validate_providers({'projectRules': [], 'testFrameworks': [], 'entrypointProviders': [
            {'strategy': 'python-fastapi-v1'}, {'strategy': 'python-fastapi-v1'}]})
