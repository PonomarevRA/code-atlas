"""Real LanceDB persistence with deterministic fake embeddings isolates index guarantees."""
import copy
from concurrent.futures import ThreadPoolExecutor
import math
from threading import Event

import pytest

from atlas.semantic_embeddings import SemanticIndexError
from atlas.semantic_index import IndexDatabase


class FakeEmbedding:
    """Small known vectors make ranking and the number of model calls observable."""

    def __init__(self, fingerprint="fake-v1"):
        self.fingerprint = fingerprint
        self.embedded = []
        self.queries = []
        self.failure = None

    def identity(self):
        return {"name": "fake", "dimensions": 3, "fingerprint": self.fingerprint}

    def split_text(self, text, max_tokens=126):
        return [{"text": text, "start": 0, "end": len(text), "tokenCount": 3}]

    def embed(self, texts):
        if self.failure:
            raise self.failure
        self.embedded.extend(texts)
        return [self._value(text) for text in texts]

    def query(self, text):
        self.queries.append(text)
        return self._value(text)

    def _value(self, text):
        return [1.0, 0.0, .1] if "currency" in text else [0.0, 1.0, .1]


def corpus(*texts, kind="comment", file="Calculator.cs"):
    """Captured source ranges remain independent of vector cache keys."""
    return {"extractorVersion": "test-v1", "sourceKind": "captured", "chunks": [{"id": f"chunk-{index}", "text": text, "kind": kind, "file": file, "line": 10 + index, "endLine": 12 + index, "sourceHash": "content", "nodeIds": ["raw-method"], "association": "declaration", "truncated": False, "sourceKind": "working-tree"} for index, text in enumerate(texts)], "stats": {"comments": len(texts)}, "limits": {"chunkMaxChars": 600}, "warnings": []}


def current():
    """Stable captured snapshot is accepted for test publication."""


def test_persistence_reuse_isolation_and_kind_filter(tmp_path):
    embedder = FakeEmbedding()
    database = IndexDatabase(tmp_path, embedder)
    captured = corpus("currency conversion", "asset valuation")
    before = copy.deepcopy(captured)
    metadata = database.build("project-a", "snapshot-a", captured, current)
    assert metadata["embeddedCount"] == 2
    assert metadata["limits"]["tokenLimitGuaranteed"] is True
    assert captured == before
    assert database.build("project-a", "snapshot-a", captured, current) == metadata
    assert len(embedder.embedded) == 2
    docs = corpus("currency conversion", kind="documentation", file="Wiki/Conversion.md")
    second = database.build("project-b", "snapshot-b", docs, current)
    assert second["embeddedCount"] == 0
    assert second["reusedCount"] == 1
    assert len(embedder.embedded) == 2
    first_hits = database.search("project-a", "snapshot-a", "currency")
    assert first_hits["hits"][0]["text"] == "currency conversion"
    assert first_hits["hits"][0]["file"] == "Calculator.cs"
    assert first_hits["hits"][0]["nodeIds"] == ["raw-method"]
    assert math.isfinite(first_hits["hits"][0]["distance"])
    assert database.search("project-a", "snapshot-a", "currency", kind="documentation")["hits"] == []
    other = database.search("project-b", "snapshot-b", "currency", kind="documentation")["hits"]
    assert len(other) == 1 and other[0]["file"] == "Wiki/Conversion.md"
    assert database.status("project-a", "snapshot-b") is None
    with pytest.raises(SemanticIndexError, match="не построен"):
        database.search("project-a", "missing", "currency")
    database.close()
    reloaded_provider = FakeEmbedding()
    reloaded = IndexDatabase(tmp_path, reloaded_provider)
    assert reloaded.status("project-a", "snapshot-a") == metadata
    reloaded.build("project-a", "snapshot-c", corpus("currency conversion", file="New.cs"), current)
    assert reloaded_provider.embedded == []
    assert reloaded.search("project-a", "snapshot-c", "currency")["hits"][0]["file"] == "New.cs"
    assert reloaded.search("project-a", "snapshot-a", "currency")["hits"][0]["file"] == "Calculator.cs"


