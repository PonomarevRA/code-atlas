"""Explicit imports, immutable evidence, exact values and bounded queries."""
import json
import time
from pathlib import Path

import pytest

from atlas.data_models import DataError, DataInput, DataQuery, MAX_RECORD_BYTES, MAX_RESPONSE_BYTES
from atlas.data_sources import discover
from atlas.data_store import DataMapStore
from atlas.data_storage import encode


@pytest.fixture
def dataset(tmp_path):
    """Separate roots ensure project discovery never sees managed snapshots."""
    root = tmp_path / 'input'; root.mkdir()
    store = DataMapStore(tmp_path / 'state')
    yield store, root
    store.close()


def wait(store, result):
    """Wait for an explicitly requested job, including terminal failures."""
    if not result['job']:
        return None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = store.job(result['job']['id'])
        if job['status'] not in {'queued', 'running'}:
            return job
        time.sleep(.01)
    raise AssertionError('Data import timeout')


def test_hash_verification_allows_reads_and_cancellation(dataset, monkeypatch):
    """Large explicit verification does not block catalog reads or another job's cancel."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from atlas import data_store

    store, root = dataset
    (root / 'a.csv').write_text('id\n1\n')
    overview = build(store, root, ['a.csv'])
    (root / 'b.csv').write_text('id\n2\n')
    captured, release_capture = Event(), Event()
    hashing, release_hash = Event(), Event()
    original_capture, original_digest = data_store.capture, data_store.digest

    def blocked_capture(*args):
        captured.set()
        assert release_capture.wait(5)
        return original_capture(*args)

    def blocked_digest(path):
        hashing.set()
        assert release_hash.wait(5)
        return original_digest(path)

    monkeypatch.setattr(data_store, 'capture', blocked_capture)
    request = store.create(DataInput(files=['b.csv']), root)
    assert captured.wait(2)
    monkeypatch.setattr(data_store, 'digest', blocked_digest)
    with ThreadPoolExecutor(max_workers=2) as threads:
        refresh = threads.submit(store.refresh, overview['id'])
        try:
            assert hashing.wait(2)
            listing = threads.submit(store.list)
            assert len(listing.result(timeout=1)['maps']) == 2
            cancellation = threads.submit(store.job, request['job']['id'], True)
            assert cancellation.result(timeout=1)['id'] == request['job']['id']
        finally:
            release_hash.set()
            release_capture.set()
        assert refresh.result(timeout=3)['verified']
    assert wait(store, request)['status'] == 'cancelled'


def test_concurrent_verification_admits_only_one_refresh(dataset, monkeypatch):
    """Requests that hash the same snapshot concurrently share one admitted build."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event
    from atlas import data_store

    store, root = dataset
    (root / 'a.csv').write_text('id\n1\n')
    overview = build(store, root, ['a.csv'])
    barrier, release_capture = Barrier(2), Event()
    original_digest, original_capture = data_store.digest, data_store.capture

    def concurrent_digest(path):
        result = original_digest(path)
        barrier.wait(timeout=3)
        return result

    def blocked_capture(*args):
        assert release_capture.wait(5)
        return original_capture(*args)

    monkeypatch.setattr(data_store, 'digest', concurrent_digest)
    monkeypatch.setattr(data_store, 'capture', blocked_capture)
    with ThreadPoolExecutor(max_workers=2) as threads:
        requests = [threads.submit(store.refresh, overview['id'], True) for _ in range(2)]
        try:
            results = [request.result(timeout=4) for request in requests]
            assert results[0]['job']['id'] == results[1]['job']['id']
        finally:
            release_capture.set()
    assert wait(store, results[0])['status'] == 'completed'


def test_source_change_during_hash_cannot_mark_map_verified(dataset, monkeypatch):
    """An edit after hashing is detected before returning an unchanged-map result."""
    from atlas import data_store

    store, root = dataset
    path = root / 'a.csv'
    path.write_text('id\n1\n')
    overview = build(store, root, ['a.csv'])
    original_digest = data_store.digest

    def changing_digest(source):
        result = original_digest(source)
        path.write_text('id\n2\n')
        return result

    monkeypatch.setattr(data_store, 'digest', changing_digest)
    request = store.refresh(overview['id'])
    assert request['job'] is not None and not request.get('verified')
    assert wait(store, request)['status'] == 'completed'
    assert store.overview(overview['id'])['snapshotId'] != overview['snapshotId']


