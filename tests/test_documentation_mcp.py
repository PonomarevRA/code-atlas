"""Реальный локальный HTTP MCP, URL-границы, схема и безопасные ответы."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
import pytest
from atlas.documentation_mcp import DocumentationMCPError, discover_tools, search_documents, validate_url


@pytest.fixture(scope='module')
def docs_server(tmp_path_factory):
    """Поднимает отдельный настоящий MCP на свободном loopback-порту, затем завершает."""
    directory = tmp_path_factory.mktemp('documentation-mcp')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    log = directory / 'calls.jsonl'
    output = (directory / 'server.log').open('w')
    fixture = Path(__file__).parent / 'fixtures/documentation_server.py'
    process = subprocess.Popen([sys.executable, str(fixture), str(port), str(log)], stdout=output, stderr=output)
    base = f'http://127.0.0.1:{port}'
    try:
        with httpx.Client(trust_env=False, timeout=.2) as client:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((directory / 'server.log').read_text())
                try:
                    if client.get(base + '/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
            else:
                pytest.fail('Local MCP fixture did not become ready')
        yield {'url': base + '/mcp', 'base': base, 'log': log}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        output.close()


def config(server, tool='search_docs', arguments=None):
    """Создаёт явную настройку инструмента; поиск отдельно передаётся строкой."""
    return {'url':server['url'],'tool':tool,'queryArgument':'query','arguments':arguments if arguments is not None else {'namespace':'guides'}}


@pytest.mark.parametrize('url',['https://127.0.0.1/mcp','http://example.com/mcp','http://127.0.0.1.evil/mcp','http://user:pass@127.0.0.1/mcp','http://127.0.0.1/mcp?token=x','http://127.0.0.1/mcp#x','http://2130706433/mcp','file:///tmp/mcp'])
def test_documentation_url_rejects_nonlocal_or_sensitive_components(url):
    """URL не становится SSRF, транспортом credentials или неоднозначным числовым host."""
    with pytest.raises(DocumentationMCPError):
        validate_url(url)


def test_documentation_real_discovery_does_not_invoke_tools(docs_server):
    """tools/list скрывает явно опасные инструменты и отмечает неизвестную annotation."""
    result = asyncio.run(discover_tools(docs_server['url']))
    tools = {tool['name']: tool for tool in result['tools']}
    assert 'search_docs' in tools and 'unknown_search' in tools
    assert 'mutate_docs' not in tools and 'destroy_docs' not in tools
    assert tools['unknown_search']['warning']
    assert not docs_server['log'].exists()


def test_documentation_real_search_limits_safe_links_and_errors(docs_server, monkeypatch):
    """Реальный tools/call даёт нормализованный текст, ограниченный объём и безопасные URL."""
    monkeypatch.setenv('HTTP_PROXY','http://127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY','http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY','')
    result = asyncio.run(search_documents(config(docs_server), 'PortfolioService'))
    assert result['documents'][0]['text'] == 'guides: local reference for PortfolioService'
    assert result['documents'][0]['url'] == 'https://docs.example/reference'
    assert 'url' not in result['documents'][1]
    large = asyncio.run(search_documents(config(docs_server), 'large'))
    assert len(large['documents']) <= 20
    assert sum(len(item['text']) for item in large['documents']) <= 24000
    assert large['truncated'] is True
    with pytest.raises(DocumentationMCPError) as failure:
        asyncio.run(search_documents(config(docs_server,'error_search',{}), 'test'))
    assert failure.value.status == 502
    assert 'INTERNAL_SECRET' not in str(failure.value)


def test_documentation_real_redirect_and_deadline(docs_server, monkeypatch):
    """SDK не может пройти redirect; deadline превращается в безопасный HTTP504."""
    from atlas import documentation_mcp
    with pytest.raises(DocumentationMCPError) as redirect:
        asyncio.run(discover_tools(docs_server['base'] + '/redirect'))
    assert redirect.value.status == 502
    assert 'REDIRECT_TARGET_REACHED' not in docs_server['log'].read_text()
    attempted_external=[]
    original_send=httpx.AsyncClient.send
    async def guarded_send(client, request, *args, **kwargs):
        """Независимый guard исключает даже пробную внешнюю сеть в тесте redirect."""
        if request.url.host not in {'127.0.0.1','localhost'}:
            attempted_external.append(str(request.url))
            raise AssertionError('external redirect attempted')
        return await original_send(client,request,*args,**kwargs)
    monkeypatch.setattr(httpx.AsyncClient,'send',guarded_send)
    with pytest.raises(DocumentationMCPError):
        asyncio.run(discover_tools(docs_server['base']+'/redirect-external'))
    assert attempted_external==[]
    monkeypatch.setattr(documentation_mcp, 'TIMEOUT', .15)
    with pytest.raises(DocumentationMCPError) as timeout:
        asyncio.run(search_documents(config(docs_server,'slow_search',{}), 'slow'))
    assert timeout.value.status == 504


def test_documentation_dangerous_tools_are_never_called(docs_server):
    """Прямой search не обходит readOnly/destructive фильтр discovery."""
    for name in ['mutate_docs','destroy_docs']:
        with pytest.raises(DocumentationMCPError) as error:
            asyncio.run(search_documents(config(docs_server,name,{}),'test'))
        assert error.value.status==409
    log=docs_server['log'].read_text() if docs_server['log'].exists() else ''
    assert 'mutate_docs' not in log and 'destroy_docs' not in log
