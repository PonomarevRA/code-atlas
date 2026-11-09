"""Contract checks for the shipped mod documentation and its Atlas section links."""

import json
from pathlib import Path
import re

from fastapi.testclient import TestClient

from atlas.server import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_every_bundled_mod_wiki_is_readable_without_activation(tmp_path):
    """
    Read every shipped page through the public API without enabling mod code.

    Args:
        tmp_path: Isolated application data directory supplied by pytest.

    The static guide owns section IDs. Package bytes remain the source of each
    page, and neither documentation discovery nor reading may write activation.
    """
    guide = json.loads((ROOT / 'web/project-wiki.json').read_text())
    sections = {page['id'] for page in guide['pages']}
    manifests = {}
    for path in (ROOT / 'mods').glob('*/mod.json'):
        manifest = json.loads(path.read_text())
        assert manifest.get('wiki'), f"Missing wiki for {manifest['id']}"
        manifests[manifest['id']] = manifest

    app = create_app(tmp_path / 'data')
    mods = app.state.store.mods
    before_state = mods.state()
    with TestClient(app, base_url='http://127.0.0.1') as client:
        response = client.get('/api/mods/wiki')
        assert response.status_code == 200, response.text
        catalog = response.json()
        assert catalog['truncated'] is False
        assert {item['id'] for item in catalog['mods']} == set(manifests)
        for item in catalog['mods']:
            assert item['section'] in sections
            assert not item['enabled']
            for page in item['pages']:
                result = client.get(
                    f"/api/mods/{item['id']}/wiki/{page['id']}",
                    params={'fingerprint': item['fingerprint']},
                )
                assert result.status_code == 200, result.text
                document = result.json()
                source = ROOT / 'mods' / item['id'] / page['path']
                assert document['text'] == source.read_text(encoding='utf-8')
                assert document['fingerprint'] == item['fingerprint']
                assert document['modId'] == item['id']
                assert document['pageId'] == page['id']
                assert document['section'] == item['section']
                assert re.search(r'^#\s+\S', document['text'], re.MULTILINE)
    assert mods.state() == before_state
    assert not mods.state_path.exists()


def test_static_guide_links_resolve_to_shipped_chapters():
    """
    Check local chapter references in the actual offline bundle.

    Wiki publication and the embedded reader share these source pages; a
    navigation link must resolve without a running mod API or semantic model.
    """
    guide = json.loads((ROOT / 'web/project-wiki.json').read_text())
    pages = {page['id']: page for page in guide['pages']}
    assert 'Mods-and-Wiki' in pages
    assert 'Chunked-Analysis' in pages
    for page in pages.values():
        assert page['text'] == (ROOT / page['source']).read_text(encoding='utf-8')
        for target in re.findall(r'\]\(([A-Za-z][A-Za-z0-9-]*)\.md\)', page['text']):
            assert target in pages, f"{page['id']} links to missing {target}"