def test_failed_build_preserves_published_generation(tmp_path):
    embedder = FakeEmbedding()
    database = IndexDatabase(tmp_path, embedder)
    original = database.build("project", "snapshot", corpus("currency conversion"), current)
    embedder.failure = RuntimeError("model failed")
    with pytest.raises(RuntimeError, match="model failed"):
        database.build("project", "snapshot", corpus("new unseen text"), current, force=True)
    assert database.status("project", "snapshot") == original
    embedder.failure = None

    def stale():
        raise SemanticIndexError("snapshot changed", 409)

    with pytest.raises(SemanticIndexError, match="snapshot changed"):
        database.build("project", "snapshot", corpus("asset valuation"), stale)
    assert database.status("project", "snapshot") == original
    assert database.search("project", "snapshot", "currency")["hits"][0]["text"] == "currency conversion"
    # A failed publication may retain validated vectors, but cannot publish provenance from that build.
    final = database.build("project", "next", corpus("asset valuation"), current)
    assert final["embeddedCount"] == 0
    assert database.status("project", "snapshot")["table"] == original["table"]


@pytest.mark.parametrize("bad", [[1, 2], [1, float("nan"), 2], [1, float("inf"), 2], [1e100, 2, 3], [0, 0, 0]])
def test_invalid_vectors_never_publish(tmp_path, bad):
    provider = FakeEmbedding()
    provider.embed = lambda texts: [bad] * len(texts)
    database = IndexDatabase(tmp_path, provider)
    with pytest.raises(SemanticIndexError):
        database.build("project", "snapshot", corpus("text"), current)
    assert database.status("project", "snapshot") is None


def test_snapshot_and_model_match_without_loading_status(tmp_path):
    provider = FakeEmbedding()
    database = IndexDatabase(tmp_path, provider)
    metadata = database.build("project", "snapshot", corpus("currency"), current)
    offline = FakeEmbedding("different")
    cold = IndexDatabase(tmp_path, offline)
    offline.identity = lambda: (_ for _ in ()).throw(AssertionError("status loaded identity"))
    assert cold.status("project", "snapshot") == metadata
    assert cold._database is None
    offline.identity = lambda: {"name": "fake", "dimensions": 3, "fingerprint": "different"}
    with pytest.raises(SemanticIndexError, match="Модель изменилась"):
        cold.search("project", "snapshot", "currency")
    assert offline.queries == []


def test_split_parts_retain_source_provenance_and_empty_corpus(tmp_path):
    provider = FakeEmbedding()
    provider.split_text = lambda text, max_tokens: [{"text": text[:5], "start": 0, "end": 5, "tokenCount": 2}, {"text": text[5:], "start": 5, "end": len(text), "tokenCount": 2}]
    database = IndexDatabase(tmp_path, provider)
    metadata = database.build("project", "snapshot", corpus("currency"), current)
    assert metadata["chunkCount"] == 2
    hits = database.search("project", "snapshot", "currency")["hits"]
    assert {hit["id"] for hit in hits} == {"chunk-0:part:1", "chunk-0:part:2"}
    assert all(hit["parentChunkId"] == "chunk-0" and hit["line"] == 10 and hit["endLine"] == 12 for hit in hits)
    empty = database.build("project", "empty", corpus(), current)
    assert empty["chunkCount"] == 0
    assert database.search("project", "empty", "currency")["hits"] == []


def test_status_and_published_queries_continue_during_embedding(tmp_path):
    """A paused writer must not block reads of the immutable published generation."""
    provider = FakeEmbedding()
    database = IndexDatabase(tmp_path, provider)
    original = database.build("project", "snapshot", corpus("currency"), current)
    entered, release = Event(), Event()
    original_embed = provider.embed

    def blocked_embed(texts):
        entered.set()
        assert release.wait(5), "Test failed to release writer"
        return original_embed(texts)

    provider.embed = blocked_embed
    with ThreadPoolExecutor(max_workers=3) as pool:
        writer = pool.submit(database.build, "project", "snapshot", corpus("asset valuation"), current)
        assert entered.wait(2)
        try:
            status = pool.submit(database.status, "project", "snapshot").result(timeout=1)
            assert status == original
            result = pool.submit(database.search, "project", "snapshot", "currency").result(timeout=1)
            assert result["index"] == original
            assert result["hits"][0]["text"] == "currency"
        finally:
            release.set()
        assert writer.result(timeout=3)["table"] != original["table"]


