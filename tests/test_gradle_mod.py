"""Gradle discovery is an independently activated build provider, shared across languages."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.mod_analysis import active_analysis_rules, assert_analysis_rules_current
from atlas.mod_models import ModManifest
from atlas.mod_store import ModError, ModStore
from atlas.project_discovery import capture_project_scopes
from atlas.project_scopes import assign_code_projects


def _store(tmp_path):
    """Use real bundled package validation and activation rather than a registry mock."""
    return ModStore(tmp_path / 'state', Path(__file__).resolve().parents[1] / 'mods')


def _enable(store, identifier):
    """Pin the exact package content fingerprint, including ordinary mod dependencies."""
    package = next(item for item in store.catalog()['mods'] if item['id'] == identifier)
    store.enable(identifier, package['fingerprint'], True)


def _source(root, name):
    """Create only static source text; no Gradle wrapper, interpreter or JVM is run."""
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('class Example {}')
    return {'id': name, 'file': name, 'label': 'Example', 'line': 1}


def _custom_build(store, identifier, languages, names=(), suffixes=()):
    """Create a valid declarative conflicting provider to exercise registry validation."""
    directory = store.directory / identifier
    directory.mkdir(parents=True)
    manifest = {'apiVersion': 1, 'id': identifier, 'name': identifier, 'version': '1',
                'analysis': {'apiVersion': 1, 'buildRules': [{'id': identifier, 'languages': languages,
                    'manifestNames': list(names), 'manifestSuffixes': list(suffixes)}]}}
    (directory / 'mod.json').write_text(json.dumps(manifest))
    _enable(store, identifier)


def test_gradle_has_independent_activation_and_language_mods_keep_only_maven(tmp_path):
    store = _store(tmp_path)
    _enable(store, 'language-java')
    language_only = active_analysis_rules(store)
    assert language_only['buildRules'] == []
    java = language_only['projectRules'][0]
    assert java['manifestNames'] == ['pom.xml']
    _enable(store, 'build-gradle')
    active = active_analysis_rules(store)
    build, = active['buildRules']
    assert build['modId'] == 'build-gradle' and len(build['modFingerprint']) == 64
    assert build['languages'] == ['java', 'kotlin']
    assert build['manifestNames'] == ['build.gradle', 'build.gradle.kts']
    assert active['fingerprint'] != language_only['fingerprint']
    assert_analysis_rules_current(store, active)
    _enable(store, 'language-kotlin')
    assert next(item for item in active_analysis_rules(store)['projectRules'] if item['language'] == 'kotlin')['manifestNames'] == ['pom.xml']


def test_gradle_build_provenance_and_language_folder_provenance_remain_separate(tmp_path):
    store = _store(tmp_path)
    for identifier in ('language-java', 'build-gradle'):
        _enable(store, identifier)
    root = tmp_path / 'repository'
    root.mkdir()
    (root / 'build.gradle').write_text("plugins { id 'java' } // never executed")
    graph = {'nodes': [_source(root, f'src/test/java/sample/{folder}/Example.java')
                       for folder in ('tests', 'assertions')], 'edges': []}
    original = deepcopy(graph)
    captured = capture_project_scopes(graph, root, active_analysis_rules(store))
    assert graph == original
    areas = [node['codeProjectFacts'] for node in captured['nodes']]
    assert all(item['kind'] == 'logical-area' and item['modId'] == 'language-java' for item in areas)
    assert all(item['buildProject']['modId'] == 'build-gradle' for item in areas)
    assert all(item['buildManifest'] == 'build.gradle' for item in areas)
    assert len({item['buildProjectId'] for item in areas}) == 1
    projects = assign_code_projects(captured['nodes'])
    assert len([item for item in projects if item.get('kind') == 'build-project']) == 1


def test_one_gradle_marker_shared_by_java_and_kotlin_is_one_build(tmp_path):
    store = _store(tmp_path)
    for identifier in ('language-java', 'language-kotlin', 'build-gradle'):
        _enable(store, identifier)
    root = tmp_path / 'repository'
    root.mkdir()
    (root / 'build.gradle.kts').write_text('plugins { java; kotlin("jvm") }')
    graph = {'nodes': [_source(root, 'src/main/java/sample/App.java'),
                       _source(root, 'src/main/kotlin/sample/Helper.kt')], 'edges': []}
    captured = capture_project_scopes(graph, root, active_analysis_rules(store))
    builds = [item for item in captured['metadata']['projectDiscovery']['projects'] if item['kind'] == 'build-project']
    build, = builds
    assert build['manifest'] == 'build.gradle.kts' and build['modId'] == 'build-gradle'
    assert build['languages'] == ['java', 'kotlin'] and build['language'] is None
    assert captured['nodes'][0]['codeProjectFacts']['id'] == captured['nodes'][1]['codeProjectFacts']['id']
    assigned = assign_code_projects(captured['nodes'])
    assert len([item for item in assigned if item.get('kind') == 'build-project']) == 1


def test_gradle_works_with_kotlin_only_and_does_not_require_java(tmp_path):
    store = _store(tmp_path)
    _enable(store, 'build-gradle')
    only_build = active_analysis_rules(store)
    assert not only_build['projectRules'] and only_build['buildRules']
    root = tmp_path / 'repository'
    root.mkdir()
    (root / 'build.gradle.kts').write_text('plugins {}')
    graph = {'nodes': [_source(root, 'code/Example.kt')], 'edges': []}
    assert not capture_project_scopes(graph, root, only_build)['metadata']['projectDiscovery']['projects']
    _enable(store, 'language-kotlin')
    captured = capture_project_scopes(graph, root, active_analysis_rules(store))
    assert captured['nodes'][0]['codeProjectFacts']['modId'] == 'build-gradle'


@pytest.mark.parametrize('first_names,first_suffixes,second_names,second_suffixes', [
    (['build.gradle'], [], ['build.gradle'], []),
    ([], ['.gradle'], [], ['.gradle']),
    (['build.gradle'], [], [], ['.gradle']),
    ([], ['.gradle'], ['build.gradle'], []),
])
def test_conflicting_build_claims_for_same_language_are_rejected(tmp_path, first_names, first_suffixes, second_names, second_suffixes):
    store = _store(tmp_path)
    _custom_build(store, 'first-build', ['java'], first_names, first_suffixes)
    _custom_build(store, 'second-build', ['java'], second_names, second_suffixes)
    with pytest.raises(ModError, match='Conflicting analysis build providers for java'):
        active_analysis_rules(store)


def test_same_marker_for_disjoint_languages_is_not_a_conflict(tmp_path):
    store = _store(tmp_path)
    _custom_build(store, 'java-build', ['java'], ['custom.build'])
    _custom_build(store, 'kotlin-build', ['kotlin'], ['custom.build'])
    assert len(active_analysis_rules(store)['buildRules']) == 2


def test_disjoint_provider_claims_for_one_physical_build_fail_closed(tmp_path):
    store = _store(tmp_path)
    for identifier in ('language-java', 'language-kotlin'):
        _enable(store, identifier)
    _custom_build(store, 'java-build', ['java'], ['custom.build'])
    _custom_build(store, 'kotlin-build', ['kotlin'], ['custom.build'])
    root = tmp_path / 'repository'
    root.mkdir()
    (root / 'custom.build').write_text('not executable')
    graph = {'nodes': [_source(root, 'code/App.java'), _source(root, 'code/Helper.kt')], 'edges': []}
    captured = capture_project_scopes(graph, root, active_analysis_rules(store))
    discovery = captured['metadata']['projectDiscovery']
    assert discovery['status'] == 'partial' and discovery['warnings']
    assert not any(item['kind'] == 'build-project' for item in discovery['projects'])
    assert all(not (node.get('codeProjectFacts') or {}).get('buildManifest') for node in captured['nodes'])
    assert all((node.get('codeProjectFacts') or {}).get('kind') != 'build-project' for node in captured['nodes'])


def test_mixed_java_kotlin_legacy_pom_does_not_publish_last_provider_winner(tmp_path):
    store = _store(tmp_path)
    for identifier in ('language-java', 'language-kotlin'):
        _enable(store, identifier)
    root = tmp_path / 'repository'
    root.mkdir()
    (root / 'pom.xml').write_text('<project/>')
    graph = {'nodes': [_source(root, 'src/main/java/sample/App.java'),
                       _source(root, 'src/main/kotlin/sample/Helper.kt')], 'edges': []}
    captured = capture_project_scopes(graph, root, active_analysis_rules(store))
    assert captured['metadata']['projectDiscovery']['status'] == 'partial'
    assert not any(item['kind'] == 'build-project' for item in captured['metadata']['projectDiscovery']['projects'])
    assigned = assign_code_projects(captured['nodes'])
    assert not any(item.get('kind') == 'build-project' for item in assigned)


def test_ambiguous_nested_build_blocks_fallback_to_valid_ancestor(tmp_path):
    store = _store(tmp_path)
    for identifier in ('language-java', 'language-kotlin', 'build-gradle'):
        _enable(store, identifier)
    _custom_build(store, 'java-build', ['java'], ['custom.build'])
    _custom_build(store, 'kotlin-build', ['kotlin'], ['custom.build'])
    root = tmp_path / 'repository'
    root.mkdir()
    (root / 'build.gradle').write_text('plugins {}')
    (root / 'child').mkdir()
    (root / 'child/custom.build').write_text('ambiguous nested boundary')
    (root / 'child/inner').mkdir()
    (root / 'child/inner/build.gradle.kts').write_text('valid closer nested boundary')
    graph = {'nodes': [_source(root, 'child/src/App.java'), _source(root, 'child/src/Helper.kt'),
                       _source(root, 'child/inner/code/Inner.kt'), _source(root, 'code/Root.java')], 'edges': []}
    captured = capture_project_scopes(graph, root, active_analysis_rules(store))
    assert captured['metadata']['projectDiscovery']['status'] == 'partial'
    assert all((node.get('codeProjectFacts') or {}).get('kind') != 'build-project' for node in captured['nodes'][:2])
    assert all(not (node.get('codeProjectFacts') or {}).get('buildManifest') for node in captured['nodes'][:2])
    assert all(node['codeProjectFacts']['path'] == 'child' and node['codeProjectFacts']['boundaryStatus'] == 'ambiguous-build-providers'
               for node in captured['nodes'][:2])
    assert all(node['codeProjectFacts']['label'] == 'child · проект не определён' for node in captured['nodes'][:2])
    assert captured['nodes'][2]['codeProjectFacts']['manifest'] == 'child/inner/build.gradle.kts'
    assign_code_projects(captured['nodes'])
    assert all(node['codeProjectId'].startswith('logical-') for node in captured['nodes'][:2])
    assert captured['nodes'][2]['codeProjectId'] == captured['nodes'][2]['codeProjectFacts']['id']
    assert captured['nodes'][3]['codeProjectId'] == captured['nodes'][3]['codeProjectFacts']['id']
    assert captured['nodes'][3]['codeProjectFacts']['manifest'] == 'build.gradle'


@pytest.mark.parametrize('rule', [
    {'id': 'gradle', 'languages': [], 'manifestNames': ['build.gradle']},
    {'id': 'gradle', 'languages': ['java', 'java'], 'manifestNames': ['build.gradle']},
    {'id': 'gradle', 'languages': ['unknown'], 'manifestNames': ['build.gradle']},
    {'id': 'gradle', 'languages': ['java'], 'manifestNames': ['../build.gradle']},
    {'id': 'gradle', 'languages': ['java'], 'manifestNames': ['**/build.gradle']},
    {'id': 'gradle', 'languages': ['java'], 'manifestNames': ['build.gradle'], 'command': './gradlew'},
    {'id': 'gradle', 'languages': ['java']},
])
def test_build_rule_schema_is_bounded_and_declarative(rule):
    with pytest.raises(ValidationError):
        ModManifest.model_validate({'apiVersion': 1, 'id': 'bad-build', 'name': 'Bad', 'version': '1',
                                    'analysis': {'apiVersion': 1, 'buildRules': [rule]}})


def test_legacy_language_markers_work_without_a_build_provider(tmp_path):
    root = tmp_path / 'repository'
    root.mkdir()
    (root / 'build.gradle').write_text('plugins {}')
    graph = {'nodes': [_source(root, 'src/test/java/Example.java')], 'edges': []}
    legacy = {'projectRules': [{'id': 'legacy-java', 'language': 'java', 'extensions': ['.java'],
        'manifestNames': ['build.gradle'], 'modId': 'legacy-language', 'modFingerprint': 'a' * 64}]}
    captured = capture_project_scopes(graph, root, legacy)
    assert captured['nodes'][0]['codeProjectFacts']['modId'] == 'legacy-language'
    assert captured['nodes'][0]['codeProjectFacts']['language'] == 'java'
