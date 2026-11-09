"""Mod activation, worker contracts and cache revisions preserve the original source graph."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest
from fastapi.testclient import TestClient
from atlas.mod_store import ModStore, ModError
from atlas.mod_grouping import provider_id, resolve, run_grouping
from atlas.grouping import enrich_graph
from atlas.server import create_app
from atlas import store
from test_grouping_cache import legacy_store


def package(root, identifier='example', **overrides):
    """Create a real user package, initially untrusted/disabled, in an isolated application folder."""
    directory=root/identifier;directory.mkdir(parents=True,exist_ok=True)
    manifest={'apiVersion':1,'id':identifier,'name':'Example','version':'1.0', 'grouping':[{'id':'kinds','label':'Kinds','mode':'field'}],**overrides}
    (directory/'mod.json').write_text(json.dumps(manifest))
    return directory


def active(service, identifier='example'):
    """Activate exactly the discovered content revision as a user would from the catalog."""
    item=next(item for item in service.catalog()['mods'] if item['id']==identifier)
    service.enable(identifier,item['fingerprint'],True)
    return next(item for item in service.catalog(internal=True)['mods'] if item['id']==identifier)


def test_discovery_never_executes_or_autoloads_repository_mods(tmp_path):
    user=tmp_path/'state/mods'; package(user,frontend='mod.js')
    (user/'example/mod.js').write_text('throw new Error("must not run during discovery");')
    service=ModStore(tmp_path/'state',tmp_path/'empty-bundled')
    catalog=service.catalog();assert catalog['mods'][0]['status']=='disabled'
    assert not service.state_path.exists()
    assert not catalog['mods'][0]['enabled']
    package(tmp_path/'project/mods',identifier='project-mod')
    assert len(service.catalog()['mods'])==1


def test_changed_content_cannot_reuse_active_revision_or_cache_id(tmp_path):
    directory=package(tmp_path/'state/mods',frontend='mod.js')
    (directory/'mod.js').write_text('export function activate() {}')
    service=ModStore(tmp_path/'state',tmp_path/'none')
    first=active(service); original_id=provider_id(first,first['manifest']['grouping'][0])
    (directory/'mod.js').write_text('export function activate() { /* updated */ }')
    changed=service.catalog()['mods'][0]
    assert changed['status']=='changed' and not changed['enabled']
    with pytest.raises(ModError): service.active('example',first['fingerprint'])
    with pytest.raises(ModError): service.enable('example',first['fingerprint'],True)
    with pytest.raises(ModError): resolve(service,original_id)
    second=active(service);assert provider_id(second,second['manifest']['grouping'][0])!=original_id


@pytest.mark.parametrize('overrides',[{'apiVersion':2},{'frontend':'../outside.js'},{'theme':{'fill':'javascript:bad'}},{'id':'different-folder'}])
def test_bad_manifests_are_independent_errors(tmp_path,overrides):
    package(tmp_path/'state/mods',**overrides)
    package(tmp_path/'state/mods','good')
    service=ModStore(tmp_path/'state',tmp_path/'none')
    statuses={item['id']:item['status'] for item in service.catalog()['mods']}
    assert statuses['example']=='error' and statuses['good']=='disabled'


def test_symlink_assets_cannot_reach_outside_package(tmp_path):
    directory=package(tmp_path/'state/mods',frontend='mod.js')
    outside=tmp_path/'outside.js';outside.write_text('secret')
    (directory/'mod.js').symlink_to(outside)
    assert ModStore(tmp_path/'state',tmp_path/'none').catalog()['mods'][0]['status']=='error'


def test_frozen_host_resource_link_discovers_bundled_workflows(tmp_path, monkeypatch):
    """macOS bundle resource indirection is host-owned; package symlink checks remain strict."""
    from atlas import mod_store
    resources = tmp_path / 'Resources'
    package(resources / 'mods', workflows=[{'id':'explore','label':'Explore','kind':'research'}])
    framework = tmp_path / 'Frameworks'
    framework.mkdir()
    (framework / 'mods').symlink_to(resources / 'mods', target_is_directory=True)
    monkeypatch.setattr(mod_store, 'resource_root', lambda: framework)
    service = ModStore(tmp_path / 'state')
    item = service.catalog()['mods'][0]
    assert item['id'] == 'example' and item['status'] == 'disabled'
    assert item['manifest']['workflows'][0]['id'] == 'explore'


def test_dependency_disable_and_recovery_do_not_remove_packages(tmp_path):
    package(tmp_path/'state/mods','foundation');package(tmp_path/'state/mods','dependent',requires=['foundation'])
    service=ModStore(tmp_path/'state',tmp_path/'none')
    with pytest.raises(ModError): active(service,'dependent')
    active(service,'foundation');active(service,'dependent')
    service.enable('foundation',None,False)
    assert next(item for item in service.catalog()['mods'] if item['id']=='dependent')['status']=='missing-dependency'
    service.disable_all();assert all(not item['enabled'] for item in service.catalog()['mods'])
    assert (tmp_path/'state/mods/dependent/mod.json').exists()


def test_grouping_preserves_exact_fact_ids_relations_and_input(tmp_path):
    package(tmp_path/'state/mods')
    service=ModStore(tmp_path/'state',tmp_path/'none');item=active(service)
    grouping=item['manifest']['grouping'][0]
    graph={'nodes':[{'id':'a','kind':'method','file':'a.py'},{'id':'b','kind':'field','file':'a.py'},{'id':'c','kind':'method','file':'b.py'}],
           'edges':[{'source':'a','target':'b','relation':'reads'},{'source':'a','target':'c','relation':'calls'}]}
    before=deepcopy(graph)
    result=enrich_graph(graph,provider_id(item,grouping),provider_override=resolve(service,provider_id(item,grouping)))
    assert graph==before and result['edges']==before['edges']
    assert {n['id'] for n in result['nodes']}=={'a','b','c'}
    membership={n['id']:n['community'] for n in result['nodes']}
    assert membership['a']==membership['c']!=membership['b']


def test_isolates_follow_mod_grouping_even_without_any_edges(tmp_path):
    package(tmp_path/'state/mods');service=ModStore(tmp_path/'state',tmp_path/'none');item=active(service)
    identifier=provider_id(item,item['manifest']['grouping'][0])
    graph={'nodes':[{'id':'a','kind':'method'},{'id':'b','kind':'method'}],'edges':[]}
    grouped=enrich_graph(graph,identifier,provider_override=resolve(service,identifier))
    assert len(grouped['communities'])==1


def test_python_worker_reads_snapshot_and_never_writes_mod_directory(tmp_path):
    directory=package(tmp_path/'state/mods',grouping=[{'id':'python','label':'Python','mode':'python','worker':'group.py'}])
    (directory/'group.py').write_text('def group(graph):\n    return [[node["id"] for node in graph["nodes"]]]\n')
    service=ModStore(tmp_path/'state',tmp_path/'none');item=active(service)
    graph={'nodes':[{'id':'a','kind':'method'},{'id':'b','kind':'method'}],'edges':[]}
    identifier=provider_id(item,item['manifest']['grouping'][0])
    grouped=enrich_graph(graph,identifier,provider_override=resolve(service,identifier))
    assert len(grouped['communities'])==1
    assert service.catalog()['mods'][0]['status']=='enabled'
    assert not (directory/'__pycache__').exists()


@pytest.mark.parametrize('output',['[["a"]]','[["a","a"],["b"]]','[["a","outside"],["b"]]'])
def test_bad_worker_partitions_do_not_publish_or_mutate_graph(tmp_path,output):
    directory=package(tmp_path/'state/mods',grouping=[{'id':'python','label':'Python','mode':'python','worker':'group.py'}])
    (directory/'group.py').write_text('def group(graph):\n    return '+output+'\n')
    service=ModStore(tmp_path/'state',tmp_path/'none');item=active(service)
    graph={'nodes':[{'id':'a'},{'id':'b'}],'edges':[]}; before=deepcopy(graph)
    with pytest.raises(ModError):enrich_graph(graph,provider_id(item,item['manifest']['grouping'][0]),provider_override=resolve(service,provider_id(item,item['manifest']['grouping'][0])))
    assert graph==before


def test_mod_api_uses_pinned_active_assets_and_source_unchanged(tmp_path):
    state,project,graph_path,_=legacy_store(tmp_path);state.close()
    directory=package(tmp_path/'state/mods',frontend='mod.js');(directory/'mod.js').write_text('export function activate() {}')
    (directory/'helper.js').write_text('export const value=1;')
    before=graph_path.read_bytes()
    with TestClient(create_app(tmp_path/'state'),base_url='http://127.0.0.1') as client:
        item=next(m for m in client.get('/api/mods').json()['mods'] if m['id']=='example')
        url=f"/api/mods/example/assets/{item['fingerprint']}/mod.js"
        assert client.get(url).status_code==400
        assert client.post('/api/mods/example/activation',json={'enabled':True,'fingerprint':item['fingerprint']}).status_code==200
        assert client.get(url).status_code==200
        assert client.get(url.replace('mod.js','helper.js')).status_code==200
        assert client.get(url.replace('mod.js','%2e%2e/outside.js')).status_code in {400,404}
        algorithm=next(row['id'] for row in client.get('/api/grouping-algorithms').json()['algorithms'] if row.get('modId')=='example')
        assert client.get(f'/api/projects/{project}/graph',params={'algorithm':algorithm}).status_code==200
        assert graph_path.read_bytes()==before
        (directory/'helper.js').write_text('export const value=2;')
        assert client.get(url).status_code==400
        assert client.post('/api/mods/disable-all',json={}).status_code==200
        assert client.get(f'/api/projects/{project}/graph',params={'algorithm':algorithm}).status_code==400


def test_dependency_cycles_cannot_activate_even_from_corrupt_state(tmp_path):
    """Cycle detection protects host loading order and rejects persisted activation shortcuts."""
    package(tmp_path/'state/mods','first',requires=['second'])
    package(tmp_path/'state/mods','second',requires=['first'])
    service=ModStore(tmp_path/'state',tmp_path/'none')
    catalog=service.catalog()
    assert all(item['status']=='error' for item in catalog['mods'])
    service.state_path.write_text(json.dumps({item['id']:item['fingerprint'] for item in catalog['mods']}))
    assert all(not item['enabled'] for item in service.catalog()['mods'])
    with pytest.raises(ModError): active(service,'first')


def test_api_version_requires_real_integer(tmp_path):
    """Python's bool/int equality must not enable an unintended executable contract."""
    package(tmp_path/'state/mods',apiVersion=True)
    assert ModStore(tmp_path/'state',tmp_path/'none').catalog()['mods'][0]['status']=='error'
