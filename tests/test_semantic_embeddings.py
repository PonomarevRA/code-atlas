"""Offline provider contract, exact token budget and real local multilingual embeddings."""
from pathlib import Path
import socket

import pytest

from atlas.semantic_embeddings import LocalEmbedding, SemanticIndexError
from atlas.semantic_index import IndexDatabase


def test_provider_missing_model_is_cheap_and_explicit(tmp_path):
    """Status does not hash weights or construct an ONNX model."""
    provider = LocalEmbedding(tmp_path)
    assert provider.available() is False
    assert provider.metadata()["dimensions"] == 384
    assert provider._model is None and provider._tokenizer is None and provider._identity is None
    with pytest.raises(SemanticIndexError, match="не установлена"):
        provider.identity()
    with pytest.raises(SemanticIndexError, match="не установлена"):
        provider.query("Валюта")
    assert provider._model is None


def test_real_local_provider_and_lancedb_never_access_network(tmp_path, monkeypatch):
    """Exercise the installed model with sockets denied; absent local weights skip rather than download."""
    cache = Path.home() / "Library/Application Support/Code Atlas/models"
    provider = LocalEmbedding(cache)
    if not provider.available():
        pytest.skip("Real local embedding model is not installed")

    def network_denied(*args, **kwargs):
        raise AssertionError("Embedding attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", network_denied)
    monkeypatch.setattr(socket.socket, "connect_ex", network_denied)
    monkeypatch.setattr(socket, "create_connection", network_denied)
    identity = provider.identity()
    assert provider.identity() == identity
    assert provider._model is None, "Fingerprinting does not create ONNX runtime"
    assert identity["dimensions"] == 384
    text = "Как работает конвертация валют? 日本語Tokenizer " + "токен_abc123! " * 160 + "   "
    parts = provider.split_text(text)
    assert len(parts) > 1
    assert "".join(part["text"] for part in parts) == text
    tokenizer = provider._get_tokenizer()
    assert all(len(tokenizer.encode(part["text"], add_special_tokens=False).ids) <= 126 for part in parts)
    assert parts[0]["start"] == 0 and parts[-1]["end"] == len(text)
    assert all(left["end"] == right["start"] for left, right in zip(parts, parts[1:]))
    vectors = provider.embed([parts[0]["text"], "Calculate asset value"])
    assert len(vectors) == 2 and all(len(vector) == 384 for vector in vectors)
    assert len(provider.query("Как происходит расчет стоимости?")) == 384
    with pytest.raises(SemanticIndexError, match="превышает 126"):
        provider.query(text)
    database = IndexDatabase(tmp_path, provider)
    captured = {"chunks": [{"id": "currency", "kind": "documentation", "text": "Currency conversion using exchange rates. Конвертация валют по курсу.", "file": "docs/currency.md", "line": 1, "endLine": 2, "sourceHash": "hash", "nodeIds": ["raw-id"], "association": "documents", "sourceKind": "working-tree", "truncated": False}]}
    metadata = database.build("project", "snapshot", captured, lambda: None)
    assert metadata["embeddedCount"] == 1
    hits = database.search("project", "snapshot", "Как конвертируется валюта?")["hits"]
    assert len(hits) == 1 and hits[0]["nodeIds"] == ["raw-id"]
    assert hits[0]["file"] == "docs/currency.md"
