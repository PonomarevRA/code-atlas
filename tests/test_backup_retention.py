"""Границы удаления резервных ZIP и сохранение копий при сбое новой записи."""

import os
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import pytest

from atlas.backup_retention import automatic_backup, prune_automatic_backups
from atlas.process_lock import ProcessLock
from atlas.runtime_paths import backup_data


def make_archive(directory: Path, day: int) -> Path:
    """Создаёт завершённый автоматический архив с контролируемой датой."""
    path = directory / f"202601{day:02d}T120000Z-00000000.zip"
    with ZipFile(path, "x") as archive:
        archive.writestr("projects.json", "{}")
    return path


def test_retention_preserves_manual_directories_links_and_incomplete_files(tmp_path):
    """Удаляются только старые штатные ZIP, независимо от даты ручных копий."""
    archives = [make_archive(tmp_path, day) for day in range(1, 6)]
    manual = tmp_path / "named-backup.zip"
    manual.write_bytes(b"manual")
    (tmp_path / "20250101T120000Z-ffffffff.zip").mkdir()
    (tmp_path / "20260106T120000Z-00000000.tmp").write_bytes(b"partial")
    (tmp_path / "20261301T120000Z-00000000.zip").write_bytes(b"invalid-date")
    (tmp_path / "20260106T120000Z-00000000Xzip").write_bytes(b"unknown-name")
    link = tmp_path / "20260106T120000Z-ffffffff.zip"
    link.symlink_to(manual)
    removed = prune_automatic_backups(tmp_path)
    assert removed == [archives[1], archives[0]]
    assert all(path.exists() for path in archives[2:])
    assert manual.read_bytes() == b"manual"
    assert link.is_symlink()
    assert len(list(tmp_path.iterdir())) == 10
    assert prune_automatic_backups(tmp_path) == []


def test_same_second_uses_completion_time_before_random_suffix(tmp_path):
    """Случайный UUID в имени не вытесняет только что созданную копию той же секунды."""
    paths = []
    for index, suffix in enumerate(["ffffffff", "eeeeeeee", "dddddddd", "00000000"]):
        path = tmp_path / f"20260101T120000Z-{suffix}.zip"
        with ZipFile(path, "x") as archive:
            archive.writestr("projects.json", "{}")
        os.utime(path, ns=(index + 1, index + 1))
        paths.append(path)
    assert prune_automatic_backups(tmp_path) == [paths[0]]
    assert paths[-1].is_file()


def test_corrupt_kept_archive_prevents_any_deletion(tmp_path):
    """Повреждённая новая копия не служит основанием удалить старую исправную."""
    archives = [make_archive(tmp_path, day) for day in range(1, 5)]
    archives[-1].write_bytes(b"unfinished zip")
    with pytest.raises(BadZipFile):
        prune_automatic_backups(tmp_path)
    assert all(path.exists() for path in archives)


@pytest.mark.parametrize("keep", [0, -1, True, 1.5])
def test_invalid_count_never_deletes(tmp_path, keep):
    """Ошибочная настройка не может удалить все копии."""
    archive = make_archive(tmp_path, 1)
    with pytest.raises(ValueError):
        prune_automatic_backups(tmp_path, keep)
    assert archive.exists()


def test_linked_backup_root_and_held_lock_are_rejected(tmp_path):
    """Очистка не выходит за назначенную папку и не конкурирует со вторым процессом."""
    root = tmp_path / "backups"
    root.mkdir()
    archive = make_archive(root, 1)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(RuntimeError, match="ссылки"):
        prune_automatic_backups(alias)
    with ProcessLock(root / ".retention.lock"):
        with pytest.raises(RuntimeError):
            prune_automatic_backups(root)
    assert archive.exists()


def test_successful_backup_prunes_but_write_failure_keeps_existing_copies(tmp_path, monkeypatch):
    """Ротация выполняется только после публикации полного читаемого нового ZIP."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "projects.json").write_text("{}")
    destination = tmp_path / "data-backups"
    destination.mkdir()
    archives = [make_archive(destination, day) for day in range(1, 4)]
    original_write = ZipFile.write

    def fail_write(*args, **kwargs):
        """Имитирует отказ диска при создании следующего архива."""
        raise OSError("disk full")

    monkeypatch.setattr(ZipFile, "write", fail_write)
    with pytest.raises(OSError, match="disk full"):
        backup_data(data)
    assert all(path.exists() for path in archives)
    assert not list(destination.glob("*.tmp"))
    monkeypatch.setattr(ZipFile, "write", original_write)
    new_archive = backup_data(data)
    assert new_archive.is_file()
    assert not archives[0].exists()
    assert all(path.exists() for path in archives[1:])
    assert len([path for path in destination.iterdir() if automatic_backup(path)]) == 3
    with ZipFile(new_archive) as archive:
        assert archive.read("projects.json") == b"{}"


def test_backup_rejects_linked_destination_before_writing(tmp_path):
    """Создание копии также не обходит ссылку в соседний чужой каталог."""
    data = tmp_path / "data"
    data.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / "data-backups").symlink_to(external, target_is_directory=True)
    with pytest.raises(RuntimeError, match="ссылки"):
        backup_data(data)
    assert list(external.iterdir()) == []
