"""Фактический размер HTTP тела проверяется независимо от Content-Length."""
import asyncio
import httpx
from atlas import server


def test_streamed_oversize_post_is_rejected_before_endpoint(tmp_path):
    """Chunked и ложный маленький length не допускают вызов обработчика."""
    app=server.create_app(tmp_path)
    calls=[]
    @app.post('/api/test-boundary')
    async def endpoint():
        """Sentinel: middleware обязан остановить большой запрос раньше."""
        calls.append(True)
        return {'ok':True}
    # StaticFiles mount is last in production; place this test endpoint before it.
    app.router.routes.insert(0,app.router.routes.pop())
    async def scenario():
        """ASGI transport передаёт реальные частичные body без bind/socket."""
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8765') as client:
            for declared in [None,'2']:
                async def chunks():
                    """Тело валидно как JSON, но превышает стандартные 16 КиБ."""
                    yield b'{"value":"'
                    yield b'x'*17_000
                    yield b'"}'
                headers={'content-type':'application/json'}
                if declared is not None:
                    headers['content-length']=declared
                response=await client.post('/api/test-boundary',content=chunks(),headers=headers)
                assert response.status_code==413,response.text
            assert calls==[]
            response=await client.post('/api/test-boundary',json={})
            assert response.status_code==200
            assert calls==[True]
    try:
        asyncio.run(scenario())
    finally:
        app.state.store.close()


def test_saved_view_body_limit_is_larger_but_still_bounded(tmp_path):
    """Views допускает до256 КиБ; 20 КиБ доходит до DTO, 270 КиБ отвергается раньше."""
    app=server.create_app(tmp_path)
    async def scenario():
        """Проверяет отдельный лимит immutable view без создания огромного артефакта."""
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8765') as client:
            for size,status in [(20_000,422),(270_000,413)]:
                async def chunks():
                    yield b'{"unknown":"'+b'x'*size+b'"}'
                response=await client.post('/api/views',content=chunks(),headers={'content-type':'application/json'})
                assert response.status_code==status,response.text
    try:
        asyncio.run(scenario())
    finally:
        app.state.store.close()


def test_unbounded_decimal_length_is_controlled_error(tmp_path):
    """Очень длинный Content-Length не провоцирует исключение Python int-parser."""
    app=server.create_app(tmp_path)
    async def scenario():
        """HTTP ошибка остаётся413 даже когда число длиннее лимита int conversion."""
        transport=httpx.ASGITransport(app=app,raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1:8765') as client:
            response=await client.post('/api/views',content=b'{}',headers={'content-type':'application/json','content-length':'9'*4500})
            assert response.status_code==413
    try:
        asyncio.run(scenario())
    finally:
        app.state.store.close()
