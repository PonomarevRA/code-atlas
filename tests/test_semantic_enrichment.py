"""The application owns conditional enrichment; no agent, compiler install or project build is needed."""
from dataclasses import replace
import json
from pathlib import Path
import os

import pytest
from fastapi.testclient import TestClient

from atlas import server, store
from atlas.semantic_engine_settings import SemanticEngineSettings
from atlas.semantic_enrichment import SemanticEnrichment
from atlas.semantic_models import SemanticAvailability
from atlas.semantic_runtime import ProbeOutcome
from test_semantic_overlay import semantic_project


class MissingProvider:
    """An absent engine is observable without starting a process or modifying a source tree."""
    def __init__(self, identifier, contexts):
        self.provider_id, self.contexts = identifier, contexts

    def probe(self, context, cancel):
        """Capture the actual project cohort supplied by the application coordinator."""
        self.contexts.append(context)
        return ProbeOutcome(SemanticAvailability('missing', 'fixture-no-engine'))


def drain(service):
    """Wait behind already submitted preparation work; production HTTP never performs this wait."""
    service.executor.submit(lambda: None).result(timeout=5)


def test_active_map_use_is_nonblocking_and_negative_cache_is_daily(semantic_project):
    state, identifier, path, root, graph, _, _, _, _ = semantic_project
    now, contexts = [1_800_000_000.0], []
    service = SemanticEnrichment(state, clock=lambda: now[0], provider_factory=lambda declaration, paths:
        MissingProvider(declaration['modId'] + '/' + declaration['id'], contexts))
    state.on_map_used = service.use
    try:
        assert state.get_graph(identifier)['metadata']['semanticEngines']['runtimeSelectionKnown'] is False
        drain(service)
        assert contexts and all(context.workspace_root == str(root) for context in contexts)
        first = len(contexts)
        for _ in range(5):
            state.get_graph(identifier)
        drain(service)
        assert len(contexts) == first
        assert service.status(identifier, path.parent.name)['providers'][0]['availability']['status'] == 'missing'
        now[0] += 86400
        state.get_graph(identifier)
        drain(service)
        assert len(contexts) == first + 1
    finally:
        service.close()


def test_selected_binding_publishes_without_agent_and_off_cancels_projection(semantic_project):
    state, identifier, path, root, graph, evidence, _, _, _ = semantic_project
    for node in graph['nodes']:
        node['codeProjectId'] = 'python-project'
    store.write_json(path, graph)
    original = path.read_bytes()
    contexts, requests = [], []

    class Provider(MissingProvider):
        """Contract fixture returns pinned source evidence; it does not qualify a real engine."""
        def probe(self, context, cancel):
            self.contexts.append(context)
            return ProbeOutcome(SemanticAvailability('available', engine_fingerprint='b' * 64,
                                                     capabilities=('definition',)), (root / 'a.py',))

        def analyze(self, context, probe, request, cancel):
            """Exercise actual request preparation and publication rather than bypassing the coordinator."""
            requests.append(request)
            return replace(evidence, context=context)

    service = SemanticEnrichment(state, provider_factory=lambda declaration, paths:
        Provider(declaration['modId'] + '/' + declaration['id'], contexts))
    try:
        service.use(identifier, path.parent.name, ['run'])
        drain(service)
        data = service.status(identifier, path.parent.name)
        assert data['enrichment'] == 'applied', data
        assert requests and requests[0]['queries'][0]['label'] == 'work'
        assert requests[0]['scope']['qualified'] is True
        assert any(edge['target'] == 'work' for edge in state.get_graph(identifier)['edges'])
        assert path.read_bytes() == original
        item = next(item for item in state.mods.catalog()['mods'] if item['id'] == 'semantic-python')
        state.mods.enable(item['id'], item['fingerprint'], False)
        service.use(identifier, path.parent.name)
        drain(service)
        assert state.get_graph(identifier)['edges'] == []
        assert service.status(identifier, path.parent.name)['enrichment'] == 'baseline'
    finally:
        service.close()


def test_discovery_cohorts_are_isolated_and_settings_recheck(semantic_project):
    state, identifier, path, _, graph, _, _, _, _ = semantic_project
    for index, node in enumerate(graph['nodes']):
        node['codeProjectId'] = 'project-' + str(index)
    store.write_json(path, graph)
    contexts = []
    service = SemanticEnrichment(state, provider_factory=lambda declaration, paths:
        MissingProvider(declaration['modId'] + '/' + declaration['id'], contexts))
    try:
        service.use(identifier, path.parent.name)
        drain(service)
        assert len(contexts) == 2
        assert {tuple(context.source_hashes) for context in contexts} == {('a.py',), ('b.py',)}
        service.settings.save('semantic-python', 'jedi-local-v1', {'pythonPath': '/missing/python'})
        service.use(identifier, path.parent.name)
        drain(service)
        assert len(contexts) == 4
        assert len({context.config_fingerprint for context in contexts}) == 2
    finally:
        service.close()


