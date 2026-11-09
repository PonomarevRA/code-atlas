"""Контракты текстовых providers и независимых document mod fingerprints."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.document_mods import active_document_rules, assert_document_rules_current, select_document_provider
from atlas.documents.models import DocumentError
from atlas.documents.providers import available_providers, resolve_provider
from atlas.mod_analysis import active_analysis_rules
from atlas.mod_models import ModManifest
from atlas.mod_store import ModError, ModStore

ROOT = Path(__file__).resolve().parents[1]


def parse(tmp_path, name, text, strategy=None, options=None, context=None):
    """Создаёт captured fixture, разбирает её и проверяет неизменность original bytes."""
    path = tmp_path / name
    path.write_bytes(text.encode('utf-8'))
    before = path.read_bytes()
    provider = resolve_provider(path, strategy=strategy, options=options, context=context)
    result = provider.parse(path, options or {}, context or {})
    assert path.read_bytes() == before
    assert len(provider.runtime_fingerprint) == 64
    return result


def test_markdown_preserves_sections_tables_fences_links_and_source_spans(tmp_path):
    """Setext/ATX уровни, повторы и escaped pipes сохраняются без HTML выполнения."""
    source = ('---\ntitle: Документ\n---\n# Повтор\n\n### Глубже\n\n'
              '| Имя | Значение |\n| --- | --- |\n| a\\|b | **значение** |\n\n'
              '- один\n- два\n\n```sh\necho unsafe\n```\n\n'
              '[ссылка][ref]\n\n[ref]: https://example.invalid/no-fetch\n\n'
              'Повтор\n======\n\n<script>danger()</script>\n')
    result = parse(tmp_path, 'notes.md', source)
    sections = [node for node in result['nodes'] if node['kind'] == 'section']
    assert [(node['label'], node['attributes']['headingLevel']) for node in sections] == [
        ('Повтор', 1), ('Глубже', 3), ('Повтор', 1)]
    assert sections[1]['parentId'] == sections[0]['id']
    assert sections[2]['parentId'] == result['nodes'][0]['id']
    cells = [node for node in result['nodes'] if node['kind'] == 'cell']
    assert [node['text'] for node in cells] == ['Имя', 'Значение', 'a|b', '**значение**']
    assert cells[-1]['attributes']['row'] == 1 and cells[-1]['attributes']['column'] == 1
    assert len([node for node in result['nodes'] if node['kind'] == 'item']) == 2
    codes = [node for node in result['nodes'] if node['kind'] == 'code']
    assert codes[0]['attributes']['syntax'] == 'yaml-front-matter'
    assert codes[1]['text'] == 'echo unsafe\n'
    references = [node for node in result['nodes'] if node['kind'] == 'reference']
    assert len(references) == 2 and references[0]['attributes']['target'].startswith('https://')
    assert any(node['attributes'].get('rawHtml') for node in result['nodes'])
    locator = sections[0]['sourceLocator']
    assert source[locator['characterStart']:locator['characterEnd']].startswith('# Повтор')
    raw = source.encode('utf-8')
    assert raw[locator['byteStart']:locator['byteEnd']].decode('utf-8').startswith('# Повтор')
    assert locator['characterStart'] != locator['byteStart']


def test_yaml_occurrences_cycles_tags_and_utf8_crlf_lexemes(tmp_path):
    """Event graph не разворачивает циклы и не теряет повторяющиеся mapping keys."""
    source = ('%YAML 1.1\r\n---\r\nключ: 001\r\nключ: yes\r\n'
              'cycle: &loop [*loop]\r\ncomplex:\r\n  ? [one, two]\r\n  : !custom tagged\r\n'
              '---\r\n"два": null\r\n')
    result = parse(tmp_path, 'values.yaml', source)
    keys = [node for node in result['nodes'] if node['kind'] == 'key' and node['text'] == 'ключ']
    assert len(keys) == 2
    assert keys[0]['attributes']['duplicateKey'] is False
    assert keys[1]['attributes']['duplicateKey'] is True
    values = [node for node in result['nodes'] if node['kind'] == 'value']
    number = next(node for node in values if node['text'] == '001')
    assert number['attributes']['rawLexeme'] == '001'
    assert number['attributes']['resolvedTag'].endswith(':int')
    tagged = next(node for node in values if node['text'] == 'tagged')
    assert tagged['attributes']['tag'] == '!custom'
    assert tagged['attributes']['rawLexeme'] == '!custom tagged'
    assert result['edges'][0]['relation'] == 'alias_of'
    alias = next(node for node in result['nodes'] if node['id'] == result['edges'][0]['source'])
    target = next(node for node in result['nodes'] if node['id'] == result['edges'][0]['target'])
    assert alias['parentId'] == target['id']
    assert len(result['nodes']) < 30
    locator = number['sourceLocator']
    assert locator['kind'] == 'yaml' and locator['streamIndex'] == 0
    assert source.encode()[locator['byteStart']:locator['byteEnd']] == b'001'
    assert locator['byteStart'] != locator['characterStart']
    assert len([node for node in result['nodes'] if node['attributes'].get('streamIndex') == 1]) == 1


def test_markdown_heading_scope_does_not_escape_blockquote_and_depth_is_bounded(tmp_path):
    """Nested heading остаётся внутри quote и не перехватывает следующий внешний paragraph."""
    result = parse(tmp_path, 'nested.md', '# Outer\n\n> ## Quoted\n>\n> inside\n\noutside\n')
    outer = next(node for node in result['nodes'] if node['label'] == 'Outer')
    quote = next(node for node in result['nodes'] if node['kind'] == 'section' and not node['label'])
    nested = next(node for node in result['nodes'] if node['label'] == 'Quoted')
    outside = next(node for node in result['nodes'] if node['text'] == 'outside')
    assert quote['parentId'] == outer['id']
    assert nested['parentId'] == quote['id']
    assert outside['parentId'] == outer['id']
    with pytest.raises(DocumentError) as error:
        parse(tmp_path, 'deep.md', '> > > > > > text\n', options={'maxDepth': 3})
    assert error.value.code == 'DEPTH_LIMIT'


@pytest.mark.parametrize('text,options,code', [
    ('a: [1, 2, 3]', {'maxNodes': 3}, 'STRUCTURE_LIMIT'),
    ('a: &a [*a, *a]', {'maxAliases': 1}, 'ALIAS_LIMIT'),
    ('%YAML 1.2\n---\na: yes', {}, 'PROFILE_UNSUPPORTED'),
    ('key: [unclosed', {}, 'INVALID_YAML'),
    ('a: x', {'maxInputBytes': 3}, 'INPUT_LIMIT'),
    ('a: x', {'maxTokens': 2}, 'TOKEN_LIMIT'),
])
def test_yaml_budgets_and_explicit_dialect_fail_closed(tmp_path, text, options, code):
    """Неограниченный результат или иной dialect не превращаются в complete дерево."""
    with pytest.raises(DocumentError) as error:
        parse(tmp_path, 'values.yaml', text, options=options)
    assert error.value.code == code


def test_yaml_custom_python_tag_is_data_and_never_calls_constructor(tmp_path):
    """Python object tags хранятся literal tags даже когда SafeLoader construction запрещена."""
    result = parse(tmp_path, 'data.yaml', '!!python/object/apply:os.system ["touch forbidden"]')
    collection = result['nodes'][2]
    assert collection['attributes']['tag'] == 'tag:yaml.org,2002:python/object/apply:os.system'
    assert result['coverage']['status'] == 'complete'
    assert not (tmp_path / 'forbidden').exists()


def test_helm_factory_context_and_static_dot_scope(tmp_path):
    """Generic YAML и chart template имеют один выбранный provider и честный scope."""
    path = tmp_path / 'deployment.yaml'
    assert resolve_provider(path).id == 'document-yaml'
    assert resolve_provider(path, context={'chartContext': True}).id == 'document-helm'
    result = parse(tmp_path, 'deployment.yaml', '{{ .Values.image.tag }}\n'
                   '{{ with .Values.service }}{{ .Values.port }} {{ $.Values.root }}{{ end }}\n'
                   '{{ include "shared" . }}{{ tpl .Values.dynamic . }}{{ lookup "v1" "Secret" "" "" }}',
                   context={'chartContext': True})
    refs = [node for node in result['nodes'] if node['attributes'].get('referenceKind') == 'helm-values']
    assert [node['attributes']['scope'] for node in refs] == [
        'chart-root', 'chart-root', 'unresolved-dot', 'chart-root', 'chart-root']
    assert all(node['attributes']['resolved'] is False for node in refs)
    assert result['coverage']['status'] == 'partial'
    assert result['nodes'][0]['attributes']['rendered'] is False


def test_helm_declared_dependencies_and_values_source_paths(tmp_path):
    """Chart requirements остаются declarations; static values не становятся rendered objects."""
    chart = parse(tmp_path, 'Chart.yaml', 'apiVersion: v2\nname: root\ndependencies:\n'
                  '  - name: child\n    version: 1.2.3\n    repository: https://example.invalid\n')
    edge = next(edge for edge in chart['edges'] if edge['relation'] == 'declares_dependency')
    dependency = next(node for node in chart['nodes'] if node['id'] == edge['target'])
    assert dependency['attributes']['declaredDependency']['name'] == 'child'
    assert dependency['attributes']['availability'] == 'unresolved'
    values = parse(tmp_path, 'values.yaml', 'image:\n  tag: v1\n', context={'chartContext': True})
    tag = next(node for node in values['nodes'] if node.get('text') == 'v1')
    assert tag['attributes']['valuePath'] == ['image', 'tag']


def test_helm_strings_comments_and_dynamic_index_are_not_literal_value_edges(tmp_path):
    """Quoted delimiters и комментарии не создают ложный lexical binding."""
    result = parse(tmp_path, 'helpers.tpl', '{{ printf ".Values.fake }}" }}\n'
                   '{{/* .Values.comment }} */}}\n{{ index .Values "key" }}\n'
                   '{{ .Values.actual }}')
    values = [node for node in result['nodes'] if node['attributes'].get('referenceKind') == 'helm-values']
    assert [node['label'] for node in values] == ['.Values.actual']
    index = next(node for node in result['nodes'] if node['label'] == 'index')
    assert index['attributes']['certainty'] == 'unresolved'


def test_helm_root_variable_rebinding_does_not_claim_chart_root(tmp_path):
    """Go root-variable reassignment переводит последующие $ references в unresolved."""
    result = parse(tmp_path, 'scope.tpl', '{{ $ := .Values.local }}{{ $.Values.port }}{{ $root.Values.unknown }}')
    references = [node for node in result['nodes'] if node['attributes'].get('referenceKind') == 'helm-values']
    assert [node['label'] for node in references] == ['.Values.local', '$.Values.port']
    assert references[-1]['attributes']['scope'] == 'unresolved-dot'
    assert references[-1]['attributes']['certainty'] == 'unresolved'


def test_native_bundled_adapter_fingerprint_does_not_require_source_file(monkeypatch):
    """Frozen PYZ code supplies fingerprint even when unpacked .py paths do not exist."""
    import atlas.documents.providers as providers
    class Loader:
        """Minimal bundled loader returns code without importing the represented module."""
        def get_code(self, name):
            """Return one deterministic stand-in code object for a PYZ entry."""
            return compile('ADAPTER_VERSION = 1', '<bundled>', 'exec')
    class Spec:
        loader = Loader()
    monkeypatch.setattr(providers.util, 'find_spec', lambda name: Spec())
    assert len(providers.runtime_fingerprint('PyYAML', '/missing/provider_yaml.py')) == 64


def test_missing_runtime_dependency_fails_before_worker_import(monkeypatch):
    """Missing asset соответствует capability_unavailable и не вызывает import side effects."""
    import atlas.documents.providers as providers
    def missing(name):
        """Simulate an intentionally absent optional runtime distribution."""
        raise providers.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(providers.metadata, 'version', missing)
    monkeypatch.setattr(providers, 'import_module', lambda *args: pytest.fail('unexpected import'))
    with pytest.raises(DocumentError) as error:
        resolve_provider(Path('x.md'))
    assert error.value.code == 'CAPABILITY_UNAVAILABLE'


def test_provider_discovery_is_metadata_only_and_strategy_allowlist_is_closed(monkeypatch):
    """Capability inspection не импортирует parser и не принимает произвольный module path."""
    import atlas.documents.providers as providers
    import atlas.lnav_runtime as lnav_runtime
    monkeypatch.setattr(providers, 'import_module', lambda *args: pytest.fail('parser import during discovery'))
    monkeypatch.setattr(lnav_runtime, 'capability', lambda: {
        'available': False, 'missingAsset': 'lnav test pack intentionally absent'})
    capabilities = available_providers()
    assert any(row['id'] == 'document-markdown' for row in capabilities)
    assert not any('ocr' in row['strategy'] for row in capabilities)
    assert next(row for row in capabilities if row['id'] == 'logs-lnav')['status'] == 'capability_unavailable'
    with pytest.raises(DocumentError) as error:
        resolve_provider(Path('x.md'), strategy='evil.module:execute')
    assert error.value.code == 'STRATEGY_UNSUPPORTED'


def inventory(*files):
    """Передаёт уже захваченный inventory вместо чтения source из mod selection."""
    return {'files': list(files), 'truncated': False, 'version': 'test', 'mode': 'selected', 'warnings': []}


def test_document_only_mod_changes_never_invalidate_code_rules_and_manual_off_wins(tmp_path):
    """PDF/parser options из document-only пакета не требуют перестройки code map."""
    store = ModStore(tmp_path / 'state', ROOT / 'mods')
    files = inventory('readme.md', 'values.yaml')
    code_before = active_analysis_rules(store, inventory=files)['fingerprint']
    rules = active_document_rules(store, inventory=files)
    assert {rule['strategy'] for rule in rules['documentProviders']} == {
        'document-markdown-v1', 'document-yaml-v1', 'document-helm-v1'}
    item = next(item for item in store.catalog()['mods'] if item['id'] == 'document-markdown')
    store.enable('document-markdown', item['fingerprint'], False)
    assert not any(rule['strategy'] == 'document-markdown-v1' for rule in
                   active_document_rules(store, inventory=files)['documentProviders'])
    assert active_analysis_rules(store, inventory=files)['fingerprint'] == code_before
    with pytest.raises(ModError, match='changed'):
        assert_document_rules_current(store, rules, inventory=files)


def test_document_selection_preserves_manual_off_in_frozen_rules(tmp_path):
    """Explicit strategy не обходит off; Helm не подменяет выключенный generic YAML."""
    store = ModStore(tmp_path / 'state', ROOT / 'mods')
    files = inventory('values.yaml', 'readme.md')
    rules = active_document_rules(store, inventory=files)
    assert select_document_provider(rules, 'any.yaml')['strategy'] == 'document-yaml-v1'
    assert select_document_provider(rules, 'any.yaml', {'chartContext': True})['strategy'] == 'document-helm-v1'
    item = next(item for item in store.catalog()['mods'] if item['id'] == 'document-yaml')
    store.enable(item['id'], item['fingerprint'], False)
    frozen = active_document_rules(store, inventory=files)
    assert select_document_provider(frozen, 'any.yaml') is None
    assert select_document_provider(frozen, 'any.yaml', strategy='document-yaml-v1') is None
    assert select_document_provider(frozen, 'any.yaml', {'chartContext': True})['strategy'] == 'document-helm-v1'
    # Existing immutable selection remains a snapshot, not a live mutable settings view.
    assert select_document_provider(rules, 'any.yaml')['strategy'] == 'document-yaml-v1'


def test_binary_profiles_auto_select_but_log_mining_requires_manual_profile(tmp_path):
    """Наличие logs не включает Drain3/lnav; ручное включение не разрешает ambiguity молча."""
    store = ModStore(tmp_path / 'state', ROOT / 'mods')
    files = inventory('a.docx', 'b.xlsx', 'c.pptx', 'd.pdf', 'events.log')
    rules = active_document_rules(store, inventory=files)
    strategies = {rule['strategy'] for rule in rules['documentProviders']}
    assert strategies == {'document-docx-v1', 'document-xlsx-v1', 'document-pptx-v1',
                          'document-pdf-basic-v1', 'logs-structured-v1'}
    catalog = store.catalog()['mods']
    for identifier in ('logs-drain3', 'logs-lnav'):
        item = next(item for item in catalog if item['id'] == identifier)
        assert item['analysisMode'] == 'manual' and not item['autoAnalysisEligible']
    drain = next(item for item in catalog if item['id'] == 'logs-drain3')
    store.enable(drain['id'], drain['fingerprint'], True)
    frozen = active_document_rules(store, inventory=files)
    with pytest.raises(ModError, match='explicit profile'):
        select_document_provider(frozen, 'events.log')
    assert select_document_provider(frozen, 'events.log', strategy='logs-drain3-v1')['modId'] == 'logs-drain3'


def test_parser_option_change_changes_only_document_fingerprint(tmp_path):
    """Host-valid options и prepublish recheck независимы от code map analysis settings."""
    directory = tmp_path / 'bundled' / 'document-test'
    directory.mkdir(parents=True)
    manifest = {'apiVersion': 1, 'id': 'document-test', 'name': 'Test', 'version': '1',
                'analysis': {'apiVersion': 1, 'documentProviders': [
                    {'id': 'yaml', 'strategy': 'document-yaml-v1', 'extensions': ['.yaml'],
                     'options': {'maxAliases': 10}}]}}
    path = directory / 'mod.json'
    path.write_text(json.dumps(manifest))
    store = ModStore(tmp_path / 'state', tmp_path / 'bundled')
    files = inventory('a.yaml')
    original = active_document_rules(store, inventory=files)
    code = active_analysis_rules(store, inventory=files)['fingerprint']
    manifest['analysis']['documentProviders'][0]['options']['maxAliases'] = 5
    path.write_text(json.dumps(manifest))
    assert active_document_rules(store, inventory=files)['fingerprint'] != original['fingerprint']
    assert active_analysis_rules(store, inventory=files)['fingerprint'] == code
    with pytest.raises(ModError, match='changed'):
        assert_document_rules_current(store, original, inventory=files)


def test_document_schema_rejects_commands_unknown_options_and_duplicate_rules():
    """Declarations допускают только versioned adapters и проверяемые finite options."""
    base = {'apiVersion': 1, 'id': 'document-example', 'name': 'Example', 'version': '1',
            'analysis': {'apiVersion': 1, 'documentProviders': [
                {'id': 'example', 'strategy': 'document-yaml-v1', 'extensions': ['.yaml']}]}}
    for patch in [{'strategy': 'run.py'}, {'options': {'command': 'execute'}}, {'extensions': ['*.yaml']}]:
        document = json.loads(json.dumps(base))
        document['analysis']['documentProviders'][0].update(patch)
        with pytest.raises(ValidationError):
            ModManifest.model_validate(document)
    base['analysis']['documentProviders'].append(base['analysis']['documentProviders'][0])
    with pytest.raises(ValidationError, match='Duplicate analysis rule id'):
        ModManifest.model_validate(base)
