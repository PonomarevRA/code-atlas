"""Добавочный обзор статических точек входа без изменения снимка Graphify."""

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

from .entrypoint_parsers import controller_routes, line_at, parse_http, parse_messaging, prepare_source
from .entrypoint_sources import read_sources
from .project_scopes import assign_code_projects
from .captured_entrypoints import captured_entries


MAX_FILES = 2500
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_SCAN_SECONDS = 15


def _topics(sources: list[dict]) -> dict:
    """Из JSON извлекает только TopicName в известной секции, без других настроек."""
    topics = defaultdict(list)
    for source in sources:
        if not source["file"].endswith(".json"):
            continue
        try:
            document = json.loads(source["text"])
        except (ValueError, RecursionError):
            continue
        if not isinstance(document, dict):
            continue
        settings = document.get("KafkaConsumersSettings", {})
        if not isinstance(settings, dict):
            continue
        for name, values in settings.items():
            if not isinstance(values, dict):
                continue
            topic = values.get("TopicName")
            if not isinstance(topic, str) or not re.fullmatch(r'[\w.\-]{1,249}', topic):
                continue
            section = re.search(r'"' + re.escape(name) + r'"\s*:', source["text"])
            position = section.start() if section else 0
            value = re.search(r'"TopicName"\s*:\s*"' + re.escape(topic) + r'"', source["text"][position:])
            if value:
                position += value.start()
            topics[(source["codeProjectId"], name)].append({
                "topic": topic, "file": source["file"], "line": line_at(source["text"], position),
            })
    return topics


def _index_handlers(nodes: list[dict]) -> dict:
    """Индексирует имена в проекте один раз; неоднозначные совпадения не теряются."""
    index = defaultdict(list)
    for node in nodes:
        if node.get("kind") != "external":
            index[(node.get("codeProjectId"), node.get("label"))].append(node)
    return dict(index)


def _handler_ids(item: dict, source: dict, handler_index: dict) -> list[str]:
    """Привязывает только совпавшее имя обработчика в нужном проекте или файле."""
    handler = item.get("handler")
    if not handler:
        return []
    candidates = source["nodes"] if item["kind"] == "http" else handler_index.get((source["codeProjectId"], handler), [])
    matches = []
    labels = {handler}
    if item["kind"] == "http":
        labels.update({f".{handler}()", f"{handler}()", f"{item['controller']}.{handler}()"})
    for node in candidates:
        if node.get("codeProjectId") != source["codeProjectId"] or node.get("kind") == "external":
            continue
        if node.get("label") in labels:
            matches.append(node)
    if len(matches) > 1:
        # При перегрузках и одинаковых именах строка снимка может устареть.
        # Не назначаем несколько обработчиков без доказанного соответствия.
        return []
    return [matches[0]["id"]] if matches else []



def _entry(item: dict, source: dict, handler_index: dict, topic: dict | None = None) -> dict:
    """Создаёт безопасную карточку: только разрешённые значения и ссылки на строки."""
    kind = item["kind"]
    handlers = _handler_ids(item, source, handler_index)
    unresolved = []
    evidence = [{"file": source["file"], "line": item["line"]}]
    route, queue = item.get("route"), item.get("queue")
    topic_name = topic["topic"] if topic else None
    if topic:
        evidence.append({"file": topic["file"], "line": topic["line"]})
    if not handlers:
        unresolved.append("Обработчик отсутствует в снимке или совпадение имени неоднозначно.")
    if kind == "http":
        origin = "Внешний HTTP-клиент; конкретный вызывающий сервис не установлен."
        if route is None:
            unresolved.append("Маршрут вычисляется или отсутствует буквальный префикс контроллера.")
        label = f"{item['httpMethod']} {route or item['handler']}"
    else:
        origin = "Kafka topic; сервис-производитель не установлен." if kind == "kafka" else "RabbitMQ queue; сервис-производитель не установлен."
        address = topic_name if kind == "kafka" else queue
        if address is None:
            unresolved.append("Имя топика/очереди не найдено как разрешённое буквальное значение.")
        unresolved.append("Производитель сообщения не определён по регистрации потребителя.")
        label = f"{kind.upper()} {address or item.get('settingsKey') or item.get('handler') or 'очередь'}"
    identity = [kind, source["file"], item.get("controller"), item.get("handler"), item.get("httpMethod"),
                item.get("settingsKey"), route, queue, topic["file"] if topic else None]
    identifier = "entry-" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:20]
    return {
        "id": identifier, "kind": kind, "label": label, "origin": origin,
        "handler": item.get("handler"), "settingsKey": item.get("settingsKey"),
        "route": route, "topic": topic_name, "queue": queue, "httpMethod": item.get("httpMethod"),
        "handlerNodeIds": handlers, "codeProjectId": source["codeProjectId"],
        "evidence": evidence, "confidence": "unresolved" if unresolved else "static",
        "unresolved": unresolved, "configFile": topic["file"] if topic else None,
    }


