"""
Architecture visits reuse source-bound infrastructure without inventing runtime bindings.
"""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError

from atlas.architecture_enrichment import enrich_architecture
from atlas.architecture_http import http_facts
from atlas.api_models import FederationInput
from atlas.execution_path import ExecutionPathError
from atlas.system_federation import build_federated_architecture


@pytest.fixture
def repository(tmp_path):
    """
    Create an actual C# source corpus with SQL, messaging and HTTP operations.

    Args:
        tmp_path: Isolated temporary directory; no user project or production data is modified.
    """
    (tmp_path / 'App.csproj').write_text('<Project/>')
    (tmp_path / 'Code.cs').write_text('''using Confluent.Kafka;
using RabbitMQ;
using Dapper;
using System.Net.Http;
class C {
 void Produce(){ producer.ProduceAsync("assets", message); }
 void Consume(){ channel.BasicConsume(queue:"updates"); }
 void Query(){ connection.Query("SELECT id FROM assets WHERE secret='SQL_SECRET'"); }
 void Http(){ httpClient.GetAsync("https://user:HTTP_SECRET@api.example/private?token=QUERY_SECRET"); }
}''')
    (tmp_path / 'appsettings.json').write_text(json.dumps({'ConnectionStrings': {
        'One': 'Host=one.example;Database=first;Password=CONFIG_SECRET',
        'Two': 'Host=two.example;Database=second;Password=OTHER_SECRET'}}))
    nodes = [{'id': 'manifest', 'label': 'App.csproj', 'file': 'App.csproj'}]
    for index, name in enumerate(['Produce', 'Consume', 'Query', 'Http'], 6):
        nodes.append({'id': name, 'label': name, 'kind': 'method', 'file': 'Code.cs', 'line': index})
    return {'id': 'repo', 'name': 'Fixture', 'path': tmp_path, 'snapshotId': 'snap',
            'graph': {'nodes': nodes, 'edges': [], 'metadata': {'snapshotId': 'snap'}}}


def view(repository):
    """
    Build the existing complete federation core for the fixture repository.

    Args:
        repository: Isolated primary repository fixture with source root, raw graph and snapshot identity.
    """
    return build_federated_architecture([repository], repository['snapshotId'])


def test_actual_visits_are_idempotent_scoped_directed_and_secret_free(repository):
    """
    Actual AST evidence adds one edge per fact, keeping DB alternatives separate from table identity.

    Args:
        repository: Isolated primary repository fixture with source root, raw graph and snapshot identity.
    """
    baseline = view(repository)
    captured = deepcopy(repository['graph'])
    before = deepcopy(baseline)
    result = enrich_architecture(baseline, repository, ['Produce', 'Consume', 'Query', 'Http', 'Produce'])
    assert result == enrich_architecture(baseline, repository, ['Http', 'Query', 'Consume', 'Produce'])
    assert baseline == before and repository['graph'] == captured
    assert result['architectureVersion'] == 2
    resources = {n['resourceKind']: n for n in result['nodes'] if n.get('resourceKind')}
    assert set(resources) == {'topic', 'queue', 'table', 'endpoint'}
    assert resources['table']['label'] == 'assets'
    assert len(resources['table']['configurationCandidates']) == 2
    assert resources['table']['configurationBindingKnown'] is False
    assert resources['endpoint']['host'] == 'api.example'
    new_edges = [e for e in result['edges'] if e.get('factId')]
    assert len(new_edges) == 4
    consume = next(e for e in new_edges if e['operation'] == 'consumes')
    assert consume['source'] == resources['queue']['id'] and consume['target'] == resources['queue']['ownerId']
    assert all(e['evidence'][0]['sourceHash'] and not e['runtimeSelectionKnown'] for e in new_edges)
    encoded = json.dumps(result)
    for secret in ['SQL_SECRET', 'HTTP_SECRET', 'QUERY_SECRET', 'CONFIG_SECRET', 'OTHER_SECRET', '/private']:
        assert secret not in encoded
    assert not any(e['source'] == resources['table']['id'] for e in new_edges)


