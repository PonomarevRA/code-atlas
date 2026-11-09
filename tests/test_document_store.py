"""Реальные process/capture/index fixtures для контракта хранилища документов."""

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from atlas.documents import DocumentError, DocumentStore, TreeBuilder
from atlas.documents.capture import capture
from atlas.documents.models import validate_tree
from atlas.documents.worker import run_provider


class FixtureProvider:
    """Picklable provider фиксирует вызовы дочернего процесса отдельным файлом."""

    id = 'fixture'
    version = '1'
    runtime_fingerprint = 'fixture-runtime-v1'
    capabilities = ['tree', 'search', 'table']

    def parse(self, path, options, context):
        """Парсит текст и одну sparse таблицу; fixture options создают timeout/crash."""
        if context.get('calls'):
            with Path(context['calls']).open('a') as output:
                output.write('parse\n')
        allocated = bytearray(options.get('allocateBytes', 0))
        time.sleep(options.get('delay', 0))
        if allocated:
            allocated[0] = 1
        if options.get('crash'):
            import os
            os._exit(7)
        if options.get('fail'):
            raise ValueError('fixture failure')
        builder = TreeBuilder(path.name)
        builder.add('section', builder.root_id, label='Heading', text=path.read_text(encoding='utf-8'))
        table = builder.add('table', builder.root_id, label='Rates')
        row = builder.add('row', table)
        builder.add('cell', row, text='42', attributes={'row': 0, 'column': 1})
        return builder.build()


def fixture_resolver(path, **options):
    """Фабрика без чтения исходника, как требует production contract."""
    return FixtureProvider()


