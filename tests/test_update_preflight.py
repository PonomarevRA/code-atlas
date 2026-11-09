"""Обновление приложения не архивирует и не переписывает сохранённые проекты."""
import json

import pytest

from atlas.process_lock import ProcessLock
from atlas.runtime_paths import ensure_data_schema
from atlas.update_preflight import prepare_update


def test_same_schema_preserves_data_and_blocks_running_application(tmp_path):
    """Проверяет реальный OS lock и неизменность bytes/inode/mtime больших данных."""
    root = tmp_path / 'state'
    ensure_data_schema(root)
    source = root / 'snapshot.sqlite'
    source.write_bytes(b'captured data' * 10000)
    with ProcessLock(root / 'server.lock'):
        with pytest.raises(RuntimeError, match='Остановите'):
            prepare_update(root)
    before = {p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in root.iterdir()}
    assert prepare_update(root)['backupCreated'] is False
    assert {p.name: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in root.iterdir()} == before
    assert not (tmp_path / 'state-backups').exists()


def test_missing_unknown_or_corrupted_schema_never_modifies_data(tmp_path):
    """Первый install не создаёт каталог, миграции не запускаются неявно."""
    root = tmp_path / 'new-state'
    assert prepare_update(root)['status'] == 'new-install' and not root.exists()
    root.mkdir()
    (root / 'keep').write_text('user text')
    with pytest.raises(RuntimeError, match='миграции'):
        prepare_update(root)
    for value in ['broken', json.dumps({'schemaVersion': 999}), json.dumps({'schemaVersion': True})]:
        (root / 'data-schema.json').write_text(value)
        with pytest.raises(RuntimeError, match='миграция'):
            prepare_update(root)
        assert (root / 'data-schema.json').read_text() == value
        assert (root / 'keep').read_text() == 'user text'
