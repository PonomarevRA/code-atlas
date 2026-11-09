"""Только сертифицированный локальный lnav pack: фиксированный SQL и ограниченный subprocess."""

import hashlib
from functools import lru_cache
import json
import os
from pathlib import Path
import platform
import signal
import struct
import subprocess
import sys
import sysconfig
import tempfile
import time

from .runtime_paths import resource_root
from .documents.models import DocumentError, require


def normalize_architecture(machine):
    """Приводит aliases ОС/CI к двум поддерживаемым 64-bit архитектурам."""
    value = str(machine).strip().lower()
    return {'amd64': 'x86_64', 'x64': 'x86_64', 'x86-64': 'x86_64', 'aarch64': 'arm64'}.get(value, value)


def process_architecture():
    """Использует Python ABI на Windows: ARM host может исполнять x64 Python."""
    require(struct.calcsize('P') == 8, 'CAPABILITY_UNAVAILABLE', 'lnav требует 64-bit процесс.', 409)
    if sys.platform == 'win32':
        machine = {'win-amd64': 'x86_64', 'win-arm64': 'arm64'}.get(sysconfig.get_platform().lower())
        require(machine is not None, 'CAPABILITY_UNAVAILABLE', 'Неизвестный Windows Python ABI.', 409)
        return machine
    return normalize_architecture(platform.machine())


def platform_key():
    """Платформа исполняемого Atlas; Rosetta-процесс использует x86_64 closure."""
    return sys.platform + '-' + process_architecture()


def _macho_architectures(source, header, size):
    """Проверяет thin/fat 64-bit Mach-O, включая реальный header каждого fat slice."""
    processors = {0x01000007: 'x86_64', 0x0100000C: 'arm64'}
    thin = {b'\xcf\xfa\xed\xfe': '<', b'\xfe\xed\xfa\xcf': '>'}
    fat = {b'\xca\xfe\xba\xbe': ('>', 20), b'\xbe\xba\xfe\xca': ('<', 20),
           b'\xca\xfe\xba\xbf': ('>', 32), b'\xbf\xba\xfe\xca': ('<', 32)}
    if header[:4] in thin:
        require(len(header) >= 32, 'CAPABILITY_UNAVAILABLE', 'Усечённый Mach-O header.', 409)
        cpu = struct.unpack_from(thin[header[:4]] + 'I', header, 4)[0]
        require(cpu in processors, 'CAPABILITY_UNAVAILABLE', 'Неподдерживаемая Mach-O архитектура.', 409)
        return {processors[cpu]}
    endian, entry_size = fat[header[:4]]
    count = struct.unpack_from(endian + 'I', header, 4)[0]
    require(1 <= count <= 32, 'CAPABILITY_UNAVAILABLE', 'Некорректная fat Mach-O таблица.', 409)
    table_end = 8 + count * entry_size
    source.seek(8)
    table = source.read(count * entry_size)
    require(len(table) == count * entry_size, 'CAPABILITY_UNAVAILABLE', 'Усечённая fat Mach-O таблица.', 409)
    architectures, ranges = set(), []
    for index in range(count):
        entry = index * entry_size
        cpu = struct.unpack_from(endian + 'I', table, entry)[0]
        layout = 'QQ' if entry_size == 32 else 'II'
        offset, slice_size = struct.unpack_from(endian + layout, table, entry + 8)
        require(cpu in processors and offset >= table_end and slice_size >= 32 and offset + slice_size <= size,
                'CAPABILITY_UNAVAILABLE', 'Некорректный fat Mach-O slice.', 409)
        require(all(offset + slice_size <= start or offset >= end for start, end in ranges),
                'CAPABILITY_UNAVAILABLE', 'Перекрывающиеся fat Mach-O slices.', 409)
        ranges.append((offset, offset + slice_size))
        source.seek(offset)
        slice_header = source.read(32)
        require(slice_header[:4] in thin, 'CAPABILITY_UNAVAILABLE', 'Fat slice не является 64-bit Mach-O.', 409)
        actual_cpu = struct.unpack_from(thin[slice_header[:4]] + 'I', slice_header, 4)[0]
        require(cpu == actual_cpu, 'CAPABILITY_UNAVAILABLE', 'Архитектура fat slice не совпадает с таблицей.', 409)
        architectures.add(processors[cpu])
    return architectures


