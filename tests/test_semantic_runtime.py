"""Optional analyzer lifecycle never delays baseline reads or executes source projects."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
from threading import Event, Timer
import time

import pytest

from atlas.semantic_models import SemanticAvailability, SemanticContext, SemanticCoverage, SemanticResult
from atlas.semantic_runtime import DISCOVERY_TTL, ProbeOutcome, SemanticRuntime
from atlas.semantic_process import SemanticProcessError, discover_executable, run_bounded

HASH = 'a' * 64
ENGINE_HASH = 'b' * 64


class Clock:
    """An injected UTC clock makes TTL and backward clock jumps deterministic."""

    def __init__(self):
        self.value = 1_800_000_000.0

    def __call__(self):
        return self.value


class Provider:
    """A host-owned adapter fixture with observable probe and analysis boundaries."""
    provider_id = 'test-provider'

    def __init__(self, asset, status='available', gate=None):
        self.asset, self.probe_status, self.gate = asset, status, gate
        self.probes = self.analyses = 0

    def probe(self, context, cancel):
        """Wait cooperatively when testing singleflight, without launching a real engine."""
        self.probes += 1
        if self.gate is not None:
            while not self.gate.wait(0.005) and not cancel.is_set():
                pass
        return ProbeOutcome(SemanticAvailability(self.probe_status,
                            engine_fingerprint=ENGINE_HASH if self.probe_status == 'available' else None,
                            capabilities=('definition',)), (self.asset,) if self.probe_status == 'available' else ())

    def analyze(self, context, probe, request, cancel):
        """Return real contract DTOs, not an invented graph or a runtime call trace."""
        self.analyses += 1
        return SemanticResult(context, self.provider_id, HASH, ENGINE_HASH, 'partial',
                              coverage={'definition': SemanticCoverage('partial', 1, 2)})


@pytest.fixture
def setup_runtime(tmp_path):
    """One installation fixture is shared by several snapshots of the same environment."""
    asset = tmp_path / 'analyzer'
    asset.write_text('version one')
    context = SemanticContext('snapshot-one', 'workspace', str(tmp_path), 'default', HASH,
                              {'source.py': HASH}, HASH)
    provider, clock = Provider(asset), Clock()
    runtime = SemanticRuntime(tmp_path / 'cache', {provider.provider_id: provider}, clock=clock)
    yield runtime, context, provider, clock
    runtime.close()


def test_polling_and_cache_hits_do_not_probe_scan_or_stat_assets(setup_runtime, monkeypatch):
    runtime, context, provider, clock = setup_runtime
    assert runtime.status(context, ['test-provider'])['test-provider'].status == 'unchecked'
    assert provider.probes == 0
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle()
    monkeypatch.setattr('atlas.semantic_runtime._asset_stamps', lambda _: pytest.fail('cache hit statted engine'))
    for _ in range(30):
        status = runtime.on_use(replace(context, snapshot_id='another-map'), ['test-provider'])
        assert status['test-provider'].status == 'available'
    assert provider.probes == 1
    assert runtime.result_for(context, ['test-provider']) == {}
    assert status['test-provider'].next_check_at == clock() + DISCOVERY_TTL


@pytest.mark.parametrize('mutation', ['change', 'delete', 'cancel', 'close', 'availability'])
def test_publication_revalidates_retained_engine_and_cancel_provenance(setup_runtime, mutation):
    """Results can outlive callback completion; final publication must reject later invalidation."""
    runtime, context, provider, _ = setup_runtime
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle()
    result = runtime.submit_analysis(context, 'test-provider', {}).result(timeout=3)
    copied = runtime.result_for(context, ['test-provider'])['test-provider']
    assert runtime.result_is_current(copied)
    assert not runtime.result_is_current(replace(result, context=replace(context, snapshot_id='other')))
    if mutation == 'change':
        provider.asset.write_text('changed engine after callback')
    elif mutation == 'delete':
        provider.asset.unlink()
    elif mutation == 'cancel':
        runtime.cancel(context, ['test-provider'])
    elif mutation == 'close':
        runtime.close()
    else:
        key = runtime._environment_key(context, 'test-provider')
        runtime.records[key]['availability'] = replace(runtime.records[key]['availability'], status='failed')
    assert not runtime.result_is_current(copied)
    assert runtime.result_for(context, ['test-provider'])['test-provider'] == copied
    assert provider.probes == 1 and provider.analyses == 1


@pytest.mark.parametrize('status', ['missing', 'incompatible', 'failed'])
def test_negative_cache_uses_rolling_attempt_ttl(setup_runtime, status):
    runtime, context, provider, clock = setup_runtime
    provider.probe_status = status
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle()
    clock.value += DISCOVERY_TTL - 0.1
    assert runtime.on_use(context, ['test-provider'])['test-provider'].status == status
    assert provider.probes == 1
    clock.value += 0.1
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle() and provider.probes == 2


def test_force_clock_rollback_and_config_change_recheck(setup_runtime):
    runtime, context, provider, clock = setup_runtime
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle()
    runtime.on_use(context, ['test-provider'], force=True)
    assert runtime.wait_for_idle() and provider.probes == 2
    clock.value -= 10
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle() and provider.probes == 3
    runtime.on_use(replace(context, config_fingerprint='c' * 64), ['test-provider'])
    assert runtime.wait_for_idle() and provider.probes == 4


def test_singleflight_across_maps_and_runtime_instances(setup_runtime):
    runtime, context, provider, clock = setup_runtime
    gate = Event()
    provider.gate = gate
    other = SemanticRuntime(runtime.directory, runtime.registry, clock=clock)
    try:
        for _ in range(20):
            runtime.on_use(context, ['test-provider'])
            other.on_use(replace(context, snapshot_id='second'), ['test-provider'])
        gate.set()
        assert runtime.wait_for_idle()
        assert provider.probes == 1
        assert other.status(context, ['test-provider'])['test-provider'].status == 'available'
    finally:
        gate.set()
        other.close()


@pytest.mark.parametrize('status', ['available', 'missing', 'incompatible', 'failed'])
def test_restart_reuses_positive_and_negative_cache_without_probe(setup_runtime, status):
    runtime, context, provider, clock = setup_runtime
    provider.probe_status = status
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle()
    runtime.close()
    with_cache = SemanticRuntime(runtime.directory, runtime.registry, clock=clock)
    try:
        assert with_cache.on_use(context, ['test-provider'])['test-provider'].status == status
        assert provider.probes == 1
    finally:
        with_cache.close()


def test_interrupted_probe_is_repaired_and_remains_cached(setup_runtime):
    runtime, context, provider, clock = setup_runtime
    key = runtime._environment_key(context, provider.provider_id)
    runtime._write(key, {'availability': SemanticAvailability('checking', checked_at=clock(),
                    next_check_at=clock() + DISCOVERY_TTL), 'assets': []})
    status = runtime.on_use(context, ['test-provider'])['test-provider']
    assert status.status == 'failed' and status.reason == 'interrupted-probe'
    assert provider.probes == 0
    runtime.on_use(context, ['test-provider'], force=True)
    assert runtime.wait_for_idle() and provider.probes == 1


def test_cancel_on_off_stops_probe_and_close_rejects_new_jobs(setup_runtime):
    runtime, context, provider, clock = setup_runtime
    provider.gate = Event()
    runtime.on_use(context, ['test-provider'])
    runtime.cancel(context, ['test-provider'])
    assert runtime.wait_for_idle()
    assert runtime.status(context, ['test-provider'])['test-provider'].status == 'failed'
    runtime.close()
    runtime.on_use(context, ['test-provider'], force=True)
    assert provider.probes == 1


def test_asset_change_prevents_analysis_and_results_are_snapshot_specific(setup_runtime):
    runtime, context, provider, clock = setup_runtime
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle()
    result = runtime.submit_analysis(context, 'test-provider', {'files': []}).result(timeout=3)
    assert result.status == 'partial' and provider.analyses == 1
    assert runtime.result_for(context, ['test-provider'])['test-provider'] == result
    assert runtime.result_for(replace(context, snapshot_id='different'), ['test-provider']) == {}
    provider.asset.write_text('version two, replacement')
    assert runtime.submit_analysis(context, 'test-provider', {'files': []}).result(timeout=3) is None
    assert provider.analyses == 1
    assert runtime.status(context, ['test-provider'])['test-provider'].status == 'failed'


def test_public_status_does_not_expose_private_paths_or_apply_results(setup_runtime):
    runtime, context, provider, clock = setup_runtime
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle()
    from dataclasses import asdict
    status = asdict(runtime.status(context, ['test-provider'])['test-provider'])
    assert str(provider.asset) not in json.dumps(status)
    assert runtime.result_for(context, ['test-provider']) == {}
    assert str(provider.asset) in next(runtime.directory.glob('*.json')).read_text()


def test_unknown_strategy_and_corrupt_cache_fall_back_without_exception(setup_runtime):
    runtime, context, provider, clock = setup_runtime
    key = runtime._environment_key(context, 'unknown')
    runtime.directory.mkdir(parents=True)
    runtime._path(key).write_text('{broken')
    runtime.on_use(context, ['unknown'])
    assert runtime.wait_for_idle()
    assert runtime.status(context, ['unknown'])['unknown'].status == 'incompatible'


def test_bounded_queue_and_cancelled_analysis_cannot_publish_late(setup_runtime):
    runtime, context, provider, clock = setup_runtime
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle()
    gate, entered = Event(), Event()

    def delayed_analysis(context, probe, request, cancel):
        """Simulate an engine returning successfully after its map was disabled."""
        entered.set()
        gate.wait(2)
        return SemanticResult(context, 'test-provider', HASH, ENGINE_HASH)

    provider.analyze = delayed_analysis
    runtime.max_pending = 1
    future = runtime.submit_analysis(context, 'test-provider', {'files': []})
    assert entered.wait(1)
    assert runtime.submit_analysis(context, 'test-provider', {'files': []}) is future
    assert runtime.submit_analysis(context, 'test-provider', {'files': [1]}) is None
    runtime.cancel(context, ['test-provider'])
    gate.set()
    assert future.result(timeout=3) is None
    assert runtime.result_for(context, ['test-provider']) == {}


def test_mismatched_engine_result_is_rejected(setup_runtime):
    runtime, context, provider, clock = setup_runtime
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle()
    provider.analyze = lambda context, probe, request, cancel: SemanticResult(
        context, 'test-provider', HASH, 'c' * 64)
    assert runtime.submit_analysis(context, 'test-provider', {}).result(timeout=3) is None
    assert runtime.result_for(context, ['test-provider']) == {}
    assert runtime.status(context, ['test-provider'])['test-provider'].status == 'failed'


def test_engine_removed_after_probe_and_invalid_request_are_bounded(setup_runtime):
    runtime, context, provider, clock = setup_runtime
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle()
    provider.asset.unlink()
    assert runtime.submit_analysis(context, 'test-provider', {}).result(timeout=3) is None
    assert provider.analyses == 0
    with pytest.raises(ValueError, match='budget'):
        runtime.submit_analysis(context, 'test-provider', {'payload': 'x' * (4 * 1024 * 1024)})


def test_cache_storage_failure_preserves_runtime_status_and_baseline(tmp_path):
    broken_directory = tmp_path / 'not-a-directory'
    broken_directory.write_text('file')
    runtime = SemanticRuntime(broken_directory, {})
    context = SemanticContext('s', 'w', str(tmp_path), 't', HASH, {})
    try:
        assert runtime.on_use(context, ['unknown'])['unknown'].status == 'unchecked'
        assert runtime.status(context, ['unknown'])['unknown'].status == 'unchecked'
    finally:
        runtime.close()


def test_discovery_precedence_has_no_execution_or_recursive_search(tmp_path):
    executable = Path(sys.executable)
    assert discover_executable('python', explicit=executable, search_path='') == executable.absolute()
    assert discover_executable('python', explicit=tmp_path / 'missing') is None
    assert discover_executable('missing', project_candidates=[executable], search_path='') == executable.absolute()


def test_real_process_io_is_bounded_and_environment_injection_is_removed(tmp_path):
    result = run_bounded([sys.executable, '-I', '-c',
                          'import os,sys;sys.stdout.buffer.write(sys.stdin.buffer.read());'
                          'sys.stderr.write(str(os.environ.get("NODE_OPTIONS")))'],
                         cwd=tmp_path, input_bytes=b'captured-source',
                         environment={'NODE_OPTIONS': '--require=evil'})
    assert result.returncode == 0 and result.stdout == b'captured-source'
    assert result.stderr == b'None'
    with pytest.raises(SemanticProcessError, match='output-limit'):
        run_bounded([sys.executable, '-I', '-c', 'print("x"*100000)'], cwd=tmp_path, max_output_bytes=128)


def test_real_process_timeout_cancellation_and_no_shell(tmp_path):
    with pytest.raises(SemanticProcessError, match='timeout'):
        run_bounded([sys.executable, '-I', '-c', 'import time;time.sleep(10)'], cwd=tmp_path, timeout=.1)
    cancel = Event()
    cancel.set()
    with pytest.raises(SemanticProcessError, match='cancelled'):
        run_bounded([sys.executable], cwd=tmp_path, cancel=cancel)
    with pytest.raises(ValueError, match='absolute'):
        run_bounded(['python; echo unsafe'], cwd=tmp_path)
    with pytest.raises(SemanticProcessError, match='memory-limit'):
        run_bounded([sys.executable, '-I', '-c', 'import time;time.sleep(10)'], cwd=tmp_path, memory_bytes=1)


def test_cancellation_terminates_running_engine(tmp_path):
    cancel = Event()
    timer = Timer(.1, cancel.set)
    timer.start()
    try:
        with pytest.raises(SemanticProcessError, match='cancelled'):
            run_bounded([sys.executable, '-I', '-c', 'import time;time.sleep(10)'],
                        cwd=tmp_path, cancel=cancel)
    finally:
        timer.cancel()


def test_restricted_process_monitor_has_stable_error_and_stops_engine(tmp_path, monkeypatch):
    import psutil
    import atlas.semantic_process as processes
    launched = []
    original_popen = processes.subprocess.Popen

    def record_process(*arguments, **options):
        """Observe the real owned process to verify cleanup after monitor failure."""
        process = original_popen(*arguments, **options)
        launched.append(process)
        return process

    def denied_children(self, recursive=False):
        """macOS may expose built-in PermissionError rather than psutil.AccessDenied."""
        raise PermissionError('restricted process observation')

    monkeypatch.setattr(processes.subprocess, 'Popen', record_process)
    monkeypatch.setattr(psutil.Process, 'children', denied_children)
    with pytest.raises(SemanticProcessError, match='process-monitor-unavailable'):
        run_bounded([sys.executable, '-I', '-c', 'import time;time.sleep(10)'], cwd=tmp_path)
    assert launched and launched[0].poll() is not None


def test_off_during_final_asset_validation_cannot_publish_result(setup_runtime, monkeypatch):
    runtime, context, provider, clock = setup_runtime
    runtime.on_use(context, ['test-provider'])
    assert runtime.wait_for_idle()
    import atlas.semantic_runtime as module
    original = module._asset_stamps
    entered, release = Event(), Event()
    calls = 0

    def delayed_final_validation(paths):
        """Pause after the old cancellation check, at the publication race boundary."""
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            assert release.wait(2)
        return original(paths)

    monkeypatch.setattr(module, '_asset_stamps', delayed_final_validation)
    future = runtime.submit_analysis(context, 'test-provider', {})
    assert entered.wait(1)
    runtime.cancel(context, ['test-provider'])
    release.set()
    assert future.result(timeout=3) is None
    assert runtime.result_for(context, ['test-provider']) == {}


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process group qualification')
def test_owned_child_cannot_keep_output_pipe_or_process_alive(tmp_path):
    marker = tmp_path / 'child.pid'
    script = ('import subprocess,sys,time;'
              'child=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(20)"]);'
              f'open({str(marker)!r},"w").write(str(child.pid));time.sleep(20)')
    with pytest.raises(SemanticProcessError, match='timeout'):
        run_bounded([sys.executable, '-I', '-c', script], cwd=tmp_path, timeout=.2)
    import psutil
    pid = int(marker.read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
