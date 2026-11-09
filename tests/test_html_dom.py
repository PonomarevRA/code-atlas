"""Проверки HTML5 recovery, точных source ranges и CSS-контракта без C-парсера."""
from copy import deepcopy
from pathlib import Path
import pytest

from atlas.html_markup import MarkupParser
from atlas.html_analysis import capture_html
from atlas.html_selectors import query_html, HtmlQueryError
from atlas.mod_store import ModStore
from atlas.mod_analysis import active_analysis_rules
from atlas.project_file_inventory import project_file_inventory

ROOT = Path(__file__).resolve().parents[1]


def html_graph(tmp_path, text):
    """Строит production HTML-слой с auto-модом в изолированном проекте."""
    root = tmp_path / 'site'
    root.mkdir()
    (root / 'index.html').write_text(text, encoding='utf-8')
    inventory = project_file_inventory(root)
    rules = active_analysis_rules(ModStore(tmp_path / 'state', ROOT / 'mods'), inventory=inventory)
    graph = capture_html({'nodes': [], 'edges': [], 'metadata': {'snapshotId': 's'}}, root, rules, inventory)
    page = next(n for n in graph['nodes'] if n['kind'] == 'file')
    return graph, page


def test_html5_optional_end_tags_and_implicit_table_nodes():
    """li становятся соседями; tbody создаётся HTML5-алгоритмом, а не source lexer."""
    parser = MarkupParser('<ul><li>A<li>B</ul><table><tr><td>X</table>').parse()
    elements = parser.elements
    li = [item for item in elements if item.tag == 'li']
    assert len(li) == 2 and li[0].parent == li[1].parent
    tbody = next(item for item in elements if item.tag == 'tbody')
    assert tbody.start is None and tbody.range_status == 'generated'
    tr = next(item for item in elements if item.tag == 'tr')
    assert elements[tr.parent] is tbody
    assert not parser.limited
    many = MarkupParser('<ul>' + '<li>x' * 300 + '</ul>').parse()
    assert not many.limited and len([e for e in many.elements if e.tag == 'li']) == 300


def test_foster_parenting_adoption_agency_raw_text_and_namespaces(tmp_path):
    """Невалидная вложенность исправляется; script/textarea не создают ложные элементы."""
    text = '<div><table><p id="foster">X</p><tr><td>x</table><p><b>A<i>B</b>C</i></div><script>"<a id=fake>"</script><textarea><b>text</b></textarea><svg><circle id="dot"/></svg>'
    graph, page = html_graph(tmp_path, text)
    nodes = {n['id']: n for n in graph['nodes']}
    p = query_html(graph, 's', page['id'], '#foster')['nodes'][0]
    assert nodes[nodes[p['id']]['htmlFacts']['parentNodeId']]['label'].startswith('div')
    assert query_html(graph, 's', page['id'], '#fake')['total'] == 0
    assert query_html(graph, 's', page['id'], 'textarea > b')['total'] == 0
    assert query_html(graph, 's', page['id'], 'svg|circle')['total'] == 1
    assert len(query_html(graph, 's', page['id'], 'p > i')['nodes']) == 1
    assert any(n['htmlFacts'].get('generated') for n in graph['nodes'] if n['kind'] == 'markup')


def test_original_unicode_crlf_offsets_exact_duplicate_evidence(tmp_path):
    """Source ranges индексируют исходные символы, не нормализованный stream или UTF-8 bytes."""
    text = '<!doctype html>\r\n<html><head><title>A &amp; B</title></head>\r<body><section id="😀">Привет\r\n<b>мир</b></section></body></html>'
    parser = MarkupParser(text).parse()
    section = next(e for e in parser.elements if e.tag == 'section')
    assert text[section.start:section.end] == '<section id="😀">Привет\r\n<b>мир</b></section>'
    assert section.line == 3 and section.range_status == 'exact'
    assert ''.join(parser.title) == 'A & B'
    html = parser.elements[0]
    assert html.start == text.index('<html>')


