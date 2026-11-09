"""Native framing and actual Chromium→host→HTTP→MCP→delete with isolated local fixtures."""
import asyncio
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
from threading import Thread
import time

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
import httpx
import pytest
import uvicorn

from atlas.browser_api import register_browser_routes
from atlas.browser_native_host import read_frame, write_frame, MAX_MESSAGE_BYTES, FRAME
from atlas.browser_sessions import BrowserSessions
from atlas.local_http_boundary import LocalHTTPBoundary
from atlas.agent_mcp import create_mcp
from test_browser_sessions import capture

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = 'a' * 32


@pytest.fixture
def browser_server(tmp_path):
    """Use production routes and boundary with no persistent ProjectStore or user data."""
    if not (ROOT / 'vendor/ag-privacy').exists():
        pytest.skip('Optional AG Privacy SDK is not installed')
    app = FastAPI()
    sessions = BrowserSessions()
    from atlas.browser_privacy import BrowserPrivacy
    privacy = BrowserPrivacy(tmp_path / 'privacy')
    assert privacy.status()['available']
    privacy.select_after_human_confirmation('off')  # Synthetic product test profile; no real installation data.
    register_browser_routes(app, sessions, privacy=privacy)
    app.add_middleware(LocalHTTPBoundary)

    @app.get('/fixture')
    def fixture():
        """The real browser renders a DOM mutation produced by an external script."""
        return HTMLResponse('<main><button id="save">Save</button><input value="PRIVATE_FORM"><script src="/fixture.js"></script></main>')

    @app.get('/fixture.js')
    def script():
        """Only our test fixture executes in Chromium; Atlas merely parses these source bytes."""
        return Response('function send(){return 1} function save(){return send()} document.querySelector("#save").textContent="RENDERED_CANARY";', media_type='text/javascript')

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    try:
        yield port, sessions
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sessions.close()
        listener.close()
        assert not thread.is_alive()


def native_request(payload, port, *, origin=None):
    """Launch the actual stdio host with hostile proxy settings and exactly one framed message."""
    content = json.dumps(payload).encode()
    process = subprocess.run([sys.executable, '-m', 'atlas.browser_native_host', '--extension-id', EXTENSION,
                              '--port', str(port), origin or 'chrome-extension://' + EXTENSION + '/'],
                             input=FRAME.pack(len(content)) + content, cwd=ROOT, timeout=25,
                             capture_output=True, env={**os.environ, 'HTTP_PROXY': 'http://127.0.0.1:1', 'ALL_PROXY': 'http://127.0.0.1:1'})
    assert process.returncode == 0, process.stderr
    assert process.stderr == b''
    assert b'RENDERED_CANARY' not in process.stdout
    return read_frame(BytesIO(process.stdout))


def test_native_framing_rejects_truncation_and_oversized_data():
    """Length limits precede body allocation; truncated frames never become partial captures."""
    for raw in (b'\x01', FRAME.pack(MAX_MESSAGE_BYTES + 1), FRAME.pack(20) + b'{}'):
        with pytest.raises(ValueError):
            read_frame(BytesIO(raw))
    output = BytesIO()
    write_frame(output, {'sessionId': 'x'})
    assert read_frame(BytesIO(output.getvalue())) == {'sessionId': 'x'}


