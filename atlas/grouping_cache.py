"""Производный файловый кэш группировки: исходный Graphify-снимок не изменяется."""

import json
from pathlib import Path

from .grouping import GROUPING_VERSION
from .health_snapshot_overlay import overlay_stamp
from .snapshot_objects import snapshot_source_stamp


def cache_path(source: Path, algorithm: str) -> Path:
    """Возвращает соседний файл производной конкретного провайдера и версии алгоритма."""
    return source.with_name(f"atlas-grouped-{GROUPING_VERSION}-{algorithm}.json")


def source_stamp(source: Path) -> list[int]:
    """Привязывает производную к JSON или manifest и версиям всех его объектов.

    Для legacy JSON сохраняется прежняя пара mtime/size; размер manifest-снимка
    отражает логический граф, чтобы маленький индекс не обходил лимит LRU.
    """
    return snapshot_source_stamp(source)


def read_cached_graph(source: Path, algorithm: str) -> dict | None:
    """Читает только готовую производную; отсутствие/повреждение означает cache miss."""
    try:
        expected_stamp = source_stamp(source)
        cached = json.loads(cache_path(source, algorithm).read_text(encoding="utf-8"))
        if (not isinstance(cached, dict) or cached.get("sourceStamp") != expected_stamp
                or cached.get('overlayStamp') != list(overlay_stamp(source))):
            return None
        graph = cached.get("graph")
        if not isinstance(graph, dict):
            return None
        metadata = graph.get("metadata")
        if not isinstance(metadata, dict):
            return None
        if metadata.get("groupingVersion") != GROUPING_VERSION or metadata.get("groupingProvider") != algorithm:
            return None
        for key in ("codeProjects", "communities", "nodes", "edges"):
            if not isinstance(graph.get(key), list):
                return None
        for node in graph["nodes"]:
            if not isinstance(node, dict) or not isinstance(node.get("id"), str):
                return None
        for edge in graph["edges"]:
            if not isinstance(edge, dict) or not isinstance(edge.get("source"), str) or not isinstance(edge.get("target"), str):
                return None
        return graph
    except (OSError, ValueError, TypeError):
        return None
