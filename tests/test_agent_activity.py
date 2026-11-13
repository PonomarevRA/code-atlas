"""Поведенческие проверки циклического журнала, observer и secure ACK stream."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from atlas.agent_activity.api import register_activity_routes, cursor_sequence
from atlas.agent_activity.events import activity_event
from atlas.agent_activity.observer import ActivityObserver
from atlas.agent_activity.service import ActivityJournal
from atlas.agent_activity.settings import ActivitySettings
from atlas.local_http_boundary import LocalHTTPBoundary
from atlas.mcp_transport import LoopbackTransport


def event(query="hello", **changes):
    """Создаёт настоящее событие контракта с разрешённой ссылкой на synthetic project."""
    context = {"operationId": "operation-1", "operation": "search", "parentOperationId": None,
               "target": {"kind": "code", "projectId": "p1", "nodeId": "n1", "snapshotId": "s1", "status": "available"},
               "query": {"mode": "preview", "preview": query, "truncated": False}}
    return {**activity_event(context, "a" * 32, "unknown", "started"), **changes}


@pytest.fixture
def journal(tmp_path):
    """Изолирует диск и сокращает batch deadline без изменения durable semantics."""
    value = ActivityJournal(tmp_path, replace(ActivitySettings(), BatchMaxDelayMs=5))
    yield value
    value.close()


def client_for(journal):
    """Открывает реальную ASGI boundary с loopback peer и shared route implementation."""
    app = FastAPI()
    store = SimpleNamespace(lock=threading.RLock(), projects={"p1": {}})
    register_activity_routes(app, journal, store)
    app.add_middleware(ActivityObserver, journal=journal)
    app.add_middleware(LocalHTTPBoundary)

    @app.post("/api/projects/p1/search-nodes")
    async def search(body: dict):
        """Возвращает известный resultCount для наблюдаемого MCP запроса."""
        return {"nodes": [{"id": "n1"}], "snapshotId": "s1"}

    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50001))


def test_persist_restart_and_contract(journal, tmp_path):
    """Commit переживает restart, sequence продолжается и соответствует общему JSON schema."""
    assert journal.submit(event("Проверка 世界"))
    assert journal.flush()
    row = journal.disk.read()[0]
    assert row["sequence"] == "1" and row["recordedAt"]
    schema = json.loads(Path("contracts/agent-activity/v1/event.schema.json").read_text())
    try:
        import jsonschema
        jsonschema.validate(row, schema)
    except ImportError:
        assert set(schema["required"]) <= row.keys()
    epoch = row["journalEpoch"]
    journal.close()
    restarted = ActivityJournal(tmp_path, replace(ActivitySettings(), BatchMaxDelayMs=5))
    try:
        assert restarted.submit(event()) and restarted.flush()
        assert restarted.disk.epoch == epoch
        assert [row["sequence"] for row in restarted.disk.read()] == ["1", "2"]
    finally:
        restarted.close()


def test_global_quota_rotates_without_count_or_age_retention(tmp_path):
    """Заполнение небольшого бюджета удаляет целые старые segments и сохраняет общий предел."""
    settings = replace(ActivitySettings(), MaxSizeMb=1, BatchMaxDelayMs=1)
    value = ActivityJournal(tmp_path, settings)
    try:
        for group in range(16):
            for index in range(80):
                assert value.submit(event("Ж" * 500, occurredAt="2000-01-01T00:00:00+00:00"))
            assert value.flush()
            assert value.disk.used_bytes() <= 1_000_000
        status = value.status()
        assert int(status["oldestSequence"]) > 1
        assert status["latestSequence"] == "1280"
        assert value.disk.read()[0]["occurredAt"].startswith("2000-")
    finally:
        value.close()


def test_partial_tail_recovery_preserves_durable_cursor(journal, tmp_path):
    """Неполная последняя строка удаляется, durable event и epoch сохраняются."""
    journal.submit(event())
    assert journal.flush()
    epoch = journal.disk.epoch
    path = journal.disk.segments[-1]["path"]
    journal.close()
    with path.open("ab") as handle:
        handle.write(b'{"sequence": "2"')
    reopened = ActivityJournal(tmp_path)
    try:
        assert reopened.error is None
        assert reopened.disk.epoch == epoch
        assert reopened.status()["latestSequence"] == "1"
        assert path.read_bytes().endswith(b"\n")
    finally:
        reopened.close()


def test_bounded_no_wait_queue_reports_loss(tmp_path, monkeypatch):
    """Остановленный writer не блокирует producer; переполнение считается явно."""
    gate = threading.Event()
    original = ActivityJournal._run
    monkeypatch.setattr(ActivityJournal, "_run", lambda self: (gate.wait(2), original(self)))
    value = ActivityJournal(tmp_path, replace(ActivitySettings(), QueueMaxItems=2, BatchMaxDelayMs=1))
    try:
        assert value.submit(event()) and value.submit(event())
        assert not value.submit(event())
        assert len(value.queue) == 2
        assert value.dropped["full_queue"] == 1
    finally:
        gate.set()
        value.close()


def test_oversize_and_invalid_do_not_break_writer(journal):
    """Неподходящее событие не переводит рабочий диск в failed state."""
    assert not journal.submit(event("x" * 10000))
    assert not journal.submit(event(durationMs=float("nan")))
    assert journal.submit(event("\n\t\0" * 100))
    assert journal.flush()
    assert journal.error is None
    assert journal.dropped["invalid_event"] == 2


def test_duplicate_id_and_clear_epoch(journal):
    """Идемпотентность действует в retained window; clear сбрасывает epoch и курсоры."""
    row = event()
    assert journal.submit(row) and journal.submit(row)
    assert journal.flush()
    assert journal.status()["latestSequence"] == "1"
    old = journal.status()["cursor"]
    result = journal.clear()
    assert result["journalEpoch"] != old["journalEpoch"]
    assert result["latestSequence"] == "0" and journal.disk.read() == []
    with pytest.raises(ValueError):
        cursor_sequence(old, result["journalEpoch"], 1, 0)


def test_service_settings_and_invalid_config_are_explicit(tmp_path):
    """Квота читается из конфигурации сервиса; неверное значение не заменяется молча."""
    path = tmp_path / "agent-activity-settings.json"
    path.write_text(json.dumps({"AgentActivity": {"MaxSizeMb": 2}}))
    value = ActivityJournal(tmp_path)
    assert value.status()["maxSizeBytes"] == 2_000_000
    value.close()
    path.write_text(json.dumps({"AgentActivity": {"MaxSizeMb": 0}}))
    invalid = ActivityJournal(tmp_path)
    assert invalid.status()["health"]["status"] == "degraded"
    assert invalid.status()["maxSizeBytes"] is None
    assert not invalid.submit(event())
    invalid.close()


def test_history_filters_and_observer_capture_actual_mcp_only(journal):
    """Обычный UI не создаёт агентов; MCP сохраняет фактический query и пару phases."""
    class ReliableTestQueueLock:
        """Keep this observer test independent of the journal's intentional contention drops."""

        def __init__(self, lock):
            self.lock = lock

        def acquire(self, blocking=True):
            return self.lock.acquire()

        def release(self):
            self.lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_error):
            self.release()

    journal.queue_lock = ReliableTestQueueLock(journal.queue_lock)
    with client_for(journal) as client:
        assert client.post("/api/projects/p1/search-nodes", json={"query": "UI"}).status_code == 200
        assert journal.flush()
        assert journal.disk.read() == []
        response = client.post("/api/projects/p1/search-nodes", json={"query": "Пиф", "nodeId": "n1", "snapshotId": "s1"},
                               headers={"x-atlas-mcp-session": "a" * 32, "x-atlas-mcp-client": "cursor"})
        assert response.status_code == 200 and journal.flush()
        rows = client.get("/api/agent-activity/history?q=Пиф&projectId=p1").json()["events"]
        assert [row["phase"] for row in rows] == ["started", "finished"]
        assert rows[1]["resultCount"] == 1 and rows[1]["outcome"] == "succeeded"
        assert rows[0]["clientKind"] == "cursor" and rows[0]["agentId"] is None
        assert rows[0]["target"]["nodeId"] == "n1"
        assert client.get("/api/agent-activity/history?projectId=other").json()["events"] == []
        assert client.get("/api/agent-activity/history?limit=101").status_code == 422