def native_architectures(path, *, system=None):
    """Читает native headers без запуска binary: Mach-O, ELF64 или PE x64/ARM64."""
    with Path(path).open('rb') as source:
        header = source.read(64)
        require(len(header) >= 8, 'CAPABILITY_UNAVAILABLE', 'Усечённый native header.', 409)
        magic = header[:4]
        if magic in {b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe',
                     b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}:
            require(system in {None, 'darwin'}, 'CAPABILITY_UNAVAILABLE', 'Ожидался native формат целевой ОС.', 409)
            return _macho_architectures(source, header, Path(path).stat().st_size)
        if magic == b'\x7fELF':
            require(system in {None, 'linux'} and len(header) >= 20 and header[4] == 2 and header[5] in {1, 2},
                    'CAPABILITY_UNAVAILABLE', 'Ожидался ELF64 целевой ОС.', 409)
            cpu = struct.unpack_from(('<' if header[5] == 1 else '>') + 'H', header, 18)[0]
            architectures = {62: 'x86_64', 183: 'arm64'}
        elif magic.startswith(b'MZ'):
            require(system in {None, 'win32'} and len(header) >= 64,
                    'CAPABILITY_UNAVAILABLE', 'Ожидался PE целевой ОС.', 409)
            offset = struct.unpack_from('<I', header, 60)[0]
            require(64 <= offset <= 1024 * 1024, 'CAPABILITY_UNAVAILABLE', 'Некорректное смещение PE header.', 409)
            source.seek(offset)
            coff = source.read(6)
            require(len(coff) == 6 and coff[:4] == b'PE\x00\x00', 'CAPABILITY_UNAVAILABLE', 'Некорректный PE header.', 409)
            cpu = struct.unpack_from('<H', coff, 4)[0]
            architectures = {0x8664: 'x86_64', 0xAA64: 'arm64'}
        else:
            raise DocumentError('CAPABILITY_UNAVAILABLE', 'Ожидался скомпилированный native binary.', 409)
        require(cpu in architectures, 'CAPABILITY_UNAVAILABLE', 'Неподдерживаемая native архитектура.', 409)
        return {architectures[cpu]}


def pack_directory(pack_root=None):
    """Выбирает только bundled каталог; PATH и скачивание не используются."""
    root = Path(pack_root) if pack_root is not None else resource_root() / 'resources' / 'lnav'
    return root if (root / 'manifest.json').is_file() else root / platform_key()


@lru_cache(maxsize=4)
def _verify_inventory(directory, inventory, stamps, native_names, system, machine):
    """Хеширует immutable closure один раз; изменение любого stat stamp сбрасывает proof."""
    digest = hashlib.sha256()
    for filename, expected in inventory:
        with (Path(directory) / filename).open('rb') as input_file:
            actual = hashlib.file_digest(input_file, 'sha256').hexdigest()
        require(actual == expected, 'CAPABILITY_UNAVAILABLE', 'Контрольная сумма lnav pack не совпадает.', 409)
        if filename in native_names:
            require(machine in native_architectures(Path(directory) / filename, system=system),
                    'CAPABILITY_UNAVAILABLE', 'lnav binary или библиотека имеет чужую архитектуру.', 409)
        digest.update(actual.encode('ascii'))
    return digest.hexdigest()