def test_css_selectors_use_captured_tree_and_preserve_order(tmp_path):
    """CSS4 structural selectors, namespaces and :empty работают на неизменном снимке."""
    graph, page = html_graph(tmp_path, '<nav id="menu"><a class="x" href="/a">A</a><a class="x" href="/b">B</a><i></i><i> </i></nav>')
    before = deepcopy(graph)
    result = query_html(graph, 's', page['id'], 'nav#menu > a.x[href^="/"]:nth-child(2), i:empty')
    assert [n['label'] for n in result['nodes']] == ['a', 'i']
    assert query_html(graph, 's', page['id'], 'nav:has(> a[href="/b"])')['total'] == 1
    assert query_html(graph, 's', page['id'], ':is(a, i):not(:empty)')['total'] == 3
    all_nodes = query_html(graph, 's', page['id'], '*', limit=2)
    assert all_nodes['nextOffset'] == 2
    assert all_nodes['runtimeStatus'] == 'unknown'
    for node in graph['nodes']:
        if node['kind'] == 'markup':
            own = query_html(graph, 's', page['id'], node['htmlFacts']['cssSelector'])
            assert [item['id'] for item in own['nodes']] == [node['id']]
    (tmp_path / 'site/index.html').unlink()
    assert query_html(graph, 's', page['id'], 'a')['total'] == 2
    assert graph == before


@pytest.mark.parametrize('selector', ['a:hover', 'a::before', 'a[', ':made-up', ':not(:focus)', 'a:has(b):has(c)', ':is(' * 12 + 'a' + ')' * 12])
def test_css_invalid_dynamic_and_expensive_queries_are_explicit(tmp_path, selector):
    """Ошибка не маскируется отсутствием совпадений и не исполняет селектор как код."""
    graph, page = html_graph(tmp_path, '<a href="x">x</a>')
    with pytest.raises(HtmlQueryError):
        query_html(graph, 's', page['id'], selector)


def test_budget_and_old_snapshots_remain_explicit(tmp_path):
    """Нет фиктивного полного дерева при лимите; старый снимок требует обновления."""
    parser = MarkupParser('<div>' * 150).parse()
    assert parser.limited and not parser.elements
    assert MarkupParser('<p>' + 'x' * 1024 * 1024).parse().limited
    graph, page = html_graph(tmp_path, '<main>x</main>')
    graph['metadata'].pop('htmlDocuments')
    with pytest.raises(HtmlQueryError, match='Обновите карту'):
        query_html(graph, 's', page['id'])