def test_stream_snapshot_events_ack_and_clear_reset(journal):
    """Browser получает snapshot H, persisted хвост и явный reset после clear."""
    journal.submit(event())
    assert journal.flush()
    with client_for(journal) as client:
        with client.websocket_connect("ws://127.0.0.1/api/agent-activity/stream?projectId=p1", headers={"origin": "http://127.0.0.1"}) as socket:
            socket.send_json({"type": "subscribe"})
            snapshot = socket.receive_json()
            assert snapshot["type"] == "snapshot" and len(snapshot["events"]) == 1
            socket.send_json({"type": "ack", "messageId": snapshot["messageId"], "cursor": snapshot["cursor"]})
            journal.submit(event(phase="finished", outcome="succeeded"))
            assert journal.flush()
            update = socket.receive_json()
            assert update["type"] == "events" and update["events"][0]["sequence"] == "2"
            socket.send_json({"type": "ack", "messageId": update["messageId"], "cursor": update["cursor"]})
            assert client.post("/api/agent-activity/clear", json={}).status_code == 200
            assert socket.receive_json()["type"] == "resetRequired"


@pytest.mark.parametrize("origin", [None, "http://evil.test", "http://localhost"])
def test_stream_rejects_cross_origin_or_missing_origin_before_accept(journal, origin):
    """HTTP boundary alone не используется как доказательство защиты WebSocket upgrade."""
    with client_for(journal) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("ws://127.0.0.1/api/agent-activity/stream", headers={"origin": origin} if origin else {}):
                pass
        assert error.value.code == 1008


