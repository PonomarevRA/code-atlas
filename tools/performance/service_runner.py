"""Measure one owned frozen HTTP backend; reject invalid work, unknown metrics and unproven cleanup."""

import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import stat
import subprocess
import threading
import time

import httpx
import psutil

SCHEMA = 'ag-service-performance/v3'


def canonical(value):
    """Encode deterministic JSON for manifests and workload identities; never hash Python object reprs."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def file_hash(path):
    """Hash files outside timed phases without loading a native vendor closure into memory."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def tree_entries(root, *, hashes):
    """Inventory a private tree without following links; reject any link that escapes its declared root."""
    root = Path(root).resolve(strict=True)
    entries = []
    for directory, names, files in os.walk(root, followlinks=False):
        for name in sorted(names + files):
            path = Path(directory) / name
            info = path.lstat()
            entry = {'path': path.relative_to(root).as_posix(), 'mode': stat.S_IMODE(info.st_mode), 'mtimeNs': info.st_mtime_ns}
            if path.is_symlink():
                if not path.resolve(strict=True).is_relative_to(root):
                    raise ValueError('Manifest symlink escapes its root')
                entry.update(kind='link', target=os.readlink(path))
            elif path.is_dir():
                entry.update(kind='directory')
            elif path.is_file():
                entry.update(kind='file', size=info.st_size)
                if hashes:
                    entry['sha256'] = file_hash(path)
            else:
                raise ValueError('Manifest contains a nonregular entry')
            entries.append(entry)
    return sorted(entries, key=lambda entry: entry['path'])


def freeze_tree(root, directory, label):
    """Create one content-addressed full manifest; later runs recheck inventory metadata and critical hashes."""
    root = Path(root).resolve(strict=True)
    entries = tree_entries(root, hashes=True)
    content = [{key: value for key, value in entry.items() if key != 'mtimeNs'} for entry in entries]
    value = {'schemaVersion': 'ag-performance-tree/v1', 'root': str(root), 'entries': entries,
             'contentSha256': hashlib.sha256(canonical(content)).hexdigest()}
    raw = canonical(value)
    digest = hashlib.sha256(raw).hexdigest()
    path = Path(directory) / f'{label}-{digest}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    elif path.read_bytes() != raw:
        raise ValueError('Immutable manifest was replaced')
    return {'path': str(path.resolve()), 'sha256': digest}


