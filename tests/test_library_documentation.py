"""Локальные зависимости, языковые fallback и отдельный явный capture библиотечных XML."""
import asyncio
import json
from pathlib import Path
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from atlas import agent_mcp
from atlas.library_service import LibraryService
from atlas.library_sources import LibraryDiscovery, below, xml_root
from atlas.reference_documents import ReferenceError
from atlas.server import create_app

EN = '<doc><assembly><name>Widget</name></assembly><members><member name="M:Acme.Widget.Run(System.Int32)"><summary>Runs the configured widget.</summary><param name="count">Number of runs.</param><returns>A result.</returns><remarks>Keep the same context.</remarks><exception cref="T:System.Exception">Failure.</exception></member><member name="M:Acme.Widget.Run(System.String)"><summary>String overload.</summary><inheritdoc cref="M:Other.Run"/></member></members></doc>'
RU = '<doc><assembly><name>Widget</name></assembly><members><member name="M:Acme.Widget.Run(System.Int32)"><summary>Выполняет запуск виджета.</summary><param name="count">Число запусков.</param></member></members></doc>'


@pytest.fixture
def libraries(tmp_path):
    """Две версии установлены, но compile assets выбирают только одну; RU неполный."""
    root = tmp_path / 'repo'; root.mkdir()
    (root / 'App.csproj').write_text('<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>')
    cache = tmp_path / 'packages'; base = cache / 'acme/1.2.3/lib/net8.0'; base.mkdir(parents=True)
    (base / 'Widget.xml').write_text(EN); (base / 'ru').mkdir(); (base / 'ru/Widget.xml').write_text(RU)
    other = cache / 'acme/2.0.0/lib/net8.0'; other.mkdir(parents=True); (other / 'Widget.xml').write_text(EN.replace('configured','wrong version'))
    assets = {'packageFolders':{str(cache) + '/':{}}, 'targets':{'net8.0':{'Acme/1.2.3':{'type':'package','compile':{'lib/net8.0/Widget.dll':{}}}}},
              'libraries':{'Acme/1.2.3':{'type':'package','path':'acme/1.2.3'}}, 'project':{'frameworks':{}}}
    (root / 'obj').mkdir(); (root / 'obj/project.assets.json').write_text(json.dumps(assets))
    app = create_app(tmp_path / 'state')
    store = app.state.store
    store.projects['one'] = {'id':'one','name':'One','path':str(root),'status':'new'}
    app.state.libraries.discovery = LibraryDiscovery([cache], [])
    return root, cache, app, assets