def test_stream_rejects_future_ack(journal):
    """Чужой ACK не освобождает окно; требуется новый согласованный snapshot."""
    with client_for(journal) as client:
        with client.websocket_connect("ws://127.0.0.1/api/agent-activity/stream", headers={"origin": "http://127.0.0.1"}) as socket:
            socket.send_json({"type": "subscribe"})
            frame = socket.receive_json()
            socket.send_json({"type": "ack", "messageId": "future", "cursor": frame["cursor"]})
            assert socket.receive_json()["reason"] == "invalid_ack"


def test_loopback_transport_supplies_real_process_session(monkeypatch):
    """Транспорт передаёт один ephemeral session на lifespan без дополнительных tool arguments."""
    received = []
    original = httpx.AsyncClient

    def handler(request):
        """Сохраняет transport headers без внешней сети."""
        received.append(request.headers)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: original(**options, transport=httpx.MockTransport(handler)))
    monkeypatch.setenv("ATLAS_MCP_CLIENT_KIND", "codex")

    async def exercise():
        """Проверяет сохранение session при нескольких вызовах одного MCP процесса."""
        transport = LoopbackTransport(8765)
        await transport.request("GET", "/api/health")
        await transport.request("GET", "/api/health")

    asyncio.run(exercise())
    assert received[0]["x-atlas-mcp-session"] == received[1]["x-atlas-mcp-session"]
    assert len(received[0]["x-atlas-mcp-session"]) == 32
    assert received[0]["x-atlas-mcp-client"] == "codex"


def test_history_and_stream_response_byte_bounds(journal):
    """Длинный Unicode payload ограничивает фактический UTF-8 размер всей страницы и WS frame."""
    for group in range(10):
        for index in range(10):
            row = event("😀" * 512)
            row["target"]["label"] = "😀" * 512
            assert journal.submit(row)
        assert journal.flush()
    with client_for(journal) as client:
        response = client.get("/api/agent-activity/history")
        assert len(response.content) <= 262144
        assert response.json()["hasMore"]
        with client.websocket_connect("ws://127.0.0.1/api/agent-activity/stream", headers={"origin": "http://127.0.0.1"}) as socket:
            socket.send_json({"type": "subscribe"})
            raw = socket.receive_text()
            frame = json.loads(raw)
            assert len(raw.encode()) <= 65536
            assert frame["omittedCount"] > 0
            socket.send_json({"type": "ack", "messageId": frame["messageId"], "cursor": frame["cursor"]})