def test_vector_chunk_cap_and_capture_metadata_are_honest(tmp_path, monkeypatch):
    """The final index budget applies after tokenizer expansion, not only to source chunk count."""
    monkeypatch.setattr("atlas.semantic_index.MAX_VECTOR_CHUNKS", 3)
    provider = FakeEmbedding()
    provider.split_text = lambda text, max_tokens: [{"text": text[:5], "start": 0, "end": 5, "tokenCount": 2}, {"text": text[5:], "start": 5, "end": len(text), "tokenCount": 2}]
    database = IndexDatabase(tmp_path, provider)
    captured = corpus("currency", "valuation", "securities")
    captured["capturedAt"] = "2026-10-04T10:00:00Z"
    captured["warnings"] = ["x" * 300] * 20
    metadata = database.build("project", "snapshot", captured, current)
    assert metadata["sourceChunkCount"] == 3 and metadata["chunkCount"] == 3
    assert metadata["truncated"] is True
    assert metadata["limits"]["vectorChunkLimit"] == 3
    assert "лимит 3" in metadata["warnings"][0]
    assert len(metadata["warnings"]) <= 10 and all(len(warning) <= 200 for warning in metadata["warnings"])
    assert metadata["capturedAt"] == captured["capturedAt"]
    captured["snapshotId"] = "other"
    with pytest.raises(SemanticIndexError, match="Корпус не соответствует"):
        database.build("project", "snapshot", captured, current)
    assert database.status("project", "snapshot") == metadata


def documented_corpus(text='currency wiki', node_id='raw-method'):
    """Full captured text and scalar references use the same atomic generation as vectors."""
    captured = corpus(text, kind='documentation', file='docs/Wiki.md')
    document = {'id': 'document:wiki', 'text': text, 'title': 'Wiki', 'format': 'markdown',
                'kind': 'documentation', 'file': 'docs/Wiki.md', 'line': 1, 'endLine': 2,
                'sourceHash': 'hash', 'sourceKind': 'working-tree', 'capturedAt': 'captured',
                'nodeIds': [node_id], 'association': 'declaration', 'sourceLinks': [], 'truncated': False}
    captured['documents'] = [document]
    for chunk in captured['chunks']:
        chunk['documentId'] = document['id']
    return captured


def test_full_documents_persist_and_read_without_model_or_live_source(tmp_path):
    provider = FakeEmbedding()
    database = IndexDatabase(tmp_path, provider)
    captured = documented_corpus(node_id="raw'node")
    metadata = database.build('project', 'snapshot', captured, current)
    assert metadata['schemaVersion'] == 2 and metadata['documentsAvailable']
    database.close()
    reloaded = IndexDatabase(tmp_path, provider)
    provider.identity = lambda: (_ for _ in ()).throw(AssertionError('Document read loaded model'))
    pinned = reloaded.published_generation('project', 'snapshot')
    docs = reloaded.documents_for_nodes('project', 'snapshot', ["raw'node"], metadata=pinned)
    assert docs[0]['id'] == 'document:wiki'
    assert docs[0]['matches'][0]['scope'] == 'symbol'
    assert reloaded.document('project', 'snapshot', 'document:wiki', metadata=pinned)['text'] == 'currency wiki'
    with pytest.raises(SemanticIndexError) as unknown:
        reloaded.document('project', 'snapshot', 'missing')
    assert unknown.value.status == 404
    with pytest.raises(SemanticIndexError) as cross:
        reloaded.document('other-project', 'snapshot', 'document:wiki', metadata=pinned)
    assert cross.value.status == 409


