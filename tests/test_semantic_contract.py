"""Optional providers cannot change baseline identity or overstate static evidence."""
from dataclasses import asdict
import json

import pytest
from pydantic import ValidationError

from atlas.mod_analysis import (active_analysis_rules, active_semantic_rules,
                                analysis_settings_fingerprint, assert_semantic_rules_current,
                                semantic_settings_fingerprint)
from atlas.mod_models import ModManifest
from atlas.mod_store import ModError, ModStore
from atlas.semantic_models import (SemanticAvailability, SemanticContext, SemanticCoverage,
                                   SemanticFact, SemanticResult, SemanticSymbol, SourceSpan,
                                   semantic_availability_from_dict, semantic_result_from_dict)

HASH = 'a' * 64
INVENTORY = {'files': ['src/example.py'], 'truncated': False, 'version': 'test', 'mode': 'all', 'warnings': []}


def _manifest(identifier='semantic-python'):
    """A semantic package claims no project extensions and carries no executable commands."""
    return {'apiVersion': 1, 'id': identifier, 'name': 'Python semantics', 'version': '1',
            'autoActivate': {'extensions': ['.py'], 'manifestNames': []},
            'analysis': {'apiVersion': 1, 'semanticProviders': [
                {'id': 'jedi', 'language': 'python', 'strategy': 'jedi-local-v1',
                 'capabilities': ['definition', 'references']}]}}


def _write_package(root, manifest):
    """Write a declarative application-owned package for the real ModStore path."""
    directory = root / manifest['id']
    directory.mkdir(parents=True)
    path = directory / 'mod.json'
    path.write_text(json.dumps(manifest))
    return path


def _context():
    """Return an explicit single-target context with one exact captured file hash."""
    return SemanticContext('snapshot', 'workspace', '/local/project', 'python-env', HASH,
                           {'src/example.py': HASH}, HASH)


def _result():
    """Create a supported confirmed static binding without implying runtime invocation."""
    span = SourceSpan('src/example.py', 0, 0, 0, 4)
    source = SemanticSymbol('method:a', 'method', 'a', span)
    target = SemanticSymbol('external:b', 'function', 'b', uri='python:package.b')
    return SemanticResult(_context(), 'jedi', HASH, HASH, facts=(
        SemanticFact('reference', source, target, 'confirmed-static', span, 'resolved definition'),),
        coverage={'definition': SemanticCoverage('partial', 1, None, ('dynamic imports unresolved',))})


def test_semantic_only_activation_has_separate_settings_and_keeps_baseline(tmp_path):
    """Discovery, activation, Off and semantic content updates leave source snapshots untouched."""
    store = ModStore(tmp_path, tmp_path / 'empty')
    baseline = active_analysis_rules(store, inventory=INVENTORY)
    before_settings = analysis_settings_fingerprint(store)
    before_semantics = semantic_settings_fingerprint(store)
    path = _write_package(tmp_path / 'mods', _manifest())
    rules = active_semantic_rules(store, inventory=INVENTORY)
    assert rules['semanticProviders'][0]['modId'] == 'semantic-python'
    assert rules['mods'][0]['activation']['mode'] == 'auto'
    assert before_semantics != semantic_settings_fingerprint(store)
    assert active_analysis_rules(store, inventory=INVENTORY) == baseline
    assert before_settings == analysis_settings_fingerprint(store)
    item = next(item for item in store.catalog()['mods'] if item['id'] == 'semantic-python')
    store.enable(item['id'], item['fingerprint'], False)
    assert not active_semantic_rules(store, inventory=INVENTORY)['semanticProviders']
    assert active_analysis_rules(store, inventory=INVENTORY) == baseline
    with pytest.raises(ModError, match='Semantic mods changed'):
        assert_semantic_rules_current(store, rules, inventory=INVENTORY)
    manifest = json.loads(path.read_text())
    manifest['analysis']['semanticProviders'][0]['capabilities'] = ['types']
    path.write_text(json.dumps(manifest))
    assert active_analysis_rules(store, inventory=INVENTORY) == baseline


def test_semantic_conflicts_are_per_language_and_do_not_replace_source_ownership(tmp_path):
    """Two selected Python engines are ambiguous even if package IDs differ."""
    _write_package(tmp_path / 'mods', _manifest())
    _write_package(tmp_path / 'mods', _manifest('another-python'))
    store = ModStore(tmp_path, tmp_path / 'empty')
    with pytest.raises(ModError, match='Conflicting semantic providers for python'):
        active_semantic_rules(store, inventory=INVENTORY)
    assert active_analysis_rules(store, inventory=INVENTORY)['projectRules'] == []


def test_semantic_fingerprint_isolated_for_mixed_declarative_mod():
    """Editing semantic declarations does not change captured source rules of a mixed mod."""
    class Catalog:
        """A fixed enabled catalog isolates scope hashing from manual full-package pin checks."""
        def __init__(self):
            self.item = {'id': 'mixed', 'fingerprint': HASH, 'origin': 'bundled', 'enabled': True,
                         'manifest': _manifest('mixed')}
            self.item['manifest']['requires'] = []
            self.item['manifest']['analysis']['projectRules'] = [
                {'id': 'python', 'language': 'python', 'extensions': ['.py']}]

        def catalog(self, **kwargs):
            """Return the current catalog without project I/O."""
            return {'mods': [self.item]}

        def state(self):
            """Use auto mode so a full-package manual reactivation cannot obscure scope hashing."""
            return {}

    store = Catalog()
    source = active_analysis_rules(store)
    semantic = active_semantic_rules(store)
    store.item['fingerprint'] = 'b' * 64
    store.item['manifest']['analysis']['semanticProviders'][0]['capabilities'] = ['types']
    assert active_analysis_rules(store) == source
    assert active_semantic_rules(store)['fingerprint'] != semantic['fingerprint']


