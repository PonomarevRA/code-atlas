"""Real palette catalogs remain declarative, fingerprinted, bounded and recoverable."""
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from atlas.color_scheme_models import ColorScheme, TOKEN_NAMES
from atlas.color_schemes import scheme_catalog, scheme_styles, create_scheme_package
from atlas.mod_models import ModManifest
from atlas.mod_store import ModStore, ModError
from atlas.server import create_app
from atlas.mod_analysis import active_analysis_rules


def test_builtin_and_active_mod_revision(tmp_path):
    """A disabled or changed palette cannot override built-ins or reuse a published revision."""
    mods = ModStore(tmp_path, tmp_path/'bundled')
    scheme = ColorScheme(id='ocean', label='Ocean', base='dark', tokens={'accent':'#55ccff'})
    before = scheme_catalog(mods)
    analysis_fingerprint = active_analysis_rules(mods)['fingerprint']
    assert [item['id'] for item in before['schemes']] == ['light', 'dark']
    assert all(set(item['tokens']) == set(TOKEN_NAMES) for item in before['schemes'])
    record = create_scheme_package(mods, scheme)
    assert len(scheme_catalog(mods)['schemes']) == 2
    mods.enable(record['id'], record['fingerprint'], True)
    assert active_analysis_rules(mods)['fingerprint'] == analysis_fingerprint
    catalog = scheme_catalog(mods)
    custom = catalog['schemes'][-1]
    assert custom['id'] == 'mod:theme-ocean:ocean' and custom['tokens']['accent'] == '#55ccff'
    assert custom['tokens']['text'] == before['schemes'][1]['tokens']['text']
    assert len(scheme_catalog(mods, safe=True)['schemes']) == 2
    css = scheme_styles(catalog)
    assert '--atlas-accent:#55ccff' in css and 'color-scheme:dark' in css
    assert css.rfind('mod:theme-ocean:ocean') > css.rfind('data-color-mode="dark"')
    manifest = mods.directory/'theme-ocean/mod.json'
    value = json.loads(manifest.read_text()); value['colorSchemes'][0]['tokens']['accent'] = '#ffffff'; manifest.write_text(json.dumps(value))
    assert len(scheme_catalog(mods)['schemes']) == 2
    with pytest.raises(ModError): create_scheme_package(mods, scheme)
    assert json.loads(manifest.read_text()) == value


@pytest.mark.parametrize('tokens', [{'text':'url(https://invalid.test)'},{'text':'#fff; color:red'},{'unknown':'#fff'},{'text':'#12345678'},{'text':None}])
def test_palette_values_cannot_inject_css(tokens):
    """Reject arbitrary CSS and unknown tokens before publishing a stylesheet."""
    with pytest.raises(ValidationError): ColorScheme(id='example', label='Example', tokens=tokens)


def test_duplicate_ids_rejected():
    """Package-local IDs are unambiguous even when other mods reuse the same scheme name."""
    scheme={'id':'example','label':'Example'}
    with pytest.raises(ValidationError): ModManifest(apiVersion=1,id='example',name='Example',version='1',colorSchemes=[scheme,scheme])


def test_palette_api_creation_and_styles_without_project_reads(tmp_path):
    """Create, explicitly enable and disable a JSON-only package through the real host API."""
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1') as client:
        assert client.get('/api/color-schemes').status_code == 200
        response = client.post('/api/color-schemes',json={'id':'my-colors','label':'My colors','base':'dark','tokens':{'card':'#123456'}})
        assert response.status_code == 201, response.text
        record=response.json(); assert record['enabled'] is False
        assert client.post('/api/mods/'+record['id']+'/activation',json={'enabled':True,'fingerprint':record['fingerprint']}).status_code == 200
        css=client.get('/api/color-schemes/styles.css')
        assert css.status_code == 200 and css.headers['content-type'].startswith('text/css')
        assert '--atlas-card:#123456' in css.text and "style-src 'self'" in css.headers['content-security-policy']
        assert len(client.get('/api/color-schemes?safe=true').json()['schemes']) == 2
        assert client.post('/api/color-schemes',json={'id':'../outside','label':'Invalid'}).status_code == 422
        assert client.post('/api/mods/disable-all',json={}).status_code == 200
        assert 'mod:theme-my-colors' not in client.get('/api/color-schemes/styles.css').text
    assert not (tmp_path/'projects.json').exists()


def test_builtin_reading_contrast():
    """Primary, secondary and syntax text meet 4.5:1 against their intended built-in surfaces."""
    def luminance(color):
        """Convert sRGB components to WCAG relative luminance for opaque six-digit colors."""
        values = [int(color[index:index+2], 16) / 255 for index in (1, 3, 5)]
        linear = [value/12.92 if value <= .04045 else ((value+.055)/1.055)**2.4 for value in values]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    path=Path(__file__).parents[1]/'web/color-schemes.json'
    for scheme in json.loads(path.read_text()):
        tokens=scheme['tokens']
        for foreground, background in [('text','surface'),('muted','surface'),('code','card'),('keyword','card'),
                                      ('string','card'),('number','card'),('accent','surface'),
                                      ('onAccent','accent'),('controlText','control'),('waitingText','waiting')]:
            low, high=sorted([luminance(tokens[foreground]),luminance(tokens[background])])
            assert (high+.05)/(low+.05) >= 4.5, (scheme['id'], foreground, background)


def test_creation_rejects_symlink_and_preserves_outside_directory(tmp_path):
    """A declarative editor cannot write through an unexpected mod-directory symlink."""
    outside=tmp_path/'outside'; outside.mkdir()
    (tmp_path/'mods').symlink_to(outside, target_is_directory=True)
    mods=ModStore(tmp_path,tmp_path/'bundled')
    with pytest.raises(ModError): create_scheme_package(mods, ColorScheme(id='example',label='Example'))
    assert not list(outside.iterdir())