def test_empty_visits_keep_exact_federated_identity(repository):
    """
    Empty POST retains the exact core graph IDs used by later visits.

    Args:
        repository: Isolated primary repository fixture with source root, raw graph and snapshot identity.
    """
    baseline = view(repository)
    result = enrich_architecture(baseline, repository, [])
    assert result['nodes'] == baseline['nodes'] and result['edges'] == baseline['edges']
    assert result['exploration']['addedNodes'] == result['exploration']['addedEdges'] == 0


def test_invalid_visits_fail_before_any_source_index(repository):
    """
    Architecture IDs and unrelated repository IDs cannot become raw source requests.

    Args:
        repository: Isolated primary repository fixture with source root, raw graph and snapshot identity.
    """
    def forbidden(*args):
        """
        Translate the fixture operation without changing its controlled inputs.

        Args:
            args: Original trusted builder positional arguments forwarded without alteration.
        """
        raise AssertionError('Invalid visits must be checked before source reads')
    with pytest.raises(ExecutionPathError) as error:
        enrich_architecture(view(repository), repository, ['system-not-raw'], index_builder=forbidden)
    assert error.value.status == 404
    with pytest.raises(ValidationError):
        FederationInput(snapshotId='s', visitedNodeIds=['x'] * 65)


@pytest.mark.parametrize('extension,text,expected', [
    ('.py', 'import requests\ndef f():\n requests.get("https://u:p@host.example/x?token=p")\n # requests.get("https://comment.example")\n s="requests.get(https://string.example)"', ['host.example']),
    ('.js', 'function f(){ fetch("https://api.example/path?secret=x"); get("https://no.example"); /*fetch("https://comment.example")*/ }', ['api.example']),
    ('.ts', 'function f(){ fetch(dynamic); fetch("/relative"); fetch(`https://${host}/x`); }', []),
])
def test_http_ast_ignores_comments_and_nonliteral_destinations(extension, text, expected):
    """
    Only known call AST nodes with ordinary HTTP string literals yield host-only candidates.

    Args:
        extension: Source file extension selecting the grammar under test.
        text: Fixture program text containing real calls and negative syntax cases.
        expected: Expected public hostnames after source parsing and URL redaction.
    """
    source = {'file': 'a' + extension, 'text': text, 'sourceByteHash': hashlib.sha256(text.encode()).hexdigest()}
    facts = http_facts(source)
    assert [f['host'] for f in facts] == expected
    assert all(f['status'] == 'candidate' and f['runtimeSelectionKnown'] is False for f in facts)


def test_source_change_in_configuration_rejects_publication(repository):
    """
    A configuration edit during declaration analysis invalidates all configuration-derived facts.

    Args:
        repository: Isolated primary repository fixture with source root, raw graph and snapshot identity.
    """
    from atlas.execution_path_source import SourceAnalysis
    class MutatingAnalysis(SourceAnalysis):
        def describe(self, node):
            """
            Translate the fixture operation without changing its controlled inputs.

            Args:
                node: Validated raw declaration passed to the real source parser.
            """
            result = super().describe(node)
            (repository['path'] / 'appsettings.json').write_text('{}')
            return result
    with pytest.raises(ExecutionPathError) as error:
        enrich_architecture(view(repository), repository, ['Produce'], analysis_factory=MutatingAnalysis)
    assert error.value.status == 409


def test_resource_limits_are_explicit_without_partial_hidden_edges(repository, monkeypatch):
    """
    A hard resource cap refuses extra resources and marks exploration incomplete.

    Args:
        repository: Isolated primary repository fixture with source root, raw graph and snapshot identity.
        monkeypatch: Scoped fixture used to alter a limit or publication race for this test.
    """
    import atlas.architecture_enrichment as module
    monkeypatch.setattr(module, 'MAX_RESOURCES', 1)
    result = enrich_architecture(view(repository), repository, ['Produce', 'Consume'])
    assert result['exploration']['limited'] is True
    assert result['exploration']['coverage']['complete'] is False
    resources = [n for n in result['nodes'] if n.get('resourceKind')]
    assert len(resources) == 1
    ids = {n['id'] for n in result['nodes']}
    assert all(e['source'] in ids and e['target'] in ids for e in result['edges'])


