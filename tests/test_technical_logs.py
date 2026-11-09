"""Independent technical ring, safe logging bridge and portable read-only HTTP contract."""

from dataclasses import replace
import json
import logging
from pathlib import Path
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from atlas.technical_logs.disk import SegmentJournal
from atlas.technical_logs.events import from_record, log_context
from atlas.technical_logs.http_api import register_technical_routes
from atlas.technical_logs.service import TechnicalHandler, TechnicalJournal
from atlas.technical_logs.settings import TechnicalSettings

BASE = '/api/technical-logs/v1'


@pytest.fixture
def journal(tmp_path):
    """Closes only an isolated temporary technical journal."""
    value = TechnicalJournal(tmp_path, TechnicalSettings(BatchMaxDelayMs=1))
    yield value
    value.close()


def record(message='Operation %s', args=('complete',), **extra):
    """Builds an actual stdlib record without mutating global logger configuration."""
    value = logging.LogRecord('atlas.test', logging.ERROR, __file__, 0, message, args, None)
    value.__dict__.update(extra)
    return value


def client_for(journal):
    """Mounts the independent feature router with real loopback peer metadata."""
    app = FastAPI()
    register_technical_routes(app, journal)
    return TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 9))


def seed(journal, count, message='Operation %s'):
    """Commits bounded test batches so tests do not rely on worker scheduling luck."""
    for index in range(count):
        assert journal.submit(record(message, (str(index),)))
        if index % 10 == 9:
            assert journal.flush()
    assert journal.flush()


def test_native_error_context_redaction_and_no_live_objects(journal):
    handler = TechnicalHandler(journal)
    logger = logging.getLogger('atlas.technical-test')
    logger.addHandler(handler)
    try:
        with log_context(operationId='op-1', traceId='trace-1'):
            try:
                raise ValueError('password=hidden Bearer secret-token')
            except ValueError:
                logger.exception('Failed token=%s', 'other-secret', extra={'properties': {'durationMs': 12, 'password': 'hidden', 'body': 'private', 'safe': 'api_key=secret'}})
        assert journal.flush()
        event = journal.disk.read()[0]
        assert event['schemaVersion'] == 'technical-log/v1'
        assert event['level'] == 'ERROR' and event['operationId'] == 'op-1'
        assert event['traceId'] == 'trace-1' and event['taskId'] is None
        assert event['exception']['type'] == 'ValueError'
        assert 'test_native_error_context' in event['exception']['stackTrace']
        raw = json.dumps(event)
        assert all(secret not in raw for secret in ('hidden', 'other-secret', 'secret-token', 'private'))
        assert event['properties']['durationMs'] == 12
        assert event['properties']['password'] == '[REDACTED]'
        assert from_record(record())['operationId'] is None
    finally:
        logger.removeHandler(handler)


def test_non_text_and_surrogates_cannot_break_logging(journal):
    class Hostile:
        """Would fail if the bridge attempted to retain/format arbitrary objects."""
        def __str__(self):
            raise RuntimeError('must not stringify')
    handler = TechnicalHandler(journal)
    handler.handle(record('%s \ud800', (Hostile(),), properties={'object': Hostile()}))
    handler.handle(record(Hostile(), ()))
    assert journal.flush()
    events = journal.disk.read()
    assert len(events) == 2
    assert 'object' not in events[0]['properties']
    assert '\ud800' not in events[0]['message']


def test_queue_contention_is_no_wait_and_counted(journal):
    journal.lock.acquire()
    try:
        start = time.monotonic()
        assert not journal.submit(record())
        assert time.monotonic() - start < .1
        assert journal.dropped['full_queue'] == 1
    finally:
        journal.lock.release()


def test_access_polling_does_not_feed_itself(journal):
    handler = TechnicalHandler(journal)
    poll = logging.LogRecord('uvicorn.access', 20, '', 0, '%s - "%s %s HTTP/%s" %d', ('127.0.0.1', 'GET', BASE + '/events?limit=2', '1.1', 200), None)
    handler.handle(poll)
    assert journal.status()['latestSequence'] == '0'
    poll.args = ('127.0.0.1', 'GET', '/api/projects', '1.1', 200)
    handler.handle(poll)
    assert journal.flush()
    assert journal.status()['latestSequence'] == '1'


def test_disk_failure_isolated_and_health_exposed(journal, monkeypatch):
    def fail(events):
        """Simulates denied/full storage after successful service startup."""
        raise OSError('sensitive path /private/secret')
    monkeypatch.setattr(journal.disk, 'append', fail)
    assert journal.submit(record())
    assert not journal.flush()
    assert not journal.submit(record())
    with client_for(journal) as client:
        status = client.get(BASE + '/status').json()
        assert status['health']['status'] == 'degraded'
        assert status['health']['droppedTotal'] >= 2
        assert 'private' not in json.dumps(status)


