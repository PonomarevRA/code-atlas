"""Отдельный процесс парсера с timeout/output limits; это не OS sandbox."""

import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time

from .models import DocumentError, encode, require, validate_tree


def _sample_process_tree(monitored, descendants, psutil):
    """Считает RSS worker и потомков; сохраняет identity-aware handles для cleanup."""
    rss = monitored.memory_info().rss
    for child in monitored.children(recursive=True):
        descendants[child.pid] = child
        try:
            rss += child.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return rss


def _stop_descendants(descendants, psutil):
    """Не оставляет native parser после отмены/timeout внешнего worker."""
    for child in reversed(list(descendants.values())):
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
            pass
    if descendants:
        try:
            psutil.wait_procs(list(descendants.values()), timeout=1)
        except (psutil.AccessDenied, PermissionError):
            pass  # Не мешает обязательному terminate/join самого worker в finally.


def _parse_child(provider, source, options, context, output, memory_bytes):
    """Не передаёт парсеру cwd проекта и переменные окружения с секретами."""
    os.chdir(Path(source).parent)
    preserved = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'LANG') if key in os.environ}
    os.environ.clear()
    os.environ.update(preserved)
    if memory_bytes:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    try:
        tree = validate_tree(provider.parse(Path(source), options, context))
        payload = {'tree': tree}
    except Exception as error:
        payload = {'error': {'code': getattr(error, 'code', 'PARSE_FAILED'), 'message': str(error)[:2000]}}
    Path(output).write_text(encode(payload), encoding='utf-8')


def run_provider(provider, source, options, context, cancelled, timeout=30, max_output_bytes=32 * 1024 * 1024, memory_bytes=512 * 1024 * 1024, address_space_bytes=None):
    """Следит за RSS каждые 50 ms с возможным overshoot; это не жёсткий OS memory cap."""
    try:
        import psutil
    except ImportError as error:
        raise DocumentError('MEMORY_MONITOR_UNAVAILABLE', 'Для импорта нужен установленный host runtime psutil.', 409) from error
    require(type(memory_bytes) is int and memory_bytes > 0, 'INVALID_MEMORY_LIMIT', 'Ожидается положительный предел RSS в байтах.')
    with tempfile.TemporaryDirectory(prefix='atlas-document-worker-') as directory:
        output = Path(directory) / 'result.json'
        process = multiprocessing.get_context('spawn').Process(target=_parse_child, args=(provider, str(source), options, context, str(output), address_space_bytes))
        process.start()
        started = time.monotonic()
        monitored = None
        descendants = {}
        sampled_at = 0.0
        try:
            while process.is_alive():
                require(not cancelled(), 'CANCELLED', 'Извлечение отменено.', 409)
                require(time.monotonic() - started <= timeout, 'WORKER_TIMEOUT', 'Парсер превысил время выполнения.', 408)
                require(not output.exists() or output.stat().st_size <= max_output_bytes, 'OUTPUT_LIMIT', 'Результат парсера слишком велик.')
                if time.monotonic() - sampled_at >= 0.05:
                    sampled_at = time.monotonic()
                    try:
                        if monitored is None:
                            monitored = psutil.Process(process.pid)
                        rss = _sample_process_tree(monitored, descendants, psutil)
                    except psutil.NoSuchProcess:
                        rss = 0
                    except (psutil.AccessDenied, PermissionError) as error:
                        raise DocumentError('MEMORY_MONITOR_UNAVAILABLE', 'Невозможно наблюдать RSS процесса парсера.', 409) from error
                    require(rss <= memory_bytes, 'WORKER_MEMORY_LIMIT', 'Процесс парсера превысил наблюдаемый предел RSS.')
                process.join(0.025)
            require(process.exitcode == 0 and output.exists(), 'WORKER_CRASH', 'Процесс парсера завершился без результата.')
            require(output.stat().st_size <= max_output_bytes, 'OUTPUT_LIMIT', 'Результат парсера слишком велик.')
            result = json.loads(output.read_text(encoding='utf-8'))
            if 'error' in result:
                raise DocumentError(result['error']['code'], result['error']['message'])
            return validate_tree(result['tree'])
        finally:
            if monitored is not None:
                try:
                    _sample_process_tree(monitored, descendants, psutil)
                except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
                    pass
            _stop_descendants(descendants, psutil)
            if process.is_alive():
                process.terminate()
                process.join(1)
                if process.is_alive():
                    process.kill()
                    process.join(1)
            process.close()