def test_published_snapshot_during_hash_is_reverified(dataset, monkeypatch):
    """A completed competing refresh cannot receive verification of an older snapshot."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    from atlas import data_store

    store, root = dataset
    (root / 'a.csv').write_text('id\n1\n')
    overview = build(store, root, ['a.csv'])
    hashing, release_hash = Event(), Event()
    original_digest = data_store.digest
    calls = []

    def blocked_first_digest(path):
        result = original_digest(path)
        if current_thread().name.startswith('verification'):
            calls.append(path)
            if len(calls) == 1:
                hashing.set()
                assert release_hash.wait(5)
        return result

    monkeypatch.setattr(data_store, 'digest', blocked_first_digest)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='verification') as threads:
        verification = threads.submit(store.refresh, overview['id'])
        try:
            assert hashing.wait(2)
            competing = store.refresh(overview['id'], force=True)
            assert wait(store, competing)['status'] == 'completed'
        finally:
            release_hash.set()
        result = verification.result(timeout=3)
    assert result['verified'] and len(calls) == 2
    assert result['map']['snapshotId'] == competing['job']['id']


def build(store, root, files, **options):
    """Publish a fixture and fail with the actionable import error."""
    result = store.create(DataInput(files=files, **options), root)
    job = wait(store, result)
    assert job is None or job['status'] == 'completed', job
    return store.overview(result['map']['id'])


def collections(store, overview):
    """Return compact schemas of a published test snapshot."""
    return list(store.snapshot(overview['id'])[1]['collections'].values())


def query(store, overview, operation, **options):
    """Bind all assertions to the same published revision."""
    return store.query(overview['id'], operation, DataQuery(snapshotId=overview['snapshotId'], **options))


def test_discovery_does_not_create_storage_or_read_contents(dataset, monkeypatch):
    store, root = dataset
    (root/'input.csv').write_text('not even valid csv')
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path.is_relative_to(root):
            raise AssertionError('Discovery read source content')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    result = discover(root, store.data_dir)
    assert result['files'][0]['path'] == 'input.csv'
    assert result['built'] is False
    assert store.list() == {'maps': []}
    assert store.executor is None and not store.root.exists()


def test_stale_reads_return_original_records_and_build_date(dataset):
    store, root = dataset
    source = root/'a.csv'; source.write_text('id,name\n001,Before\n')
    overview = build(store, root, ['a.csv'])
    cid = collections(store, overview)[0]['id']
    original = query(store, overview, 'records', collectionId=cid)
    source.write_text('id,name\n002,After\n')
    count = len(store.jobs)
    stale = query(store, overview, 'records', collectionId=cid)
    assert stale['items'] == original['items']
    assert stale['items'][0]['values']['id'] == '001'
    assert stale['builtAt'] == overview['builtAt']
    assert stale['lastVerifiedAt'] == overview['lastVerifiedAt']
    assert stale['freshness']['status'] == 'stale'
    assert stale['freshness']['changedFiles'] == ['a.csv']
    assert len(store.jobs) == count
    fresh = wait(store, store.refresh(overview['id']))
    assert fresh['status'] == 'completed'
    updated = store.overview(overview['id'])
    assert updated['snapshotId'] != overview['snapshotId']
    assert query(store, updated, 'records', collectionId=cid)['items'][0]['values']['id'] == '002'
    assert store.refresh(overview['id'])['job'] is None
    with pytest.raises(DataError, match='Снимок изменился'):
        query(store, overview, 'schema')


def test_json_exact_types_nested_arrays_and_literal_keys(dataset):
    store, root = dataset
    (root/'data.json').write_text('[{"id":9007199254740993,"amount":1234567890.1234567890123456789,"a/b~":{"$collection":4},"nested":[[2,3]],"nullable":null},{"id":2}]')
    overview = build(store, root, ['data.json'])
    items = collections(store, overview)
    main = next(item for item in items if item['label']=='data.json:/')
    records = query(store, overview, 'records', collectionId=main['id'])['items']
    first = records[0]
    assert first['values']['id'] == 9007199254740993
    assert first['values']['amount'] == '1234567890.1234567890123456789'
    assert first['values']['a/b~']['$collection'] == 4
    assert first['provenance']['valueTypes']['/a~1b~0/$collection'] == 'number'
    nulls = query(store, overview, 'records', collectionId=main['id'], filters=[{'field':'/nullable','op':'isNull'}])
    missing = query(store, overview, 'records', collectionId=main['id'], filters=[{'field':'/nullable','op':'missing'}])
    assert len(nulls['items']) == len(missing['items']) == 1
    inner = next(item for item in items if item['label']=='data.json:/0/nested/0')
    children = query(store, overview, 'records', collectionId=inner['id'])['items']
    assert [item['values']['value'] for item in children] == [2,3]
    assert children[0]['parentRecordId']
    projection = query(store, overview, 'record', recordId=first['id'], fields=['/a~1b~0/$collection'])
    assert projection['items'][0]['values'] == {'/a~1b~0/$collection': 4}


def test_failed_refresh_preserves_dated_snapshot(dataset):
    store, root = dataset
    (root/'a.json').write_text('[{"id":1}]')
    overview = build(store, root, ['a.json'])
    (root/'a.json').write_text('{invalid')
    failure = wait(store, store.refresh(overview['id']))
    assert failure['status'] == 'failed'
    result = query(store, overview, 'schema')
    assert result['builtAt'] == overview['builtAt'] and result['freshness']['status'] == 'stale'


def test_input_limits_and_path_isolation(dataset):
    store, root = dataset
    outside = root.parent/'outside.csv'; outside.write_text('a\nx\n')
    (root/'symlink.csv').symlink_to(outside)
    for filename in ['../outside.csv', 'symlink.csv', str(outside)]:
        with pytest.raises(DataError):
            store.create(DataInput(files=[filename]), root)
    (root/'large.json').write_text('["' + 'x' * (MAX_RECORD_BYTES + 10) + '"]')
    failure = wait(store, store.create(DataInput(files=['large.json']), root))
    assert failure['status']=='failed' and failure['code']=='TOKEN_LIMIT'
    assert not store.overview(failure['mapId'])['snapshotId']


def test_many_to_many_trace_can_continue_without_dropped_neighbors(dataset):
    store, root = dataset
    (root/'a.csv').write_text('id\nsame\n')
    (root/'b.csv').write_text('id\n' + 'same\n'*20)
    overview = build(store, root, ['a.csv','b.csv'])
    main = next(item for item in collections(store, overview) if item['file']=='a.csv')
    seed = query(store, overview, 'records', collectionId=main['id'])['items'][0]['id']
    relations = query(store, overview, 'relations')['items']
    rule = next(item for item in relations if item['sourceCollection']==main['id'])
    assert rule['coverage']['duplicateTargetKeys']==19
    cursor = None; visited = set()
    for _ in range(20):
        result = query(store, overview, 'trace', recordId=seed, maxNodes=4, maxDepth=1, cursor=cursor, relationIds=[rule['id']])
        assert len(result['nodes'])<=4 and len(encode(result).encode())<=MAX_RESPONSE_BYTES
        visited.update(item['id'] for item in result['nodes'] if item['id']!=seed)
        cursor = result['nextCursor']
        if not cursor: break
    assert len(visited)==20 and cursor is None
    saved = query(store, overview, 'view', recordId=seed, maxNodes=4, maxDepth=1)
    assert store.saved_view(overview['id'], saved['id'])['builtAt']==overview['builtAt']


def test_large_records_paginate_and_preserve_provenance(dataset):
    store, root = dataset
    (root/'large.json').write_text(json.dumps([{'id':i,'text':'x'*80000} for i in range(3)]))
    overview = build(store, root, ['large.json'])
    cid = collections(store, overview)[0]['id']
    cursor = None; visited = []
    for _ in range(10):
        result = query(store, overview, 'records', collectionId=cid, cursor=cursor, limit=2)
        assert len(encode(result).encode()) <= MAX_RESPONSE_BYTES
        assert all(item['provenance']['file']=='large.json' for item in result['items'])
        visited.extend(item['ordinal'] for item in result['items'])
        cursor=result['nextCursor']
        if not cursor: break
    assert visited==[1,2,3]
    projection=query(store,overview,'record',recordId=result['items'][0]['id'],fields=['/id'])
    assert projection['items'][0]['values']=={'/id':2}


def test_gherkin_background_examples_docstrings_and_descriptions(dataset):
    store, root = dataset
    (root/'a.feature').write_text('''@data
Feature: Account
  Explains account selection.
  Background:
    Given an existing account
  Rule: Balances
    Scenario Outline: Select <id>
      Explains selected data.
      # commentary for agents
      When selecting <id>
        """
        requested <id>
        """
      Then amount is <amount>
      And arguments are
        | key | <id> |
      @example
      Examples:
        | id  | amount |
        | 001 | 100    |
''')
    overview=build(store,root,['a.feature'])
    found=query(store,overview,'scenarios',query='selected',tag='@data')['items']
    assert len(found)==1 and 'Explains' in found[0]['description']
    template=query(store,overview,'scenario',scenarioId=found[0]['id'])
    examples=template['scenario']['examples'][0]['collectionId']
    example=query(store,overview,'records',collectionId=examples)['items'][0]
    assert example['values']=={'id':'001','amount':'100'}
    resolved=query(store,overview,'scenario',scenarioId=found[0]['id'],exampleId=example['id'])
    assert resolved['items'][0]['backgroundOwner']
    assert resolved['items'][1]['resolved']['text']=='selecting 001'
    assert resolved['items'][1]['resolved']['docString']['content']=='requested 001'
    assert resolved['runtimeStatus']=='unknown' and resolved['comments']
    assert resolved['items'][1]['source']['line']==10


def test_jsonl_literal_reference_key_and_cursor_isolation(dataset):
    """Literal marker-like objects remain data; references have separate provenance."""
    store,root=dataset
    (root/'lines.jsonl').write_text('{"$atlasCollection":"literal","items":[1]}\n{"id":2}\n')
    overview=build(store,root,['lines.jsonl'])
    main=next(item for item in collections(store,overview) if item['label']=='lines.jsonl:/')
    result=query(store,overview,'records',collectionId=main['id'],limit=1)
    first=result['items'][0]
    assert first['values']['$atlasCollection']=='literal'
    assert '/items' in first['provenance']['collectionReferences']
    assert first['provenance']['pointer']=='/0'
    with pytest.raises(DataError,match='Курсор'):
        query(store,overview,'schema',cursor=result['nextCursor'],limit=1)
    second=query(store,overview,'records',collectionId=main['id'],limit=1,cursor=result['nextCursor'])
    assert second['items'][0]['values']=={'id':2}


def test_csv_utf8_sniff_boundary_and_quoted_multiline(dataset):
    """Sniffing may end inside a multibyte code point without rejecting valid CSV."""
    store,root=dataset
    (root/'a.csv').write_text('id;text\n1;"'+'а'*4092+'\nsecond line"\n')
    overview=build(store,root,['a.csv'])
    item=query(store,overview,'records',collectionId=collections(store,overview)[0]['id'])['items'][0]
    assert item['values']['text'].endswith('\nsecond line')
    assert item['provenance']['line']==2 and item['provenance']['endLine']==3


def test_typed_and_string_relations_preserve_leading_zeros(dataset):
    """An explicit cross-type rule does not make numeric 1 equal to CSV 001."""
    store,root=dataset
    (root/'a.csv').write_text('id\n001\n1\n')
    (root/'b.json').write_text('[{"id":1}]')
    overview=build(store,root,['a.csv','b.json'],relations=[{'sourceFile':'a.csv','targetFile':'b.json','sourceField':'/id','targetField':'/id','compareAs':'string'}])
    rule=next(item for item in query(store,overview,'relations')['items'] if item['status']=='declared')
    assert rule['coverage']['matched']==1 and rule['coverage']['unmatched']==1


def test_python_json_backend_memory_is_bounded():
    """A fresh process catches Lexer buffer retention hidden by small fixtures."""
    import os
    import subprocess
    import sys
    result=subprocess.run([sys.executable,'scripts/benchmark-data-maps.py','--bytes','40000000'],
                          env={**os.environ,'IJSON_BACKEND':'python'},capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
    measurement=json.loads(result.stdout)
    assert 39000000<measurement['inputBytes']<=40000000
    assert measurement['peakRssMiB']<512,measurement
    assert measurement['pageRecords']==20


def test_identical_gherkin_files_have_distinct_nodes_and_background_tables(dataset):
    """The source path namespaces AST IDs even when source hashes match."""
    store,root=dataset
    feature='''Feature: Data
  Background:
    Given these inputs
      | key | value |
  Scenario: Read
    Then input is present
'''
    (root/'a.feature').write_text(feature);(root/'b.feature').write_text(feature)
    overview=build(store,root,['a.feature','b.feature'])
    found=query(store,overview,'scenarios')['items']
    assert len(found)==2 and found[0]['id']!=found[1]['id']
    for item in found:
        scenario=query(store,overview,'scenario',scenarioId=item['id'])
        table_id=scenario['items'][0]['dataTableId']
        record=query(store,overview,'records',collectionId=table_id)['items'][0]
        assert record['values']=={'column1':'key','column2':'value'}
        assert record['provenance']['file']==item['source']['file']


def test_cancelled_refresh_keeps_published_snapshot(dataset,monkeypatch):
    """Cancellation of staging cannot replace the last usable revision."""
    from threading import Event
    import atlas.data_store as module
    store,root=dataset
    (root/'a.csv').write_text('id\n1\n')
    overview=build(store,root,['a.csv'])
    entered,release=Event(),Event()
    original=module.capture
    def blocked(*args,**kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'capture',blocked)
    request=store.refresh(overview['id'],force=True)
    try:
        assert entered.wait(3)
        store.job(request['job']['id'],cancel=True)
    finally:release.set()
    cancelled=wait(store,request)
    assert cancelled['status']=='cancelled'
    assert store.overview(overview['id'])['snapshotId']==overview['snapshotId']
    assert query(store,overview,'schema')['builtAt']==overview['builtAt']
    assert not list(store.directory(overview['id']).glob('.staging-*'))


def test_key_ranking_values_and_isolated_paths(dataset):
    """Repeated values lead the ranking; isolated paths exclude disconnected records."""
    store, root = dataset
    (root/'groups.csv').write_text('id,group\n001,A\n002,A\n003,B\n004,A\n')
    (root/'details.csv').write_text('id,note\n001,first\n002,second\n999,unrelated\n')
    overview = build(store, root, ['groups.csv', 'details.csv'])
    ranked = query(store, overview, 'keys')
    group = next(key for key in ranked['items'] if key['file'] == 'groups.csv' and key['field'] == '/group')
    assert group['repeatOccurrences'] == 2 and group['maxFrequency'] == 3
    assert next(key for key in ranked['items'] if key['file']=='groups.csv' and key['field']=='/id')['matchedRecords']==2
    assert ranked['items'][0]['score'] >= ranked['items'][1]['score']
    values = query(store, overview, 'key-values', keyId=group['id'], limit=1)
    assert values['items'][0]['label'] == 'A' and values['items'][0]['count'] == 3
    other = query(store, overview, 'key-values', keyId=group['id'], limit=1, cursor=values['nextCursor'])
    assert other['items'][0]['label'] == 'B' and not other['nextCursor']
    path = query(store, overview, 'key-path', keyId=group['id'], valueRecordId=values['items'][0]['valueRecordId'])
    assert path['matchedRecords'] == 3
    rows = [node for node in path['nodes'] if node['kind'] == 'data-record']
    assert sorted(node['values']['id'] for node in rows) == ['001','001','002','002','004']
    assert all(node['provenance']['line'] >= 2 for node in rows)
    assert {node['column'] for node in rows} == {2,3}
    ids = {node['id'] for node in path['nodes']}
    assert all(edge['source'] in ids and edge['target'] in ids for edge in path['links'])
    assert path['builtAt'] == overview['builtAt'] and path['freshness']['status'] == 'current'
    jobs = len(store.jobs)
    (root/'groups.csv').write_text('id,group\n005,C\n')
    stale = query(store, overview, 'key-path', keyId=group['id'], valueRecordId=values['items'][0]['valueRecordId'])
    assert stale['freshness']['status'] == 'stale' and stale['nodes'] == path['nodes']
    assert len(store.jobs) == jobs
    with pytest.raises(DataError, match='Курсор'):
        query(store, overview, 'key-values', keyId=ranked['items'][1]['id'], limit=1, cursor=values['nextCursor'])


def test_key_types_null_and_bounded_continuation(dataset):
    """Typed groups preserve zeros, distinguish missing/null, and paginate capped roots."""
    store, root = dataset
    (root/'mixed.json').write_text(json.dumps([{'key': '001'}, {'key': 1}, {'key': '1'},
        {'key': None}, {'key': None}, {}, *[{'key': 'same'} for _ in range(12)]]))
    overview = build(store, root, ['mixed.json'])
    key = query(store, overview, 'keys')['items'][0]
    assert key['repeatOccurrences'] == 11  # null is displayed but never increases the score.
    values = query(store, overview, 'key-values', keyId=key['id'])['items']
    assert len(values) == 5
    assert {(item['label'],item['valueType']) for item in values} >= {('001','string'),('1','string'),('1','number')}
    chosen = values[0]; cursor = None; seen = set()
    for _ in range(10):
        path = query(store, overview, 'key-path', keyId=key['id'], valueRecordId=chosen['valueRecordId'],
                     maxNodes=5, maxDepth=0, limit=10, cursor=cursor)
        assert len(path['nodes']) <= 5 and len(encode(path).encode()) <= MAX_RESPONSE_BYTES
        seen.update(node['id'] for node in path['nodes'] if node['kind'] == 'data-record')
        cursor = path['nextCursor']
        if not cursor: break
    assert len(seen) == 12 and not cursor
    null = next(item for item in values if item['valueType']=='null')
    assert null['label']=='null'
    assert query(store, overview, 'key-path', keyId=key['id'], valueRecordId=null['valueRecordId'])['matchedRecords']==2
    with pytest.raises(DataError, match='Представитель'):
        query(store, overview, 'key-path', keyId=key['id'], valueRecordId='foreign')


def test_key_path_byte_budget_keeps_paginated_roots(dataset):
    """Large records do not evict root rows from a page when neighboring graphs grow."""
    store, root = dataset
    (root/'large.json').write_text(json.dumps([{'group':'all','text':'x'*8000,'ordinal':index} for index in range(55)]))
    overview = build(store, root, ['large.json'], indexFields=['/group'])
    key = query(store, overview, 'keys')['items'][0]
    value = query(store, overview, 'key-values', keyId=key['id'])['items'][0]
    seen = set(); cursor = None
    for _ in range(10):
        path = query(store, overview, 'key-path', keyId=key['id'], valueRecordId=value['valueRecordId'],
                     cursor=cursor, limit=50, maxNodes=120)
        assert len(encode(path).encode()) <= MAX_RESPONSE_BYTES
        rows = [node for node in path['nodes'] if node['kind']=='data-record']
        assert all(node['column']==2 for node in rows)
        assert [node['ordinal'] for node in rows] == sorted(node['ordinal'] for node in rows)
        seen.update(node['id'] for node in rows)
        cursor = path['nextCursor']
        if not cursor: break
    assert len(seen)==55 and not cursor