def test_app_api_exposes_paths_and_fallback_without_mcp(semantic_project, tmp_path):
    state, identifier, path, _, _, _, _, _, _ = semantic_project
    store.write_json(state.registry, state.projects)
    app = server.create_app(state.data_dir)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        settings = client.get('/api/semantic-engines/settings').json()
        assert settings['agentRequired'] is False and settings['defaultMode'] == 'automatic'
        route = '/api/mods/semantic-python/semantic-engine-settings'
        saved = client.post(route, json={'paths': {'pythonPath': '/missing/python'}})
        assert saved.status_code == 200 and saved.json()['mode'] == 'explicit'
        assert client.post(route, json={'paths': {'command': 'evil'}}).status_code == 400
        assert client.post(route, json={'paths': {'pythonPath': 'python -c evil'}}).status_code == 400
        base = f'/api/projects/{identifier}/semantic-enrichment'
        assert client.get(base, params={'snapshotId': 'old'}).status_code == 409
        assert client.post(base, json={'snapshotId': path.parent.name, 'force': 'yes'}).status_code == 422
        result = client.post(base, json={'snapshotId': path.parent.name, 'nodeIds': ['run']})
        assert result.status_code == 200 and result.json()['runtimeSelectionKnown'] is False
        drain(app.state.semantic_enrichment)
        current = client.get(base, params={'snapshotId': path.parent.name}).json()
        assert current['providers'][0]['availability']['status'] == 'missing'
        assert client.post(route, json={'paths': {}}).json()['mode'] == 'automatic'


def test_invalid_settings_never_fall_back_or_discard_other_paths(tmp_path):
    settings = SemanticEngineSettings(tmp_path)
    settings.save('python', 'jedi-local-v1', {'pythonPath': '/python'})
    settings.save('ts', 'typescript-local-v1', {'enginePath': '/typescript/lib/typescript.js'})
    assert settings.read()['python']['pythonPath'] == '/python'
    settings.path.write_text('{invalid')
    with pytest.raises(ValueError):
        settings.save('ts', 'typescript-local-v1', {})
    assert settings.path.read_text() == '{invalid'


@pytest.mark.parametrize('change', ['source', 'configuration', 'off', 'snapshot'])
def test_late_results_never_publish_across_changed_context(semantic_project, change):
    state, identifier, path, root, graph, evidence, _, _, _ = semantic_project
    for node in graph['nodes']:
        node['codeProjectId'] = 'one-project'
    store.write_json(path, graph)
    engine = state.data_dir / 'fixture-engine'
    engine.write_text('fixture')

    class Provider(MissingProvider):
        """The fixture deliberately changes a publication fence during the analysis callback."""
        def probe(self, context, cancel):
            return ProbeOutcome(SemanticAvailability('available', engine_fingerprint='b' * 64), (engine,))

        def analyze(self, context, probe, request, cancel):
            """Return late but otherwise valid static facts after a real observable mutation."""
            if change == 'source':
                (root / 'b.py').write_text('def work():\n    return 2\n')
            elif change == 'configuration':
                (root / 'pyproject.toml').write_text('[project]\nname="changed"\n')
            elif change == 'off':
                item = next(item for item in state.mods.catalog()['mods'] if item['id'] == 'semantic-python')
                state.mods.enable(item['id'], item['fingerprint'], False)
            else:
                newer = path.parent.parent / 'newer' / path.name
                store.write_json(newer, graph)
                with state.lock:
                    state.projects[identifier]['_graph'] = str(newer)
            return replace(evidence, context=context)

    service = SemanticEnrichment(state, provider_factory=lambda declaration, paths:
        Provider(declaration['modId'] + '/' + declaration['id'], []))
    try:
        service.use(identifier, path.parent.name, ['run'])
        drain(service)
        assert not path.with_name('semantic-overlay.json').exists()
        assert state.get_graph(identifier)['edges'] == []
    finally:
        service.close()


