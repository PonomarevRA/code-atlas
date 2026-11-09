"""Настройка Cursor/MCP только выдаёт конфигурацию и локальные файлы загрузки."""
import io
import json
from pathlib import Path
import sys
import zipfile
from fastapi.testclient import TestClient
from atlas import server
from test_grouping_cache import legacy_store


def test_agent_setup_selected_and_general_config_are_current_and_readonly(tmp_path):
    """JSON использует реальный порт/исполняемый файл и не устанавливает ничего в проект."""
    state,project_id,_,_=legacy_store(tmp_path)
    state.close()
    before=set(tmp_path.rglob('*'))
    with TestClient(server.create_app(tmp_path/'state'),base_url='http://127.0.0.1:8877') as client:
        general=client.get('/api/agent-setup')
        assert general.status_code==200,general.text
        assert general.json()['scope']=='all-projects' and general.json()['project'] is None
        response=client.get('/api/agent-setup',params={'projectId':project_id})
        assert response.status_code==200,response.text
        data=response.json()
        assert data['scope']=='project' and data['project']['id']==project_id
        assert data['project']['snapshotId']=='first'
        assert data['serverUrl']=='http://127.0.0.1:8877'
        config=next(iter(data['mcpConfig']['mcpServers'].values()))
        assert Path(config['command']).is_absolute() and Path(config['command']).is_file()
        assert Path(config['command']).resolve()==Path(sys.executable).resolve()
        assert config['args'][config['args'].index('--port')+1]=='8877'
        assert config['args'][config['args'].index('--project-id')+1]==project_id
        general_config=next(iter(general.json()['mcpConfig']['mcpServers'].values()))
        assert '--project-id' not in general_config['args']
        download=client.get(data['downloads']['config'])
        assert download.status_code==200
        assert json.loads(download.content)==data['mcpConfig']
        assert 'attachment' in download.headers['content-disposition']
        assert client.get('/api/agent-setup?projectId=unknown').status_code==404
    assert not (tmp_path/'.cursor').exists()
    assert not (tmp_path/'mcp.json').exists()
    assert not [path for path in set(tmp_path.rglob('*'))-before if path.name=='SKILL.md']


def test_agent_setup_skill_zip_is_exact_documentation_artifact(tmp_path):
    """ZIP содержит ровно skill; Markdown доступен без выбора и без исходников репозитория."""
    with TestClient(server.create_app(tmp_path),base_url='http://127.0.0.1:8765') as client:
        text=client.get('/api/agent-setup/skill')
        archive=client.get('/api/agent-setup/skill.zip')
        assert text.status_code==200 and archive.status_code==200
        with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
            assert zipped.namelist()==['code-atlas/SKILL.md', 'code-atlas/references/code-health.md',
                                      'code-atlas/references/browser-sessions.md', 'code-atlas/references/checkout-sessions.md',
                                      'code-atlas/references/client-installation.md', 'code-atlas/agents/openai.yaml']
            checkout_reference = zipped.read('code-atlas/references/checkout-sessions.md')
            assert b'ensure_checkout_map' in checkout_reference and b'finish_checkout_map' in checkout_reference
            health_reference = zipped.read('code-atlas/references/code-health.md')
            from atlas.agent_setup import ROOT
            assert health_reference == (ROOT / 'skills/code-atlas/references/code-health.md').read_bytes()
            assert health_reference != zipped.read('code-atlas/SKILL.md')
            assert b'get_code_health_record' in health_reference
            assert zipped.read('code-atlas/SKILL.md')==text.content
        markdown=text.text
        assert markdown.startswith('---')
        for tool in ['list_projects','search_nodes','describe_node','create_view','get_view']:
            assert tool in markdown
        assert 'snapshotId' in markdown
        assert 'excludeNodeIds' in markdown
        assert 'nodeIds' in markdown


def test_agent_setup_unbuilt_project_and_missing_skill_are_explicit(tmp_path,monkeypatch):
    """Подключение до индексации возможно, но отсутствие поставляемого skill не маскируется."""
    app=server.create_app(tmp_path)
    app.state.store.projects['new']={'id':'new','name':'New','path':str(tmp_path),'status':'pending'}
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        response=client.get('/api/agent-setup?projectId=new')
        assert response.status_code==200
        assert response.json()['project']['snapshotId'] is None
        monkeypatch.setattr('atlas.agent_setup.SKILL_PATH',tmp_path/'missing/SKILL.md')
        assert client.get('/api/agent-setup/skill').status_code==503
        assert client.get('/api/agent-setup/skill.zip').status_code==503


def test_agent_setup_rejects_invalid_port_and_ignores_forwarded_host(tmp_path):
    """Доверяет фактическому loopback URL, а не внешнему X-Forwarded-Host."""
    app=server.create_app(tmp_path)
    with TestClient(app,base_url='http://127.0.0.1:8877') as client:
        data=client.get('/api/agent-setup',headers={'x-forwarded-host':'evil.invalid:9999'}).json()
        assert data['serverUrl']=='http://127.0.0.1:8877'
    with TestClient(server.create_app(tmp_path),base_url='http://127.0.0.1:80') as client:
        assert client.get('/api/agent-setup').status_code==400
