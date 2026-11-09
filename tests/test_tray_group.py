"""Cross-process restoration must survive stale, unready and malformed peers."""
import pytest
from desktop_support.tray_group import PeerGroup, decode_presence


def group(pid=200, alive=None):
    """A synthetic OS catalogue never reads real process or service data."""
    alive = {100: 'code-atlas', 300: 'ag-tasks'} if alive is None else alive
    return PeerGroup('ag-design', pid, lambda app, candidate: alive.get(candidate) == app)


@pytest.mark.parametrize('value', [None, 1, '', '1|other|1|1', '1|code-atlas|-1|1',
    '1|code-atlas|2147483648|1', '1|code-atlas|١|1', '1|code-atlas|100|yes',
    '1|code-atlas|100|1|extra', 'x' * 161])
def test_malformed_messages_cannot_join(value):
    """The namespace is not a parser for page payloads or executable commands."""
    assert decode_presence(value) is None
    assert not group().receive(value, 0)


def test_lowest_verified_pid_wins_only_after_native_readiness():
    """An unready lower PID cannot take away the follower's only restore icon."""
    state = group()
    assert state.receive('1|code-atlas|100|0', 0)
    assert state.leader(1) == 100
    assert not state.ready_leader(1)
    assert state.receive('1|code-atlas|100|1', 1)
    assert state.ready_leader(1)
    assert state.receive('1|ag-tasks|300|1', 1)
    assert state.leader(2) == 100


def test_dead_reused_and_expired_pids_cannot_keep_a_hidden_owner():
    """OS death, PID reuse and heartbeat loss all trigger a local owner fallback."""
    alive = {100: 'code-atlas'}
    state = group(alive=alive)
    assert state.receive('1|code-atlas|100|1', 0)
    alive[100] = 'other'
    assert state.leader(1) == 200
    alive[100] = 'code-atlas'
    assert state.receive('1|code-atlas|100|1', 2)
    assert state.leader(9) == 200
    assert not state.peers


def test_unknown_or_self_claim_never_replaces_native_readiness():
    """A notification cannot falsify this process's own status item or OS bundle identity."""
    state = group()
    assert not state.receive('1|ag-tasks|100|1', 0)
    assert not state.receive('1|ag-design|200|1', 0)
    assert state.leader(0) == 200
    assert not state.ready_leader(0)


def test_peer_budget_is_fixed_and_recovers_after_expiry():
    """A message flood cannot grow the native menu membership without bound."""
    state = PeerGroup('ag-design', 200, lambda *args: True)
    for pid in range(300, 400):
        state.receive(f'1|ag-tasks|{pid}|1', 0)
    assert len(state.peers) == 32
    assert state.receive('1|ag-tasks|500|1', 7)
    assert len(state.peers) == 1


def test_presence_flood_is_bounded_before_os_queries(monkeypatch):
    """1000 messages cannot perform1000 native lookups or reconcile status items on every receipt."""
    from desktop_support.cocoa_tray import CocoaTray
    from desktop_support.tray_group import EventBudget
    lookups = []
    tray = CocoaTray.__new__(CocoaTray)
    tray.closed = False
    tray.presence_budget = EventBudget(32, 2)
    tray.group = PeerGroup('ag-design', 200, lambda app, pid: lookups.append(pid) or True)
    tray.reconcile = lambda: pytest.fail('Incoming messages must not drive native reconciliation')
    monkeypatch.setattr('desktop_support.cocoa_tray.monotonic', lambda: 100.0)
    for _ in range(1000):
        tray.receive_presence('1|code-atlas|100|1')
    assert len(lookups) <= 32
    assert len(tray.group.peers) == 1
    monkeypatch.setattr('desktop_support.cocoa_tray.monotonic', lambda: 102.0)
    tray.receive_presence('1|code-atlas|100|1')
    assert len(lookups) == 33


def test_quit_flood_creates_one_worker_before_shutdown_callback(monkeypatch):
    """Deduplication belongs before worker creation even when the callback has not run yet."""
    from types import SimpleNamespace
    from desktop_support.cocoa_tray import CocoaTray
    workers = []
    class PendingWorker:
        """Do not execute shutdown: model1000messages arriving before the worker is scheduled."""
        def __init__(self, **kwargs):
            workers.append(kwargs)
        def start(self):
            """Leave the worker pending so callback-level idempotency cannot conceal amplification."""
            pass
    monkeypatch.setattr('desktop_support.cocoa_tray.Thread', PendingWorker)
    tray = CocoaTray.__new__(CocoaTray)
    tray.closed = False
    tray.quitting = False
    tray.handle = SimpleNamespace(on_quit=lambda: None)
    for _ in range(1000):
        tray.command('quit')
    assert len(workers) == 1
