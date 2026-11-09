"""Pinned pack validation, fixed subprocess arguments and native parser cleanup."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import time
from unittest.mock import patch

import psutil
import pytest

from atlas import lnav_runtime
from atlas.documents.models import DocumentError, TreeBuilder
from atlas.documents.worker import run_provider


def _macho(machine):
    """Minimal non-executable header fixture isolates CPU inspection from OS execution."""
    cpu = {'x86_64': 0x01000007, 'arm64': 0x0100000C}[machine]
    return b'\xcf\xfa\xed\xfe' + struct.pack('<7I', cpu, 0, 2, 0, 0, 0, 0)


def _fat_macho(*machines, fat64=False):
    """Create a complete universal header and matching thin slices with bounded offsets."""
    entry_size = 32 if fat64 else 20
    offset = 8 + entry_size * len(machines)
    result = struct.pack('>II', 0xCAFEBABF if fat64 else 0xCAFEBABE, len(machines))
    slices = []
    for machine in machines:
        cpu = {'x86_64': 0x01000007, 'arm64': 0x0100000C}[machine]
        data = _macho(machine)
        result += struct.pack('>IIQQII' if fat64 else '>IIIII', cpu, 0, offset, len(data), 0, *([0] if fat64 else []))
        slices.append(data)
        offset += len(data)
    return result + b''.join(slices)


@pytest.mark.parametrize('alias,expected', [('AMD64', 'x86_64'), ('x86_64', 'x86_64'),
                                           ('x64', 'x86_64'), ('x86-64', 'x86_64'),
                                           ('aarch64', 'arm64'), ('ARM64', 'arm64')])
def test_architecture_aliases_share_runtime_directory(alias, expected):
    """ARM and x64 CI/OS spelling differences do not select a foreign pack."""
    assert lnav_runtime.normalize_architecture(alias) == expected
    with patch.object(lnav_runtime.sys, 'platform', 'darwin'), \
         patch.object(lnav_runtime.platform, 'machine', return_value=alias):
        assert lnav_runtime.platform_key() == 'darwin-' + expected


def test_windows_arm_host_with_emulated_x64_python_selects_x64_pack():
    """Host ARM64 must not choose ARM DLLs when the Python interpreter uses win-amd64 ABI."""
    with patch.object(lnav_runtime.sys, 'platform', 'win32'), \
         patch.object(lnav_runtime.platform, 'machine', return_value='ARM64'), \
         patch.object(lnav_runtime.sysconfig, 'get_platform', return_value='win-amd64'):
        assert lnav_runtime.platform_key() == 'win32-x86_64'
        assert lnav_runtime.process_architecture() == 'x86_64'
    with patch.object(lnav_runtime.sys, 'platform', 'win32'), \
         patch.object(lnav_runtime.platform, 'machine', return_value='AMD64'), \
         patch.object(lnav_runtime.sysconfig, 'get_platform', return_value='win-arm64'):
        assert lnav_runtime.platform_key() == 'win32-arm64'


@pytest.mark.parametrize('fat64', [False, True])
def test_native_header_reads_arm64_and_universal_macho(tmp_path, fat64):
    """Universal certification inspects real thin slices, not the textual file description."""
    path = tmp_path / 'binary'
    path.write_bytes(_macho('arm64'))
    assert lnav_runtime.native_architectures(path, system='darwin') == {'arm64'}
    path.write_bytes(_fat_macho('x86_64', 'arm64', fat64=fat64))
    assert lnav_runtime.native_architectures(path, system='darwin') == {'x86_64', 'arm64'}


def test_macho_fat_table_cannot_lie_about_actual_slice_cpu(tmp_path):
    """A declared ARM64 slice with an actual x86_64 header fails closed."""
    path = tmp_path / 'binary'
    payload = bytearray(_fat_macho('arm64'))
    payload[-32:] = _macho('x86_64')
    path.write_bytes(payload)
    with pytest.raises(DocumentError, match='slice'):
        lnav_runtime.native_architectures(path, system='darwin')


@pytest.mark.parametrize('system,cpu,expected', [('linux', 62, 'x86_64'), ('linux', 183, 'arm64'),
                                                ('win32', 0x8664, 'x86_64'), ('win32', 0xAA64, 'arm64')])
def test_native_header_reads_elf_and_pe_cpu_without_execution(tmp_path, system, cpu, expected):
    """Linux/Windows target validation also rejects architecture inferred only from filenames."""
    payload = bytearray(128)
    if system == 'linux':
        payload[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', payload, 18, cpu)
    else:
        payload[:2] = b'MZ'
        struct.pack_into('<I', payload, 60, 64)
        payload[64:68] = b'PE\x00\x00'
        struct.pack_into('<H', payload, 68, cpu)
    path = tmp_path / 'binary'
    path.write_bytes(payload)
    assert lnav_runtime.native_architectures(path, system=system) == {expected}


@pytest.mark.parametrize('foreign_file', ['lnav', 'lib/foreign.dylib'])
def test_lnav_pack_rejects_foreign_executable_or_dylib(tmp_path, foreign_file):
    """Hash-valid manifests cannot enable the wrong executable or dependency architecture."""
    _fixture_pack(tmp_path, platform='darwin-arm64')
    files = {}
    for filename in ['lnav', 'lib/foreign.dylib']:
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_macho('x86_64' if filename == foreign_file else 'arm64'))
        files[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    manifest['files'] = files
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with patch.object(lnav_runtime.sys, 'platform', 'darwin'), \
         patch.object(lnav_runtime.platform, 'machine', return_value='aarch64'):
        assert not lnav_runtime.capability(tmp_path)['available']


def _fixture_pack(tmp_path, **changes):
    """Create a hash-valid native inventory for manifest-gate tests, never certify its behavior."""
    executable = tmp_path / 'lnav'
    shutil.copy2(sys.executable, executable)
    manifest = {'schemaVersion': 1, 'platform': lnav_runtime.platform_key(), 'version': '0.14.1',
                'certified': True, 'secureModeConverterPatch': True, 'withoutLibcurl': True,
                'executable': 'lnav', 'files': {'lnav': hashlib.sha256(executable.read_bytes()).hexdigest()}}
    manifest.update(changes)
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    return executable


def test_lnav_gate_requires_patched_curl_free_hash_verified_native_pack(tmp_path):
    """A PATH binary or mere manifest presence cannot enable the optional provider."""
    assert not lnav_runtime.capability(tmp_path)['available']
    executable = _fixture_pack(tmp_path)
    assert lnav_runtime.capability(tmp_path)['available']
    executable.write_bytes(b'not the shipped native binary')
    assert not lnav_runtime.capability(tmp_path)['available']


def test_lnav_warm_capability_reuses_hash_proof_until_file_identity_changes(tmp_path):
    """Warm discovery does stat checks, but never rehashes the entire native closure per document."""
    executable = _fixture_pack(tmp_path)
    lnav_runtime._verify_inventory.cache_clear()
    with patch('hashlib.file_digest', wraps=hashlib.file_digest) as hashes:
        assert lnav_runtime.capability(tmp_path)['available']
        assert hashes.call_count == 1
        assert lnav_runtime.capability(tmp_path)['available']
        assert hashes.call_count == 1
        executable.touch()
        assert lnav_runtime.capability(tmp_path)['available']
        assert hashes.call_count == 2


@pytest.mark.parametrize('changes', [
    {'secureModeConverterPatch': False}, {'withoutLibcurl': False}, {'certified': False},
    {'platform': 'wrong-platform'}, {'version': 'old'}, {'files': {'../escape': 'abc'}},
])
def test_uncertified_or_mismatched_runtime_is_unavailable(tmp_path, changes):
    """Missing source-build safety guarantees fail closed before any execution."""
    _fixture_pack(tmp_path, **changes)
    assert not lnav_runtime.capability(tmp_path)['available']


def _script(tmp_path, body):
    """Trusted test subprocess only; production gate rejects scripts as native packs."""
    executable = tmp_path / 'fixture-parser'
    executable.write_text('#!' + sys.executable + '\n' + body)
    executable.chmod(0o755)
    return executable


@pytest.mark.skipif(os.name == 'nt', reason='Test shebang fixtures are POSIX only')
def test_lnav_uses_fixed_sql_and_isolated_environment(tmp_path):
    """Only bounded numeric LIMIT enters SQL; user environment/config are never inherited."""
    captured = tmp_path / 'argv.json'
    script = _script(tmp_path, 'import os,sys,json\n'
                     + f'open({str(captured)!r},"w").write(json.dumps({{"args":sys.argv,"env":dict(os.environ)}}))\n'
                     + 'print(json.dumps([{"log_line":0,"log_body":"ok"}]))\n')
    source = tmp_path / 'input.log'
    source.write_text('2026-10-21T12:00:00 INFO ready\n')
    with patch.object(lnav_runtime, '_checked_pack', return_value=(script, {}, 'fixture')), \
         patch.dict(os.environ, {'ATLAS_SECRET': 'must-not-propagate', 'LNAV_CONFIG_DIR': 'untrusted'}):
        result = lnav_runtime.parse_log(source, max_events=4)
    invocation = json.loads(captured.read_text())
    assert invocation['args'][1:] == ['-n', '-N', '-q', '-c',
        ';SELECT log_line, log_time, log_level, log_body, log_text FROM all_logs LIMIT 5',
        '-c', ':write-json-to -', '--', str(source)]
    assert invocation['env']['LNAVSECURE'] == '1'
    assert 'ATLAS_SECRET' not in invocation['env'] and 'LNAV_CONFIG_DIR' not in invocation['env']
    assert invocation['env']['HOME'] != str(Path.home())
    assert result['rows'][0]['log_body'] == 'ok'


@pytest.mark.skipif(os.name == 'nt', reason='Test shebang fixtures are POSIX only')
@pytest.mark.parametrize('budget,body,code', [
    ({'timeout_seconds': 0.15}, 'import time\ntime.sleep(10)\n', 'LNAV_TIMEOUT'),
    ({'max_output_bytes': 1000}, 'print("x"*100000)\n', 'OUTPUT_LIMIT'),
    ({'memory_bytes': 1}, 'import time\ntime.sleep(10)\n', 'WORKER_MEMORY_LIMIT'),
])
def test_lnav_enforces_timeout_output_and_memory_limits(tmp_path, budget, body, code):
    """A hostile/broken parser process cannot run beyond the monitored task budgets."""
    script = _script(tmp_path, body)
    source = tmp_path / 'input.log'
    source.write_text('2026-10-21T12:00:00 INFO ready\n')
    with patch.object(lnav_runtime, '_checked_pack', return_value=(script, {}, 'fixture')):
        with pytest.raises(DocumentError) as error:
            lnav_runtime.parse_log(source, **budget)
    assert error.value.code == code


class ChildProcessProvider:
    """Spawn an inert child so the outer worker timeout can prove tree cleanup."""

    def parse(self, path, options, context):
        import subprocess
        process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(20)'])
        Path(context['pidFile']).write_text(str(process.pid))
        time.sleep(20)
        return TreeBuilder(path.name).build()


def test_document_worker_timeout_cleans_native_parser_descendant(tmp_path):
    """Cancelling the Python provider also stops its already observed native subprocess."""
    source = tmp_path / 'source.log'
    source.write_text('fixture')
    pid_file = tmp_path / 'child.pid'
    with pytest.raises(DocumentError) as error:
        run_provider(ChildProcessProvider(), source, {}, {'pidFile': str(pid_file)}, lambda: False, timeout=1.5)
    assert error.value.code == 'WORKER_TIMEOUT'
    assert pid_file.exists()
    child_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 2
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        if psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE:
            break
        time.sleep(0.05)
    assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE


def test_bundled_lnav_real_parser_if_certified(tmp_path):
    """Release/source CI exercises native parsing only when its platform pack is certified."""
    if not lnav_runtime.capability()['available']:
        pytest.skip('No certified native lnav pack for this platform yet')
    source = tmp_path / 'fixture.log'
    source.write_text('2026-10-21T12:00:00.000 INFO atlas ready\n2026-10-21T12:00:01.000 ERROR atlas failed\n')
    result = lnav_runtime.parse_log(source)
    assert len(result['rows']) == 2
    assert result['rows'][1]['log_level'].lower() == 'error'


def test_native_lnav_preserves_apache_record_when_log_body_is_empty(tmp_path):
    """A real Apache event keeps URL/status despite upstream all_logs.log_body being empty."""
    if not lnav_runtime.capability()['available']:
        pytest.skip('No certified native lnav pack for this platform yet')
    from atlas.documents.provider_logs import LnavLogsProvider
    source = tmp_path / 'access.log'
    raw = '127.0.0.1 - - [21/Oct/2026:12:00:00 +0000] "GET /native-check HTTP/1.1" 200 42'
    source.write_text(raw + '\n')
    result = lnav_runtime.parse_log(source)
    assert '/native-check' in result['rows'][0]['log_text']
    tree = LnavLogsProvider().parse(source, {}, {})
    events = [node for node in tree['nodes'] if node['attributes'].get('role') == 'event']
    envelopes = [node for node in tree['nodes'] if node['attributes'].get('role') == 'raw_envelope']
    assert len(events) == 1 and '/native-check' in events[0]['text']
    assert len(envelopes) == 1 and envelopes[0]['text'] == raw
    assert events[0]['sourceLocator']['precision'] == 'structural'


def test_macos_certification_rejects_foreign_dylib_and_runner(tmp_path):
    """A native runner and every closure slice must match the explicitly selected target."""
    spec = importlib.util.spec_from_file_location('atlas_lnav_arch_build', Path(__file__).parents[1] / 'packaging/lnav_pack.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    binary = tmp_path / 'arm64'
    binary.write_bytes(_macho('arm64'))
    with patch.object(module.platform, 'machine', return_value='AMD64'):
        with pytest.raises(ValueError, match='matching native'):
            module._target_architecture(binary, 'aarch64')
    with patch.object(module.platform, 'machine', return_value='aarch64'):
        assert module._target_architecture(binary, 'arm64') == 'arm64'
    binary.write_bytes(_macho('x86_64'))
    with pytest.raises(ValueError, match='Foreign-architecture'):
        module._check_macho_architecture(binary, 'arm64')


def test_build_download_accepts_only_pinned_official_bytes(tmp_path):
    """Build helper rejects a changed upstream artifact; runtime has no download path."""
    from io import BytesIO
    spec = importlib.util.spec_from_file_location('atlas_lnav_pack_build', Path(__file__).parents[1] / 'packaging/lnav_pack.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch.object(module.urllib.request, 'urlopen', return_value=BytesIO(b'wrong release bytes')):
        with pytest.raises(ValueError, match='hash mismatch'):
            module.download_verified('linux-x86_64', tmp_path)
    assert not list(tmp_path.iterdir())


def test_runtime_adapter_participates_in_document_cache_identity():
    """Changing SQL/environment translation invalidates cached trees even with the same native binary."""
    with patch('atlas.documents.providers._adapter_bytes', return_value=b'adapter-v1'):
        first = lnav_runtime.parser_fingerprint()
    with patch('atlas.documents.providers._adapter_bytes', return_value=b'adapter-v2'):
        second = lnav_runtime.parser_fingerprint()
    assert first != second
