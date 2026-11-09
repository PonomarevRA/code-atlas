"""HTML-мод публикует реальные статические факты и не превращает generated HTML в дубли."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest
from pydantic import ValidationError

from atlas.analysis_facts import analysis_preview
from atlas.agent_views import search_nodes
from atlas.enrichment_pipeline import enrich_snapshot
from atlas.graph_context import read_graph_node
from atlas.html_analysis import capture_html
from atlas.html_markup import MarkupParser
from atlas.mod_analysis import active_analysis_rules, assert_analysis_rules_current
from atlas.mod_models import ModManifest
from atlas.mod_store import ModStore, ModError
from atlas.node_description import build_node_description
from atlas.project_file_inventory import project_file_inventory

ROOT = Path(__file__).resolve().parents[1]
HEADER = '<header id="main"><nav aria-label="Main"><a href="index.html">Главная</a><a href="contact.html">Контакты</a><button type="button">Меню</button></nav></header>'


def pages(root):
    """Три авторские страницы имеют одну явно скопированную шапку."""
    for name in ('index.html', 'contact.html', 'services.html'):
        (root / name).write_text('<!doctype html><html><head><title>' + name + '</title></head><body>' + HEADER + '</body></html>')


def context(tmp_path, root):
    """Применяет bundled auto без ручного enable; замораживает inventory для публикации."""
    mods = ModStore(tmp_path / 'state', ROOT / 'mods')
    inventory = project_file_inventory(root)
    rules = active_analysis_rules(mods, inventory=inventory)
    return mods, inventory, rules


def test_html_structure_duplicates_and_mcp_share_captured_facts(tmp_path):
    """Production enrichment сохраняет raw IDs/edges; common search/get/describe видят HTML."""
    root = tmp_path / 'site'
    root.mkdir()
    pages(root)
    mods, inventory, rules = context(tmp_path, root)
    raw = {'nodes': [{'id': 'existing', 'kind': 'external', 'label': 'Existing', 'comment': 'keep'}],
           'edges': [{'source': 'existing', 'target': 'existing', 'relation': 'custom'}], 'metadata': {}}
    before = deepcopy(raw)
    graph = enrich_snapshot(raw, root, analysis_rules=rules, file_inventory=inventory)
    graph['metadata']['snapshotId'] = 'captured'
    assert raw == before
    assert next(n for n in graph['nodes'] if n['id'] == 'existing')['comment'] == 'keep'
    assert raw['edges'][0] in graph['edges']
    duplicates = [n for n in graph['nodes'] if n['kind'] == 'markup_duplicate']
    assert len(duplicates) == 1
    duplicate = duplicates[0]
    assert duplicate['htmlFacts']['occurrenceCount'] == 3
    assert all(item.get('nodeId') for item in duplicate['htmlFacts']['occurrences'])
    assert len([n for n in graph['nodes'] if n.get('htmlFacts', {}).get('role') == 'page']) == 3
    assert 'language-html' in {item['id'] for item in rules['mods']}
    assert next(item for item in rules['mods'] if item['id'] == 'language-html')['activation']['mode'] == 'auto'
    assert analysis_preview(duplicate)['html']['recommendation']
    found = search_nodes(graph, 'статической генерацией')
    assert duplicate['id'] in {n['id'] for n in found['nodes']}
    assert read_graph_node(graph, duplicate['id'])['node']['htmlFacts']['occurrenceCount'] == 3
    description = build_node_description(root, graph, duplicate['id'], 'captured')
    assert description['sourceStatus'] == 'captured'
    assert description['htmlFacts']['runtimeStatus'] == 'unknown'
    assert description['analysisContext']['mods']
    assert_analysis_rules_current(mods, rules, inventory=inventory)


def test_generated_roots_and_unknown_output_never_count_as_authoring_duplicates(tmp_path):
    """Явный generated manifest исключается; неподтверждённый output остаётся unknown."""
    root = tmp_path / 'site'
    root.mkdir()
    pages(root)
    generated = root / 'rendered'
    generated.mkdir()
    pages(generated)
    output = root / 'dist'
    output.mkdir()
    pages(output)
    (root / 'atlas-html.json').write_text(json.dumps({'sourceRoots': ['.'], 'generatedRoots': ['rendered']}))
    _, inventory, rules = context(tmp_path, root)
    graph = capture_html({'nodes': [], 'edges': []}, root, rules, inventory)
    assert graph['metadata']['htmlAnalysis']['excludedFiles'] == 3
    groups = [n for n in graph['nodes'] if n['kind'] == 'markup_duplicate']
    assert len(groups) == 1 and groups[0]['htmlFacts']['occurrenceCount'] == 3
    assert not any(n.get('file', '').startswith('rendered/') for n in graph['nodes'])
    dist = next(n for n in graph['nodes'] if n.get('file') == 'dist/index.html' and n['kind'] == 'file')
    assert dist['htmlFacts']['sourceRole'] == 'unknown-output'


def test_semantic_differences_and_malformed_templates_do_not_receive_exact_duplicate_advice(tmp_path):
    """ARIA, handlers, whitespace и неподдержанные шаблоны не стираются ради совпадения."""
    root = tmp_path / 'site'
    root.mkdir()
    (root / 'a.html').write_text(HEADER)
    (root / 'b.html').write_text(HEADER.replace('aria-label="Main"', 'aria-label="Other"'))
    (root / 'c.html').write_text(HEADER.replace('Меню', 'Меню '))
    (root / 'd.html').write_text(HEADER.replace('type="button"', 'onclick="submit()"'))
    (root / 'template.html').write_text(HEADER + '{{ dynamic }}')
    _, inventory, rules = context(tmp_path, root)
    graph = capture_html({'nodes': [], 'edges': []}, root, rules, inventory)
    assert not any(n['kind'] == 'markup_duplicate' for n in graph['nodes'])
    template = next(n for n in graph['nodes'] if n.get('file') == 'template.html' and n['kind'] == 'file')
    assert template['htmlFacts']['status'] == 'partial'
    parser = MarkupParser('<div><span></div>').parse()
    assert parser.warnings
    assert MarkupParser('<div>' * 200).parse().limited


def test_auto_modes_are_project_scoped_and_manual_off_or_changed_pin_wins(tmp_path):
    """Auto не меняет глобальный enabled и не обходит явное выключение или revision pin."""
    root = tmp_path / 'site'
    root.mkdir()
    pages(root)
    mods, inventory, rules = context(tmp_path, root)
    assert not mods.state()
    html = next(item for item in mods.catalog()['mods'] if item['id'] == 'language-html')
    assert html['status'] == 'auto' and html['analysisMode'] == 'auto' and not html['enabled']
    mods.enable('language-html', html['fingerprint'], False)
    assert not active_analysis_rules(mods, inventory=inventory).get('markupProviders')
    with pytest.raises(ModError, match='changed'):
        assert_analysis_rules_current(mods, rules, inventory=inventory)
    mods.enable('language-html', html['fingerprint'], None)
    assert active_analysis_rules(mods, inventory=inventory)['markupProviders']
    mods.disable_all()
    assert not active_analysis_rules(mods, inventory=inventory)['mods']
    mods.enable('language-html', html['fingerprint'], None)
    mods.set_analysis_auto(True)
    assert active_analysis_rules(mods, inventory=inventory)['markupProviders']
    mods.state_path.write_text(json.dumps({'language-html': '0' * 64}))
    assert not active_analysis_rules(mods, inventory=inventory).get('markupProviders')
    assert not active_analysis_rules(mods, inventory={**inventory, 'files': ['main.py']}).get('markupProviders')


def test_auto_languages_and_dependencies_use_file_types_without_activating_presentation(tmp_path):
    """Одинаковые правила выбора подходят Java/Python и не включают темы/скрипты."""
    mods = ModStore(tmp_path, ROOT / 'mods')
    inventory = {'files': ['src/Test.java', 'build.gradle', 'main.py'], 'version': 'test',
                 'mode': 'fixture', 'truncated': False, 'warnings': []}
    rules = active_analysis_rules(mods, inventory=inventory)
    ids = {item['id'] for item in rules['mods']}
    assert {'language-java', 'language-python', 'build-gradle', 'framework-junit5', 'framework-fastmcp'} <= ids
    assert 'language-html' not in ids and 'readable-cards' not in ids
    java = next(item for item in mods.catalog()['mods'] if item['id'] == 'language-java')
    mods.enable('language-java', java['fingerprint'], False)
    ids = {item['id'] for item in active_analysis_rules(mods, inventory=inventory)['mods']}
    assert 'framework-junit5' not in ids and 'language-python' in ids


def test_automatic_manifest_cannot_load_executable_or_visual_capabilities():
    """Selectors не могут автозагрузить произвольный JS/Python или менять оформление."""
    manifest = json.loads((ROOT / 'mods/language-html/mod.json').read_text())
    for extra in ({'frontend': 'mod.js'}, {'grouping': [{'id': 'x', 'label': 'X'}]},
                  {'theme': {'fill': '#fff'}}, {'autoActivate': {'extensions': ['*.html']}}):
        with pytest.raises(ValidationError):
            ModManifest.model_validate({**manifest, **extra})


def test_inventory_uses_git_ignore_and_never_follows_links(tmp_path):
    """Игнорируемые файлы, node_modules и ссылки не становятся источниками HTML-фактов."""
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / '.gitignore').write_text('private/\n')
    (tmp_path / 'private').mkdir()
    (tmp_path / 'private/hidden.html').write_text(HEADER)
    (tmp_path / 'index.html').write_text(HEADER)
    (tmp_path / 'link.html').symlink_to(tmp_path / 'index.html')
    inventory = project_file_inventory(tmp_path)
    assert inventory['mode'] == 'git-files'
    assert 'index.html' in inventory['files'] and 'private/hidden.html' not in inventory['files']
    _, _, rules = context(tmp_path, tmp_path)
    graph = capture_html({'nodes': [], 'edges': []}, tmp_path, rules, inventory)
    assert 'link.html' in graph['metadata']['htmlAnalysis']['unavailableFiles']
    assert not any(n.get('file') == 'link.html' for n in graph['nodes'])


def test_corrupt_state_disables_auto_instead_of_forgetting_an_explicit_off(tmp_path):
    """Повреждённые настройки не должны восстановить выключенные пользователем моды."""
    mods = ModStore(tmp_path, ROOT / 'mods')
    inventory = {'files': ['index.html'], 'version': 'test', 'mode': 'fixture', 'truncated': False, 'warnings': []}
    for payload in ('broken', '[]', '{"__autoAnalysisDisabled__":"false"}'):
        mods.state_path.write_text(payload)
        assert not active_analysis_rules(mods, inventory=inventory)['mods']
