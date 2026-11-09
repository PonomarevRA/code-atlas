"""Bounded reference reuse preserves published vectors and overload-level identity."""
import hashlib
import sqlite3

import lancedb
import pytest

from atlas import reference_vectors
from atlas.reference_vectors import ReferenceVectors, _EmbeddingCache


class DeterministicModel:
    """Tracks actual encoding calls while producing stable vectors for each description."""

    def __init__(self):
        self.encoded = []

    def identity(self):
        return {"dimensions": 2, "fingerprint": "resource-test"}

    def split_text(self, text):
        return [{"text": text}]

    def embed(self, texts):
        self.encoded.extend(texts)
        return [self.vector(text) for text in texts]

    @staticmethod
    def vector(text):
        number = hashlib.sha256(text.encode()).digest()[0]
        return [float(number), 1.]


def test_embedding_reuse_limits_count_and_estimated_bytes(monkeypatch):
    """Unique corpora cannot grow the reuse cache; eviction never alters a vector."""
    monkeypatch.setattr(reference_vectors, "MAX_REUSED_VECTORS", 8)
    monkeypatch.setattr(reference_vectors, "MAX_REUSED_VECTOR_BYTES", 2500)
    cache = _EmbeddingCache()
    for index in range(1000):
        text = f"Member {index}"
        cache.put(text, [float(index), 1.])
        assert len(cache.entries) <= 8
        assert cache.bytes <= 2500
        assert cache.get(text) == [float(index), 1.]
    assert cache.get("Member 0") is None
    retained = list(cache.entries)
    assert cache.get(retained[0]) is not None
    cache.put("Member new", [1001., 1.])
    assert cache.get(retained[0]) is not None
    assert cache.get(retained[1]) is None


def test_evicted_vectors_reencode_without_changing_lance_rows(tmp_path, monkeypatch):
    """Cross-batch overloads reuse nearby summaries and safely reencode evicted ones."""
    monkeypatch.setattr(reference_vectors, "MAX_REUSED_VECTORS", 4)
    source = tmp_path / "source"
    source.mkdir()
    texts = [f"Unique description {index}" for index in range(256)]
    texts.extend([texts[-1], texts[0], texts[0], "Final description"])
    with sqlite3.connect(source / "documents.sqlite") as database:
        database.execute("CREATE TABLE documents(id TEXT, path TEXT, title TEXT, symbol TEXT, text TEXT, summary TEXT)")
        database.executemany("INSERT INTO documents VALUES(?,?,?,?,?,?)",
                             [(f"{index:04d}", "api.xml", "Member", "API", text, text)
                              for index, text in enumerate(texts)])
    pack = {"id": "bounded", "language": "csharp", "_directory": str(source), "documentCount": len(texts)}
    model = DeterministicModel()
    vectors = ReferenceVectors(tmp_path / "vectors", model)
    manifest = vectors.build(pack, lambda **values: None)
    rows = lancedb.connect(tmp_path / "vectors" / "bounded" / "lance").open_table(manifest["table"]).to_arrow().to_pylist()
    assert {row["id"]: row["vector"] for row in rows} == {
        f"{index:04d}": model.vector(text) for index, text in enumerate(texts)}
    assert model.encoded.count(texts[0]) == 2
    assert model.encoded.count(texts[255]) == 1
    assert manifest["embeddedCount"] == 258
    assert manifest["reusedCount"] == 2


@pytest.mark.parametrize("cancel_phase", ["progress", "embed"])
def test_cancel_last_batch_preserves_manifest_and_removes_unpublished_table(tmp_path, cancel_phase):
    """Cancellation during final embedding/progress cannot publish its generation."""
    from threading import Event
    from atlas.reference_documents import ReferenceError

    source = tmp_path / "source"
    source.mkdir()
    with sqlite3.connect(source / "documents.sqlite") as database:
        database.execute("CREATE TABLE documents(id TEXT, path TEXT, title TEXT, symbol TEXT, text TEXT, summary TEXT)")
        database.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", ("1", "api.xml", "Member", "API", "Description", "Description"))
    pack = {"id": "cancelled", "language": "csharp", "_directory": str(source), "documentCount": 1}
    vectors = ReferenceVectors(tmp_path / "vectors", DeterministicModel())
    previous = vectors.build(pack, lambda **values: None)
    directory = tmp_path / "vectors" / "cancelled"
    published_bytes = (directory / "vectors.json").read_bytes()
    cancelled = Event()
    original_embed = vectors.embedder.embed

    def cancel_after_embedding(texts):
        result = original_embed(texts)
        if cancel_phase == "embed":
            cancelled.set()
        return result

    vectors.embedder.embed = cancel_after_embedding

    def cancel_at_final_batch(**progress):
        if cancel_phase == "progress" and progress["indexed"] == pack["documentCount"]:
            cancelled.set()

    with pytest.raises(ReferenceError, match="cancelled"):
        vectors.build(pack, cancel_at_final_batch, cancelled.is_set)
    assert (directory / "vectors.json").read_bytes() == published_bytes
    assert vectors.status(pack["id"]) == previous
    database = lancedb.connect(directory / "lance")
    assert database.table_names() == [previous["table"]]
    assert database.open_table(previous["table"]).count_rows() == 1
    assert not list(directory.glob(".vectors-*"))