@pytest.fixture
def publication_service(semantic_project):
    """A retained real runtime result lets tests change the last publication boundary deterministically."""
    state, identifier, path, root, graph, evidence, _, _, _ = semantic_project
    for node in graph['nodes']:
        node['codeProjectId'] = 'one-project'
    store.write_json(path, graph)
    engine = state.data_dir / 'fixture-engine'
    engine.write_text('fixture')
    config = root / 'pyproject.toml'
    config.write_text('[project]\nname="first"\n')

    class Provider(MissingProvider):
        """Return source-anchored evidence; this remains a contract fixture, not native qualification."""
        def probe(self, context, cancel):
            return ProbeOutcome(SemanticAvailability('available', engine_fingerprint='b' * 64), (engine,))

        def analyze(self, context, probe, request, cancel):
            return replace(evidence, context=context)

    service = SemanticEnrichment(state, provider_factory=lambda declaration, paths:
        Provider(declaration['modId'] + '/' + declaration['id'], []))
    try:
        yield service, state, identifier, path, root, engine, config
    finally:
        service.close()


@pytest.mark.parametrize('change', ['source', 'configuration', 'engine'])
def test_changes_after_overlay_construction_are_rejected(publication_service, monkeypatch, change):
    """The final fence checks actual bytes/assets after construction, even when mtimes are preserved."""
    from atlas import semantic_enrichment as module
    service, state, identifier, path, root, engine, config = publication_service
    original = module.make_overlay

    def construct_then_change(*args, **kwargs):
        """Inject the critic's exact late publication window, after the initial source verification."""
        overlay = original(*args, **kwargs)
        target = root / 'b.py' if change == 'source' else config if change == 'configuration' else engine
        stamp = target.stat()
        if change == 'source':
            target.write_text('def work():\n    return 2\n')
        elif change == 'configuration':
            target.write_text('[project]\nname="other"\n')
        else:
            target.write_text('changed-engine')
        os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        return overlay

    monkeypatch.setattr(module, 'make_overlay', construct_then_change)
    service.use(identifier, path.parent.name, ['run'])
    drain(service)
    assert not path.with_name('semantic-overlay.json').exists()
    assert state.get_graph(identifier)['edges'] == []


def test_evicted_state_cannot_reuse_running_publication_epoch(publication_service, monkeypatch):
    """Returning to an evicted map cannot resurrect its older in-flight generation."""
    from atlas import semantic_enrichment as module
    service, state, identifier, path, _, _, _ = publication_service
    state.projects['other'] = {**state.projects[identifier], 'id': 'other'}
    original, write = module.make_overlay, module.write_overlay
    obsolete, published = [], []

    def evict_before_publish(*args, **kwargs):
        """Force LRU replacement during the old result's final construction window."""
        overlay = original(*args, **kwargs)
        if not obsolete:
            obsolete.append(overlay)
            service.use('other', path.parent.name)
            service.use(identifier, path.parent.name, ['work'])
        return overlay

    def observe_write(snapshot, overlay):
        """Observe object identity so subsequent legitimate work cannot race an absence assertion."""
        published.append(overlay)
        write(snapshot, overlay)

    monkeypatch.setattr(module, 'MAX_STATES', 1)
    monkeypatch.setattr(module, 'make_overlay', evict_before_publish)
    monkeypatch.setattr(module, 'write_overlay', observe_write)
    service.use(identifier, path.parent.name, ['run'])
    drain(service)
    assert obsolete and all(item is not obsolete[0] for item in published)


def test_final_source_fence_keeps_raw_bom_identity(semantic_project):
    """Identical decoded text cannot hide a changed original byte hash."""
    state, _, _, root, _, _, _, _, _ = semantic_project
    content = b'def run(): pass\n'
    raw = b'\xef\xbb\xbf' + content
    (root / 'a.py').write_bytes(raw)
    from hashlib import sha256
    service = SemanticEnrichment(state)
    try:
        assert service._sources_current(root, {'a.py': content}, {'a.py': sha256(raw).hexdigest()})
        (root / 'a.py').write_bytes(content)
        assert not service._sources_current(root, {'a.py': content}, {'a.py': sha256(raw).hexdigest()})
    finally:
        service.close()


def test_demoted_copy_keeps_original_engine_provenance(publication_service):
    """Restricting confidence does not fabricate or lose the runtime's immutable provenance token."""
    service, state, identifier, path, _, _, _ = publication_service
    graph = json.loads(path.read_text())
    for node in graph['nodes']:
        node.pop('codeProjectId', None)
    store.write_json(path, graph)
    service.use(identifier, path.parent.name, ['run', 'work'])
    drain(service)
    delta = json.loads(path.with_name('semantic-overlay.json').read_text())
    assert delta['facts']['run'][0]['confidence'] == 'candidate'
    assert delta['edges'] == []
    assert service.status(identifier, path.parent.name)['enrichment'] == 'applied'
