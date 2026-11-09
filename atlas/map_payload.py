"""Проекция загрузки карты: подробные атрибуты доступны через описание узла и MCP."""

import json


def serialize_graph(graph: dict, projection: str = "full") -> bytes:
    """Сериализует JSON один раз; map исключает подробные деревья атрибутов и тестов.

    Не изменяет опубликованный снимок. Поля, комментарии, доказательства рёбер и
    идентификаторы сохраняются. Полный HTTP/MCP-контракт остаётся по умолчанию.
    """
    payload = graph
    if projection == "map":
        nodes = []
        for node in graph.get("nodes", []):
            projected = {key: value for key, value in node.items() if key not in {"attributeFacts", "testFacts", "entrypointFacts"}}
            if isinstance(node.get('testContainerFacts'), dict):
                projected['testContainerFacts'] = {key: value for key, value in node['testContainerFacts'].items() if key != 'tests'}
            nodes.append(projected)
        metadata = dict(graph.get("metadata", {}))
        # Captured preview labels are read only on demand through html-preview.
        metadata.pop('htmlPreviewText', None)
        if isinstance(metadata.get('testDiscovery'), dict):
            metadata['testDiscovery'] = {key: value for key, value in metadata['testDiscovery'].items() if key != 'entries'}
        if isinstance(metadata.get('entrypointDiscovery'), dict):
            metadata['entrypointDiscovery'] = {key: value for key, value in metadata['entrypointDiscovery'].items() if key != 'entries'}
        metadata["projection"] = {"name": "map", "omittedNodeFields": ["attributeFacts", "testFacts", "entrypointFacts"]}
        payload = {**graph, "nodes": nodes, "metadata": metadata}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