def wait_job(service, job):
    """Bounded polling verifies the real writer and atomic published catalog."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = service.job('one', job['id'])
        if current['status'] != 'running':
            return current
        time.sleep(.01)
    pytest.fail('Library capture did not finish')


def test_actual_dependency_version_and_language_discovery(libraries):
    root, cache, app, assets = libraries
    with TestClient(app, base_url='http://127.0.0.1') as client:
        result = client.get('/api/projects/one/library-documentation/sources').json()
        assert len(result['sources']) == 2
        assert {s['version'] for s in result['sources']} == {'1.2.3'}
        assert {s['locale'] for s in result['sources']} == {'en','ru'}
        assert result['sources'][0]['usedBy'] == [{'manifest':'App.csproj','target':'net8.0'}]
        assert not app.state.libraries.directory.exists()
        assert client.get('/api/projects/one/library-documentation').json()['capturedAt'] is None
        assert client.get('/api/projects/one/library-documentation/sources?manifest=../foreign.csproj').status_code == 400


def test_explicit_capture_search_fallback_refresh_and_scope(libraries):
    root, cache, app, assets = libraries
    with TestClient(app, base_url='http://127.0.0.1') as client:
        before = dict(app.state.store.projects)
        url = '/api/projects/one/library-documentation'
        response = client.post(url + '/capture', json={'manifest':'App.csproj'})
        assert response.status_code == 202
        job = wait_job(app.state.libraries, response.json()); assert job['status'] == 'completed', job
        catalog = client.get(url).json(); assert len(catalog['packs']) == 2
        ru = client.post(url + '/search', json={'query':'Acme.Widget.Run','locale':'ru','mode':'exact'}).json()
        assert len(ru['results']) == 2
        assert {h['locale'] for h in ru['results']} == {'ru','en'}
        assert any(h['localeFallback'] for h in ru['results'])
        integer = next(h for h in ru['results'] if 'Int32' in h['signature'])
        document = client.get(url + f"/{integer['packId']}/documents/{integer['documentId']}?limit=12").json()
        assert document['nextOffset'] == 12
        assert 'Выполняет' in document['text']
        assert document['library']['version'] == '1.2.3' and document['sourceKind'] == 'project-library-xml'
        assert document['library']['sourceHash']
        english = client.post(url + '/search', json={'query':'Acme.Widget.Run','mode':'exact'}).json()
        assert {h['locale'] for h in english['results']} == {'en'}
        string = next(h for h in english['results'] if 'String' in h['signature'])
        page = client.get(url + f"/{string['packId']}/documents/{string['documentId']}").json()
        assert 'Unresolved inheritdoc' in page['text']
        assert client.get('/api/projects/two/library-documentation').status_code == 404
        assert client.post(url + '/search', json={'query':'Run','packId':'reference-foreign'}).status_code == 404
        # A bad XML update must keep the usable English pack rather than silently delete it.
        (cache / 'acme/1.2.3/lib/net8.0/Widget.xml').write_text('<doc><members>')
        (cache / 'acme/1.2.3/lib/net8.0/ru/Widget.xml').write_text('<doc><members>')
        failed = wait_job(app.state.libraries, client.post(url + '/capture', json={}).json())
        assert failed['status'] == 'failed'
        assert client.get(url).json()['capturedAt'] == catalog['capturedAt']
        assert app.state.store.projects == before


def test_reference_packs_require_exact_version_and_local_references(libraries, tmp_path):
    root, cache, app, assets = libraries
    packs = tmp_path / 'packs'; directory = packs / 'Microsoft.NETCore.App.Ref/8.0.23/ref/net8.0'; directory.mkdir(parents=True)
    (directory / 'System.Runtime.xml').write_text(EN)
    newer = packs / 'Microsoft.NETCore.App.Ref/8.0.24/ref/net8.0'; newer.mkdir(parents=True); (newer / 'System.Runtime.xml').write_text(EN)
    assets['project']['frameworks'] = {'net8.0':{'downloadDependencies':[{'name':'Microsoft.NETCore.App.Ref','version':'[8.0.23, 8.0.23]'}]}}
    (root / 'obj/project.assets.json').write_text(json.dumps(assets))
    (root / 'vendor').mkdir(); (root / 'vendor/Widget.xml').write_text(EN)
    (root / 'App.csproj').write_text('<Project><ItemGroup><Reference Include="Widget"><HintPath>vendor/Widget.dll</HintPath></Reference></ItemGroup></Project>')
    app.state.libraries.discovery = LibraryDiscovery([cache], [packs])
    with TestClient(app, base_url='http://127.0.0.1'):
        inventory = app.state.libraries.discover('one')
        ref = [s for s in inventory['sources'] if s['kind']=='reference-pack']
        assert len(ref)==1 and ref[0]['version']=='8.0.23'
        assert any(s['kind']=='local-reference' and s['version']=='unknown' for s in inventory['sources'])
        (directory / 'System.Runtime.xml').unlink()
        assert not any(s['kind']=='reference-pack' for s in app.state.libraries.discover('one')['sources'])


def test_xml_utf16_entities_and_path_escape(tmp_path):
    assert xml_root(RU.encode('utf-16')).findtext('assembly/name') == 'Widget'
    with pytest.raises(ReferenceError, match='entities'):
        xml_root('<!DOCTYPE doc [<!ENTITY x "evil">]><doc/>'.encode('utf-16'))
    with pytest.raises(ReferenceError):
        below(tmp_path, '../escape.xml')
    outside = tmp_path.parent / 'outside'; outside.mkdir(exist_ok=True)
    (tmp_path / 'link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ReferenceError):
        below(tmp_path, 'link/evil.xml')


def test_scoped_mcp_library_tools_reject_other_project_before_http(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'projectId':'one','capturedAt':None,'packs':[],'warnings':[]})
    original=httpx.AsyncClient
    monkeypatch.setattr(agent_mcp.httpx,'AsyncClient',lambda **kw: original(**kw,transport=httpx.MockTransport(handler)))
    async def scenario():
        mcp=agent_mcp.create_mcp(8877,project_id='one')
        for tool,args in [('list_library_documentation',{}),('discover_library_sources',{}),('capture_library_documentation',{}),
                          ('get_library_documentation_job',{'jobId':'job'}),('search_library_documentation',{'query':'Run'}),
                          ('get_library_documentation',{'packId':'pack','documentId':'doc'})]:
            with pytest.raises(Exception):
                await mcp.call_tool(tool,{'projectId':'two',**args})
        assert not calls
        await mcp.call_tool('list_library_documentation',{})
        assert calls[-1].url.path == '/api/projects/one/library-documentation'
    asyncio.run(scenario())


def test_refresh_reuses_content_and_excludes_removed_versions(libraries):
    """Новая resolved version меняет current, одинаковый capture не плодит immutable корпусы."""
    root, cache, app, assets = libraries
    with TestClient(app, base_url='http://127.0.0.1'):
        service = app.state.libraries
        first = service._capture('one', '', lambda **values: None)
        second = service._capture('one', '', lambda **values: None)
        assert {p['id'] for p in first['packs']} == {p['id'] for p in second['packs']}
        assert len(list((service.folder('one')/'language-references').glob('reference-*'))) == 2
        assets['targets']['net8.0'] = {'Acme/2.0.0':{'type':'package','compile':{'lib/net8.0/Widget.dll':{}}}}
        assets['libraries'] = {'Acme/2.0.0':{'type':'package','path':'acme/2.0.0'}}
        (root/'obj/project.assets.json').write_text(json.dumps(assets))
        service._capture('one', '', lambda **values: None)
        hits = service.search('one', 'Acme.Widget.Run', locale='ru', mode='exact')['results']
        assert hits and {h['version'] for h in hits} == {'2.0.0'}
        assert all(h['localeFallback'] for h in hits)
        old = first['packs'][0]
        with pytest.raises(ReferenceError, match='current'):
            service.document('one', old['id'], 'missing', 0, 100)


def test_dependencies_changed_during_capture_keep_previous_catalog(libraries):
    """Fingerprint проверяется перед публикацией, а не только до начала writer."""
    root, cache, app, assets = libraries
    with TestClient(app, base_url='http://127.0.0.1'):
        service = app.state.libraries
        first = service._capture('one','',lambda **values:None)
        changed = False
        def change_inputs(**values):
            nonlocal changed
            if not changed:
                changed = True
                (root/'App.csproj').write_text('<Project><PropertyGroup><TargetFramework>net9.0</TargetFramework></PropertyGroup></Project>')
        with pytest.raises(ReferenceError,match='Dependencies changed'):
            service._capture('one','',change_inputs)
        assert service.catalog('one')['capturedAt']==first['capturedAt']
