"""Verify release metadata, persistent cooldown and the optional MCP reminder in isolation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.release_update_api import UPDATE_NOTICE_PATH, register_release_update_routes
from atlas.release_update_mcp import register_release_update_tools
from atlas.release_updates import CHECK_INTERVAL, NOTICE_INTERVAL, ReleaseUpdates, installed_update_enabled
from desktop_support import updates


RELEASES = 'https://github.com/PonomarevRA/code-atlas/releases'
OLD, NEW = '0.1.6', 'v0.1.7'


def release_metadata():
    """Construct a stable public release with one uploaded macOS ARM64 installer."""
    filename = 'code-atlas-0.1.7-macos-arm64.dmg'
    return {
        'tag_name': NEW, 'html_url': f'{RELEASES}/tag/{NEW}', 'draft': False, 'prerelease': False,
        'assets': [{'name': filename, 'state': 'uploaded', 'size': 500,
                    'browser_download_url': f'{RELEASES}/download/{NEW}/{filename}'}],
        'body': '## [0.1.7]\n\n### Русский\n- Открывайте обзор быстрее.\n\n### English\n'
                '- Open the [overview](https://example.invalid) faster.\n- <b>Read details</b>.\n'
                '## [0.1.6]\n### English\n- Earlier version.\n',
    }


def stub_github(monkeypatch, metadata):
    """Replace urllib's opener and capture the URL and timeout without a socket."""
    seen = []

    def open_metadata(request, timeout):
        """Capture the outgoing request and return a local serialized response."""
        seen.append((request.full_url, timeout))
        return closing(io.BytesIO(json.dumps(metadata).encode()))

    monkeypatch.setattr(updates, 'build_opener', lambda *_handlers: SimpleNamespace(open=open_metadata))
    return seen


def test_github_metadata_is_target_pinned_and_notes_are_plain_text(monkeypatch):
    """The checker uses fixed URLs and gives only the current version's short notes."""
    seen = stub_github(monkeypatch, release_metadata())
    found = updates.check_release('code-atlas', OLD, target_platform='darwin', machine='arm64')
    assert seen == [('https://api.github.com/repos/PonomarevRA/code-atlas/releases/latest', 8)]
    assert found['available'] and found['installerAvailable']
    assert found['localizedNotes'] == {
        'ru': ['Открывайте обзор быстрее.'], 'en': ['Open the overview faster.', 'Read details.'],
    }
    assert not updates.check_release('code-atlas', OLD, target_platform='win32', machine='AMD64')['available']
    assert not updates.check_release('code-atlas', NEW, target_platform='darwin', machine='arm64')['available']


@pytest.mark.parametrize('changes', [
    {'draft': True}, {'prerelease': True}, {'tag_name': 'v0.1.7-rc.1'},
    {'html_url': 'https://example.invalid/releases/tag/v0.1.7'},
])
def test_unexpected_release_is_rejected(monkeypatch, changes):
    """A prerelease, draft or mismatched URL cannot be returned to the caller."""
    stub_github(monkeypatch, {**release_metadata(), **changes})
    with pytest.raises(ValueError):
        updates.check_release('code-atlas', OLD, target_platform='darwin', machine='arm64')


@pytest.mark.parametrize('asset_change', [
    {'state': 'starter'}, {'size': 0}, {'browser_download_url': 'https://example.invalid/installer'},
])
def test_unusable_installer_does_not_qualify(monkeypatch, asset_change):
    """A newer tag alone never offers a native upgrade."""
    metadata = release_metadata()
    metadata['assets'][0].update(asset_change)
    stub_github(monkeypatch, metadata)
    found = updates.check_release('code-atlas', OLD, target_platform='darwin', machine='arm64')
    assert not found['available'] and not found['installerAvailable']


def local_release():
    """Return release facts already verified by the metadata checker."""
    return {'version': NEW, 'installerAvailable': True, 'url': f'{RELEASES}/tag/{NEW}',
            'localizedNotes': {'ru': ['Проверка.'], 'en': ['Check.']}}


def test_notice_claim_is_cache_only_shared_and_persistent(tmp_path):
    """Exactly one of eight clients gets a notice, including across restarts and locales."""
    calls, now = [], [1000]

    def checker(*args):
        """Record actual metadata refresh attempts without external I/O."""
        calls.append(args)
        return local_release()

    service = ReleaseUpdates(tmp_path, enabled=True, current_version=OLD, checker=checker, clock=lambda: now[0])
    assert service.claim('ru') == {'notice': None} and calls == []
    service.check_if_due()
    service.check_if_due()
    assert len(calls) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(service.claim, ['ru'] * 8))
    assert [claim['notice']['notes'] for claim in claims if claim['notice']] == [['Проверка.']]
    restarted = ReleaseUpdates(tmp_path, enabled=True, current_version=OLD, checker=checker, clock=lambda: now[0])
    now[0] += NOTICE_INTERVAL - 1
    assert restarted.claim('en') == {'notice': None}
    now[0] += 1
    assert restarted.claim('en')['notice']['notes'] == ['Check.']
    assert len(calls) == 1
    updated = ReleaseUpdates(tmp_path, enabled=True, current_version=NEW, checker=checker, clock=lambda: now[0])
    assert updated.claim('en') == {'notice': None}