def test_native_host_origin_scope_and_source_installer(tmp_path, browser_server):
    """The launcher pins one extension; installer output contains no wildcard or shell interpolation."""
    port, sessions = browser_server
    result = native_request({'capture': capture()}, port, origin='chrome-extension://' + 'b' * 32 + '/')
    assert result['error']['code'] == 'native-request-rejected'
    assert sessions.list()['sessions'] == []
    spec = importlib.util.spec_from_file_location('browser_installer', ROOT / 'tools/install_browser_capture_host.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = module.install(EXTENSION, tmp_path / 'host with spaces', port=port)
    data = json.loads(manifest.read_text())
    assert data['allowed_origins'] == ['chrome-extension://' + EXTENSION + '/']
    assert Path(data['path']).stat().st_mode & 0o100
    raw = json.dumps({'capture': capture()}).encode()
    process = subprocess.run([data['path'], 'chrome-extension://' + EXTENSION + '/'],
                             input=FRAME.pack(len(raw)) + raw, capture_output=True, timeout=25)
    assert process.returncode == 0, process.stderr
    assert read_frame(BytesIO(process.stdout))['sessionId']


def test_real_chromium_capture_native_http_mcp_finish(tmp_path, browser_server):
    """Verify actual rendered DOM and loaded JS through the full local analysis pipeline."""
    collector = ROOT / 'tests/browser_cdp_capture.mjs'
    if not collector.is_file():
        pytest.skip('Optional browser collector is not bundled')
    node = shutil.which('node')
    cache = Path.home() / 'Library/Caches/ms-playwright'
    candidates = sorted(cache.glob('chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell'))
    configured = os.environ.get('ATLAS_TEST_CHROMIUM')
    browser = Path(configured) if configured else candidates[-1] if candidates else None
    if not node or browser is None or not browser.is_file():
        pytest.skip('Existing Chromium headless shell and Node required; no downloads')
    port, sessions = browser_server
    profile = tmp_path / 'chromium-profile'
    log = (tmp_path / 'chromium.log').open('wb')
    process = subprocess.Popen([str(browser), '--headless', '--no-first-run', '--no-default-browser-check',
                                '--disable-background-networking', '--remote-debugging-port=0',
                                '--user-data-dir=' + str(profile), 'about:blank'], stdout=log, stderr=log)
    try:
        active = profile / 'DevToolsActivePort'
        deadline = time.monotonic() + 10
        while not active.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(.05)
        assert active.exists(), (tmp_path / 'chromium.log').read_text(errors='replace')
        debugging_port = int(active.read_text().splitlines()[0])
        with httpx.Client(trust_env=False) as client:
            targets = client.get(f'http://127.0.0.1:{debugging_port}/json/list').json()
        endpoint = next(item['webSocketDebuggerUrl'] for item in targets if item['type'] == 'page')
        captured = subprocess.run([node, str(collector), endpoint,
                                   f'http://127.0.0.1:{port}/fixture'], capture_output=True, text=True, timeout=25)
        assert captured.returncode == 0, captured.stderr
        payload = json.loads(captured.stdout)
        assert any(item['text'] == 'RENDERED_CANARY' for item in payload['capture']['nodes'])
        assert 'PRIVATE_FORM' not in json.dumps(payload)
        assert any('function save' in (item['source'] or '') for item in payload['capture']['scripts'])
        result = native_request(payload, port)
        identifier = result['sessionId']

        async def scenario():
            """Call registered production MCP tools over real HTTP and release the imported session."""
            server = create_mcp(port)
            def unpack(result):
                """FastMCP returns a content/structured tuple on direct tool calls."""
                return result[1] if isinstance(result, tuple) else json.loads(result[0].text)
            try:
                found = unpack(await server.call_tool('query_browser_session', {'sessionId': identifier, 'query': 'save', 'kind': 'function'}))
                assert found['total'] == 1
                dom = unpack(await server.call_tool('query_browser_session', {'sessionId': identifier, 'query': 'RENDERED_CANARY'}))
                assert dom['total'] == 1
                detail = unpack(await server.call_tool('get_browser_node', {'sessionId': identifier, 'nodeId': found['nodes'][0]['id']}))
                assert any(edge['relation'] == 'call_candidate' for edge in detail['edges'])
            finally:
                await server.call_tool('finish_browser_session', {'sessionId': identifier, 'reason': 'completed'})
        asyncio.run(scenario())
        assert sessions.list()['sessions'] == []
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


def test_collector_contracts_with_chrome_transport():
    """Run production JS capture behavior under success, navigation and transport failures."""
    collector = ROOT / 'tests/test_browser_collector.mjs'
    if not collector.is_file():
        pytest.skip('Optional browser collector is not bundled')
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node required')
    result = subprocess.run([node, str(collector)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'detach PASS' in result.stdout


def test_native_status_finish_and_recovery_are_exact_and_bounded(browser_server):
    """Only the selected ID is addressed; no page data or remote error text crosses the host."""
    port, sessions = browser_server
    first = native_request({'capture': capture()}, port)
    second = native_request({'capture': capture()}, port)
    identifier = first['sessionId']
    status = native_request({'operation': 'status', 'sessionId': identifier}, port)
    assert status == first
    assert 'PRIVATE' not in json.dumps(status)
    assert native_request({'operation': 'finish', 'sessionId': identifier}, port)['deleted']
    assert native_request({'operation': 'finish', 'sessionId': identifier}, port)['deleted']
    missing = native_request({'operation': 'status', 'sessionId': identifier}, port)
    assert missing['error']['code'] == 'browser-item-unavailable'
    assert missing['error']['recoveryAction'] == 'capture-again'
    assert [x['sessionId'] for x in sessions.list()['sessions']] == [second['sessionId']]
    for payload in ({'operation': 'status', 'sessionId':'../projects'}, {'operation':'list'},
                    {'operation':'finish','sessionId':second['sessionId'],'url':'https://evil.test'}):
        assert native_request(payload, port)['error']['code'] == 'native-request-rejected'
    assert len(sessions.list()['sessions']) == 1


def test_native_health_drops_all_session_metadata(browser_server):
    """The settings handshake checks availability without returning session IDs, counts or page text."""
    port,sessions=browser_server
    native_request({'capture':capture()},port)
    assert native_request({'operation':'health'},port)=={'available':True,'protocol':'atlas-browser-agent-v1'}
    assert native_request({'operation':'health','url':'http://evil.test'},port)['error']['code']=='native-request-rejected'
