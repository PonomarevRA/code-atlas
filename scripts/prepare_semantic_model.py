"""Явная однократная загрузка весов; сервер индексации всегда работает offline."""

import argparse
from pathlib import Path
import shutil
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from atlas.runtime_paths import default_data_dir
from atlas.semantic_embeddings import LocalEmbedding


def materialize_model_cache(cache_dir: Path) -> int:
    """Заменяет только внутренние HF-ссылки этой модели файлами для portable backup.

    Backup Atlas намеренно отклоняет symlinks. Скачанный кэш Hugging Face содержит
    ссылки snapshot → blobs; копирование байтов сохраняет веса и fingerprint.
    Разрешены blobs этой модели и общий cache/blobs. Соседние данные и
    специальные файлы не разыменовываются.
    """
    cache_dir = cache_dir.resolve()
    root = (cache_dir / LocalEmbedding.CACHE_NAME).resolve()
    if not root.is_relative_to(cache_dir):
        raise ValueError("Каталог модели указывает за границу кэша")
    shared_blobs = cache_dir / "blobs"
    count = 0
    if not root.is_dir():
        return count
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        target = path.resolve()
        # Новые версии HF также используют общий content-addressed каталог cache/blobs.
        internal = target.is_relative_to(root) or target.is_relative_to(shared_blobs)
        if not internal or not target.is_file():
            raise ValueError("Ссылка кэша модели указывает за границу каталога или не на файл")
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            shutil.copyfile(target, temporary)
            temporary.replace(path)
            count += 1
        finally:
            temporary.unlink(missing_ok=True)
    return count


def main():
    """Подготавливает общую локальную модель без чтения файлов проекта."""
    parser = argparse.ArgumentParser(description="Подготовить локальный RU/EN embedding для Code Atlas")
    parser.add_argument("--cache-dir", type=Path, default=default_data_dir() / "models")
    parser.add_argument("--materialize-only", action="store_true", help="Сделать существующий HF-кэш переносимым без сети")
    args = parser.parse_args()
    if not args.materialize_only:
        from fastembed import TextEmbedding
        model = TextEmbedding(model_name=LocalEmbedding.NAME, cache_dir=str(args.cache_dir),
                              threads=2, providers=["CPUExecutionProvider"])
        vector = next(model.embed(["Проверка локальной модели"], batch_size=1))
        print(f"Модель готова: {len(vector)} измерений; кэш {args.cache_dir}")
    print(f"Переносимый кэш: преобразовано ссылок {materialize_model_cache(args.cache_dir)}")


if __name__ == "__main__":
    main()