@pytest.mark.parametrize('change', [
    {'command': 'pip install jedi'}, {'python': 'adapter.py'}, {'arguments': ['--unsafe']},
    {'strategy': 'project-code-v1'}, {'language': 'java'}, {'capabilities': ['calls', 'calls']},
])
def test_semantic_manifest_rejects_arbitrary_execution_and_invalid_profiles(change):
    """The declarative surface selects only compatible host strategies and bounded capabilities."""
    manifest = _manifest()
    manifest['analysis']['semanticProviders'][0].update(change)
    with pytest.raises(ValidationError):
        ModManifest.model_validate(manifest)


def test_old_v1_manifests_and_independent_js_ts_profiles_remain_valid():
    """Old manifests need no migration; JS can select the TS backend without a TS mod dependency."""
    old = ModManifest.model_validate({'apiVersion': 1, 'id': 'old', 'name': 'Old', 'version': '1'})
    assert old.analysis is None
    manifest = _manifest('semantic-javascript')
    manifest['analysis']['semanticProviders'] = [
        {'id': 'js', 'language': 'javascript', 'strategy': 'typescript-local-v1'}]
    validated = ModManifest.model_validate(manifest)
    assert validated.requires == []


def test_result_json_roundtrip_preserves_unknown_and_static_evidence():
    """Persistent records preserve null totals, static confidence and exact source identity."""
    result = _result()
    payload = json.loads(json.dumps(result.to_dict()))
    restored = semantic_result_from_dict(payload)
    assert restored == result
    assert restored.coverage['definition'].total is None
    assert restored.facts[0].confidence == 'confirmed-static'
    assert 'runtimeSelectionKnown' not in payload['facts'][0]
    assert 'runtime' not in payload['facts'][0]['confidence']


@pytest.mark.parametrize('field,value', [('start_line', True), ('start_column', -1),
                                        ('end_column', '4'), ('coordinate_kind', 'unknown'),
                                        ('path', '../outside.py')])
def test_invalid_or_coerced_source_spans_are_rejected(field, value):
    """Invalid persisted coordinates cannot accidentally bind another graph node."""
    payload = asdict(SourceSpan('src/example.py', 0, 0, 0, 4))
    payload[field] = value
    with pytest.raises((ValueError, TypeError)):
        SourceSpan(**payload)


def test_coverage_requires_exact_totals_before_claiming_complete():
    """Missing scopes remain unknown; boolean counts and falsely complete records fail closed."""
    for payload in ({'status': 'complete'}, {'status': 'partial', 'analyzed': True},
                    {'status': 'partial', 'analyzed': 2, 'total': 1}):
        with pytest.raises(ValueError):
            SemanticCoverage(**payload)
    with pytest.raises(ValueError, match='complete capability coverage'):
        SemanticResult(_context(), 'jedi', HASH, HASH, status='complete')


def test_unanchored_sources_and_unknown_persisted_fields_fail_closed():
    """An engine result cannot bind a source absent from its captured inventory."""
    payload = _result().to_dict()
    payload['facts'][0]['callsite']['path'] = 'other.py'
    with pytest.raises(ValueError, match='not pinned'):
        semantic_result_from_dict(payload)
    payload = _result().to_dict()
    payload['facts'][0]['runtimeSelectionKnown'] = True
    with pytest.raises(TypeError):
        semantic_result_from_dict(payload)
    payload = _result().to_dict()
    payload['facts'][0]['target'] = None
    with pytest.raises(ValueError, match='requires a target'):
        semantic_result_from_dict(payload)


def test_availability_roundtrip_is_separate_and_does_not_imply_applied_facts():
    """A found engine carries capability discovery and daily expiration, not an applied result."""
    state = SemanticAvailability('available', engine_version='local', engine_fingerprint=HASH,
                                 capabilities=('definition',), checked_at=100, next_check_at=86500)
    assert semantic_availability_from_dict(json.loads(json.dumps(asdict(state)))) == state
    for payload in ({'status': 'PASS'}, {'status': 'available', 'checked_at': True},
                    {'status': 'missing', 'checked_at': 100, 'next_check_at': 0}):
        with pytest.raises(ValueError):
            semantic_availability_from_dict(payload)


def test_semantic_activation_identity_ignores_source_enumeration_order(tmp_path):
    """Producer and reader select the same first-three evidence files for an identical inventory."""
    _write_package(tmp_path / 'mods', _manifest())
    store = ModStore(tmp_path, tmp_path / 'empty')
    files = ['helper.py', 'caller.py', 'other.py', 'last.py']
    left = active_semantic_rules(store, inventory={**INVENTORY, 'files': files})
    right = active_semantic_rules(store, inventory={**INVENTORY, 'files': list(reversed(files))})
    assert left == right
    assert left['fingerprint'] == right['fingerprint']
    assert left['mods'][0]['activation']['matchedFiles'] == sorted(files)[:3]
