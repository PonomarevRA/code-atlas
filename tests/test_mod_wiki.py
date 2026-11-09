"""Wiki declarations and HTTP reads remain bounded, revision-pinned and execution-free."""
import json
import os
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from atlas.mod_api import register_mod_routes
from atlas.mod_models import ModManifest
from atlas.mod_store import ModError, ModStore
from atlas import mod_wiki


def manifest(**changes):
    """Build the smallest existing API v1 package with optional wiki metadata."""
    return {'apiVersion': 1, 'id': 'example', 'name': 'Example', 'version': '1', **changes}


def wiki(**changes):
    """Use the public existing Atlas section and one package-local Markdown page."""
    return {'section': 'Explore-Code', 'pages': [
        {'id': 'overview', 'title': 'Overview', 'path': 'wiki/Overview.md'}], **changes}


@pytest.fixture
def package(tmp_path):
    """Expose real disabled package files through only the production mod HTTP routes."""
    root = tmp_path / 'state/mods/example'
    (root / 'wiki').mkdir(parents=True)
    (root / 'mod.json').write_text(json.dumps(manifest(wiki=wiki(), frontend='mod.js')))
    (root / 'mod.js').write_text('throw new Error("must never execute");')
    (root / 'wiki/Overview.md').write_text('# Привет\n\n[Next](Other.md)\n', encoding='utf-8')
    service = ModStore(tmp_path / 'state', tmp_path / 'no-bundled')
    app = FastAPI()
    register_mod_routes(app, SimpleNamespace(mods=service))
    with TestClient(app) as client:
        yield root, service, client


def test_disabled_catalog_and_page_have_exact_public_contract(package):
    root, service, client = package
    catalog = client.get('/api/mods/wiki').json()
    assert set(catalog) == {'apiVersion', 'mods', 'truncated'}
    item = catalog['mods'][0]
    assert item['status'] == 'disabled' and item['enabled'] is False
    assert item['section'] == 'Explore-Code' and item['pages'] == wiki()['pages']
    assert 'text' not in json.dumps(catalog)
    assert str(root) not in json.dumps(catalog)
    response = client.get('/api/mods/example/wiki/overview', params={'fingerprint': item['fingerprint']})
    assert response.status_code == 200
    assert response.json() == {'modId': 'example', 'pageId': 'overview', 'title': 'Overview',
        'section': 'Explore-Code', 'text': '# Привет\n\n[Next](Other.md)\n', 'format': 'markdown',
        'fingerprint': item['fingerprint'], 'source': {'origin': 'user', 'path': 'wiki/Overview.md'}}
    assert response.headers['cache-control'] == 'no-store'
    assert not service.state_path.exists()


def test_legacy_manifest_and_absent_pages_are_compatible(package):
    root, service, client = package
    (root / 'mod.json').write_text(json.dumps(manifest()))
    assert client.get('/api/mods/wiki').json()['mods'] == []
    assert 'wiki' not in service.catalog()['mods'][0]['manifest']
    for path in ('example/wiki/overview', 'missing/wiki/overview'):
        assert client.get('/api/mods/' + path).status_code == 404


@pytest.mark.parametrize('path', ['../secret.md', '/tmp/page.md', 'wiki/../page.md',
    'wiki//page.md', './page.md', 'wiki\\page.md', 'C:page.md', 'page.js', 'bad\0.md'])
def test_manifest_rejects_unsafe_or_executable_paths(path):
    declaration = wiki(pages=[{'id': 'overview', 'title': 'Page', 'path': path}])
    with pytest.raises(ValidationError):
        ModManifest.model_validate(manifest(wiki=declaration))


@pytest.mark.parametrize('declaration', [wiki(section='Unknown'), wiki(pages=wiki()['pages'] * 2),
    wiki(pages=[{'id': f'p{i}', 'title': 'Page', 'path': 'page.md'} for i in range(17)]),
    wiki(pages=[{'id': 'Bad ID', 'title': 'Page', 'path': 'page.md'}])])
