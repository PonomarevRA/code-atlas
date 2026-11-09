"""Real subprocess contract tests for source-only projection generations."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
import io
from pathlib import Path
import shutil
import struct
from threading import Event
from types import SimpleNamespace
import time

import pytest

from atlas.core_generations import BundleArtifact, CoreGenerations, GenerationError, build_bundle
from atlas.core_generations.artifacts import FILES, canonical
from atlas.core_generations.protocol import MAX_FRAME_BYTES, decode_payload, encode_frame
from atlas.module_analysis import project_modules

ROOT = Path(__file__).resolve().parents[1]


def projection():
    """Capture a tiny immutable synthetic graph through the real host projection."""
    return project_modules({'analysisStatus': 'complete', 'nodes': [
        {'id': 'a', 'file': 'one/a.py'}, {'id': 'b', 'file': 'two/b.py'}],
        'edges': [{'source': 'a', 'target': 'b', 'relation': 'imports'}]}, snapshot_id='s')


@pytest.fixture
def supervisor():
    """Always close owned children, including when a negative assertion fails."""
    owner = CoreGenerations()
    try:
        yield owner
    finally:
        owner.close()


def bundle(tmp_path, generation, *, delayed=False, variant=False, broken=False,
           delay_seconds=5, exit_on_request=False):
    """Copy actual compute sources; test variants wrap real analysis, never echo it."""
    root = tmp_path / ('source-'+generation)
    root.mkdir()
    for name in FILES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    compute = root / 'atlas/module_compute.py'
    if variant or delayed or broken or exit_on_request:
        with compute.open('a') as stream:
            stream.write("\n_original_compute = compute_module_projection\n")
            stream.write("def compute_module_projection(projection, *, algorithms=('current',), seed=42, resolution=1.0):\n")
            if delayed:
                stream.write("    if projection.get('projectionHash') == 'slow':\n        __import__('time').sleep("+str(delay_seconds)+")\n")
            if exit_on_request:
                stream.write("    if projection.get('projectionHash') == 'exit':\n        __import__('os')._exit(17)\n")
            if broken:
                stream.write("    return {'assessment': {}, 'comparison': {}}\n")
            else:
                stream.write("    computed = _original_compute(projection, algorithms=algorithms, seed=seed, resolution=resolution)\n")
                stream.write("    return {'assessment': dict(computed['assessment']), 'comparison': dict(computed['comparison'])}\n")
    return build_bundle(root, tmp_path / ('bundle-'+generation), generation_id=generation)


def without_elapsed(value):
    """Exclude per-proposal timing only when comparing deterministic compute facts."""
    value = deepcopy(value)
    for item in value['comparison']['proposals']:
        item.pop('elapsedSeconds', None)
    return value


def wait_busy(supervisor, generation):
    """Use observable supervisor state rather than arbitrary sleeps for drain tests."""
    deadline = time.monotonic()+3
    while time.monotonic() < deadline:
        if any(item['generationId'] == generation and item['busy'] for item in supervisor.state()['generations']):
            return
        time.sleep(0.01)
    raise AssertionError('Worker did not enter a pinned request')


def test_actual_source_process_switch_preserves_compute_and_input(tmp_path, supervisor):
    """N+1 imports different actual compute code and produces equivalent real analysis."""
    first = supervisor.prepare(bundle(tmp_path, 'N'))
    supervisor.activate('N')
    captured = projection()
    original = deepcopy(captured)
    before = supervisor.compute(captured, algorithms=['current', 'louvain'])
    second = supervisor.prepare(bundle(tmp_path, 'N1', variant=True))
    assert first['pid'] != second['pid']
    assert first['computeHash'] != second['computeHash']
    assert first['bundleHash'] != second['bundleHash']
    assert supervisor.activate('N1') == {'previous': 'N', 'current': 'N1'}
    after = supervisor.compute(captured, algorithms=['current', 'louvain'])
    assert without_elapsed(before) == without_elapsed(after)
    assert captured == original
    assert after['assessment']['metrics']['externalDependencyWeight'] == 1
    assert supervisor.retire('N')['forced'] is False


def test_prepared_copy_survives_original_artifact_changes(tmp_path, supervisor):
    """A ready generation imports supervisor-owned captured bytes, not mutable source."""
    artifact = bundle(tmp_path, 'N')
    supervisor.prepare(artifact)
    (artifact.directory / 'atlas/module_compute.py').write_text('raise RuntimeError()')
    supervisor.activate('N')
    assert supervisor.compute(projection(), algorithms=['current'])['assessment']['status'] == 'observed'


@pytest.mark.parametrize('field,value', [('protocol', 'v2'), ('semantics', 'v2'), ('storage', 'writer'),
                                         ('runtime', {'python': 'other', 'networkx': 'other'})])
def test_incompatible_profile_never_switches_current(tmp_path, supervisor, field, value):
    """Self-consistent manifest hash still cannot bypass semantic/storage/profile gates."""
    supervisor.prepare(bundle(tmp_path, 'N'))
    supervisor.activate('N')
    artifact = bundle(tmp_path, 'bad')
    path = artifact.directory / 'manifest.json'
    manifest = json.loads(path.read_bytes()); manifest[field] = value
    encoded = canonical(manifest); path.write_bytes(encoded)
    with pytest.raises(GenerationError, match='incompatible'):
        supervisor.prepare(BundleArtifact(artifact.directory, sha256(encoded).hexdigest()))
    assert supervisor.state()['active'] == 'N'
    assert supervisor.compute(projection(), algorithms=['current'])


@pytest.mark.parametrize('change', ['source', 'manifest', 'symlink'])
def test_changed_artifact_bytes_rejected_before_import(tmp_path, supervisor, change):
    """Mutating approved identity or following a link cannot prepare a process."""
    artifact = bundle(tmp_path, 'bad')
    path = artifact.directory / ('manifest.json' if change == 'manifest' else 'atlas/module_compute.py')
    if change == 'symlink':
        victim = tmp_path / 'victim.py'; victim.write_text('raise AssertionError()')
        path.unlink(); path.symlink_to(victim)
    else:
        path.write_bytes(path.read_bytes()+b'\n# changed')
    with pytest.raises(GenerationError):
        supervisor.prepare(artifact)
    assert supervisor.state()['generations'] == []


def test_readiness_requires_actual_analysis_canary(tmp_path, supervisor):
    """A live handshake/PID with an empty fake computation is not readiness."""
    with pytest.raises(GenerationError, match='canary'):
        supervisor.prepare(bundle(tmp_path, 'bad', broken=True))
    assert supervisor.state()['generations'] == []


def test_busy_and_drain_pin_then_force_only_old_generation(tmp_path, supervisor):
    """An old request never migrates/replays; new dispatch uses ready successor."""
    first = supervisor.prepare(bundle(tmp_path, 'N', delayed=True))
    supervisor.activate('N')
    slow = projection(); slow['projectionHash'] = 'slow'
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(supervisor.compute, slow, algorithms=['current'])
        wait_busy(supervisor, 'N')
        with pytest.raises(GenerationError) as busy:
            supervisor.compute(projection(), algorithms=['current'])
        assert busy.value.status == 429
        second = supervisor.prepare(bundle(tmp_path, 'N1', variant=True))
        supervisor.activate('N1')
        assert supervisor.compute(projection(), algorithms=['current'])
        retired = supervisor.retire('N', deadline=time.monotonic()+0.05)
        assert retired == {'generationId': 'N', 'forced': True, 'pid': first['pid']}
        with pytest.raises(GenerationError) as old:
            future.result(timeout=2)
        assert old.value.code == 'worker_unavailable'
        assert supervisor.state()['generations'][0]['pid'] == second['pid']


def test_old_request_completes_on_original_generation_during_graceful_drain(tmp_path, supervisor):
    """A switch affects future calls; an old pinned response remains deliverable."""
    supervisor.prepare(bundle(tmp_path, 'N', delayed=True, delay_seconds=0.25))
    supervisor.activate('N')
    slow = projection(); slow['projectionHash'] = 'slow'
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(supervisor.compute, slow, algorithms=['current'])
        wait_busy(supervisor, 'N')
        supervisor.prepare(bundle(tmp_path, 'N1', variant=True))
        supervisor.activate('N1')
        assert supervisor.compute(projection(), algorithms=['current'])['assessment']['projectionHash'] != 'slow'
        retired = supervisor.retire('N', deadline=time.monotonic()+2)
        assert retired['forced'] is False
        assert future.result(timeout=1)['assessment']['projectionHash'] == 'slow'


def test_child_exit_is_explicit_and_never_replayed(tmp_path, supervisor):
    """A real process exit invalidates its generation instead of using another one."""
    supervisor.prepare(bundle(tmp_path, 'N', exit_on_request=True)); supervisor.activate('N')
    value = projection(); value['projectionHash'] = 'exit'
    with pytest.raises(GenerationError) as error:
        supervisor.compute(value, algorithms=['current'])
    assert error.value.code == 'worker_unavailable'
    assert supervisor.state()['generations'][0]['alive'] is False


@pytest.mark.parametrize('deadline', [True, 'now', float('nan'), float('inf'), -1])
def test_invalid_drain_deadline_rejected(supervisor, deadline):
    """Deadline mistakes remain typed errors, never an unbounded drain wait."""
    with pytest.raises(GenerationError) as error:
        supervisor.retire('missing', deadline=deadline)
    assert error.value.status == 400


@pytest.mark.parametrize('kind', ['timeout', 'cancel'])
def test_failed_wait_terminates_process_without_retry(tmp_path, supervisor, kind):
    """Cancellation/deadline discard the pinned child and never auto-fallback."""
    supervisor.prepare(bundle(tmp_path, 'N', delayed=True))
    supervisor.activate('N')
    slow = projection(); slow['projectionHash'] = 'slow'
    cancel = Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(supervisor.compute, slow, algorithms=['current'],
                             timeout=0.1 if kind == 'timeout' else 5, cancel_event=cancel)
        wait_busy(supervisor, 'N')
        if kind == 'cancel':
            cancel.set()
        with pytest.raises(GenerationError) as error:
            future.result(timeout=2)
        assert error.value.code == ('worker_timeout' if kind == 'timeout' else 'worker_cancelled')
    assert supervisor.state()['generations'][0]['alive'] is False
    with pytest.raises(GenerationError):
        supervisor.compute(projection(), algorithms=['current'])


def test_generation_bound_retirement_and_idempotent_close(tmp_path, supervisor):
    """One active and one prepared/retiring generation is a hard process bound."""
    supervisor.prepare(bundle(tmp_path, 'N')); supervisor.activate('N')
    with pytest.raises(GenerationError) as active:
        supervisor.retire('N')
    assert active.value.code == 'generation_active'
    supervisor.prepare(bundle(tmp_path, 'N1'))
    with pytest.raises(GenerationError) as limit:
        supervisor.prepare(bundle(tmp_path, 'N2'))
    assert limit.value.code == 'generation_limit'
    supervisor.close(); supervisor.close()
    assert supervisor.state() == {'active': None, 'closed': True, 'generations': []}


@pytest.mark.parametrize('value', [None, True, 0, -1, float('nan'), float('inf'), 121])
def test_invalid_waits_rejected(supervisor, value):
    """All lifecycle waits are finite and bounded before spawning or dispatch."""
    with pytest.raises(GenerationError):
        supervisor.compute(projection(), algorithms=['current'], timeout=value)


def test_protocol_negative_limits_and_nonfinite():
    """Allocation bounds and finite JSON are explicit protocol contracts."""
    with pytest.raises(GenerationError):
        encode_frame({'x': float('nan')})
    with pytest.raises(GenerationError):
        encode_frame({'x': 'x' * MAX_FRAME_BYTES})
    for invalid in (b'[]', b'{"x":NaN}', b'broken', b'{"x":1e309}', b'{"x":[-1e309]}'):
        with pytest.raises(GenerationError):
            decode_payload(invalid)


@pytest.mark.parametrize('payload', [b'{"x":1e309}', b'{"x":[-1e309]}', b'{"x":NaN}'])
def test_child_input_parser_rejects_numeric_overflow(monkeypatch, payload):
    """CG1: actual child-side frame reader rejects the same nonfinite input."""
    from atlas.core_generations import worker
    stream = io.BytesIO(struct.pack('!I', len(payload))+payload)
    monkeypatch.setattr(worker.sys, 'stdin', SimpleNamespace(buffer=stream))
    with pytest.raises(ValueError):
        worker.receive()


def test_retiring_process_keeps_capacity_slot_until_reaped(tmp_path, supervisor, monkeypatch):
    """CG2: blocked process stop cannot expose a free slot to concurrent prepare."""
    supervisor.prepare(bundle(tmp_path, 'N')); supervisor.activate('N')
    supervisor.prepare(bundle(tmp_path, 'N1')); supervisor.activate('N1')
    retiring = supervisor._workers['N']
    entered, release = Event(), Event()
    original_stop = retiring.stop
    def delayed_stop():
        """Hold the real owned process alive at the critic's observed race boundary."""
        entered.set()
        assert release.wait(3)
        original_stop()
    monkeypatch.setattr(retiring, 'stop', delayed_stop)
    third = bundle(tmp_path, 'N2')
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(supervisor.retire, 'N')
        try:
            assert entered.wait(3)
            assert retiring.process.poll() is None
            state = supervisor.state()
            assert len(state['generations']) == 2
            assert next(row for row in state['generations'] if row['generationId'] == 'N')['retiring']
            with pytest.raises(GenerationError) as error:
                supervisor.prepare(third)
            assert error.value.code == 'generation_limit'
        finally:
            release.set()
        assert future.result(timeout=3)['generationId'] == 'N'
    assert retiring.process.poll() is not None
    assert supervisor.prepare(third)['generationId'] == 'N2'


def test_domain_error_does_not_silently_retry_or_break_valid_generation(tmp_path, supervisor):
    """Ordinary validation failures preserve the healthy pinned worker."""
    supervisor.prepare(bundle(tmp_path, 'N')); supervisor.activate('N')
    with pytest.raises(GenerationError) as error:
        supervisor.compute(projection(), algorithms=['unknown'])
    assert error.value.code == 'module_analysis_invalid'
    assert supervisor.state()['generations'][0]['alive'] is True
    assert supervisor.compute(projection(), algorithms=['current'])
