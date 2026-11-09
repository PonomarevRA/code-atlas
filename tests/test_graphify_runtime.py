"""The vendor runtime is replaceable without importing Atlas into that environment."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from atlas.integrations.graphify.runtime import GraphifyRuntime, command
from atlas.graphify_adapter import build_graph
from atlas.mcp_bridge import describe_node
from atlas.server import create_app
from fastapi.testclient import TestClient
from conftest import source_snapshot


def test_external_runtime_extracts_and_serves_without_importing_atlas(source_project,tmp_path):
    executable=str(Path(sys.executable).absolute())
    check=subprocess.run([executable,'-I','-c','import importlib.util; print(importlib.util.find_spec("atlas"))'],capture_output=True,text=True,check=True)
    assert check.stdout.strip()=='None'
    runtime=GraphifyRuntime(tmp_path/'settings');info=runtime.set(executable)
    assert info['mode']=='external' and info['available']
    before=source_snapshot(source_project)
    with runtime.activate():
        selected=command('graphify',['extract']);assert selected[:2]==[executable,'-I']
        graph=build_graph(source_project,tmp_path/'graphs')
        node=next(n for n in graph['nodes'] if 'normalize' in n['label'].lower())
        result=asyncio.run(describe_node(Path(graph['metadata']['rawGraphPath']),node['id']))
    assert graph['metadata']['integration']['mode']=='external'
    assert result['provider']=='Graphify MCP'
    assert source_snapshot(source_project)==before
    assert runtime.set(None)['mode']=='embedded'


def test_runtime_validation_failure_does_not_replace_last_working_choice(tmp_path):
    runtime=GraphifyRuntime(tmp_path)
    runtime.set(str(Path(sys.executable).absolute()));before=runtime.path.read_bytes()
    import pytest
    with pytest.raises(ValueError): runtime.set('relative/python')
    assert runtime.path.read_bytes()==before


def test_runtime_http_reports_selected_provider_and_existing_maps_stay_readable(tmp_path):
    with TestClient(create_app(tmp_path),base_url='http://127.0.0.1') as client:
        assert client.get('/api/integrations/graphify').json()['mode']=='embedded'
        selected=client.post('/api/integrations/graphify/runtime',json={'python':str(Path(sys.executable).absolute())})
        assert selected.status_code==200 and selected.json()['mode']=='external'
        assert client.get('/api/health').json()['graphifyVersion']==selected.json()['version']
        assert client.post('/api/integrations/graphify/runtime',json={'python':'../bad'}).status_code==400
        assert client.post('/api/integrations/graphify/runtime',json={'python':None}).json()['mode']=='embedded'