def build_entrypoints(project_path: Path, graph: dict, snapshot_id: str) -> dict:
    """Строит независимый слой из текущих файлов, не изменяя факты и ID Graphify.

    Это ограниченный статический анализ, а не трассировка или конфигурация
    запущенного приложения. Варианты appsettings представлены отдельно.
    """
    nodes = [dict(node) for node in graph.get("nodes", [])]
    projects = assign_code_projects(nodes)
    sources, coverage = read_sources(
        project_path, nodes, projects, snapshot_id, max_files=MAX_FILES,
        max_total_bytes=MAX_TOTAL_BYTES, max_scan_seconds=MAX_SCAN_SECONDS,
    )
    handler_index = _index_handlers(nodes)
    shared_routes = defaultdict(dict)
    code_sources = []
    markers = ("Route", "Http", "AddPortfolioBatchConsumer", "ReceiveEndpoint", "BasicConsume")
    for source in sources:
        if not source["file"].endswith(".cs") or not any(marker in source["text"] for marker in markers):
            continue
        source["syntax"] = prepare_source(source["text"])
        code_sources.append(source)
        shared_routes[source["codeProjectId"]].update(controller_routes(source["syntax"]))
    topics = _topics(sources)
    entries = {}
    for source in code_sources:
        items = parse_http(source["syntax"], shared_routes[source["codeProjectId"]])
        items.extend(parse_messaging(source["syntax"]))
        for item in items:
            variants = topics.get((source["codeProjectId"], item.get("settingsKey")), []) if item["kind"] == "kafka" else []
            for variant in variants or [None]:
                entry = _entry(item, source, handler_index, variant)
                entries[entry["id"]] = entry
    from .infrastructure.index import infrastructure_index
    from .infrastructure.entrypoints import registered_entries
    infrastructure = infrastructure_index(project_path, graph, snapshot_id)
    kafka_publish_sites = sum(
        fact["kind"] == "kafka" and fact["operation"] == "publishes"
        for facts in infrastructure.facts.values() for fact in facts
    )
    for entry in registered_entries(infrastructure, nodes):
        entries[entry['id']] = entry
    captured_tests = graph.get('metadata', {}).get('testDiscovery', {})
    for entry in captured_entries(graph):
        entries[entry['id']] = entry
    return {
        "snapshotId": snapshot_id, "sourceKind": "mixed" if captured_tests.get('entries') else "working-tree",
        "entries": sorted(entries.values(), key=lambda entry: (entry["kind"], entry["label"], entry["id"])),
        "warning": "Тесты — объявления сохранённого снимка; API и сообщения — статический поиск в текущих файлах. Счётчик проверенных файлов относится к текущему поиску API и сообщений. Условия компиляции, окружение и выполнение не вычисляются. Отсутствие найденной точки не доказывает её отсутствие.",
        "coverage": coverage,
        "infrastructureCoverage": infrastructure.coverage,
        "kafkaPublishSites": kafka_publish_sites,
        "testDiscovery": {key: value for key, value in captured_tests.items() if key != 'entries'},
        "analysisMods": graph.get('metadata', {}).get('analysisMods', {}),
    }
