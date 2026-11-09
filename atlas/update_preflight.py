"""Проверка совместимости обновления без копирования пользовательского каталога."""
import json
import os
from pathlib import Path

from .runtime_paths import DATA_SCHEMA_VERSION


def _check_schema(root):
    """Не создаёт marker: существующий неизвестный формат требует отдельной миграции."""
    marker = root / 'data-schema.json'
    if not marker.exists():
        if any(path.name != 'server.lock' for path in root.iterdir()):
            raise RuntimeError('Каталог данных без версии требует отдельной миграции с резервной копией.')
        return 'empty'
    if marker.is_symlink():
        raise RuntimeError('Marker схемы данных не должен быть ссылкой.')
    try:
        value = json.loads(marker.read_text(encoding='utf-8'))['schemaVersion']
        if type(value) is not int or value != DATA_SCHEMA_VERSION:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError('Версия данных несовместима: требуется отдельная миграция с резервной копией.') from error
    return 'compatible'


def prepare_update(data_dir):
    """Проверяет остановку сервера и схему; не делает ZIP, migration или записи данных."""
    root = Path(data_dir)
    if not root.exists():
        return {'status': 'new-install', 'dataCopied': False, 'backupCreated': False}
    if not root.is_dir():
        raise RuntimeError('Путь данных должен быть каталогом.')
    lock_path = root / 'server.lock'
    handle = None
    try:
        if lock_path.exists():
            if lock_path.is_symlink():
                raise RuntimeError('Ссылка вместо server.lock не поддерживается.')
            handle = lock_path.open('r+b')
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError('Каталог данных открыт Code Atlas. Остановите приложение перед обновлением.') from error
        status = _check_schema(root)
        return {'status': status, 'dataSchemaVersion': DATA_SCHEMA_VERSION, 'dataCopied': False, 'backupCreated': False}
    finally:
        if handle is not None:
            handle.close()