def test_status_and_forward_backward_exactly_once(journal):
    seed(journal, 17)
    with client_for(journal) as client:
        status = client.get(BASE + '/status').json()
        assert status['schemaVersion'] == 'technical-logs-http/v1'
        assert status['maxSizeBytes'] == 100_000_000
        assert status['limits'] == {'maxPageEvents': 100, 'maxPageBytes': 262144}
        for direction in ('forward', 'backward'):
            params = {'limit': 5}
            if direction == 'forward':
                params.update(journalEpoch=status['journalEpoch'], afterSequence='0')
            seen = []
            while True:
                response = client.get(BASE + '/events', params=params)
                page = response.json()
                assert response.status_code == 200
                assert response.headers['cache-control'] == 'no-store'
                rows = [int(event['sequence']) for event in page['events']]
                assert rows == sorted(rows)
                seen.extend(rows)
                if not page['hasMore']:
                    break
                params = {'limit': 5, 'journalEpoch': page['nextCursor']['journalEpoch'],
                          'afterSequence' if direction == 'forward' else 'beforeSequence': page['nextCursor']['sequence']}
            assert sorted(seen) == list(range(1, 18))
            assert len(set(seen)) == 17
        assert journal.status()['latestSequence'] == '17'
        expired = client.get(BASE + '/events', params={'journalEpoch': 'old', 'afterSequence': '0'})
        assert expired.status_code == 409 and expired.json()['resetCursor'] == status['cursor']


@pytest.mark.parametrize('query', ['q=x', 'level=ERROR', 'limit=01', 'limit=101', 'limit=0', 'limit=', 'limit=1&limit=2',
    'journalEpoch=x', 'afterSequence=0', 'journalEpoch=e&afterSequence=01', 'journalEpoch=e&afterSequence=9223372036854775808',
    'journalEpoch=e&afterSequence=0&beforeSequence=1', 'limit=%', 'limit=%GG', 'journalEpoch=%80&afterSequence=0',
    'journalEpoch=%ED%A0%80&afterSequence=0', 'l%69mit=1&limit=2'])
def test_strict_queries_safe_errors(journal, query):
    with client_for(journal) as client:
        response = client.get(BASE + '/events?' + query)
        assert response.status_code == 400
        assert response.json() == {'schemaVersion': 'technical-logs-http/v1', 'application': 'atlas',
            'error': {'code': 'invalid_query', 'message': 'Invalid query parameters.', 'retryable': False}, 'resetCursor': None}
        assert client.get(BASE + '/status?' + query).status_code == 400