def test_orphan_file_uses_explicit_unknown_source_owner(tmp_path):
    """
    A Python file without a project manifest is never bound to an unrelated root project.

    Args:
        tmp_path: Isolated temporary directory; no user project or production data is modified.
    """
    (tmp_path / 'f.py').write_text('import requests\ndef f():\n requests.get("https://api.example")\n')
    repository = {'id': 'r', 'name': 'R', 'path': tmp_path, 'snapshotId': 's',
        'graph': {'nodes': [{'id': 'f', 'file': 'f.py', 'label': 'f', 'kind': 'function', 'line': 2}], 'edges': []}}
    result = enrich_architecture(view(repository), repository, ['f'])
    resource = next(n for n in result['nodes'] if n.get('resourceKind') == 'endpoint')
    owner = next(n for n in result['nodes'] if n['id'] == resource['ownerId'])
    assert owner['kind'] == 'code-scope' and owner['ownershipKnown'] is False


def test_unknown_messaging_binding_remains_owner_local(repository):
    """
    A nonliteral destination survives as unknown; configurations never become selected endpoints.

    Args:
        repository: Isolated primary repository fixture with source root, raw graph and snapshot identity.
    """
    file = repository['path'] / 'Code.cs'
    file.write_text(file.read_text().replace('"assets", message', 'dynamicTopic, message'))
    result = enrich_architecture(view(repository), repository, ['Produce'])
    resource = next(n for n in result['nodes'] if n.get('resourceKind') == 'topic')
    assert resource['logicalName'] is None and resource['status'] == 'unknown'
    assert resource['identityScope'] == 'owner-resource'
    assert resource['configurationBindingKnown'] is False and resource['runtimeSelectionKnown'] is False


def test_repeated_calls_keep_distinct_callsite_facts(repository):
    """
    Two callsites to one topic retain multiplicity; revisiting a declaration cannot duplicate either.

    Args:
        repository: Isolated primary repository fixture with source root, raw graph and snapshot identity.
    """
    file = repository['path'] / 'Code.cs'
    file.write_text(file.read_text().replace('producer.ProduceAsync("assets", message);',
        'producer.ProduceAsync("assets", message); producer.ProduceAsync("assets", other);'))
    result = enrich_architecture(view(repository), repository, ['Produce', 'Produce'])
    topics = [n for n in result['nodes'] if n.get('resourceKind') == 'topic']
    assert len(topics) == 1
    edges = [e for e in result['edges'] if e.get('operation') == 'publishes']
    assert len(edges) == 2 and len({e['factId'] for e in edges}) == 2


def test_unavailable_declaration_reports_coverage(repository):
    """
    A missing source declaration does not claim successful enrichment.

    Args:
        repository: Isolated primary repository fixture with source root, raw graph and snapshot identity.
    """
    (repository['path'] / 'Code.cs').unlink()
    result = enrich_architecture(view(repository), repository, ['Produce'])
    assert result['exploration']['rejectedVisits']
    assert result['exploration']['coverage']['complete'] is False
    assert result['exploration']['addedEdges'] == 0


def test_edge_budget_refuses_whole_fact(repository, monkeypatch):
    """
    The edge cap cannot leave a newly accepted resource without an accepted fact.

    Args:
        repository: Isolated primary repository fixture with source root, raw graph and snapshot identity.
        monkeypatch: Scoped fixture used to alter a limit or publication race for this test.
    """
    import atlas.architecture_enrichment as module
    monkeypatch.setattr(module, 'MAX_EDGES', 0)
    result = enrich_architecture(view(repository), repository, ['Produce'])
    assert result['exploration']['limited'] and result['exploration']['addedEdges'] == 0
    assert not any(n.get('resourceKind') for n in result['nodes'])


def test_http_comment_namespace_is_not_import_evidence():
    """
    A C# comment containing a namespace cannot qualify a custom GetAsync method as HTTP.
    """
    text = '// using System.Net.Http;\nclass C{void F(){client.GetAsync("https://not-http.example");}}'
    assert not http_facts({'file': 'a.cs', 'text': text, 'sourceByteHash': 'h'})


