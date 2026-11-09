"""Локальное извлечение Graphify и преобразование его графа для интерфейса."""

import json
import os
from pathlib import Path
import re
import subprocess

from ...runtime_paths import worker_creation_flags
from .runtime import command as provider_command, current_info


class GraphifyError(RuntimeError):
    """Ошибка извлечения или некорректный результат Graphify."""


def _offline_environment() -> dict[str, str]:
    """Убирает настройки внешних провайдеров и переопределения каталога вывода."""
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith("GRAPHIFY_") or key.endswith("_API_KEY"):
            environment.pop(key)
    environment["PYTHONHASHSEED"] = "0"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
    return environment


def _line_number(location: object) -> int | None:
    """Извлекает первую строку из координат Graphify вида L42 или L42-L51."""
    match = re.search(r"\d+", str(location or ""))
    return int(match.group()) if match else None


def normalize_graph(raw: dict, graph_path: Path) -> dict:
    """Нормализует raw edges и NetworkX links, сохраняя исходные атрибуты узлов."""
    raw_nodes = raw.get("nodes")
    raw_edges = raw.get("edges", raw.get("links", []))
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise GraphifyError("Graphify вернул неверный формат nodes/edges.")
    nodes = []
    identifiers = set()
    for item in raw_nodes:
        if not isinstance(item, dict) or item.get("id") is None:
            raise GraphifyError("В графе обнаружен узел без идентификатора.")
        node = dict(item)
        node_id = str(item["id"])
        if node_id in identifiers:
            continue
        identifiers.add(node_id)
        # Graphify emits documentation as rationale, not an executable declaration.
        fallback_kind = "note" if item.get("file_type") == "rationale" else "symbol"
        node.update({
            "id": node_id,
            "label": str(item.get("label") or node_id),
            "kind": str(item.get("kind") or item.get("type") or fallback_kind),
            "file": str(item.get("source_file") or item.get("file") or ""),
            "line": _line_number(item.get("source_location") or item.get("line")),
            "community": item.get("community"),
            "description": str(item.get("description") or ""),
        })
        nodes.append(node)
    edges = []
    implicit_nodes = 0
    for item in raw_edges:
        if not isinstance(item, dict):
            raise GraphifyError("В графе обнаружена некорректная связь.")
        if item.get("source") is None or item.get("target") is None:
            raise GraphifyError("В графе обнаружена связь без source или target.")
        source = str(item["source"])
        target = str(item["target"])
        if not source or not target:
            raise GraphifyError("В графе обнаружена связь с пустым идентификатором.")
        # NetworkX внутри Graphify MCP создаёт узлы для внешних ссылок сам.
        # Повторяем эту семантику, чтобы интерфейс и MCP показывали один граф.
        for endpoint in (source, target):
            if endpoint in identifiers:
                continue
            identifiers.add(endpoint)
            nodes.append({
                "id": endpoint,
                "label": endpoint,
                "kind": "external",
                "file": "",
                "line": None,
                "community": None,
                "description": "Узел упомянут в связях; определение отсутствует в извлечённых исходниках.",
            })
            implicit_nodes += 1
        edge = dict(item)
        edge.update({
            "source": source,
            "target": target,
            "relation": str(item.get("relation") or "related"),
            "confidence": str(item.get("confidence") or "UNKNOWN"),
        })
        edges.append(edge)
    return {
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "omittedEdges": 0,
            "implicitNodes": implicit_nodes,
            "rawGraphPath": str(graph_path.resolve()),
            "provider": "Graphify",
            "mode": "code-only",
        },
    }


