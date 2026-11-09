"""Captured Java project scopes distinguish Gradle/Maven builds from inferred folder areas."""
from copy import deepcopy
from pathlib import Path
import json
import pytest

from atlas.project_discovery import capture_project_scopes
from atlas.project_scopes import assign_code_projects, UNASSIGNED_PROJECT


def _rules(**overrides):
    """Inject one explicit pinned rule without relying on user activation state."""
    rule = {'id': 'java-projects', 'language': 'java', 'extensions': ['.java'],
            'manifestNames': ['pom.xml', 'build.gradle', 'build.gradle.kts'], 'manifestSuffixes': [],
            'sourceRootMarkers': ['src/main/java', 'src/test/java'], 'logicalFolders': True,
            'modId': 'language-java', 'modFingerprint': 'a' * 64, **overrides}
    return {'fingerprint': 'b' * 64, 'projectRules': [rule]}


def _source(root, name):
    """Source contents are irrelevant to path ownership and are never imported."""
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('class Example {}')
    return {'id': name, 'kind': 'method', 'label': 'run()', 'file': name}


def test_missing_raw_gradle_manifest_and_shared_package_folder_areas(tmp_path):
    """One actual Gradle project yields separately labelled logical areas without fake modules."""
    (tmp_path / 'build.gradle').write_text('plugins { id "java" }')
    names = ['src/test/java/org/demo/assertions/Checks.java', 'src/test/java/org/demo/steps/Load.java',
             'src/test/java/org/demo/tests/CostChange.java', 'src/test/java/org/demo/test_data/Rows.java']
    graph = {'nodes': [_source(tmp_path, name) for name in names],
             'edges': [{'source': names[2], 'target': names[0], 'relation': 'calls'}]}
    before = deepcopy(graph)
    captured = capture_project_scopes(graph, tmp_path, _rules())
    assert graph == before and captured['edges'] == graph['edges']
    assert [node['id'] for node in captured['nodes']] == names
    projects = assign_code_projects(captured['nodes'])
    areas = [project for project in projects if project.get('kind') == 'logical-area']
    assert {area['label'] for area in areas} == {'assertions', 'steps', 'tests', 'test_data'}
    assert all(area['confidence'] == 'folder-heuristic' and area['buildManifest'] == 'build.gradle' for area in areas)
    assert len({area['buildProjectId'] for area in areas}) == 1
    assert len([project for project in projects if project.get('kind') == 'build-project']) == 1
    assert sum(project['nodeCount'] for project in projects) == len(names)
    assert all(node['codeProjectId'].startswith('logical-') for node in captured['nodes'])
    # Readers recover the exact captured ownership even after the source tree disappears.
    (tmp_path / 'build.gradle').unlink()
    repeated = assign_code_projects([dict(node) for node in captured['nodes']])
    assert repeated == projects


def test_nested_maven_build_wins_and_similar_directory_prefix_does_not(tmp_path):
    """Nearest complete path boundaries determine build ownership, not substring matches."""
    (tmp_path / 'pom.xml').write_text('<project/>')
    (tmp_path / 'child').mkdir()
    (tmp_path / 'child/pom.xml').write_text('<project/>')
    names = ['child/src/test/java/One.java', 'children/src/test/java/Two.java']
    captured = capture_project_scopes({'nodes': [_source(tmp_path, name) for name in names], 'edges': []},
                                      tmp_path, _rules(logicalFolders=False))
    facts = [node['codeProjectFacts'] for node in captured['nodes']]
    assert facts[0]['manifest'] == 'child/pom.xml'
    assert facts[1]['manifest'] == 'pom.xml'
    assert facts[0]['id'] != facts[1]['id']


def test_custom_source_root_and_ambiguous_manifest_are_explicit(tmp_path):
    """Custom path rules work; competing build files cannot invent a chosen build project."""
    (tmp_path / 'build.gradle').write_text('plugins {}')
    (tmp_path / 'pom.xml').write_text('<project/>')
    nodes = [_source(tmp_path, f'checks/java/acme/{area}/Example.java') for area in ['tests', 'assertions']]
    captured = capture_project_scopes({'nodes': nodes, 'edges': []}, tmp_path,
                                      _rules(sourceRootMarkers=['checks/java']))
    for node in captured['nodes']:
        facts = node['codeProjectFacts']
        assert facts['sourceRoot'] == 'checks/java'
        assert facts['kind'] == 'logical-area'
        assert 'buildProjectId' not in facts


def test_paths_and_symlinks_cannot_discover_external_builds(tmp_path):
    """Path traversal and link markers never read an outside manifest or assign ownership."""
    root = tmp_path / 'project'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'pom.xml').write_text('outside secret')
    (root / 'pom.xml').symlink_to(outside / 'pom.xml')
    node = _source(root, 'src/test/java/Only.java')
    graph = {'nodes': [node, {'id': 'outside', 'file': '../outside/A.java'},
                       {'id': 'absolute', 'file': str(outside / 'A.java')}], 'edges': []}
    captured = capture_project_scopes(graph, root, _rules(logicalFolders=False))
    assign_code_projects(captured['nodes'])
    assert all(node['codeProjectId'] == UNASSIGNED_PROJECT for node in captured['nodes'])
    assert captured['metadata']['projectDiscovery']['manifests'] == 0
    assert captured['metadata']['projectDiscovery']['status'] == 'partial'