def verify_tree(reference, root, critical=(), *, full_hash=False):
    """Verify a frozen reference, all paths/size/mode/mtime and critical code bytes; trust other cached content only under that freeze."""
    raw = Path(reference['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ValueError('Manifest reference digest changed')
    manifest = json.loads(raw)
    root = Path(root).resolve(strict=True)
    if manifest['schemaVersion'] != 'ag-performance-tree/v1' or manifest['root'] != str(root):
        raise ValueError('Manifest root or schema mismatch')
    expected = [{key: value for key, value in entry.items() if key != 'sha256'} for entry in manifest['entries']]
    if tree_entries(root, hashes=False) != expected:
        raise ValueError('Frozen tree inventory changed; explicitly freeze a new artifact or seed')
    entries = {entry['path']: entry for entry in manifest['entries']}
    content = [{key: value for key, value in entry.items() if key != 'mtimeNs'} for entry in manifest['entries']]
    if hashlib.sha256(canonical(content)).hexdigest() != manifest['contentSha256']:
        raise ValueError('Manifest content identity mismatch')
    if full_hash:
        critical = [name for name, entry in entries.items() if entry['kind'] == 'file']
    for name in critical:
        entry = entries.get(name)
        if not entry or entry['kind'] != 'file' or file_hash(root / name) != entry['sha256']:
            raise ValueError('Critical artifact content changed')
    return {'manifest': reference, 'contentSha256': manifest['contentSha256'], 'entryCount': len(entries),
            'verification': 'all file hashes' if full_hash else 'full inventory metadata + critical file hashes; other content cached under immutable reference'}


def freeze_spec(path):
    """Explicit preparation only: hash full artifact/seed once, save immutable references, launch no application."""
    path = Path(path)
    spec = json.loads(path.read_text())
    directory = path.parent / 'manifests'
    spec['artifactManifest'] = freeze_tree(spec['artifactRoot'], directory, spec['service'] + '-artifact')
    spec['seedManifest'] = freeze_tree(spec['seed'], directory, spec['service'] + '-seed')
    path.write_text(json.dumps(spec, indent=2) + '\n')
    return spec


def provenance(spec):
    """Pin fixture bytes, code closure, runner and semantic workload independently of the before/after executable path."""
    artifact = verify_tree(spec['artifactManifest'], spec['artifactRoot'], spec['criticalArtifactFiles'])
    seed = verify_tree(spec['seedManifest'], spec['seed'], full_hash=True)
    workload = {key: spec.get(key) for key in ('service', 'health', 'healthAssertions', 'requests', 'env', 'seedFilesReplaceData', 'soakSeconds')}
    inherited = {key: value for key, value in os.environ.items() if key.startswith(('DOTNET_', 'COMPlus_', 'OMP_', 'OPENBLAS_', 'VECLIB_')) or key == 'PYTHONHASHSEED'}
    return {'artifact': artifact, 'seed': seed, 'specSha256': hashlib.sha256(canonical(spec)).hexdigest(),
            'workloadSha256': hashlib.sha256(canonical(workload)).hexdigest(), 'runnerSha256': file_hash(__file__),
            'inheritedRuntimeEnvironment': inherited}


def pair_compatible(before, after):
    """Permit numeric comparison only for valid v3 reports with identical fixture/workload/runtime measurement conditions."""
    if not before.get('valid') or not after.get('valid') or before.get('schemaVersion') != SCHEMA or after.get('schemaVersion') != SCHEMA:
        return False
    for key in ('system', 'logicalCpuCount', 'profile'):
        if before.get(key) != after.get(key):
            return False
    try:
        for key in ('workloadSha256', 'runnerSha256', 'inheritedRuntimeEnvironment'):
            if before['provenance'][key] != after['provenance'][key]:
                return False
        return before['provenance']['seed']['contentSha256'] == after['provenance']['seed']['contentSha256']
    except (KeyError, TypeError):
        return False



def disk_usage(directory):
    """Count logical bytes and allocated blocks without following links; shared APFS extents remain unknown."""
    total, allocated, files = 0, 0, 0
    inodes = set()
    for root, directories, names in os.walk(directory, followlinks=False):
        directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
        for name in names:
            info = (Path(root) / name).lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            key = (info.st_dev, info.st_ino)
            if key in inodes:
                continue
            inodes.add(key)
            total += info.st_size
            allocated += info.st_blocks * 512 if hasattr(info, 'st_blocks') else info.st_size
            files += 1
    return {'logicalBytes': total, 'allocatedBytes': allocated, 'files': files,
            'allocationScope': 'per-inode st_blocks; APFS shared extents and snapshots not deduplicated'}


def disk_bytes(directory):
    """Compatibility logical-byte measure; detailed allocation remains separately reported."""
    return disk_usage(directory)['logicalBytes']


def isolated_environment(spec, values):
    """Inherit process necessities and declared runtime knobs, excluding all production data selectors."""
    allowed = {'PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LC_ALL',
               'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'DOTNET_ROOT', 'PYTHONHASHSEED'}
    environment = {key: value for key, value in os.environ.items()
                   if key in allowed or key.startswith(('DOTNET_', 'COMPlus_', 'OMP_', 'OPENBLAS_', 'VECLIB_'))}
    environment.update({key: value.format(**values) for key, value in spec.get('env', {}).items()})
    return environment


def percentile(values, fraction):
    """Nearest-rank observed latency; an empty successful population remains unknown."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


def field(value, path):
    """Resolve declared dotted object/list paths strictly, never substitute an empty or zero default."""
    for key in path.split('.'):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def validate_spec(spec):
    """Reject ambiguous workloads before launching: unique names, loopback port and a semantic assertion per request."""
    if type(spec.get('port')) is not int or not 1024 <= spec['port'] <= 65535:
        raise ValueError('Invalid private test port')
    soak = spec.get('soakSeconds', 0)
    if type(soak) not in (int, float) or not math.isfinite(soak) or not 0 <= soak <= 3600:
        raise ValueError('soakSeconds must be finite and between 0 and 3600')
    requests = spec.get('requests')
    if not isinstance(requests, list) or not requests:
        raise ValueError('A nonempty workload is required')
    names = set()
    for request in requests:
        name = request.get('name')
        if not isinstance(name, str) or not name or name in names:
            raise ValueError('Request names must be nonempty and unique')
        names.add(name)
        if not request.get('path', '').startswith('/') or request['path'].startswith('//'):
            raise ValueError('Requests must use relative same-origin paths')
        assertions = ('expectedValues', 'expectedLengths', 'minimumLengths', 'minimumValues', 'expectedText', 'minimumLineCounts')
        if not any(request.get(key) for key in assertions):
            raise ValueError('Every operation needs seeded semantic assertions')


def assert_response(response, request):
    """Reject HTTP and semantic failures, including successful unavailable/empty responses in seeded workloads."""
    response.raise_for_status()
    if not request.get('jsonResponse', True):
        text = response.text
        for required in request.get('expectedText', []):
            if required not in text:
                raise ValueError('Missing required text family')
        for prefix, minimum in request.get('minimumLineCounts', {}).items():
            if sum(line.startswith(prefix) for line in text.splitlines()) < minimum:
                raise ValueError('Insufficient text samples')
        return
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError('Expected a JSON object')
    for key in request.get('expectedKeys', []):
        field(data, key)
    for key, expected in request.get('expectedValues', {}).items():
        actual = field(data, key)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError('Unexpected response value: ' + key)
    types = {'array': list, 'object': dict, 'string': str, 'number': (int, float), 'boolean': bool, 'null': type(None)}
    for key, expected in request.get('expectedTypes', {}).items():
        actual = field(data, key)
        if expected not in types or type(actual) not in (types[expected] if isinstance(types[expected], tuple) else (types[expected],)):
            raise ValueError('Unexpected response type: ' + key)
    for key, minimum in request.get('minimumValues', {}).items():
        actual = field(data, key)
        if type(actual) not in (int, float) or not math.isfinite(actual) or actual < minimum:
            raise ValueError('Insufficient numeric value: ' + key)
    for key, expected in request.get('expectedLengths', {}).items():
        if len(field(data, key)) != expected:
            raise ValueError('Unexpected collection size: ' + key)
    for key, minimum in request.get('minimumLengths', {}).items():
        if len(field(data, key)) < minimum:
            raise ValueError('Insufficient collection: ' + key)


async def workload(client, requests, total, concurrency):
    """Bounded closed-loop attempts; successful latency/RPS never include HTTP or semantic failures."""
    next_index = 0
    observations = {request['name']: {'attemptLatenciesMs': [], 'latenciesMs': [], 'failureLatenciesMs': [], 'errors': [], 'responseBytes': []} for request in requests}

    async def worker():
        """Claim attempts without awaits between reading and advancing the shared cursor."""
        nonlocal next_index
        while next_index < total:
            index = next_index
            next_index += 1
            request = requests[index % len(requests)]
            result = observations[request['name']]
            started = time.perf_counter()
            succeeded = False
            response = None
            try:
                response = await client.request(request.get('method', 'GET'), request['path'], json=request.get('json'))
                assert_response(response, request)
                result['responseBytes'].append(len(response.content))
                succeeded = True
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
                result['errors'].append({'kind': type(error).__name__, 'httpStatus': response.status_code if response is not None else None,
                                         'assertion': str(error)[:120] if isinstance(error, (ValueError, KeyError, TypeError, IndexError)) else None})
            finally:
                latency = (time.perf_counter() - started) * 1000
                result['attemptLatenciesMs'].append(latency)
                result['latenciesMs' if succeeded else 'failureLatenciesMs'].append(latency)

    await asyncio.gather(*(worker() for _ in range(concurrency)))
    for result in observations.values():
        values = result['latenciesMs']
        result.update(attempted=len(result['attemptLatenciesMs']), succeeded=len(values), failed=len(result['errors']),
                      count=len(values), p50Ms=percentile(values, .5), p95Ms=percentile(values, .95),
                      p99Ms=percentile(values, .99), maxMs=max(values, default=None))
    return observations


class OwnedTree:
    """Retain PID/create-time receipts; private-session discovery also finds children after their parent exits."""

    def __init__(self, process):
        self.process = process
        root = psutil.Process(process.pid)
        self.root_created = root.create_time()
        self.group = os.getpgid(process.pid)
        if self.group != process.pid or os.getsid(process.pid) != process.pid:
            raise ValueError('Owned process must start in its own session')
        self.receipts = {process.pid: self.root_created}

    def observe(self, processes):
        """Record only descendants supplied by the verified root or members of its private original session."""
        for process in processes:
            self.receipts[process.pid] = process.create_time()

    def discover_group(self):
        """Discover orphaned children without ever admitting a reused root PID or another OS session."""
        try:
            if psutil.Process(self.process.pid).create_time() != self.root_created:
                return
        except psutil.NoSuchProcess:
            pass
        for candidate in psutil.process_iter(['pid', 'create_time', 'uids']):
            try:
                if candidate.info['uids'] and candidate.info['uids'].real != os.getuid():
                    continue
                if os.getpgid(candidate.pid) == self.group and os.getsid(candidate.pid) == self.group and candidate.create_time() >= self.root_created:
                    self.observe([candidate])
            except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError, PermissionError):
                continue

    def live(self):
        """Revalidate each owned receipt before signaling; changed identities and zombies are never targets."""
        result = []
        for pid, created in list(self.receipts.items()):
            try:
                process = psutil.Process(pid)
                if process.create_time() == created and process.status() != psutil.STATUS_ZOMBIE:
                    result.append(process)
            except psutil.NoSuchProcess:
                pass
        return result


class Sampler:
    """Sample observed owned counters at 5Hz; access failures produce unknown metrics, never fabricated zero."""

    def __init__(self, process, owned=None):
        self.process = psutil.Process(process.pid)
        self.owned = owned or OwnedTree(process)
        self.cpu, self.samples = {}, []
        self.lock, self.finished = threading.RLock(), threading.Event()
        self.thread = threading.Thread(target=self.run, name='perf-owned-process-sampler', daemon=True)

    def snapshot(self):
        """Serialize boundary and periodic observations on one timeline."""
        with self.lock:
            return self._snapshot()

    def _snapshot(self):
        """Retain exited-child observed CPU, disclose partial access and count only actually sampled RSS."""
        errors, rss, count = [], 0, 0
        try:
            processes = [self.process, *self.process.children(recursive=True)]
        except psutil.NoSuchProcess:
            processes = []
            errors.append('root_unavailable')
        except psutil.AccessDenied:
            processes = [self.process]
            errors.append('children_access_denied')
        for process in processes:
            try:
                with process.oneshot():
                    created = process.create_time()
                    usage = process.cpu_times()
                    self.cpu[(process.pid, created)] = max(self.cpu.get((process.pid, created), 0), usage.user + usage.system)
                    self.owned.observe([process])
                    rss += process.memory_info().rss
                    count += 1
            except psutil.NoSuchProcess:
                errors.append('process_exited_before_sample')
            except psutil.AccessDenied:
                errors.append('process_access_denied')
        sample = {'at': time.monotonic(), 'cpuSeconds': None if errors else sum(self.cpu.values()),
                  'rssBytes': None if errors else rss, 'processes': count,
                  'health': {'status': 'degraded' if errors else 'ok', 'errors': sorted(set(errors))}}
        self.samples.append(sample)
        return sample

    def run(self):
        """Sampling avoids database/filesystem walks; short-lived unobserved children remain an explicit CPU limitation."""
        while not self.finished.is_set():
            try:
                self.snapshot()
            except Exception as error:
                with self.lock:
                    self.samples.append({'at': time.monotonic(), 'cpuSeconds': None, 'rssBytes': None, 'processes': None,
                                         'health': {'status': 'degraded', 'errors': ['sampler_' + type(error).__name__]}})
                return
            self.finished.wait(.2)

    def summarize(self, start, end):
        """Report unknown CPU/RSS when any observation is incomplete, alongside the disclosed sampled known lower bound."""
        selected = [sample for sample in self.samples if start['at'] <= sample['at'] <= end['at']]
        complete = bool(selected) and all(sample['health']['status'] == 'ok' for sample in selected)
        wall = end['at'] - start['at']
        complete = complete and start['cpuSeconds'] is not None and end['cpuSeconds'] is not None
        cpu = end['cpuSeconds'] - start['cpuSeconds'] if complete else None
        known_rss = [sample['rssBytes'] for sample in selected if sample['rssBytes'] is not None]
        return {'wallSeconds': wall, 'cpuSeconds': cpu, 'cpuPercentOneCore': 100 * cpu / wall if cpu is not None and wall > 0 else None,
                'rssStartBytes': start['rssBytes'], 'rssEndBytes': end['rssBytes'],
                'peakRssBytes': max(known_rss) if complete and known_rss else None,
                'knownSampledPeakRssBytes': max(known_rss, default=None), 'metricsComplete': complete,
                'peakProcessCount': max([sample['processes'] for sample in selected if sample['processes'] is not None], default=None)}


def port_free(port):
    """Check loopback listener absence; never signal an unowned replacement listener."""
    with socket.socket() as probe:
        probe.settimeout(.2)
        return probe.connect_ex(('127.0.0.1', port)) != 0


def stop_owned(process, owned, port, *, deadlines=(12, 3, 2)):
    """Bound cleanup of verified descendants, including orphans; denied identity access invalidates cleanup."""
    signals, errors = [], []

    def remaining():
        """Reap our direct child and refresh receipts; an inaccessible identity remains unknown, never safe to signal."""
        process.poll()
        owned.discover_group()
        return owned.live()

    try:
        for termination, seconds in zip((signal.SIGINT, signal.SIGTERM, signal.SIGKILL), deadlines):
            for child in remaining():
                try:
                    child.send_signal(termination)
                    signals.append({'pid': child.pid, 'signal': int(termination)})
                except psutil.NoSuchProcess:
                    pass
                except psutil.AccessDenied:
                    errors.append('owned_signal_access_denied')
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline and remaining():
                time.sleep(.05)
            if not remaining():
                break
        survivors = [{'pid': child.pid, 'createdAt': owned.receipts[child.pid]} for child in remaining()]
    except (psutil.AccessDenied, PermissionError):
        survivors = None
        errors.append('owned_identity_access_denied')
    except (OSError, psutil.Error) as error:
        survivors = None
        errors.append('owned_cleanup_' + type(error).__name__)
    free = port_free(port)
    return {'valid': survivors == [] and free and not errors, 'remainingOwned': survivors,
            'portFree': free, 'signals': signals, 'errors': errors}


def stop_direct_child(process):
    """Fallback for receipt initialization failure: stop only the unreaped child owned by this Popen, never guess descendants."""
    for method in (process.terminate, process.kill):
        if process.poll() is not None:
            return
        method()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            continue


def finalize(report, process, sampler, owned, run, port):
    """Save cleanup and measurement failures independently so a fault never disappears behind teardown exceptions."""
    if sampler:
        sampler.finished.set()
        sampler.thread.join(timeout=2)
        report['rawSamples'] = sampler.samples
        if sampler.thread.is_alive():
            report['valid'] = False
            report['samplerError'] = 'sampler_stop_timeout'
    if process:
        try:
            if owned:
                report['cleanup'] = stop_owned(process, owned, port)
            else:
                stop_direct_child(process)
                report['cleanup'] = {'valid': False, 'remainingOwned': None, 'portFree': port_free(port),
                                     'errors': ['owned_receipts_unavailable']}
        except Exception as error:
            report['cleanup'] = {'valid': False, 'remainingOwned': None, 'portFree': None,
                                 'errors': ['cleanup_' + type(error).__name__]}
        report['exitCode'] = process.poll()
        report['valid'] = report['valid'] and report['cleanup']['valid'] and report['exitCode'] == 0
    if run.exists():
        try:
            report['diskAfter'] = disk_usage(run)
            report['diskAfterBytes'] = report['diskAfter']['logicalBytes']
            report['fileSystemFreeAfterBytes'] = shutil.disk_usage(run).free
        except OSError as error:
            report['diskAfterBytes'] = None
            report['diskError'] = type(error).__name__
            report['valid'] = False


async def wait_ready(client, spec, process):
    """Wait for the owned subprocess and validate its health identity when the specification declares one."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Owned service exited before readiness')
        try:
            response = await client.get(spec['health'])
            assert_response(response, spec.get('healthAssertions', {}))
            return
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            await asyncio.sleep(.1)
    raise RuntimeError('Owned service readiness exceeded 60 seconds')


def load_summary(operations, summary, attempted):
    """Keep attempt and success rates separate; one failure or missing metric invalidates canonical comparison."""
    succeeded = sum(row['succeeded'] for row in operations.values())
    failed = sum(row['failed'] for row in operations.values())
    return {'attempted': attempted, 'succeeded': succeeded, 'failed': failed,
            'attemptsPerSecond': attempted / summary['wallSeconds'], 'successfulRequestsPerSecond': succeeded / summary['wallSeconds'],
            'valid': failed == 0 and succeeded == attempted and summary['metricsComplete']}


async def measure(spec, output, repeat, requests_per_phase, idle_seconds):
    """Copy a verified immutable seed, measure serial phases, save failures and prove owned teardown before another run."""
    output = Path(output).resolve()
    run = output.parent / (output.stem + '-data')
    if run.exists() or output.exists():
        raise ValueError('Refuse to overwrite a previous performance run')
    report = {'schemaVersion': SCHEMA, 'service': spec['service'], 'repeat': repeat, 'valid': False, 'phases': [],
              'system': platform.platform(), 'logicalCpuCount': psutil.cpu_count(),
              'profile': {'requestsPerPhase': requests_per_phase, 'concurrency': [1, 8], 'idleSeconds': idle_seconds, 'warmupRequests': 12},
              'measurement': 'owned backend tree; 5Hz sampled summed RSS (shared pages double-counted); observed CPU counters may miss short-lived children; closed-loop HTTP'}
    process = sampler = owned = None
    try:
        validate_spec(spec)
        report['provenance'] = provenance(spec)
        shutil.copytree(Path(spec['seed']).resolve(), run)
        for name in spec.get('seedFilesReplaceData', []):
            path = run / name
            if not path.resolve().is_relative_to(run):
                raise ValueError('Seed replacement escapes copied data')
            path.write_text(path.read_text().replace('__PERF_DATA__', str(run)))
        values = {'data': str(run), 'port': str(spec['port'])}
        environment = isolated_environment(spec, values)
        command = [argument.format(**values) for argument in spec['command']]
        if not Path(command[0]).resolve().is_relative_to(Path(spec['artifactRoot']).resolve()):
            raise ValueError('Executable is outside the frozen artifact')
        with socket.socket() as reservation:
            reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            reservation.bind(('127.0.0.1', spec['port']))
        report['fileSystemFreeBeforeBytes'] = shutil.disk_usage(run).free
        report.update(command=command, binarySha256=file_hash(command[0]), diskBeforeBytes=disk_bytes(run), diskBefore=disk_usage(run))
        with output.with_suffix('.log').open('w') as log:
            process = subprocess.Popen(command, cwd=spec.get('cwd', str(run)), env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            owned = OwnedTree(process)
            sampler = Sampler(process, owned)
            sampler.thread.start()
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{spec['port']}", timeout=15, limits=httpx.Limits(max_connections=8, max_keepalive_connections=8), headers={'Origin': f"http://127.0.0.1:{spec['port']}"}, trust_env=False, follow_redirects=False) as client:
                startup_started = time.perf_counter()
                await wait_ready(client, spec, process)
                report['readinessSeconds'] = time.perf_counter() - startup_started
                start = sampler.snapshot()
                await asyncio.sleep(idle_seconds)
                report['phases'].append({'name': 'idle', **sampler.summarize(start, sampler.snapshot())})
                report['warmup'] = await workload(client, spec['requests'], 12, 1)
                if any(row['failed'] for row in report['warmup'].values()):
                    raise RuntimeError('Warmup semantic contract failed')
                for concurrency in (1, 8):
                    start = sampler.snapshot()
                    operations = await workload(client, spec['requests'], requests_per_phase, concurrency)
                    summary = sampler.summarize(start, sampler.snapshot())
                    report['phases'].append({'name': f'load-c{concurrency}', 'concurrency': concurrency, 'operations': operations,
                                             **summary, **load_summary(operations, summary, requests_per_phase)})
                if spec.get('soakSeconds', 0):
                    start = sampler.snapshot()
                    deadline = time.monotonic() + spec['soakSeconds']
                    attempts = failures = 0
                    while time.monotonic() < deadline:
                        batch = await workload(client, spec['requests'], 40, 4)
                        attempts += sum(row['attempted'] for row in batch.values())
                        failures += sum(row['failed'] for row in batch.values())
                        if failures:
                            break
                        await asyncio.sleep(.1)
                    summary = sampler.summarize(start, sampler.snapshot())
                    report['phases'].append({'name': 'soak-c4', 'attempted': attempts, 'failed': failures,
                                             'valid': failures == 0 and summary['metricsComplete'], **summary})
                start = sampler.snapshot()
                await asyncio.sleep(idle_seconds)
                report['phases'].append({'name': 'cooldown', **sampler.summarize(start, sampler.snapshot())})
        report['valid'] = all(phase.get('valid', phase['metricsComplete']) for phase in report['phases'])
    except Exception as error:
        report['failure'] = {'kind': type(error).__name__, 'message': str(error)[:240]}
    finally:
        finalize(report, process, sampler, owned, run, spec['port'])
        output.write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    """Prepare manifests explicitly or run fresh processes sequentially; reject invalid runs before continuing."""
    parser = argparse.ArgumentParser()
    parser.add_argument('spec', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--freeze-manifests', action='store_true')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--requests', type=int, default=600)
    parser.add_argument('--idle-seconds', type=float, default=10)
    args = parser.parse_args()
    if args.freeze_manifests:
        freeze_spec(args.spec)
        return
    if args.output is None or args.repeats < 1 or args.requests < 1 or not math.isfinite(args.idle_seconds) or args.idle_seconds < 0:
        parser.error('Output and positive repeat/request bounds are required')
    args.output.mkdir(parents=True, exist_ok=True)
    spec = json.loads(args.spec.read_text())
    for repeat in range(args.repeats):
        report = asyncio.run(measure(spec, args.output / f"{spec['service']}-{repeat + 1}.json", repeat + 1, args.requests, args.idle_seconds))
        print(json.dumps({'service': spec['service'], 'repeat': repeat + 1, 'valid': report['valid'], 'failure': report.get('failure'),
                          'phases': [{key: row.get(key) for key in ('name', 'attempted', 'succeeded', 'failed', 'valid', 'wallSeconds', 'cpuSeconds', 'peakRssBytes')} for row in report['phases']]}), flush=True)
        if not report['valid']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
