"""Raw-карта с тысячами узлов не повторяет файловые проверки для каждого символа."""
from collections import Counter
from atlas import test_coverage_sources as sources


def test_raw_file_index_keeps_aliases_order_and_checks_each_input_once(tmp_path, monkeypatch):
    """Дубликаты и опасные пути проверяются один раз; все raw-ID и порядок сохраняются."""
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'Service.cs').write_text('class Service {}')
    filenames = ['Service.cs'] * 1000 + ['./Service.cs', '../outside.cs', '../outside.cs', '']
    nodes = [{'id': str(index), 'file': filename} for index, filename in enumerate(filenames)]
    calls = Counter()
    original = sources._source_path

    def measured(project, node):
        """Считает реальные boundary проверки, сохраняя их поведение."""
        calls[node['file']] += 1
        return original(project, node)

    monkeypatch.setattr(sources, '_source_path', measured)
    indexed = sources.index_raw_files(root, {'nodes': nodes})
    assert list(indexed) == ['Service.cs']
    assert [item['id'] for item in indexed['Service.cs']] == [str(index) for index in range(1001)]
    assert calls == Counter({name: 1 for name in set(filenames)})
    assert nodes[1001]['file'] == '../outside.cs'


def test_index_has_no_cache_between_analyses_and_read_rechecks_symlink(tmp_path):
    """Подмена файла после группировки не обходит safe read; следующий анализ видит новый путь."""
    root = tmp_path / 'project'
    root.mkdir()
    source = root / 'Service.cs'
    source.write_text('class Service {}')
    outside = tmp_path / 'outside.cs'
    outside.write_text('private external data')
    graph = {'nodes': [{'id': 'one', 'file': 'Service.cs'}]}
    assert sources.index_raw_files(root, graph)['Service.cs'][0]['id'] == 'one'
    source.unlink()
    source.symlink_to(outside)
    budget = sources.SourceBudget(root, 's', {'maxSourceFiles': 10, 'maxSourceBytes': 4_000_000})
    assert budget.read('Service.cs') is None
    assert not sources.index_raw_files(root, graph)
    source.unlink()
    source.write_text('class Updated {}')
    assert sources.index_raw_files(root, graph)['Service.cs'][0]['id'] == 'one'
    fresh = sources.SourceBudget(root, 's', {'maxSourceFiles': 10, 'maxSourceBytes': 4_000_000})
    assert fresh.read('Service.cs')['content'] == b'class Updated {}'