def _checked_pack(pack_root=None):
    """Проверяет происхождение, parser-only patch и SHA256 каждого файла поставки."""
    directory = pack_directory(pack_root).resolve()
    manifest_path = directory / 'manifest.json'
    require(manifest_path.is_file() and manifest_path.stat().st_size <= 200_000,
            'CAPABILITY_UNAVAILABLE', 'Сертифицированный lnav pack отсутствует.', 409)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    require(manifest.get('schemaVersion') == 1 and manifest.get('platform') == platform_key()
            and manifest.get('version') == '0.14.1' and manifest.get('certified') is True
            and manifest.get('secureModeConverterPatch') is True
            and manifest.get('withoutLibcurl') is True,
            'CAPABILITY_UNAVAILABLE', 'lnav pack не прошёл parser-only сертификацию.', 409)
    files = manifest.get('files', {})
    require(isinstance(files, dict) and 1 <= len(files) <= 1000,
            'CAPABILITY_UNAVAILABLE', 'Некорректный inventory lnav pack.', 409)
    total = 0
    stamps = []
    digest = hashlib.sha256(manifest_path.read_bytes())
    for filename, expected in sorted(files.items()):
        relative = Path(filename)
        require(not relative.is_absolute() and '..' not in relative.parts,
                'CAPABILITY_UNAVAILABLE', 'Недопустимый путь lnav pack.', 409)
        path = directory / relative
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(directory.resolve()),
                'CAPABILITY_UNAVAILABLE', 'Файл lnav pack отсутствует или заменён ссылкой.', 409)
        metadata = path.stat()
        total += metadata.st_size
        stamps.append((metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns))
        require(total <= 256 * 1024 * 1024, 'CAPABILITY_UNAVAILABLE', 'lnav pack превышает бюджет проверки.', 409)
    executable = manifest.get('executable')
    require(isinstance(executable, str) and executable in files,
            'CAPABILITY_UNAVAILABLE', 'Executable не включён в inventory lnav pack.', 409)
    native_names = []
    for filename in files:
        name = Path(filename).name.lower()
        if filename == executable or name.endswith(('.dylib', '.dll', '.so')) or '.so.' in name:
            native_names.append(filename)
    proof = _verify_inventory(str(directory.resolve()), tuple(sorted(files.items())), tuple(stamps),
                              tuple(sorted(native_names)), sys.platform, process_architecture())
    digest.update(proof.encode('ascii'))
    return directory / executable, manifest, digest.hexdigest()


def capability(pack_root=None):
    """Возвращает доступность без запуска lnav и без чтения пользовательских логов."""
    try:
        _, manifest, fingerprint = _checked_pack(pack_root)
        return {'available': True, 'version': manifest['version'], 'fingerprint': fingerprint,
                'parserOnly': True, 'runtimeInstalls': False, 'network': False}
    except (DocumentError, OSError, ValueError, TypeError):
        return {'available': False, 'missingAsset': 'certified-lnav-runtime-pack', 'runtimeInstalls': False}


def parser_fingerprint(pack_root=None):
    """Версия артефакта включает binary closure и parser-only provenance."""
    from .documents.providers import _adapter_bytes
    result = capability(pack_root)
    adapter = hashlib.sha256(_adapter_bytes(__name__)).hexdigest()
    return 'lnav:' + result.get('fingerprint', 'unavailable') + ':adapter:' + adapter


def _terminate(process):
    """Убирает только потомков запущенного subprocess и его процессную группу."""
    import psutil
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
        children = []
    for child in reversed(children):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    if process.poll() is None:
        if os.name != 'nt':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
    process.wait(timeout=3)