class DocumentStoreTests(unittest.TestCase):
    """Каждый тест использует отдельное хранилище и оригинал вне user storage."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.project = self.directory / 'project'
        self.project.mkdir()
        (self.project / 'a.md').write_text('Привет source', encoding='utf-8')
        self.calls = self.directory / 'calls'
        self.store = DocumentStore(self.directory / 'storage', self.project, 'fixture', fixture_resolver)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def build(self, map_id='a', **options):
        """Дожидается terminal job и требует успешную публикацию fixture."""
        job = self.store.ensure(map_id, ['a.md'], context={'calls': str(self.calls)}, **options)
        result = self.store.wait(job['id'], 10)
        self.assertEqual(result['status'], 'completed', result)
        return result

    def test_cache_and_parser_free_historical_reads_after_restart(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first['snapshotId'], second['snapshotId'])
        self.assertEqual(second['cacheHits'], 1)
        self.assertEqual(self.calls.read_text().count('parse'), 1)
        old = first['snapshotId']
        (self.project / 'a.md').write_text('New text', encoding='utf-8')
        newest = self.build()
        self.assertNotEqual(old, newest['snapshotId'])
        self.store.close()
        self.store = DocumentStore(self.directory / 'storage', self.project, 'fixture', lambda *args, **kwargs: self.fail('parser selected during read'))
        (self.project / 'a.md').unlink()
        for _ in range(100):
            result = self.store.search('a', 'Привет', snapshot_id=old)
            self.assertEqual(len(result['items']), 1)
        self.assertNotIn('text', result['items'][0])
        node = self.store.node('a', result['items'][0]['id'], snapshot_id=old)
        self.assertEqual(node['text'], 'Привет source')
        self.assertEqual(node['sourcePath'], 'a.md')
        self.assertEqual(self.calls.read_text().count('parse'), 2)

    def test_tree_table_graph_and_scope_bound_cursor(self):
        self.build()
        root = self.store.tree('a')['items'][0]
        children = self.store.tree('a', parent_id=root['id'], limit=1)
        self.assertTrue(children['nextCursor'])
        second = self.store.tree('a', parent_id=root['id'], cursor=children['nextCursor'], limit=1)
        table = second['items'][0]
        self.assertEqual(table['kind'], 'table')
        result = self.store.table('a', table['id'])
        self.assertEqual([(cell['text'], cell['attributes']['column']) for cell in result['cells']], [('42', 1)])
        with self.assertRaises(DocumentError):
            self.store.search('a', 'Heading', cursor=children['nextCursor'])
        projection = self.store.graph('a')
        self.assertTrue(any(edge['relation'] == 'contains' for edge in projection['edges']))
        self.assertFalse(any(node['kind'] == 'cell' for node in projection['nodes']))

    def test_only_changed_source_creates_a_new_revision(self):
        (self.project / 'b.md').write_text('Different text', encoding='utf-8')
        arguments = {'context': {'calls': str(self.calls)}}
        job = self.store.ensure('a', ['a.md', 'b.md'], **arguments)
        self.assertEqual(self.store.wait(job['id'], 10)['status'], 'completed')
        before = self.store.snapshot('a')['documents']
        (self.project / 'b.md').write_text('Changed second file', encoding='utf-8')
        job = self.store.ensure('a', ['a.md', 'b.md'], **arguments)
        result = self.store.wait(job['id'], 10)
        self.assertEqual(result['status'], 'completed', result)
        after = self.store.snapshot('a')['documents']
        self.assertEqual(before[0]['revisionId'], after[0]['revisionId'])
        self.assertNotEqual(before[1]['revisionId'], after[1]['revisionId'])
        self.assertEqual(result['cacheHits'], 1)
        self.assertEqual(self.calls.read_text().count('parse'), 3)

    def test_failed_refresh_keeps_previous_and_partial_drops_failed_binding(self):
        first = self.build()
        job = self.store.ensure('a', ['a.md'], options={'fail': True})
        self.assertEqual(self.store.wait(job['id'], 10)['status'], 'failed')
        self.assertEqual(self.store.snapshot('a')['snapshotId'], first['snapshotId'])
        job = self.store.ensure('a', ['a.md'], options={'fail': True}, allow_partial=True)
        result = self.store.wait(job['id'], 10)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['capturedBytes'], (self.project / 'a.md').stat().st_size)
        partial = self.store.snapshot('a')
        self.assertEqual(partial['documents'], [])
        self.assertEqual(len(partial['failures']), 1)
        self.assertEqual(self.store.snapshot('a', first['snapshotId'])['coverage']['status'], 'complete')

    def test_shared_parse_cancel_one_consumer_preserves_other(self):
        arguments = {'options': {'delay': 0.7}, 'context': {'calls': str(self.calls)}}
        a = self.store.ensure('a', ['a.md'], **arguments)
        b = self.store.ensure('b', ['a.md'], **arguments)
        deadline = time.monotonic() + 5
        shared = False
        while time.monotonic() < deadline:
            with self.store.lock:
                shared = any(len(flight['consumers']) == 2 for flight in self.store.flights.values())
            if shared:
                break
            time.sleep(0.01)
        self.assertTrue(shared)
        self.assertEqual(self.store.cancel(a['id'])['status'], 'cancelled')
        self.assertEqual(self.store.wait(b['id'], 10)['status'], 'completed')
        self.assertEqual(self.calls.read_text().count('parse'), 1)

    def test_force_runtime_and_scope_history(self):
        first = self.build()
        forced = self.build(force=True)
        self.assertNotEqual(first['snapshotId'], forced['snapshotId'])
        self.build('b')
        with self.assertRaises(DocumentError):
            self.store.snapshot('b', first['snapshotId'])
        other = DocumentStore(self.directory / 'storage', self.project, 'other', fixture_resolver)
        try:
            self.assertEqual(other.maps(), {'maps': []})
        finally:
            other.close()

    def test_capture_rejects_escape_symlink_and_mutation(self):
        with self.assertRaises(DocumentError):
            capture(self.project, '../outside', self.directory / 'copy')
        (self.project / 'link').symlink_to(self.project / 'a.md')
        with self.assertRaises(DocumentError):
            capture(self.project, 'link', self.directory / 'copy')
        import os
        original = os.fstat
        count = [0]
        def changed(descriptor):
            """Меняет исходник между контрольными fstat для воспроизводимой гонки."""
            count[0] += 1
            if count[0] == 2:
                (self.project / 'a.md').write_text('mutated-longer')
            return original(descriptor)
        with patch('atlas.documents.capture.os.fstat', changed), self.assertRaises(DocumentError) as error:
            capture(self.project, 'a.md', self.directory / 'copy')
        self.assertEqual(error.exception.code, 'SOURCE_CHANGED')

    def test_timeout_crash_and_cancel_last_leave_old_snapshot(self):
        first = self.build()
        started = time.monotonic()
        job = self.store.ensure('a', ['a.md'], options={'delay': 5, 'workerTimeoutSeconds': 0.1})
        result = self.store.wait(job['id'], 10)
        self.assertEqual(result['errorCode'], 'WORKER_TIMEOUT')
        self.assertLess(time.monotonic() - started, 3)
        job = self.store.ensure('a', ['a.md'], options={'crash': True})
        self.assertEqual(self.store.wait(job['id'], 10)['errorCode'], 'WORKER_CRASH')
        self.assertEqual(self.store.snapshot('a')['snapshotId'], first['snapshotId'])

    def test_parent_rss_watchdog_preserves_previous_snapshot(self):
        first = self.build()
        job = self.store.ensure('a', ['a.md'], options={'allocateBytes': 64 * 1024 * 1024, 'delay': 0.5, 'workerMemoryBytes': 48 * 1024 * 1024})
        result = self.store.wait(job['id'], 10)
        self.assertEqual(result['errorCode'], 'WORKER_MEMORY_LIMIT', result)
        self.assertEqual(self.store.snapshot('a')['snapshotId'], first['snapshotId'])

    def test_reachability_retention_keeps_selected_history_and_shared_revisions(self):
        first = self.build()
        self.build('b')
        (self.project / 'a.md').write_text('Changed')
        self.build()
        result = self.store.retention(keep_last=3)
        self.assertEqual(result['unreferencedRevisions'], [])
        self.assertTrue(self.store.search('b', 'Привет')['items'])
        self.assertEqual(self.store.snapshot('a', first['snapshotId'])['snapshotId'], first['snapshotId'])
        self.assertTrue(self.store.retention(dry_run=False)['deletionAvailable'])

    def test_freshness_stat_is_separate_from_coverage_and_missing_source_is_readable(self):
        first = self.build()
        self.assertEqual(self.store.freshness('a')['status'], 'current')
        (self.project / 'a.md').write_text('changed source')
        self.assertEqual(self.store.freshness('a')['status'], 'stale')
        (self.project / 'a.md').unlink()
        self.assertEqual(self.store.freshness('a')['changedFiles'], ['a.md'])
        self.assertEqual(self.store.snapshot('a')['coverage']['status'], 'complete')
        self.assertEqual(self.store.snapshot('a')['snapshotId'], first['snapshotId'])
        self.assertTrue(self.store.search('a', 'Привет')['items'])

    def test_content_windows_preserve_unicode_and_bound_bytes(self):
        original = '😀Ж\0' * 10000
        (self.project / 'a.md').write_text(original, encoding='utf-8')
        self.build()
        node = self.store.search('a', 'Ж')['items'][0]
        pieces, offset = [], 0
        while offset is not None:
            result = self.store.read_content('a', node['id'], offset=offset, limit=4000)
            from atlas.documents.models import encode
            self.assertLess(len(encode(result).encode('utf-8')), 64000)
            pieces.append(result['text'])
            offset = result['nextOffset']
        self.assertEqual(''.join(pieces), original)
        with self.assertRaises(DocumentError):
            self.store.read_content('a', node['id'], offset=-1)

    def test_dedup_identical_files_keeps_binding_scoped_node_ids(self):
        (self.project / 'b.md').write_text('Привет source', encoding='utf-8')
        job = self.store.ensure('a', ['a.md', 'b.md'], context={'calls': str(self.calls)})
        self.assertEqual(self.store.wait(job['id'], 10)['status'], 'completed')
        roots = self.store.tree('a')['items']
        self.assertEqual([node['label'] for node in roots], ['a.md', 'b.md'])
        self.assertNotEqual(roots[0]['id'], roots[1]['id'])
        self.assertEqual(self.calls.read_text().count('parse'), 1)
        children = self.store.tree('a', parent_id=roots[1]['id'])['items']
        self.assertTrue(all(node['sourcePath'] == 'b.md' for node in children))
        projection = self.store.graph('a')
        ids = [node['id'] for node in projection['nodes']]
        self.assertEqual(len(ids), len(set(ids)))
        forced = self.store.ensure('a', ['a.md', 'b.md'], context={'calls': str(self.calls)}, force=True)
        self.assertEqual(self.store.wait(forced['id'], 10)['status'], 'completed')
        self.assertEqual(self.calls.read_text().count('parse'), 2)

    def test_prepublish_callback_aborts_without_losing_old_snapshot(self):
        first = self.build()
        def reject(configuration):
            """Имитирует изменение frozen mod rules до публикации."""
            raise DocumentError('RULES_CHANGED', 'Изменился fingerprint модов.')
        self.store.prepublish = reject
        job = self.store.ensure('a', ['a.md'], force=True)
        result = self.store.wait(job['id'], 10)
        self.assertEqual(result['errorCode'], 'RULES_CHANGED')
        self.assertEqual(self.store.snapshot('a')['snapshotId'], first['snapshotId'])

    def test_job_recovery_and_runtime_fingerprint_invalidate_cache(self):
        first = self.build()
        self.store.close()
        import json
        jobs = json.loads(self.store.jobs_path.read_text())
        jobs[first['id']]['status'] = 'running'
        jobs['interrupted'] = {'id': 'interrupted', 'mapId': 'a', 'status': 'running'}
        self.store.jobs_path.write_text(json.dumps(jobs))
        self.store = DocumentStore(self.directory / 'storage', self.project, 'fixture', fixture_resolver)
        self.assertEqual(self.store.job(first['id'])['status'], 'completed')
        self.assertEqual(self.store.wait('interrupted')['status'], 'interrupted')
        provider = FixtureProvider()
        provider.runtime_fingerprint = 'fixture-runtime-v2'
        self.store.resolver = lambda *args, **kwargs: provider
        changed = self.build()
        self.assertNotEqual(first['snapshotId'], changed['snapshotId'])

    def test_real_markdown_yaml_process_index_roundtrip(self):
        from atlas.documents.providers import resolve_provider
        self.store.resolver = resolve_provider
        (self.project / 'a.md').write_text('# Заголовок\n\n| A | B |\n|---|---|\n| 1 | 2 |\n', encoding='utf-8')
        (self.project / 'config.yaml').write_text('a: &shared {x: 1}\nb: *shared\na: 2\n', encoding='utf-8')
        job = self.store.ensure('a', ['a.md', 'config.yaml'])
        result = self.store.wait(job['id'], 10)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(self.store.tree('a')['items']), 2)
        self.assertTrue(self.store.search('a', 'Заголовок')['items'])
        self.assertTrue(any(edge['relation'] == 'alias_of' for edge in self.store.graph('a')['edges']))

    def test_builder_rejects_bad_structure_and_incremental_budgets(self):
        builder = TreeBuilder(max_nodes=1)
        with self.assertRaises(DocumentError):
            builder.add('text', builder.root_id)
        bad = {'nodes': [{'id': 'a', 'kind': 'document', 'parentId': None}, {'id': 'b', 'kind': 'text', 'parentId': 'b'}]}
        with self.assertRaises(DocumentError):
            validate_tree(bad)


if __name__ == '__main__':
    unittest.main()