def test_manifest_rejects_ambiguous_or_unbounded_declarations(declaration):
    with pytest.raises(ValidationError):
        ModManifest.model_validate(manifest(wiki=declaration))


@pytest.mark.parametrize('content', [b'\xff', b'text\x00binary', b'a' * (65536 + 1)])
def test_bad_markdown_does_not_break_catalog_or_executable_activation(package, content):
    root, service, client = package
    (root / 'wiki/Overview.md').write_bytes(content)
    item = client.get('/api/mods/wiki').json()['mods'][0]
    service.enable('example', item['fingerprint'], True)
    assert service.catalog()['mods'][0]['enabled']
    assert client.get('/api/mods/example/wiki/overview').status_code == 400
    assert client.get(f"/api/mods/example/assets/{item['fingerprint']}/mod.js").status_code == 200


def test_exact_byte_limit_and_utf8_bom(package):
    root, _, client = package
    content = b'\xef\xbb\xbf' + b'a' * (65536 - 3)
    (root / 'wiki/Overview.md').write_bytes(content)
    response = client.get('/api/mods/example/wiki/overview')
    assert response.status_code == 200 and len(response.json()['text']) == 65533


def test_missing_undeclared_and_other_package_resources_are_not_served(package):
    root, _, client = package
    (root / 'wiki/Other.md').write_text('Not declared')
    assert client.get('/api/mods/example/wiki/other').status_code == 404
    (root / 'wiki/Overview.md').unlink()
    assert client.get('/api/mods/wiki').status_code == 200
    assert client.get('/api/mods/example/wiki/overview').status_code == 400


def test_document_changes_keep_existing_exact_activation_fingerprint(package):
    root, service, client = package
    first = client.get('/api/mods/wiki').json()['mods'][0]
    service.enable('example', first['fingerprint'], True)
    (root / 'wiki/Overview.md').write_text('# New revision')
    changed = client.get('/api/mods/wiki').json()['mods'][0]
    assert changed['fingerprint'] != first['fingerprint'] and changed['status'] == 'changed'
    assert client.get('/api/mods/example/wiki/overview', params={'fingerprint': first['fingerprint']}).status_code == 409
    assert client.get('/api/mods/example/wiki/overview').json()['text'] == '# New revision'
    with pytest.raises(ModError):
        service.active('example', first['fingerprint'])


@pytest.mark.parametrize('replacement', ['file-symlink', 'directory-symlink', 'fifo', 'changed-page', 'changed-manifest'])
def test_replacement_after_catalog_fails_closed(package, monkeypatch, tmp_path, replacement):
    root, _, client = package
    original_reader = mod_wiki._read_regular_file

    def replace_then_read(base, relative, limit):
        """Mutate after discovery to exercise the real no-follow reader and revision guard."""
        if replacement == 'changed-manifest':
            (root / 'mod.json').write_text(json.dumps(manifest(wiki=wiki(), name='Changed')))
        elif replacement == 'directory-symlink':
            (root / 'wiki/Overview.md').unlink()
            (root / 'wiki').rmdir()
            (root / 'wiki').symlink_to(tmp_path, target_is_directory=True)
            (tmp_path / 'Overview.md').write_text('outside')
        else:
            page = root / relative
            page.unlink()
            if replacement == 'file-symlink':
                outside = tmp_path / 'secret.md'
                outside.write_text('outside')
                page.symlink_to(outside)
            elif replacement == 'fifo':
                os.mkfifo(page)
            else:
                page.write_text('changed')
        return original_reader(base, relative, limit)

    monkeypatch.setattr(mod_wiki, '_read_regular_file', replace_then_read)
    response = client.get('/api/mods/example/wiki/overview')
    expected = 409 if replacement.startswith('changed-') else 400
    assert response.status_code == expected
    assert 'outside' not in response.text


def test_wiki_catalog_does_not_use_page_reader(package, monkeypatch):
    _, _, client = package

    def forbidden(*args):
        raise AssertionError('Catalog must not decode or materialize wiki page content')

    monkeypatch.setattr(mod_wiki, '_read_regular_file', forbidden)
    assert client.get('/api/mods/wiki').status_code == 200