def test_document_generations_are_atomic_and_legacy_requires_rebuild(tmp_path):
    provider = FakeEmbedding()
    database = IndexDatabase(tmp_path, provider)
    original = database.build('project', 'snapshot', documented_corpus(), current)
    pinned = database.published_generation('project', 'snapshot')
    changed = documented_corpus('asset wiki')
    changed['documents'][0]['id'] = 'document:changed'
    changed['chunks'][0]['documentId'] = 'document:changed'
    def stale():
        raise SemanticIndexError('snapshot changed', 409)
    with pytest.raises(SemanticIndexError):
        database.build('project', 'snapshot', changed, stale)
    assert database.status('project', 'snapshot') == original
    database.build('project', 'snapshot', changed, current)
    assert database.document('project', 'snapshot', 'document:wiki', metadata=pinned)['text'] == 'currency wiki'
    assert database.document('project', 'snapshot', 'document:changed')['text'] == 'asset wiki'
    database.build('project', 'legacy', corpus('currency'), current)
    with pytest.raises(SemanticIndexError, match='полные документы') as legacy:
        database.published_generation('project', 'legacy')
    assert legacy.value.status == 409
    assert database.search('project', 'legacy', 'currency')['hits']


def test_document_offsets_survive_token_split_and_reference_cap_is_explicit(tmp_path, monkeypatch):
    """Embedding parts point to exact captured character offsets; no silent linkage overflow."""
    provider = FakeEmbedding()
    provider.split_text = lambda text, max_tokens: [{'text': text[:5], 'start': 0, 'end': 5, 'tokenCount': 2},
                                                   {'text': text[5:], 'start': 5, 'end': len(text), 'tokenCount': 2}]
    captured = documented_corpus('currency')
    captured['chunks'][0].update(documentOffsetStart=10, documentOffsetEnd=18)
    captured['documents'][0]['sourceLinks'] = [{'nodeIds': ['second'], 'association': 'source-link', 'scope': 'file'}]
    monkeypatch.setattr('atlas.semantic_index.MAX_DOCUMENT_REFERENCES', 1)
    database = IndexDatabase(tmp_path, provider)
    metadata = database.build('project', 'snapshot', captured, current)
    assert metadata['truncated'] and any('references' in warning for warning in metadata['warnings'])
    hits = database.search('project', 'snapshot', 'currency')['hits']
    assert {(hit['documentOffsetStart'], hit['documentOffsetEnd']) for hit in hits} == {(10, 15), (15, 18)}
    with pytest.raises(SemanticIndexError) as injected:
        database.document('project', 'snapshot', "missing' OR 1=1 --")
    assert injected.value.status == 404


@pytest.mark.parametrize('damaged', [[], None, 'broken', {'projectId': 'project', 'snapshotId': 'snapshot'}])
def test_status_rejects_corrupt_manifest_without_loading_model_or_database(tmp_path, monkeypatch, damaged):
    """Syntactically valid but incomplete JSON returns a controlled repair error, not AttributeError."""
    import json
    database = IndexDatabase(tmp_path, FakeEmbedding())
    path = database._manifest_path('project', 'snapshot')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(damaged))

    def forbidden(*args):
        """Manifest checking must stay cheap even on a cold offline installation."""
        raise AssertionError('status loaded model/database')

    monkeypatch.setattr(database, '_db', forbidden)
    monkeypatch.setattr(database.embedder, 'identity', forbidden)
    with pytest.raises(SemanticIndexError, match='Пересоберите') as error:
        database.status('project', 'snapshot')
    assert error.value.status == 503


@pytest.mark.parametrize('damage', [
    {'chunkCount': -1}, {'chunkCount': True}, {'reusedCount': 100},
    {'model': []}, {'dimensions': 10}, {'modelFingerprint': 'other'},
    {'table': '../outside'}, {'schemaVersion': 3},
    {'schemaVersion': 2}, {'documentsAvailable': True},
    {'warnings': 42}, {'warnings': [None]}, {'truncated': 'false'},
])
def test_status_checks_generation_contract_before_search(tmp_path, damage):
    """A malformed count, identity or document-table reference cannot masquerade as a ready generation."""
    import json
    embedder = FakeEmbedding()
    database = IndexDatabase(tmp_path, embedder)
    metadata = database._metadata('project', 'snapshot', corpus(), 'hash', embedder.identity(),
                                  'generation_' + 'a' * 32, [], 0, False)
    metadata.update(damage)
    path = database._manifest_path('project', 'snapshot')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(metadata))
    with pytest.raises(SemanticIndexError, match='Пересоберите'):
        database.status('project', 'snapshot')
