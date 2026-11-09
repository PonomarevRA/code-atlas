"""Подготовленный HF-кэш переносится без ссылок, выхода из границы и скачивания."""
from pathlib import Path
import pytest
from scripts.prepare_semantic_model import materialize_model_cache
from atlas.semantic_embeddings import LocalEmbedding


def test_materialize_internal_model_links_preserves_bytes(tmp_path):
    """Только cache этой модели изменяется; повторный проход ничего не пишет."""
    root = tmp_path / LocalEmbedding.CACHE_NAME
    root.mkdir()
    blob = root / "weights"
    blob.write_bytes(b"official-model-placeholder")
    snapshot = root / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    link = snapshot / "model_optimized.onnx"
    link.symlink_to(blob)
    assert materialize_model_cache(tmp_path) == 1
    assert not link.is_symlink() and link.read_bytes() == blob.read_bytes()
    assert materialize_model_cache(tmp_path) == 0


def test_materialize_external_link_rejected(tmp_path):
    """Ссылку на чужой файл нельзя превратить в скрытую копию данных."""
    outside = tmp_path / "outside"
    outside.write_text("unrelated")
    root = tmp_path / LocalEmbedding.CACHE_NAME
    root.mkdir()
    link = root / "tokenizer.json"
    link.symlink_to(outside)
    with pytest.raises(ValueError):
        materialize_model_cache(tmp_path)
    assert link.is_symlink()


def test_materialize_shared_huggingface_blob(tmp_path):
    """Общий HF content-addressed blob внутри cache допустим, соседние файлы — нет."""
    blob = tmp_path / "blobs" / "ab" / "checksum"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"shared-weights")
    root = tmp_path / LocalEmbedding.CACHE_NAME
    root.mkdir()
    link = root / "model_optimized.onnx"
    link.symlink_to(blob)
    assert materialize_model_cache(tmp_path) == 1
    assert link.read_bytes() == b"shared-weights" and not link.is_symlink()