def test_stream_resumes_from_durable_cursor(journal):
    """Reconnect подтверждает выбранный cursor и возвращает только события после него."""
    journal.submit(event())
    assert journal.flush()
    cursor = journal.status()["cursor"]
    journal.submit(event())
    assert journal.flush()
    with client_for(journal) as client:
        with client.websocket_connect("ws://127.0.0.1/api/agent-activity/stream", headers={"origin": "http://127.0.0.1"}) as socket:
            socket.send_json({"type": "subscribe", "cursor": cursor})
            resumed = socket.receive_json()
            assert resumed["cursor"] == cursor and resumed["events"] == []
            socket.send_json({"type": "ack", "messageId": resumed["messageId"], "cursor": resumed["cursor"]})
            tail = socket.receive_json()
            assert [row["sequence"] for row in tail["events"]] == ["2"]
            socket.send_json({"type": "ack", "messageId": tail["messageId"], "cursor": tail["cursor"]})


def test_stream_timeout_does_not_block_writer(journal):
    """Медленный browser отключается по ACK deadline, producer и durable commit продолжаются."""
    with client_for(journal) as client:
        with client.websocket_connect("ws://127.0.0.1/api/agent-activity/stream", headers={"origin": "http://127.0.0.1"}) as socket:
            socket.send_json({"type": "subscribe"})
            assert socket.receive_json()["type"] == "snapshot"
            assert journal.submit(event()) and journal.flush()
            assert socket.receive_json()["type"] == "resetRequired"
            assert journal.status()["latestSequence"] == "1"


def test_escaped_surrogate_and_observer_failure_preserve_operation(journal, monkeypatch):
    """Невалидный Unicode в запросе и сбой logging adapter не ломают основную операцию."""
    with client_for(journal) as client:
        response = client.post("/api/projects/p1/search-nodes", content=b'{"query":"\\ud800"}',
                               headers={"content-type": "application/json", "x-atlas-mcp-session": "a" * 32})
        assert response.status_code == 200 and journal.flush()
        assert journal.error is None

        def broken_submit(value):
            """Имитирует неожиданный сбой независимого observer sink."""
            raise OSError("synthetic disk failure")

        monkeypatch.setattr(journal, "submit", broken_submit)
        assert client.post("/api/projects/p1/search-nodes", json={"query": "valid"},
                           headers={"x-atlas-mcp-session": "a" * 32}).status_code == 200


def test_storage_failure_is_degraded_bounded_and_preserves_existing_data(journal, monkeypatch):
    """Disk full не вызывает синхронный fallback и дальнейший рост файла."""
    assert journal.submit(event()) and journal.flush()
    original_bytes = journal.disk.used_bytes()

    def denied(events):
        """Имитирует disk full после уже сохранённого первого события."""
        raise OSError("synthetic full disk")

    monkeypatch.setattr(journal.disk, "append", denied)
    assert journal.submit(event())
    assert not journal.flush()
    assert journal.status()["health"]["status"] == "degraded"
    assert journal.disk.used_bytes() == original_bytes
    assert not journal.submit(event())
    assert len(journal.disk.read()) == 1


def test_request_and_actual_response_summaries_are_paired(journal):
    """История показывает запрос агента и фактические имена полученных узлов, без source payload."""
    with client_for(journal) as client:
        response = client.post("/api/projects/p1/search-nodes", json={"query": "Calculate"},
                               headers={"x-atlas-mcp-session": "a" * 32})
        assert response.status_code == 200 and journal.flush()
        rows = client.get("/api/agent-activity/history").json()["events"]
        assert rows[0]["operationId"] == rows[1]["operationId"]
        assert rows[0]["request"] == {"tool": "search_nodes", "summary": "search_nodes: Calculate"}
        assert rows[1]["response"]["summary"] == "Получено результатов: 1"
        assert rows[1]["response"]["items"] == [{"label": "n1", "nodeId": "n1"}]
        assert rows[1]["response"]["resultCount"] == 1


def test_large_response_summary_keeps_only_actual_bounded_results():
    """Большой ответ отдаёт три полностью прочитанных результата и явный truncated без выдуманного total."""
    from atlas.agent_activity.summaries import response_summary
    raw = json.dumps({"nodes": [{"id": f"n{i}", "label": f"Method{i}", "file": "calc.py"} for i in range(1000)]}).encode()
    summary = response_summary(raw[:32768], len(raw), True, 200, None)
    assert [row["label"] for row in summary["items"]] == ["Method0", "Method1", "Method2"]
    assert summary["truncated"] and "resultCount" not in summary
    assert "байт" in summary["summary"]