def test_post_contract_validates_visits_and_fences_federation(repository, monkeypatch):
    """
    Actual route parses bounded raw visits, preserves empty core IDs and checks final snapshot race.

    Args:
        repository: Isolated primary repository fixture with source root, raw graph and snapshot identity.
        monkeypatch: Scoped fixture used to alter a limit or publication race for this test.
    """
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from atlas.graph_analysis_api import register_graph_analysis_routes
    from atlas.store import StoreError
    import atlas.graph_analysis_api as api
    class Store:
        """
        Minimal trusted federation store with a controlled final snapshot fence.
        """
        changed = False
        checked = None
        def federation_snapshots(self, project_id, snapshot_id, related_ids):
            """
            Implement the controlled test-store contract.

            Args:
                project_id: Primary repository identity supplied by the route.
                snapshot_id: Requested pinned primary snapshot; the stub rejects stale values.
                related_ids: Explicit related repository selection from the request.
            """
            if snapshot_id != repository['snapshotId']:
                raise StoreError('stale', 409)
            return [repository]
        def assert_snapshots(self, snapshots):
            """
            Implement the controlled test-store contract.

            Args:
                snapshots: Pinned repository-to-snapshot identities checked immediately before publication.
            """
            self.checked = snapshots
            if self.changed:
                raise StoreError('changed', 409)
    state, app = Store(), FastAPI()
    register_graph_analysis_routes(app, state)
    @app.exception_handler(StoreError)
    def error_handler(_, error):
        """
        Translate the fixture operation without changing its controlled inputs.

        Args:
            _: Unused synthetic HTTP request context.
            error: Expected StoreError raised by the route fixture.
        """
        return JSONResponse({'error': str(error)}, status_code=error.status)
    with TestClient(app) as client:
        path = '/api/projects/repo/system-architecture'
        base = client.post(path, json={'snapshotId': 'snap', 'visitedNodeIds': []})
        assert base.status_code == 200
        full = client.post(path, json={'snapshotId': 'snap', 'visitedNodeIds': ['Produce']})
        assert full.status_code == 200 and full.json()['exploration']['addedEdges'] == 1
        assert {n['id'] for n in base.json()['nodes']} <= {n['id'] for n in full.json()['nodes']}
        assert state.checked == {'repo': 'snap'}
        for visits in [['Produce'] * 65, [''], [123], ['x' * 4097]]:
            assert client.post(path, json={'snapshotId': 'snap', 'visitedNodeIds': visits}).status_code == 422
        assert client.post(path, json={'snapshotId': 'snap', 'visitedNodeIds': ['architecture-id']}).status_code == 404
        assert client.post(path, json={'snapshotId': 'stale', 'visitedNodeIds': []}).status_code == 409
        original = api.enrich_architecture
        def changing(*args, **kwargs):
            """
            Translate the fixture operation without changing its controlled inputs.

            Args:
                args: Original trusted builder positional arguments forwarded without alteration.
                kwargs: Original trusted builder keyword arguments forwarded without alteration.
            """
            result = original(*args, **kwargs)
            state.changed = True
            return result
        monkeypatch.setattr(api, 'enrich_architecture', changing)
        assert client.post(path, json={'snapshotId': 'snap', 'visitedNodeIds': []}).status_code == 409


@pytest.mark.parametrize('schemes', [('http', 'https'), ('https', 'http')])
def test_http_schemes_are_distinct_in_actual_ast_projection(tmp_path, schemes):
    """
    HTTP and HTTPS on one host never lose protocol identity through first-fact wins.

    Args:
        tmp_path: Isolated temporary directory; no user project or production data is modified.
        schemes: HTTP/HTTPS declaration order being compared by the actual AST recognizer.
    """
    calls = '\n'.join(f' requests.get("{scheme}://api.example")' for scheme in schemes)
    (tmp_path / 'f.py').write_text('import requests\ndef f():\n' + calls + '\n')
    repository = {'id':'r','name':'R','path':tmp_path,'snapshotId':'s',
        'graph':{'nodes':[{'id':'f','file':'f.py','label':'f','kind':'function','line':2}],'edges':[]}}
    result = enrich_architecture(view(repository), repository, ['f'])
    resources = [n for n in result['nodes'] if n.get('resourceKind') == 'endpoint']
    assert {n['scheme'] for n in resources} == {'http','https'}
    assert len({n['id'] for n in resources}) == 2
    edges = [e for e in result['edges'] if e.get('factId')]
    assert {e['target'] for e in edges} == {n['id'] for n in resources}