def test_refresh_failure_keeps_valid_state_and_retries_later(tmp_path):
    """A temporary network failure neither breaks the claim nor retries every call."""
    now, calls = [1000], []

    def checker(*args):
        """Fail after the initial successful release read."""
        calls.append(args)
        if len(calls) > 1:
            raise OSError('offline')
        return local_release()

    service = ReleaseUpdates(tmp_path, enabled=True, current_version=OLD, checker=checker, clock=lambda: now[0])
    service.check_if_due()
    now[0] += CHECK_INTERVAL
    service.check_if_due()
    service.check_if_due()
    assert len(calls) == 2
    assert service.claim('en')['notice']['version'] == NEW
    now[0] += 8 * 24 * 60 * 60
    assert service.claim('en') == {'notice': None}


def test_source_mode_does_not_start_or_create_update_cache(tmp_path):
    """A source run exposes a no-op endpoint and creates no update artifacts."""
    assert not installed_update_enabled(tmp_path)
    service = ReleaseUpdates(tmp_path)
    service.start()
    service.check_if_due()
    service.close()
    assert service.worker is None and service.claim('en') == {'notice': None}
    assert list(tmp_path.iterdir()) == []


def test_claim_http_is_an_explicit_post(tmp_path):
    """An ordinary GET or invalid locale cannot consume the shared reminder."""
    service = ReleaseUpdates(tmp_path, enabled=True, current_version=OLD, checker=lambda *_: local_release())
    service.check_if_due()
    app = FastAPI()
    register_release_update_routes(app, service)
    with TestClient(app) as client:
        assert client.get(UPDATE_NOTICE_PATH).status_code == 405
        assert client.post(UPDATE_NOTICE_PATH, json={'locale': 'unknown'}).status_code == 422
        assert client.post(UPDATE_NOTICE_PATH, json={'locale': 'ru'}).json()['notice']['version'] == NEW
        assert client.post(UPDATE_NOTICE_PATH, json={'locale': 'en'}).json() == {'notice': None}


@pytest.mark.anyio
async def test_mcp_claim_fails_open_when_local_endpoint_is_unavailable():
    """An older local service cannot turn the optional tool into a task failure."""
    from mcp.server.fastmcp import FastMCP

    async def request(method, path, body, timeout):
        """Require bounded local access and simulate an older server."""
        assert (method, path, body, timeout) == ('POST', UPDATE_NOTICE_PATH, {'locale': 'ru'}, .5)
        raise ValueError('Not found')

    server = FastMCP('test')
    register_release_update_tools(server, request)
    result = await server.call_tool('get_update_notice_after_task', {'locale': 'ru'})
    assert json.loads(result[0].text) == {'notice': None}


def test_private_advisory_uses_only_private_listing(tmp_path):
    """Private installs can see public notes without being offered a public replacement."""
    release = {**local_release(), 'installerAvailable': False}
    service = ReleaseUpdates(tmp_path, enabled=True, private=True, current_version=OLD, checker=lambda *_: release)
    service.check_if_due()
    notice = service.claim('en')['notice']
    assert notice['edition'] == 'private' and notice['releaseSource'] == 'public'
    assert notice['installerAvailable'] is None
    assert notice['url'] == notice['releasesUrl'] == 'https://github.com/PonomarevRA/code-atlas-private/releases'
    assert notice['version'] == NEW and notice['notes'] == ['Check.']


def test_optional_checker_disabled_for_invalid_installer_locator(monkeypatch, tmp_path):
    """A malformed installation cannot enable networking or prevent startup."""
    from atlas import release_updates

    def malformed(*_args):
        """Simulate the installer settings reader encountering a non-object."""
        raise AttributeError('non-object')

    monkeypatch.setattr(release_updates.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(release_updates, 'read_installation', malformed)
    assert not release_updates.installed_update_enabled(tmp_path)


def test_thread_failure_keeps_application_available(monkeypatch, tmp_path):
    """An exhausted thread pool does not propagate into server startup."""
    from atlas import release_updates

    def unavailable(_thread):
        """Fail as Thread.start does when no native worker can be allocated."""
        raise RuntimeError('cannot start thread')

    monkeypatch.setattr(release_updates.Thread, 'start', unavailable)
    service = ReleaseUpdates(tmp_path, enabled=True, current_version=OLD)
    service.start()
    assert service.worker is None and service.claim('en') == {'notice': None}


def test_legacy_macos_install_requires_official_bundle_and_default_data(monkeypatch, tmp_path):
    """An older native install can check updates without activating frozen test copies."""
    from atlas import release_updates

    default = tmp_path / 'default'
    executable = tmp_path / 'Applications' / 'Code Atlas.app' / 'Contents' / 'MacOS' / 'CodeAtlas'
    monkeypatch.setattr(release_updates.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(release_updates.sys, 'platform', 'darwin')
    monkeypatch.setattr(release_updates.sys, 'executable', str(executable))
    monkeypatch.setattr(Path, 'home', classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(release_updates, 'read_installation', lambda *_: SimpleNamespace(application_path=None))
    monkeypatch.setattr(release_updates, 'default_data_dir', lambda **_: default)
    assert release_updates.installed_update_enabled(default)
    assert not release_updates.installed_update_enabled(tmp_path / 'isolated')
    monkeypatch.setattr(release_updates.sys, 'executable', str(tmp_path / 'build' / 'CodeAtlas'))
    assert not release_updates.installed_update_enabled(default)