def test_checksum_corruption_changes_epoch_and_signals_reset(journal, tmp_path):
    """Валидный JSON с изменёнными байтами не выдаётся за непрерывный retained журнал."""
    journal.submit(event("original"))
    assert journal.flush()
    old_epoch = journal.disk.epoch
    path = journal.disk.segments[-1]["path"]
    journal.close()
    path.write_bytes(path.read_bytes().replace(b"original", b"modified"))
    recovered = ActivityJournal(tmp_path)
    try:
        assert recovered.error is None
        assert recovered.disk.epoch != old_epoch
        assert recovered.status()["health"]["recoveryReset"]
        assert recovered.disk.read() == []
    finally:
        recovered.close()


def test_description_response_previews_only_known_semantic_fields():
    """Краткое описание показывает фактическое пояснение, но не копирует code/text/source bodies."""
    from atlas.agent_activity.summaries import response_summary
    raw = json.dumps({"label": "Calculate()", "factualSummary": "Рассчитывает доход по договору.", "code": "secret source body"}).encode()
    summary = response_summary(raw, len(raw), False, 200, None)
    assert "Рассчитывает доход" in summary["summary"]
    assert "secret source body" not in json.dumps(summary)


def test_long_node_reference_is_omitted_instead_of_truncated():
    """Journal не конструирует несуществующий raw ID усечением реальной ссылки."""
    from atlas.agent_activity.events import operation_context
    context = operation_context({"path": "/api/projects/p1/graph-node", "query_string": b""}, {"nodeId": "n" * 600})
    assert context["target"]["nodeId"] is None
    assert context["target"]["status"] == "unavailable"


def test_failed_file_sync_never_publishes_uncommitted_tail(journal, monkeypatch):
    """Записанные bytes без успешного fsync не становятся историей или snapshot."""
    from atlas.agent_activity import disk
    assert journal.submit(event()) and journal.flush()

    def failed_sync(descriptor):
        """Имитирует ошибку flush на новой пачке после принятого persisted cursor."""
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(disk.os, "fsync", failed_sync)
    assert journal.submit(event())
    assert not journal.flush()
    assert journal.status()["latestSequence"] == "1"
    assert [row["sequence"] for row in journal.disk.read()] == ["1"]


def test_recovery_detects_internal_sequence_gap_but_allows_evicted_prefix(journal, tmp_path):
    """Исчезнувший middle segment меняет epoch, обычное вытеснение prefix остаётся допустимым."""
    journal.disk.segment_limit = 1
    for _ in range(6):
        journal.submit(event())
        assert journal.flush()
    epoch = journal.disk.epoch
    first = journal.disk.segments[0]["path"]
    middle = journal.disk.segments[2]["path"]
    journal.close()
    first.unlink()
    prefix_recovery = ActivityJournal(tmp_path)
    assert prefix_recovery.disk.epoch == epoch
    assert prefix_recovery.disk.read()[0]["sequence"] == "2"
    prefix_recovery.close()
    middle.unlink()
    gap_recovery = ActivityJournal(tmp_path)
    try:
        assert gap_recovery.error is None
        assert gap_recovery.disk.epoch != epoch
        assert gap_recovery.status()["health"]["recoveryReset"]
        assert gap_recovery.disk.read() == []
    finally:
        gap_recovery.close()


def test_storage_error_discards_accepted_pending_batches(tmp_path, monkeypatch):
    """После I/O ошибки writer не повторяет append оставшихся принятых событий."""
    value = ActivityJournal(tmp_path, replace(ActivitySettings(), BatchMaxBytes=8192, BatchMaxDelayMs=5))
    entered, release = threading.Event(), threading.Event()
    attempts = []

    def failing_append(events):
        """Удерживает первый append, чтобы другие события успели попасть в pending queue."""
        attempts.append(len(events))
        entered.set()
        release.wait(2)
        raise OSError("synthetic persistent failure")

    monkeypatch.setattr(value.disk, "append", failing_append)
    try:
        assert value.submit(event())
        assert entered.wait(1)
        for _ in range(20):
            assert value.submit(event())
        release.set()
        assert not value.flush()
        assert len(attempts) == 1
        assert value.status()["health"]["queueItems"] == 0
        assert value.dropped["storage_unavailable"] == 21
    finally:
        release.set()
        value.close()