def test_discovery_and_rule_budgets_are_explicit(tmp_path, monkeypatch):
    """Bounded scans report incompleteness instead of quietly claiming a complete project map."""
    from atlas import project_discovery
    (tmp_path / 'pom.xml').write_text('<project/>')
    graph = {'nodes': [_source(tmp_path, 'src/test/java/One.java')], 'edges': []}
    monkeypatch.setattr(project_discovery, 'MAX_DIRECTORIES', 1)
    result = capture_project_scopes(graph, tmp_path, _rules())
    coverage = result['metadata']['projectDiscovery']
    assert coverage['limited'] and coverage['status'] == 'partial' and coverage['warnings']


def test_no_active_rules_preserves_legacy_project_assignment(tmp_path):
    """Disabling analysis mods keeps the established graph-manifest fallback."""
    graph = {'nodes': [{'id': 'p', 'file': 'App.csproj'}, {'id': 'm', 'file': 'Service.cs'}], 'edges': []}
    result = capture_project_scopes(graph, tmp_path, {'fingerprint': 'empty', 'projectRules': []})
    projects = assign_code_projects(result['nodes'])
    assert len(projects) == 2
    assert result['nodes'][0]['codeProjectId'] == result['nodes'][1]['codeProjectId']


@pytest.mark.parametrize('language,filename,manifest', [
    ('csharp', 'src/Service.CS', 'Example.CsProj'), ('java', 'src/test/java/Test.java', 'pom.xml'),
    ('python', 'src/example.py', 'pyproject.toml'), ('javascript', 'src/app.js', 'package.json'),
    ('typescript', 'src/app.ts', 'package.json'), ('go', 'internal/app.go', 'go.mod'),
    ('cpp', 'src/app.cpp', 'CMakeLists.txt'), ('kotlin', 'src/test/kotlin/Test.kt', 'pom.xml'),
    ('php', 'src/Example.php', 'composer.json'), ('rust', 'src/lib.rs', 'Cargo.toml'),
    ('swift', 'Sources/Example.swift', 'Package.swift'),
])
def test_each_bundled_language_rule_discovers_its_real_marker(tmp_path, language, filename, manifest):
    """Each configured language uses the same host contract with an independently declared marker."""
    package = Path(__file__).resolve().parents[1] / f'mods/language-{language}/mod.json'
    rule = json.loads(package.read_text())['analysis']['projectRules'][0]
    rule.update(modId=f'language-{language}', modFingerprint='a' * 64, logicalFolders=False)
    (tmp_path / manifest).write_text('marker contents are captured, never executed')
    graph = {'nodes': [_source(tmp_path, filename)], 'edges': []}
    result = capture_project_scopes(graph, tmp_path, {'projectRules': [rule], 'fingerprint': 'b' * 64})
    facts = result['nodes'][0]['codeProjectFacts']
    assert facts['manifest'] == manifest and facts['kind'] == 'build-project'


def test_malformed_captured_evidence_and_identity_do_not_create_project(tmp_path):
    """A provenance string alone cannot launder traversal paths or arbitrary IDs into scopes."""
    node = {'id': 'n', 'file': 'src/A.java', 'codeProjectFacts': {
        'discoveryVersion': 'project-discovery-v1', 'sourceKind': 'snapshot-captured-source',
        'kind': 'build-project', 'id': 'project-' + 'a' * 16, 'path': '.',
        'manifest': '../external/pom.xml', 'modFingerprint': 'bad'}}
    projects = assign_code_projects([node])
    assert node['codeProjectId'] == UNASSIGNED_PROJECT and len(projects) == 1
    (tmp_path / 'pom.xml').write_text('<project/>')
    valid = capture_project_scopes({'nodes': [_source(tmp_path, 'src/A.java')], 'edges': []},
                                   tmp_path, _rules(logicalFolders=False))['nodes'][0]
    for key, value in [('manifest', '../pom.xml'), ('sourceHash', 'bad'), ('modFingerprint', 'bad'),
                       ('id', 'project-' + 'f' * 16), ('path', '/src')]:
        changed = deepcopy(valid)
        changed['codeProjectFacts'][key] = value
        assign_code_projects([changed])
        assert changed['codeProjectId'] == UNASSIGNED_PROJECT


def test_posix_directory_replacement_cannot_redirect_marker_enumeration(tmp_path):
    """A directory handle keeps enumeration inside the already opened repository directory."""
    import os
    from atlas.project_discovery_io import discovery_directory
    if os.name == 'nt':
        pytest.skip('Descriptor traversal is POSIX-specific; marker reads have a separate Windows reader')
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'pom.xml').write_text('inside')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'build.gradle').write_text('outside')
    with discovery_directory(tmp_path, Path('source')) as descriptor:
        source.rename(tmp_path / 'retained')
        source.symlink_to(outside, target_is_directory=True)
        with os.scandir(descriptor) as listing:
            assert {entry.name for entry in listing} == {'pom.xml'}