def build_graph(project_path: Path, output_parent: Path, *, chunked: bool = True,
                chunk_max_files: int = 32, chunk_target_bytes: int = 2 * 1024 * 1024) -> dict:
    """
    Строит граф за 600 секунд; кэш и результат пишет только в output_parent.

    Исходный проект не изменяется. Семантическая обработка и генерация через
    LLM отключены явным --code-only. Все грамматики должны быть установлены.
    По умолчанию целые файлы извлекаются ограниченными пакетами, после чего
    Graphify выполняет штатное глобальное разрешение по своему кэшу. Параметры
    chunk_max_files и chunk_target_bytes ограничивают пакет, а не общий RSS.
    chunked=False оставляет прежний монолитный путь для проверки эквивалентности.

    Args:
        project_path: Корень исходников; не изменяется.
        output_parent: Отдельный каталог результата и временного кэша.
        chunked: Использовать общий standalone pipeline с явными ошибками отдельных файлов.
        chunk_max_files: Максимальное число целых файлов в обычном пакете.
        chunk_target_bytes: Целевой размер пакета, не ограничение общего RSS.
    Returns:
        Нормализованный граф с отдельными diagnostic nodes и coverage при частичном анализе.
    Raises:
        GraphifyError: Runtime, глобальная целостность или формат результата недействителен.
    """
    project_path = project_path.expanduser().resolve()
    output_parent = output_parent.expanduser().resolve()
    if not project_path.is_dir():
        raise GraphifyError("Укажите существующий каталог проекта.")
    if output_parent == project_path or project_path in output_parent.parents:
        raise GraphifyError("Каталог данных должен находиться вне исходного проекта.")
    output_parent.mkdir(parents=True, exist_ok=True)
    integration = current_info()
    if not integration['available']:
        raise GraphifyError('Graphify provider unavailable; select an installed independent runtime')
    if type(chunked) is not bool:
        raise GraphifyError('chunked должен быть логическим значением.')
    if type(chunk_max_files) is not int or not 1 <= chunk_max_files <= 1024:
        raise GraphifyError('chunk_max_files должен быть от 1 до 1024.')
    if type(chunk_target_bytes) is not int or not 1 <= chunk_target_bytes <= 64 * 1024 * 1024:
        raise GraphifyError('chunk_target_bytes должен быть от 1 до 67108864.')
    if chunked:
        command = provider_command('graphify-chunks', [str(project_path), str(output_parent),
                                   '--max-files', str(chunk_max_files),
                                   '--target-bytes', str(chunk_target_bytes)])
    else:
        command = provider_command('graphify', ['extract', str(project_path), '--code-only', '--no-cluster',
                                               '--out', str(output_parent), '--max-workers', '1'])
    try:
        result = subprocess.run(
            command, cwd=output_parent, env=_offline_environment(),
            capture_output=True, text=True, timeout=600, check=False,
            creationflags=worker_creation_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise GraphifyError("Извлечение Graphify превысило лимит 600 секунд.") from exc
    except OSError as exc:
        raise GraphifyError(f"Не удалось запустить Graphify: {exc}") from exc
    if result.returncode:
        details = (result.stderr or result.stdout).strip()[-4000:]
        raise GraphifyError(f"Graphify завершился с кодом {result.returncode}: {details}")
    graph_path = output_parent / "graphify-out" / "graph.json"
    try:
        raw = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GraphifyError(f"Не удалось прочитать результат Graphify: {exc}") from exc
    if not isinstance(raw, dict):
        raise GraphifyError("Корень графа Graphify должен быть объектом JSON.")
    graph = normalize_graph(raw, graph_path)
    if chunked:
        from .chunking import read_chunk_report
        try:
            graph['metadata']['chunkAnalysis'] = read_chunk_report(output_parent)
            diagnostics = graph['metadata']['chunkAnalysis'].get('analysisDiagnostics')
            if diagnostics is not None:
                from ...analysis_diagnostics import attach_diagnostics
                attach_diagnostics(graph, diagnostics)
        except (OSError, ValueError, TypeError) as error:
            raise GraphifyError('Не удалось проверить результат чанкового анализа Graphify.') from error
    graph["metadata"]["buildLog"] = (result.stdout + result.stderr)[-12000:]
    graph["metadata"]["integration"] = integration
    return graph
