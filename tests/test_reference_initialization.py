"""Первый запуск готовит общий индекс без действий на странице и сохраняет готовые поколения."""
from threading import Event
import time
import zipfile

from fastapi.testclient import TestClient
import pytest

from atlas.reference_documents import ReferenceError
from atlas.reference_service import ReferenceService
from atlas.reference_store import import_archive
from atlas.server import create_app


class LocalModel:
    """Детерминированные векторы проверяют настоящий LanceDB без загрузки большой модели."""

    def available(self): return True
    def identity(self): return {"dimensions": 2, "fingerprint": "startup-local"}
    def split_text(self, text): return [{"text": text[:500]}]
    def embed(self, texts): return [[1., 0.] for _ in texts]
    def query(self, text): return [1., 0.]


def corpus(tmp_path, directory, edition):
    """Устанавливает одну локальную редакцию с настоящими FTS и provenance."""
    path = tmp_path / f"{edition}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("guide.md", "# Sequence\nCreates an array from the sequence.")
    metadata = {"language": "python", "version": "3.13", "edition": edition,
                "title": edition, "locale": "en", "sourceUrl": "https://example.org/python",
                "license": "Test fixture"}
    return import_archive(path, metadata, directory)


def finished(service, job):
    """Ожидает короткую фоновую задачу; лимит выявляет зависший writer."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        current = service.job(job["id"])
        if current["status"] != "running":
            return current
        time.sleep(.01)
    pytest.fail("Initial reference indexing did not finish")


def test_first_start_builds_missing_and_restart_reuses_local_and_bundled(tmp_path):
    """Поиск получает векторы автоматически; перезапуск не меняет ни одно поколение."""
    bundled = tmp_path / "bundled"
    source = corpus(tmp_path, bundled, "bundled")
    state = tmp_path / "state"
    service = ReferenceService(state, LocalModel(), bundled)
    try:
        added = corpus(tmp_path, service.store.directory, "local")
        job = service.start_initial_indexing()
        assert service.start_initial_indexing()["id"] == job["id"]
        completed = finished(service, job)
        assert completed["status"] == "completed", completed
        assert completed["result"] == {"indexedPacks": 2, "skippedPacks": 0, "failedPacks": []}
        assert service.catalog()["initialIndexing"]["status"] == "completed"
        result = service.search("unmatched paraphrase", mode="semantic", pack_id=source["id"])
        assert result["results"][0]["match"] == "semantic"
        previous = {pack["id"]: service.vectors.status(pack["id"]) for pack in service.store.catalog()}
    finally:
        service.close()
    # Симулируем индекс, включённый в пакет приложения: его нельзя переписывать.
    bundled_vectors = state / "reference-search"
    restarted = ReferenceService(tmp_path / "second-state", LocalModel(), bundled, bundled_vectors)
    try:
        completed = finished(restarted, restarted.start_initial_indexing())
        assert completed["result"]["indexedPacks"] == 0
        assert completed["result"]["skippedPacks"] == 1
        assert restarted.vectors.status(source["id"]) == previous[source["id"]]
        assert not restarted.vectors.directory.exists()
    finally:
        restarted.close()
    local_restart = ReferenceService(state, LocalModel(), bundled)
    try:
        completed = finished(local_restart, local_restart.start_initial_indexing())
        assert completed["result"]["skippedPacks"] == 2
        assert local_restart.vectors.status(added["id"]) == previous[added["id"]]
    finally:
        local_restart.close()


def test_startup_without_model_keeps_text_lookup_available(tmp_path):
    """Отсутствие модели не ломает запуск и не вызывает загрузку модели/сети."""
    model = LocalModel()
    model.available = lambda: False
    def forbidden(*args, **kwargs):
        raise AssertionError("Model must not be loaded")
    model.embed = model.identity = model.query = forbidden
    service = ReferenceService(tmp_path, model)
    try:
        source = corpus(tmp_path, service.store.directory, "local")
        assert service.start_initial_indexing()["status"] == "model-unavailable"
        assert service.catalog()["initialIndexing"]["status"] == "model-unavailable"
        assert service.search("Sequence", mode="fulltext", pack_id=source["id"])["results"]
        assert not service.jobs and not service.vectors.directory.exists()
    finally:
        service.close()


def test_one_failure_does_not_block_other_corpora_and_restart_resumes(tmp_path):
    """После сбоя повторяется только отсутствующий индекс, успешный остаётся прежним."""
    service = ReferenceService(tmp_path, LocalModel())
    try:
        bad = corpus(tmp_path, service.store.directory, "bad")
        good = corpus(tmp_path, service.store.directory, "good")
        build = service.vectors.build
        def fail_one(pack, progress, cancelled):
            if pack["id"] == bad["id"]:
                raise ReferenceError("Fixture index failure")
            return build(pack, progress, cancelled)
        service.vectors.build = fail_one
        job = finished(service, service.start_initial_indexing())
        assert job["status"] == "failed"
        assert job["result"]["failedPacks"][0]["packId"] == bad["id"]
        assert service.catalog()["initialIndexing"]["result"]["failedPackCount"] == 1
        assert "failedPacks" not in service.catalog()["initialIndexing"]["result"]
        assert service.vectors.status(bad["id"]) is None
        previous = service.vectors.status(good["id"])
        assert previous
        assert service.search("Sequence", mode="fulltext", pack_id=bad["id"])["results"]
    finally:
        service.close()
    restarted = ReferenceService(tmp_path, LocalModel())
    try:
        job = finished(restarted, restarted.start_initial_indexing())
        assert job["status"] == "completed"
        assert job["result"]["indexedPacks"] == job["result"]["skippedPacks"] == 1
        assert restarted.vectors.status(good["id"]) == previous
    finally:
        restarted.close()


def test_initial_writer_is_exclusive_and_shutdown_stops_queue(tmp_path):
    """Подготовка не допускает второго writer; shutdown прекращает оставшиеся корпуса."""
    service = ReferenceService(tmp_path, LocalModel())
    entered, release = Event(), Event()
    for edition in ("first", "second"):
        corpus(tmp_path, service.store.directory, edition)
    calls = []
    def blocked_build(pack, progress, cancelled):
        calls.append(pack["id"])
        entered.set()
        assert release.wait(5)
        assert cancelled()
        raise ReferenceError("Index cancelled")
    service.vectors.build = blocked_build
    try:
        job = service.start_initial_indexing()
        assert entered.wait(5)
        with pytest.raises(ReferenceError, match="already running"):
            service.submit("index", pack_id=calls[0])
        with service.lock:
            service.closed = True
        release.set()
        assert finished(service, job)["status"] == "failed"
        assert len(calls) == 1
    finally:
        release.set()
        service.close()


def test_server_lifespan_starts_index_before_any_page_or_search(tmp_path):
    """Настоящий lifecycle вызывает подготовку; создание app само по себе не пишет индекс."""
    app = create_app(tmp_path / "state")
    service = app.state.references
    service.store.bundled_dir = None
    service.vectors.bundled_dir = None
    service.embedder = service.vectors.embedder = LocalModel()
    source = corpus(tmp_path, service.store.directory, "local")
    assert service.initial_indexing is None and not service.jobs
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        assert service.initial_indexing["operation"] == "index-missing"
        assert finished(service, service.initial_indexing)["status"] == "completed"
        catalog = client.get("/api/language-references").json()
        assert catalog["packs"][0]["vectorIndex"]
        assert catalog["initialIndexing"]["status"] == "completed"
        result = client.post("/api/language-references/search", json={
            "query": "paraphrase with no lexical match", "mode": "semantic", "packId": source["id"]})
        assert result.status_code == 200
        assert result.json()["results"][0]["match"] == "semantic"