def test_local_security_and_unavailable(tmp_path, journal):
    with client_for(journal) as client:
        assert client.get(BASE + '/events', headers={'Host': 'evil.example'}).status_code == 403
        assert client.get(BASE + '/events', headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.get(BASE + '/events', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
        with TestClient(client.app, base_url='http://127.0.0.1', client=('192.0.2.1', 1)) as remote:
            assert remote.get(BASE + '/events').status_code == 403
    broken = TechnicalJournal(tmp_path, TechnicalSettings(MaxSizeBytes=0))
    with client_for(broken) as client:
        response = client.get(BASE + '/status')
        assert response.status_code == 503 and response.json()['error']['retryable']
    broken.close()


def test_multibyte_pages_are_byte_bounded_and_no_holes(journal):
    seed(journal, 100, '漢' * 1800 + '%s')
    with client_for(journal) as client:
        epoch = journal.status()['journalEpoch']
        for direction in ('forward', 'backward'):
            params = {'journalEpoch': epoch, 'afterSequence': '0'} if direction == 'forward' else {}
            seen = []
            while True:
                response = client.get(BASE + '/events', params=params)
                assert response.status_code == 200 and len(response.content) <= 262144
                page = response.json()
                seen.extend(event['sequence'] for event in page['events'])
                if not page['hasMore']:
                    break
                assert page['truncated']
                params = {'journalEpoch': epoch, 'afterSequence' if direction == 'forward' else 'beforeSequence': page['nextCursor']['sequence']}
            assert sorted(map(int, seen)) == list(range(1, 101))


def test_ring_quota_recovery_and_expired_prefix(tmp_path):
    settings = TechnicalSettings(MaxSizeBytes=100_000, BatchMaxBytes=16384, BatchMaxDelayMs=1)
    value = TechnicalJournal(tmp_path, settings)
    seed(value, 70, 'x' * 1800 + '%s')
    status = value.status()
    assert status['usedBytes'] <= settings.MaxSizeBytes and int(status['oldestSequence']) > 1
    epoch = status['journalEpoch']
    with client_for(value) as client:
        assert client.get(BASE + '/events', params={'journalEpoch': epoch, 'afterSequence': '0'}).status_code == 409
    value.close()
    recovered = TechnicalJournal(tmp_path, settings)
    assert recovered.status()['cursor'] == status['cursor']
    assert recovered.disk.read()[-1]['sequence'] == '70'
    recovered.close()


def test_16k_recovery_and_corruption_reset(tmp_path):
    settings = TechnicalSettings(BatchMaxDelayMs=1)
    value = TechnicalJournal(tmp_path, settings)
    seed(value, 10, '漢' * 1900 + '%s')
    epoch = value.status()['journalEpoch']
    assert any(path.stat().st_size > 8192 for path in value.disk.directory.glob('segment-*'))
    value.close()
    recovered = TechnicalJournal(tmp_path, settings)
    assert recovered.status()['journalEpoch'] == epoch and len(recovered.disk.read()) == 10
    recovered.close()
    directory = tmp_path / 'technical-logs'
    paths = sorted(directory.glob('segment-*'))
    with paths[-1].open('ab') as handle:
        handle.write(b'{partial')
    recovered = TechnicalJournal(tmp_path, settings)
    assert recovered.status()['journalEpoch'] == epoch
    recovered.close()
    path = sorted(directory.glob('segment-*'))[0]
    raw = path.read_bytes().replace(b'"atlas"', b'"other"', 1)
    path.write_bytes(raw)
    recovered = TechnicalJournal(tmp_path, settings)
    assert recovered.status()['journalEpoch'] != epoch and recovered.disk.recovery_reset
    recovered.close()


def test_format_widths_and_exception_objects_are_bounded(journal):
    """Untrusted width/precision and foreign objects cannot cause huge formatting allocations."""
    for template, arguments in (("%999999999s", ("x",)), ("%.*s", (999999999, "x")), ("%.999999999f", (1.2,))):
        assert journal.submit(record(template, arguments))
    assert journal.flush()
    assert len(journal.disk.read()) == 3
    assert all(len(row['message']) < 64 for row in journal.disk.read())


def test_internal_missing_segment_changes_epoch(tmp_path):
    """Prefix eviction is valid; a missing committed interior segment must not falsely replay continuity."""
    settings = TechnicalSettings(MaxSizeBytes=1_000_000, BatchMaxBytes=16384, BatchMaxDelayMs=1)
    value = TechnicalJournal(tmp_path, settings)
    seed(value, 50, 'x' * 1800 + '%s')
    epoch = value.status()['journalEpoch']
    paths = sorted(value.disk.directory.glob('segment-*'))
    assert len(paths) >= 3
    value.close()
    paths[1].unlink()
    recovered = TechnicalJournal(tmp_path, settings)
    assert recovered.status()['journalEpoch'] != epoch and recovered.disk.recovery_reset
    recovered.close()


def test_native_metrics_truthful_memory_units():
    """Current RSS and peak RSS are distinct native values; no maximum is relabeled as current."""
    from atlas.technical_logs.metrics import native_process
    result = native_process()
    assert result['processId'] > 0 and result['uptimeSeconds'] >= 0
    assert result['residentBytes'] is None or result['residentBytes'] > 0
    assert result['peakResidentBytes'] is None or result['peakResidentBytes'] > 0


def test_metrics_owned_journals_cache_and_nullable_failures(journal, monkeypatch):
    from atlas.technical_logs.metrics import Diagnostics
    seed(journal, 1)
    value = Diagnostics((journal,))
    first = value.snapshot()
    assert first['schemaVersion'] == 'service-diagnostics/v1'
    assert first['storage']['journalBytes'] == journal.disk.used_bytes()
    assert first['storage']['dataBytes'] is None
    assert first['storage']['measurement'] == 'journals-only'
    assert str(journal.disk.directory) not in json.dumps(first)
    seed(journal, 1)
    assert value.snapshot()['storage'] == first['storage']
    value.expires = 0
    assert value.snapshot()['storage']['journalBytes'] > first['storage']['journalBytes']
    def unavailable():
        """Makes native counters unavailable without leaking exception details."""
        raise OSError('private path')
    monkeypatch.setattr('atlas.technical_logs.metrics.native_process', unavailable)
    failed = value.snapshot()
    assert failed['process']['residentBytes'] is None and failed['health']['status'] == 'degraded'
    journal.disk.directory = Path('/nonexistent/private')
    value.expires = 0
    failed = value.snapshot()
    assert failed['storage']['journalBytes'] is None and 'private' not in json.dumps(failed)


def test_metrics_local_route_and_poll_suppression(journal):
    from atlas.technical_logs.metrics import register_diagnostics_routes
    app = FastAPI()
    register_diagnostics_routes(app, (journal,))
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 1)) as client:
        response = client.get('/api/diagnostics/v1/metrics')
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert client.get('/api/diagnostics/v1/metrics', headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.post('/api/diagnostics/v1/metrics').status_code == 405
    handler = TechnicalHandler(journal)
    poll = logging.LogRecord('uvicorn.access', 20, '', 0, '%s', ('peer', 'GET', '/api/diagnostics/v1/metrics', '1.1', 200), None)
    handler.handle(poll)
    assert journal.status()['latestSequence'] == '0'


def test_manifest_failure_never_publishes_pending_head(journal, monkeypatch):
    seed(journal, 1)
    cursor = journal.status()['cursor']
    def fail(sequence=None):
        """Fails the final durable manifest commit after segment bytes reached disk."""
        raise OSError('manifest failure')
    monkeypatch.setattr(journal.disk, '_manifest', fail)
    assert journal.submit(record('pending', ()))
    assert not journal.flush()
    assert journal.status()['cursor'] == cursor
    assert [row['sequence'] for row in journal.disk.read()] == ['1']
    with client_for(journal) as client:
        page = client.get(BASE + '/events').json()
        assert page['highWatermark'] == cursor and len(page['events']) == 1


def test_uptime_uses_monotonic_after_native_age_anchor(monkeypatch):
    from atlas.technical_logs import metrics
    monkeypatch.setattr(metrics, 'UPTIME_ANCHOR', None)
    monkeypatch.setattr(metrics.time, 'time', lambda: 1000)
    monotonic = [20]
    monkeypatch.setattr(metrics.time, 'monotonic', lambda: monotonic[0])
    assert metrics.process_uptime(900) == 100
    monkeypatch.setattr(metrics.time, 'time', lambda: 100000)
    monotonic[0] = 25
    assert metrics.process_uptime(900) == 105


def test_owned_data_usage_cache_and_symlink_boundary(tmp_path, journal):
    from atlas.technical_logs.metrics import Diagnostics
    owned = tmp_path / 'owned'
    owned.mkdir()
    (owned / 'database').write_bytes(b'x' * 123)
    value = Diagnostics((journal,), owned_data_dir=owned)
    first = value.snapshot()
    assert first['storage']['measurement'] == 'owned-data' and first['storage']['dataBytes'] == 123
    (owned / 'database').write_bytes(b'x' * 456)
    assert value.snapshot()['storage']['dataBytes'] == 123
    value.data_expires = 0
    assert value.snapshot()['storage']['dataBytes'] == 456
    (owned / 'outside').symlink_to('/tmp')
    value.data_expires = 0
    failed = value.snapshot()
    assert failed['storage']['dataBytes'] is None and failed['health']['status'] == 'degraded'
    assert failed['health']['errorCode'] == 'owned_data_measurement_unavailable'


def test_owned_data_depth_and_entry_limit_are_unknown(tmp_path, journal):
    from atlas.technical_logs.metrics import owned_directory_bytes
    directory = tmp_path / 'owned'
    directory.mkdir()
    for index in range(9):
        directory = directory / 'deeper'
        directory.mkdir()
    with pytest.raises(OSError):
        owned_directory_bytes(tmp_path / 'owned')


@pytest.mark.parametrize('query', ['limit=1', 'x=a&x=b', 'q=%80', 'q=%GG', 'q=%'])
def test_metrics_strict_query_error_contract(journal, query):
    from atlas.technical_logs.metrics import register_diagnostics_routes
    app = FastAPI()
    register_diagnostics_routes(app, (journal,))
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 1)) as client:
        response = client.get('/api/diagnostics/v1/metrics?' + query)
        assert response.status_code == 400
        assert response.json() == {'schemaVersion': 'service-diagnostics/v1', 'application': 'atlas',
            'error': {'code': 'invalid_query', 'message': 'Invalid query parameters.', 'retryable': False}, 'resetCursor': None}
        denied = client.get('/api/diagnostics/v1/metrics', headers={'Origin': 'https://evil.example'})
        assert denied.status_code == 403 and denied.json()['error']['code'] == 'forbidden'


def test_activity_reader_does_not_create_technical_polling_feedback(journal):
    handler = TechnicalHandler(journal)
    for path in ('/api/agent-activity/v1/status', '/api/agent-activity/v1/events?limit=10'):
        handler.handle(logging.LogRecord('uvicorn.access', 20, '', 0, '%s', ('peer', 'GET', path, '1.1', 200), None))
    assert journal.status()['latestSequence'] == '0'
