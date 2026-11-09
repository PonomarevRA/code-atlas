"""SVG attachment остаётся локальным, ограниченным и неизменным."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from atlas.local_http_boundary import LocalHTTPBoundary
from atlas.map_exports import register_map_export_routes


def client():
    """Создаёт независимый временный export-кэш без проектов и файловой записи."""
    app = FastAPI()
    app.add_middleware(LocalHTTPBoundary)
    register_map_export_routes(app)
    return TestClient(app, base_url="http://127.0.0.1:8766")


def test_svg_attachment_utf8_limits_and_expiration(monkeypatch):
    """Проверяет большой валидный файл, ограниченный кэш и истечение ссылки."""
    with client() as browser:
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>Расчёт</text>' + ' ' * 300000 + '</svg>'
        result = browser.post('/api/map-export', json={'svg': svg, 'filename': 'Расчёт.svg'})
        assert result.status_code == 200
        url = result.json()['url']
        downloaded = browser.get(url)
        assert downloaded.content == svg.encode()
        assert downloaded.headers['content-disposition'].startswith('attachment;')
        assert 'filename*=UTF-8' in downloaded.headers['content-disposition']
        assert 'nosniff' == downloaded.headers['x-content-type-options']
        for _ in range(2):
            assert browser.post('/api/map-export', json={'svg': svg, 'filename': 'map.svg'}).status_code == 200
        assert browser.get(url).status_code == 404
        last = browser.post('/api/map-export', json={'svg': svg, 'filename': 'map.svg'}).json()['url']
        monkeypatch.setattr('atlas.map_exports.monotonic', lambda: float('inf'))
        assert browser.get(last).status_code == 404


def test_svg_export_rejects_wrong_input_and_cross_site():
    """Новая граница размера не ослабляет Origin и ограничения остальных API."""
    with client() as browser:
        for payload in [{'svg': '<svg/>', 'filename': '../map.svg'}, {'svg': '<html/>', 'filename': 'map.svg'},
                        {'svg': '<svg/>', 'filename': 'map.svg', 'extra': True}]:
            assert browser.post('/api/map-export', json=payload).status_code == 422
        assert browser.post('/api/map-export', json={'svg': '<svg/>', 'filename': 'map.svg'},
                            headers={'Origin': 'https://other.example'}).status_code == 403
        assert browser.post('/api/map-export', content=b'x' * 32000001,
                            headers={'Content-Type': 'application/json'}).status_code == 413
        assert browser.post('/api/views', json={'value': 'x' * 270000}).status_code == 413