def test_http_contract_snapshot_validation_and_missing_tree(tmp_path):
    """Production HTTP route проверяет snapshot, поля и runtime-selector отдельно."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from atlas.html_api import register_html_routes
    from atlas.store import StoreError
    graph, page = html_graph(tmp_path, '<table><tr><td>x</table>')

    class CapturedStore:
        def comparison_snapshot(self, project_id):
            assert project_id == 'p'
            return tmp_path / 'nonexistent', graph

        def assert_snapshot(self, project_id, snapshot_id):
            assert project_id == 'p' and snapshot_id == 's'

    app = FastAPI()
    @app.exception_handler(StoreError)
    async def store_error(request: Request, error):
        return JSONResponse({'error': str(error)}, status_code=error.status)
    register_html_routes(app, CapturedStore())
    with TestClient(app) as client:
        body = {'snapshotId': 's', 'nodeId': page['id'], 'selector': 'table > tbody > tr > td'}
        result = client.post('/api/projects/p/html-query', json=body)
        assert result.status_code == 200 and result.json()['total'] == 1
        assert client.post('/api/projects/p/html-query', json={**body, 'snapshotId': 'old'}).status_code == 409
        assert client.post('/api/projects/p/html-query', json={**body, 'selector': ':hover'}).status_code == 422
        assert client.post('/api/projects/p/html-query', json={**body, 'offset': True}).status_code == 422
        assert client.post('/api/projects/p/html-query', json={**body, 'unexpected': 'x'}).status_code == 422


def test_source_ranges_not_claimed_for_html5_inserted_and_ignored_tokens(tmp_path):
    """Второй html не заменяет source position первого; восстановленные i без диапазона."""
    text = '<html><head></head><body><b><i>x</b>y</i><html lang="ru"></body></html>'
    graph, page = html_graph(tmp_path, text)
    nodes = query_html(graph, 's', page['id'], 'html, body > i')['nodes']
    assert nodes[0]['htmlFacts']['start'] == 0
    assert nodes[1]['htmlFacts']['generated'] and nodes[1]['htmlFacts']['start'] is None


def test_css_complexity_budget_counts_escaped_pseudo_names(tmp_path):
    """CSS escaping не обходит ограничение :has; строки атрибутов не псевдоклассы."""
    graph, page = html_graph(tmp_path, '<main>' + '<p>x</p>' * 1100 + '</main>')
    with pytest.raises(HtmlQueryError, match='1000'):
        query_html(graph, 's', page['id'], r'main:h\61s(p)')
    assert query_html(graph, 's', page['id'], '[title=":hover"]')['total'] == 0


def test_template_content_and_resource_limits_do_not_claim_browser_dom(tmp_path):
    """Неподдержанный template fragment и comment/error floods явно ограничены."""
    graph, page = html_graph(tmp_path, '<template><p>x</p></template>')
    assert page['htmlFacts']['unsupported'] == ['template-content']
    with pytest.raises(HtmlQueryError, match='template-content'):
        query_html(graph, 's', page['id'], 'template p')
    assert MarkupParser('<!-- x -->' * 6000).parse().limited
    assert MarkupParser('<\0' * 10000).parse().limited


def test_self_closing_html_and_multiline_source_end_positions():
    """Slash не закрывает обычный HTML-элемент; void/foreign ranges сохраняют строки."""
    text = '<div/>abc</div><img\n src="x"\n><svg><circle\n/></svg>'
    parser = MarkupParser(text).parse()
    div = next(e for e in parser.elements if e.tag == 'div')
    assert div.range_status == 'start-tag-only' and div.end is None
    image = next(e for e in parser.elements if e.tag == 'img')
    assert image.end_line == 3
    circle = next(e for e in parser.elements if e.tag == 'circle')
    assert circle.end_line == 4 and circle.range_status == 'exact'


def test_css_large_numbers_return_validation_error_instead_of_internal_error(tmp_path):
    """Предел int conversion Python сохраняется и объясняется как ошибка запроса."""
    graph, page = html_graph(tmp_path, '<main>x</main>')
    with pytest.raises(HtmlQueryError, match='бюджет'):
        query_html(graph, 's', page['id'], ':nth-child(' + '9' * 5000 + ')')


def test_foreign_camel_case_selectors_and_generated_paths(tmp_path):
    """HTML регистронезависим, SVG foreignObject case-sensitive; каждый CSS path уникален."""
    graph, page = html_graph(tmp_path, '<svg><foreignObject><DIV class="embedded">x</DIV></foreignObject><linearGradient id="paint"/><circle/></svg><math><mi>x</mi></math>')
    assert query_html(graph, 's', page['id'], 'svg|foreignObject > DIV.embedded')['total'] == 1
    assert query_html(graph, 's', page['id'], 'foreignobject')['total'] == 0
    assert query_html(graph, 's', page['id'], 'svg|linearGradient')['total'] == 1
    for node in graph['nodes']:
        if node['kind'] == 'markup':
            result = query_html(graph, 's', page['id'], node['htmlFacts']['cssSelector'])
            assert [n['id'] for n in result['nodes']] == [node['id']]


def test_html_preview_omits_active_and_private_subtrees(tmp_path):
    """Preview exposes passive shapes and captured text without raw HTML or resource attributes."""
    from atlas.html_preview import build_html_preview

    source = ('<main id="component"><h2>Читаемый заголовок</h2><form action="https://evil.test">'
              '<input type="text" value="form-value" onfocus="evil()">'
              '<input type="hidden" value="hidden-value"><textarea>private-note</textarea>'
              '<button onclick="fetch(1)"><svg><path/></svg>Открыть</button></form>'
              '<p>&lt;img src=evil&gt;</p><img src="https://evil.test" onerror="evil()">'
              '<script>steal-secret</script><style>css-secret</style>'
              '<div hidden><strong>hidden-secret</strong></div><iframe src="https://evil.test"></iframe>'
              '<svg><foreignObject><div>foreign-secret</div></foreignObject></svg></main><aside>Other</aside>')
    graph, page = html_graph(tmp_path, source)
    component = next(node for node in graph['nodes'] if node['label'] == 'main#component')
    script = next(node for node in graph['nodes'] if node['label'] == 'script')
    (tmp_path / 'site' / 'index.html').unlink()
    from atlas.map_payload import serialize_graph
    import json
    assert 'htmlPreviewText' not in json.loads(serialize_graph(graph, 'map'))['metadata']
    preview = build_html_preview(graph, 's', component['id'])
    rows = preview['rows']
    assert rows[0] == {'parent': None, 'depth': 0, 'kind': 'container', 'label': 'main'}
    assert any(row['label'] == 'Читаемый заголовок' and row['kind'] == 'heading' for row in rows)
    assert any(row['label'] == '<img src=evil>' and row['kind'] == 'text' for row in rows)
    assert any(row['label'] == 'Открыть' and row['kind'] == 'control' for row in rows)
    assert sum(row['kind'] == 'media' for row in rows) == 1
    assert all(set(row) == {'parent', 'depth', 'kind', 'label'} for row in rows)
    for token in ('form-value', 'private-note', 'hidden-value', 'steal-secret', 'css-secret',
                  'foreign-secret', 'hidden-secret', 'https://evil.test', 'Other'):
        assert token not in repr(preview)
    with pytest.raises(HtmlQueryError, match='эскиз недоступен'):
        build_html_preview(graph, 's', script['id'])
    assert build_html_preview(graph, 's', page['id'])['rows']


def test_html_preview_limits_deep_or_long_shapes_and_accepts_old_snapshots(tmp_path):
    """An old captured tree remains usable, and large components return a partial sketch."""
    from atlas.html_preview import build_html_preview, MAX_PREVIEW_ROWS, MAX_PREVIEW_DEPTH

    graph, page = html_graph(tmp_path, '<main>' + '<div>' * 20 + 'Deep' + '</div>' * 20 + '<p>x</p>' * 100 + '</main>')
    graph['metadata'].pop('htmlPreviewText', None)
    preview = build_html_preview(graph, 's', page['id'])
    assert preview['truncated']
    assert len(preview['rows']) == MAX_PREVIEW_ROWS
    assert max(row['depth'] for row in preview['rows']) <= MAX_PREVIEW_DEPTH



def test_html_preview_rejects_even_template_ignored_by_html5_recovery(tmp_path):
    """A discarded template tag must not make its formerly inert options visible in a sketch."""
    from atlas.html_preview import build_html_preview

    graph, page = html_graph(tmp_path, '<select><template><option>secret</option></template><option>Shown</option></select>')
    assert 'template-content' in graph['metadata']['htmlDocuments']['index.html']['unsupported']
    assert 'htmlPreviewText' not in graph['metadata']
    with pytest.raises(HtmlQueryError, match='template'):
        build_html_preview(graph, 's', page['id'])



def test_literal_template_text_does_not_disable_preview_or_css(tmp_path):
    """RCDATA cannot be mistaken for an ignored or active HTML5 template element."""
    from atlas.html_preview import build_html_preview

    graph, page = html_graph(tmp_path, '<title>literal <template> title</title>'
                             '<textarea>literal <template> text</textarea><button>Save</button>')
    assert not graph['metadata']['htmlDocuments']['index.html']['unsupported']
    assert query_html(graph, 's', page['id'], 'button')['total'] == 1
    labels = [row['label'] for row in build_html_preview(graph, 's', page['id'])['rows']]
    assert 'Save' in labels and not any('literal' in label for label in labels)
