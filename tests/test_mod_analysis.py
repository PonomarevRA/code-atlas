"""Analysis packages are declarative, explicitly activated and immutable within a map build."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.mod_analysis import active_analysis_rules, assert_analysis_rules_current
from atlas.mod_models import ModManifest
from atlas.mod_store import ModError, ModStore


def _package(root, identifier='language-java', **analysis):
    """Write one small local package; no source repository content is executable."""
    directory = root / identifier
    directory.mkdir(parents=True)
    manifest = {'apiVersion': 1, 'id': identifier, 'name': 'Java', 'version': '1',
                'analysis': {'apiVersion': 1, **analysis}}
    (directory / 'mod.json').write_text(json.dumps(manifest))
    return directory


def _enable(store, identifier):
    """Activate the content fingerprint exactly as the management API requires."""
    item = next(item for item in store.catalog()['mods'] if item['id'] == identifier)
    store.enable(identifier, item['fingerprint'], True)


def test_analysis_registry_requires_activation_and_rechecks_version(tmp_path):
    """Content edits or deactivation invalidate a pending source-facts publication."""
    directory = _package(tmp_path / 'mods', projectRules=[{'id': 'java-projects', 'language': 'java',
        'extensions': ['.java'], 'manifestNames': ['pom.xml']}])
    store = ModStore(tmp_path, tmp_path / 'empty')
    assert active_analysis_rules(store)['projectRules'] == []
    _enable(store, 'language-java')
    rules = active_analysis_rules(store)
    assert rules['projectRules'][0]['modId'] == 'language-java'
    assert len(rules['projectRules'][0]['modFingerprint']) == 64
    assert_analysis_rules_current(store, rules)
    before = deepcopy(rules)
    (directory / 'notes.txt').write_text('new analysis package revision')
    with pytest.raises(ModError, match='changed'):
        assert_analysis_rules_current(store, rules)
    assert rules == before
    assert active_analysis_rules(store)['projectRules'] == []


@pytest.mark.parametrize('analysis', [
    {'apiVersion': True},
    {'apiVersion': '1'},
    {'apiVersion': 1, 'python': 'run.py'},
    {'apiVersion': 1, 'projectRules': [{'id': 'java', 'language': 'java', 'extensions': ['.java'], 'manifestNames': ['../pom.xml']}]},
    {'apiVersion': 1, 'projectRules': [{'id': 'java', 'language': 'java', 'extensions': ['*.java']}]},
    {'apiVersion': 1, 'testFrameworks': [{'id': 'junit', 'language': 'java', 'annotationRules': [{'name': '.*Test', 'role': 'test'}]}]},
])
def test_analysis_schema_rejects_code_patterns_and_coerced_versions(analysis):
    """A source rule cannot become a script, a regex or an unbounded traversal."""
    with pytest.raises(ValidationError):
        ModManifest.model_validate({'apiVersion': 1, 'id': 'example', 'name': 'Example',
                                    'version': '1', 'analysis': analysis})


def test_bundled_language_and_library_rules_are_valid_and_allure_has_no_tests(tmp_path):
    """Every advertised package has a real schema-valid rule; Allure is metadata/steps only."""
    bundled = Path(__file__).resolve().parents[1] / 'mods'
    store = ModStore(tmp_path, bundled)
    catalog = store.catalog()
    selected = [item for item in catalog['mods'] if item['id'].startswith(('language-', 'framework-'))]
    assert len(selected) == 21
    assert all(item['status'] == 'auto' and not item['enabled'] for item in selected)
    for item in selected:
        if item['id'].startswith('language-'):
            assert item['manifest']['analysis']['projectRules']
    allure = next(item for item in selected if item['id'] == 'framework-allure')
    roles = {rule['role'] for rule in allure['manifest']['analysis']['testFrameworks'][0]['annotationRules']}
    assert roles == {'step', 'metadata'}
    assert allure['manifest']['requires'] == ['language-java']
    nunit = next(item for item in selected if item['id'] == 'framework-nunit')
    nunit_rules = nunit['manifest']['analysis']['testFrameworks'][0]['annotationRules']
    by_name = {rule['name']: rule['role'] for rule in nunit_rules}
    assert by_name['NUnit.Framework.TestCaseAttribute'] == 'test'
    assert by_name['NUnit.Framework.TestCaseSourceAttribute'] == 'test'


def test_analysis_packages_preserve_dependency_activation_contract(tmp_path):
    """Framework packages cannot silently activate their language provider dependency."""
    _package(tmp_path / 'mods')
    directory = _package(tmp_path / 'mods', 'framework-junit5', testFrameworks=[{
        'id': 'junit5', 'language': 'java', 'annotationRules': [{'name': 'org.junit.jupiter.api.Test', 'role': 'test'}]}])
    manifest = json.loads((directory / 'mod.json').read_text())
    manifest['requires'] = ['language-java']
    (directory / 'mod.json').write_text(json.dumps(manifest))
    store = ModStore(tmp_path, tmp_path / 'empty')
    with pytest.raises(ModError, match='dependencies'):
        _enable(store, 'framework-junit5')
    _enable(store, 'language-java')
    _enable(store, 'framework-junit5')
    assert active_analysis_rules(store)['testFrameworks'][0]['id'] == 'junit5'


def test_overlapping_language_providers_cannot_overwrite_captured_ownership(tmp_path):
    """Ambiguous custom packages reject a new build instead of silently winning by catalog order."""
    rule = {'id': 'projects', 'language': 'java', 'extensions': ['.java'], 'manifestNames': ['pom.xml']}
    _package(tmp_path / 'mods', projectRules=[rule])
    _package(tmp_path / 'mods', 'custom-java', projectRules=[rule])
    store = ModStore(tmp_path, tmp_path / 'empty')
    _enable(store, 'language-java')
    _enable(store, 'custom-java')
    with pytest.raises(ModError, match='Conflicting analysis project providers'):
        active_analysis_rules(store)