def parse_log(path, *, max_events=5000, timeout_seconds=15, max_output_bytes=8 * 1024 * 1024,
              memory_bytes=256 * 1024 * 1024, pack_root=None):
    """Возвращает bounded rows из captured UTF-8 файла, не принимая SQL/config/commands извне.

    RSS наблюдается каждые 50 ms с возможным overshoot; это не жёсткая OS sandbox.
    """
    import psutil
    require(type(max_events) is int and 1 <= max_events <= 20_000, 'INVALID_LIMIT', 'Некорректный предел событий.')
    require(type(timeout_seconds) in {int, float} and 0 < timeout_seconds <= 30
            and type(max_output_bytes) is int and 0 < max_output_bytes <= 32 * 1024 * 1024
            and type(memory_bytes) is int and 0 < memory_bytes <= 512 * 1024 * 1024,
            'INVALID_LIMIT', 'Некорректный бюджет lnav.')
    executable, _, fingerprint = _checked_pack(pack_root)
    path = Path(path).resolve(strict=True)
    require(path.is_file() and path.stat().st_size <= 50 * 1024 * 1024,
            'INPUT_LIMIT', 'lnav принимает ограниченный captured текстовый файл.')
    # Не передаём архивы/бинарные контейнеры подсистеме автоматических converters.
    payload = path.read_bytes()
    require(b'\x00' not in payload, 'INVALID_INPUT', 'lnav принимает только UTF-8 текст.')
    payload.decode('utf-8', errors='strict')
    with tempfile.TemporaryDirectory(prefix='atlas-lnav-') as directory:
        temporary = Path(directory)
        output_path, error_path = temporary / 'stdout.json', temporary / 'stderr.txt'
        environment = {'HOME': directory, 'XDG_CONFIG_HOME': directory, 'XDG_DATA_HOME': directory,
                       'XDG_CACHE_HOME': directory, 'TMPDIR': directory, 'TEMP': directory, 'TMP': directory,
                       'LNAVSECURE': '1', 'LANG': 'C', 'LC_ALL': 'C', 'TERM': 'dumb'}
        if os.environ.get('SYSTEMROOT'):
            environment['SYSTEMROOT'] = os.environ['SYSTEMROOT']
        sql = f';SELECT log_line, log_time, log_level, log_body, log_text FROM all_logs LIMIT {max_events + 1}'
        command = [str(executable), '-n', '-N', '-q', '-c', sql, '-c', ':write-json-to -', '--', str(path)]
        with output_path.open('wb') as output, error_path.open('wb') as errors:
            process = subprocess.Popen(command, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=errors, start_new_session=os.name != 'nt',
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
            started = time.monotonic()
            try:
                monitored = psutil.Process(process.pid)
                while process.poll() is None:
                    require(time.monotonic() - started <= timeout_seconds, 'LNAV_TIMEOUT', 'lnav превысил время выполнения.', 408)
                    require(output_path.stat().st_size + error_path.stat().st_size <= max_output_bytes,
                            'OUTPUT_LIMIT', 'lnav превысил бюджет вывода.')
                    try:
                        rss = monitored.memory_info().rss
                        for child in monitored.children(recursive=True):
                            try:
                                rss += child.memory_info().rss
                            except psutil.NoSuchProcess:
                                pass
                        require(rss <= memory_bytes, 'WORKER_MEMORY_LIMIT', 'lnav превысил наблюдаемый предел RSS.')
                    except psutil.NoSuchProcess:
                        pass
                    except (psutil.AccessDenied, PermissionError) as error:
                        raise DocumentError('MEMORY_MONITOR_UNAVAILABLE', 'Невозможно наблюдать lnav RSS.', 409) from error
                    time.sleep(0.05)
                require(output_path.stat().st_size + error_path.stat().st_size <= max_output_bytes,
                        'OUTPUT_LIMIT', 'lnav превысил бюджет вывода.')
                require(process.returncode == 0, 'LNAV_PARSE_FAILED', error_path.read_text(errors='replace')[:1000])
            finally:
                _terminate(process)
        rows = json.loads(output_path.read_text(encoding='utf-8'))
        require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows), 'LNAV_PARSE_FAILED', 'Ожидался JSON-массив событий.')
        return {'rows': rows[:max_events], 'truncated': len(rows) > max_events, 'runtimeFingerprint': fingerprint}
