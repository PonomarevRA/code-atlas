"""Task residency tests exercise expiry, ownership and concurrent computation, not implementation shape."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from atlas.analysis_contexts import AnalysisContexts, AnalysisContextError


def test_use_extends_idle_but_checks_do_not_and_other_tasks_survive():
    """Successful reads renew one task; polling and another owner's activity do not."""
    now = [0]
    scopes = AnalysisContexts(clock=lambda: now[0], idle_seconds=20)
    one = scopes.open('p', 's')['contextId']
    two = scopes.open('p', 's')['contextId']
    now[0] = 15
    scopes.check(one, 'p', 's')
    scopes.touch(two, 'p', 's')
    now[0] = 21
    with pytest.raises(AnalysisContextError, match='expired'):
        scopes.check(one, 'p', 's')
    scopes.check(two, 'p', 's')
    with pytest.raises(AnalysisContextError) as error:
        scopes.check(two, 'other', 's')
    assert error.value.status == 409
    scopes.release(two, 'p', 's')
    with pytest.raises(AnalysisContextError):
        scopes.check(two, 'p', 's')


def test_concurrent_same_key_is_single_flight_and_different_source_is_fresh():
    """A task computes one immutable source revision once, with independent later revisions."""
    scopes = AnalysisContexts()
    identifier = scopes.open('p', 's')['contextId']
    entered, release = Event(), Event()
    calls = []

    def factory():
        """Hold real concurrent readers until the main test releases the one computation."""
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return ('tree', len(calls))

    def read(_):
        """Ask the public cache for the same source revision."""
        return scopes.value(identifier, 'p', 's', '', ('file', 'hash1'), factory, lambda _: (5, 1))

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(read, index) for index in range(4)]
        assert entered.wait(5)
        release.set()
        assert [job.result() for job in futures] == [('tree', 1)] * 4
    assert calls == [1]
    result = scopes.value(identifier, 'p', 's', '', ('file', 'hash2'), lambda: 'new', lambda _: (5, 1))
    assert result == 'new'


@pytest.mark.parametrize('retire', ['expire', 'close', 'release'])
def test_retired_inflight_work_never_resurrects(retire):
    """Elapsed TTL and explicit ownership changes fence publication after expensive work."""
    now = [0]
    scopes = AnalysisContexts(clock=lambda: now[0], idle_seconds=20)
    identifier = scopes.open('p', 's')['contextId']

    def factory():
        """Retire the context during computation to reproduce a publication race deterministically."""
        if retire == 'expire':
            now[0] = 21
        elif retire == 'close':
            scopes.close()
        else:
            scopes.release(identifier, 'p', 's')
        return 'late'

    with pytest.raises(AnalysisContextError):
        scopes.value(identifier, 'p', 's', '', 'key', factory, lambda _: (4, 0))
    assert not scopes.contexts


def test_capacity_and_oversize_do_not_evict_another_owner():
    """A full service rejects new ownership; oversized results are computed but never retained."""
    scopes = AnalysisContexts(max_contexts=1, max_context_bytes=8)
    identifier = scopes.open('p', 's', 'v1')['contextId']
    with pytest.raises(AnalysisContextError) as error:
        scopes.open('p', 's')
    assert error.value.status == 429
    calls = []
    for _ in range(2):
        assert scopes.value(identifier, 'p', 's', 'v1', 'large',
                            lambda: calls.append(1) or 'large', lambda _: (9, 0)) == 'large'
    assert len(calls) == 2
    with pytest.raises(AnalysisContextError) as error:
        scopes.check(identifier, 'p', 's', 'v2')
    assert error.value.status == 409
    # Settings changes must not prevent a caller releasing its old context.
    scopes.release(identifier, 'p', 's', 'v2')


def test_replacing_entries_preserves_byte_and_ast_quotas():
    """Per-task eviction bounds retained trees while allowing the next useful computation."""
    scopes = AnalysisContexts(max_context_bytes=10, max_units=3)
    identifier = scopes.open('p', 's')['contextId']
    for index in range(5):
        scopes.value(identifier, 'p', 's', '', index, lambda: index, lambda _: (4, 2))
    context = scopes.contexts[identifier]
    assert context.bytes <= 10 and context.units <= 3
    assert scopes.value(identifier, 'p', 's', '', 4, lambda: pytest.fail('lost latest result'), lambda _: (4, 2)) == 4
