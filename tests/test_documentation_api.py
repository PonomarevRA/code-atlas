"""Сохранение MCP-конфигурации и версия поиска без сетевого fixture."""
from fastapi.testclient import TestClient
from atlas import server

SCHEMA = {'type':'object','properties':{'query':{'type':'string'},'namespace':{'type':'string'},'limit':{'type':'integer'}},'required':['query','namespace'],'additionalProperties':False}
CONFIG = {'url':'http://localhost:8879/mcp','tool':'search_docs','queryArgument':'query','arguments':{'namespace':'guides'}}


def test_documentation_config_schema_persistence_and_version(tmp_path, monkeypatch):
    """Невалидные аргументы не заменяют рабочую конфигурацию; версия переживает перезапуск."""
    async def discovered(url):
        """Возвращает точную схему допустимого инструмента, не исполняя его."""
        return {'tools':[{'name':'search_docs','inputSchema':SCHEMA,'readOnlyHint':True}], 'truncated':False}
    monkeypatch.setattr(server,'discover_tools',discovered)
    with TestClient(server.create_app(tmp_path),base_url='http://127.0.0.1:8765') as client:
        base='/api/documentation-mcp'
        assert client.get(base+'/config').json()['configured'] is False
        assert client.post(base+'/search',json={'query':'keywords'}).status_code == 409
        saved=client.post(base+'/config',json=CONFIG)
        assert saved.status_code == 200, saved.text
        token=saved.json()['configVersion']
        assert saved.json()['config']['url']=='http://127.0.0.1:8879/mcp'
        for arguments in [{},{'namespace':1},{'namespace':'guides','limit':'wrong'},{'namespace':'guides','extra':1}]:
            response=client.post(base+'/config',json={**CONFIG,'arguments':arguments})
            assert response.status_code == 400, response.text
            assert client.get(base+'/config').json()['configVersion']==token
        assert client.post(base+'/config',json={**CONFIG,'tool':'mutate_docs'}).status_code==400
        assert client.post(base+'/config',json={**CONFIG,'queryArgument':'limit'}).status_code==400
        assert client.post(base+'/search',json={'query':'x'*501}).status_code==422
        assert client.post(base+'/search',json={'query':'x','source':'RAW_CODE'}).status_code==422
    with TestClient(server.create_app(tmp_path),base_url='http://127.0.0.1:8765') as client:
        assert client.get(base+'/config').json()['configVersion']==token
        assert client.post(base+'/disconnect',json={}).json()['configured'] is False
        assert client.post(base+'/search',json={'query':'keywords'}).status_code==409


def test_documentation_search_config_races(tmp_path,monkeypatch):
    """Смена сохранённого инструмента в ходе запроса отклоняет готовый ответ."""
    app=server.create_app(tmp_path)
    current=app.state.documentation_config.save(CONFIG)
    async def forbidden(*args):
        """Неверный токен проверяется до tools/call."""
        raise AssertionError('stale config called tool')
    monkeypatch.setattr(server,'search_documents',forbidden)
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        route='/api/documentation-mcp/search'
        assert client.post(route,json={'query':'words','configVersion':'stale'}).status_code==409
        async def changed(config,query):
            """Моделирует настройку другого namespace параллельно с поиском."""
            app.state.documentation_config.save({**CONFIG,'arguments':{'namespace':'other'}})
            return {'documents':[{'title':'old','text':'old'}], 'truncated':False,'tool':'search_docs','warning':''}
        monkeypatch.setattr(server,'search_documents',changed)
        response=client.post(route,json={'query':'words','configVersion':current['configVersion']})
        assert response.status_code==409
        assert 'old' not in response.text


def test_documentation_tool_query_constraint_applies_to_actual_search_not_placeholder(tmp_path,monkeypatch):
    """Настройка инструмента с узким pattern сохраняется без выполнения фиктивного поиска."""
    constrained={'type':'object','properties':{'query':{'type':'string','pattern':'^[A-Z]{1,3}$'}},'required':['query'],'additionalProperties':False}
    async def discovered(url):
        """Инструмент принимает короткий uppercase search, а не слово «поиск»."""
        return {'tools':[{'name':'short','inputSchema':constrained}], 'truncated':False}
    monkeypatch.setattr(server,'discover_tools',discovered)
    with TestClient(server.create_app(tmp_path),base_url='http://127.0.0.1:8765') as client:
        response=client.post('/api/documentation-mcp/config',json={**CONFIG,'tool':'short','arguments':{}})
        assert response.status_code==200,response.text


def test_documentation_schema_cannot_resolve_external_references(monkeypatch):
    """Схема MCP не создаёт скрытый сетевой канал при валидации аргументов."""
    import socket
    import pytest
    from atlas.documentation_mcp import validate_arguments,DocumentationMCPError
    def forbidden(*args,**kwargs):
        """Любая попытка сети из JSON Schema — ошибка теста."""
        raise AssertionError('JSON schema network access')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    with pytest.raises(DocumentationMCPError):
        validate_arguments({'$ref':'https://outside.invalid/schema'},{})
    validate_arguments({'type':'object','properties':{'query':{'$ref':'#/$defs/query'}},'$defs':{'query':{'type':'string'}}},{'query':'words'})
