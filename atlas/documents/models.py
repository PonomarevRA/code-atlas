"""Версионированные общие инварианты деревьев документов без форматных импортов."""

import hashlib
import json

SCHEMA_VERSION = "atlas-document-tree-v1"
MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_NODES = 100_000
MAX_DEPTH = 128
MAX_RESPONSE_BYTES = 64_000
MAX_QUERY_BYTES = 54_000


class DocumentError(Exception):
    """Ожидаемая ошибка импорта или запроса с устойчивым HTTP-кодом."""

    def __init__(self, code, message, status=400):
        """Сохраняет безопасное сообщение и machine-readable причину отказа."""
        super().__init__(message)
        self.code, self.status = code, status


def require(condition, code, message, status=400):
    """Прерывает операцию до её побочных эффектов при нарушении контракта."""
    if not condition:
        raise DocumentError(code, message, status)


def encode(value):
    """Канонический JSON сохраняет Unicode и запрещает нечисловые значения float."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(*values):
    """Идентичность содержит все переданные версии и семантические настройки."""
    return hashlib.sha256(encode(values).encode("utf-8")).hexdigest()


def validate_tree(tree):
    """Проверяет один корень, одно родительство, глубину и локальные концы рёбер."""
    require(isinstance(tree, dict), "INVALID_TREE", "Провайдер вернул неверное дерево.")
    nodes, edges = tree.get("nodes"), tree.get("edges", [])
    require(isinstance(nodes, list) and 0 < len(nodes) <= MAX_NODES, "NODE_LIMIT", "Неверное число узлов документа.")
    require(isinstance(edges, list) and len(edges) <= MAX_NODES * 3, "EDGE_LIMIT", "Превышен предел связей.")
    by_id = {}
    for node in nodes:
        require(isinstance(node, dict) and isinstance(node.get("id"), str) and bool(node["id"]), "INVALID_NODE", "Узел должен иметь строковый id.")
        require(node["id"] not in by_id, "DUPLICATE_NODE", "Повторный id узла.")
        require(isinstance(node.get("kind"), str) and isinstance(node.get("order", 0), int), "INVALID_NODE", "Неверный kind/order узла.")
        require(isinstance(node.get("sourceLocator", {}), dict), "INVALID_LOCATOR", "Locator должен быть объектом.")
        by_id[node["id"]] = node
    roots = [node for node in nodes if node.get("parentId") is None]
    require(len(roots) == 1, "INVALID_TREE", "Документ должен иметь один корень.")
    for node in nodes:
        seen, current = set(), node
        while current.get("parentId") is not None:
            parent = current["parentId"]
            require(parent in by_id and parent not in seen, "INVALID_TREE", "Цикл или отсутствующий родитель.")
            seen.add(parent)
            require(len(seen) <= MAX_DEPTH, "DEPTH_LIMIT", "Превышена глубина документа.")
            current = by_id[parent]
    for edge in edges:
        require(isinstance(edge, dict) and edge.get("source") in by_id and edge.get("target") in by_id, "INVALID_EDGE", "Связь ссылается на отсутствующий узел.")
    coverage = tree.get("coverage", {"status": "partial"})
    require(isinstance(coverage, dict) and coverage.get("status") in {"complete", "partial", "unsupported", "failed"}, "INVALID_COVERAGE", "Неизвестный статус покрытия.")
    encode(tree)
    return tree


class TreeBuilder:
    """Небольшой помощник провайдеров; идентификаторы локальны до публикации ревизии."""

    def __init__(self, label="Document", max_nodes=MAX_NODES, max_text_bytes=16 * 1024 * 1024):
        """Создаёт корень и бюджеты, проверяемые при каждом добавлении узла."""
        self.nodes, self.edges, self.warnings = [], [], []
        self.max_nodes, self.max_text_bytes, self.text_bytes = max_nodes, max_text_bytes, 0
        self.root_id = self.add("document", label=label)

    def add(self, kind, parent_id=None, label="", text="", source_locator=None, attributes=None, **extra):
        """Добавляет узел в исходном порядке и возвращает его локальный id."""
        require(len(self.nodes) < self.max_nodes, "NODE_LIMIT", "Превышен предел узлов документа.")
        self.text_bytes += len(text.encode("utf-8")) + len(label.encode("utf-8"))
        require(self.text_bytes <= self.max_text_bytes, "TEXT_LIMIT", "Превышен предел текстовых байтов.")
        node_id = str(len(self.nodes))
        self.nodes.append({"id": node_id, "kind": kind, "parentId": parent_id, "order": len(self.nodes), "label": label, "text": text, "sourceLocator": source_locator or {}, "attributes": attributes or {}, **extra})
        return node_id

    def build(self, coverage=None):
        """Возвращает проверенное сериализуемое дерево провайдера."""
        return validate_tree({"nodes": self.nodes, "edges": self.edges, "coverage": coverage or {"status": "complete"}, "warnings": self.warnings})
