"""Captured wiki выбранного узла: source associations, настоящие соседи и semantic recommendations."""
from copy import deepcopy

from .store import StoreError

MAX_NEIGHBORS = 24
MAX_DOCUMENT_PAGE_CHARS = 12000


def _node_summary(node):
    """Сохраняет raw ID и bounded подпись; никаких новых code nodes из similarities."""
    result = {key: node.get(key) for key in ("id", "label", "kind", "file", "line")}
    result["label"] = str(result.get("label") or "")[:200]
    result["file"] = node.get("file") or node.get("source_file")
    result["line"] = node.get("line") or node.get("source_location")
    return result


def _index_summary(index):
    """В ответ не попадают внутренние имена LanceDB таблиц и filesystem directories."""
    fields = (
        "schemaVersion", "corpusHash", "capturedAt", "indexedAt", "model",
        "documentCount", "chunkCount", "truncated", "warnings",
    )
    return {key: index.get(key) for key in fields}


class WikiService:
    """Читает один pinned generation; документы — данные, не инструкции и не runtime-доказательства."""

    def __init__(self, store, semantic_service):
        """Использует тот же lifecycle/database/model, не создавая второго writer."""
        self.store = store
        self.semantic = semantic_service
        self.database = semantic_service.database
        self.embedder = semantic_service.embedder

    def _graph(self, project_id, snapshot_id):
        """Проверяет карту до чтения и дополнительно сверяет snapshot закреплённого графа."""
        _, graph = self.store.comparison_snapshot(project_id)
        self.store.assert_snapshot(project_id, snapshot_id)
        if graph.get("metadata", {}).get("snapshotId") != snapshot_id:
            raise StoreError("Карта обновилась. Выберите актуальный снимок.", 409)
        return graph

    def _call(self, method, *args, **kwargs):
        """Переводит ожидаемые index errors, сохраняя HTTP-статус для API/MCP."""
        try:
            return method(*args, **kwargs)
        except RuntimeError as error:
            raise StoreError(str(error), getattr(error, "status", 503)) from error

    def _neighbors(self, graph, node_id, nodes):
        """Только один шаг существующих edges; relation/confidence не повышаются до runtime-факта."""
        found = []
        seen = set()
        for edge in graph.get("edges", []):
            source, target = edge.get("source"), edge.get("target")
            if node_id not in {source, target}:
                continue
            other = target if source == node_id else source
            if other == node_id or other not in nodes:
                continue
            direction = "outgoing" if source == node_id else "incoming"
            key = other, direction, edge.get("relation")
            if key in seen:
                continue
            seen.add(key)
            evidence = {}
            for field in ("source", "target", "relation", "confidence", "source_file",
                          "source_location", "lineKind", "evidence"):
                if field in edge:
                    evidence[field] = deepcopy(edge[field])
            found.append({
                "node": _node_summary(nodes[other]),
                "direction": direction,
                "edge": evidence,
            })
        found.sort(key=lambda item: (item["edge"].get("relation") not in {"calls", "dispatches_to"}, item["direction"] != "outgoing", item["node"]["id"], str(item["edge"].get("relation"))))
        return found[:MAX_NEIGHBORS], len(found) > MAX_NEIGHBORS

    def _recommendations(self, project_id, snapshot_id, node, direct, excluded, index, limit):
        """Ранжирует saved chunks, помечая рекомендации сходством; модель ничего не сочиняет."""
        if not self.embedder.available():
            return [], "model-unavailable", False, ["Рекомендации недоступны: локальная embedding-модель не подготовлена."]
        query_parts = [str(node.get("label") or ""), str(node.get("file") or node.get("source_file") or "")]
        rationale = node.get("rationale") or node.get("description")
        if isinstance(rationale, str):
            query_parts.append(rationale[:800])
        query_parts.extend(document.get("preview", "")[:300] for document in direct[:2])
        context = "\n".join(query_parts).strip()
        if not context:
            return [], "no-context", False, []
        try:
            pieces = self.embedder.split_text(context, max_tokens=126)
            query = pieces[0]["text"]
            result = self.database.search(project_id, snapshot_id, query, limit=min(100, limit * 8), metadata=index)
        except RuntimeError as error:
            return [], "unavailable", False, [str(error)[:200]]
        documents = []
        seen = set(excluded)
        for hit in result["hits"]:
            identifier = hit.get("documentId")
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            captured = self._call(self.database.document, project_id, snapshot_id, identifier, metadata=index)
            summary = {key: value for key, value in captured.items() if key not in {"text", "sourceLinks", "nodeIds", "fileNodeIds"}}
            summary.update(reason="semantic-similarity", distance=hit["distance"], excerpt=hit["text"][:400],
                           explanation="Близость по смыслу; связь графа и соответствие реализации не утверждаются.")
            documents.append(summary)
            if len(documents) >= limit:
                break
        return documents, "ready", len(pieces) > 1, []

    def node_wiki(self, project_id, snapshot_id, node_id, limit=8, *, include_recommendations=True):
        """Читает captured документы и связи; false пропускает всю embedding-обработку.

        По умолчанию сохраняет смысловые рекомендации для существующих клиентов.
        Все разделы одного ответа используют одно закреплённое поколение индекса.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise StoreError("Лимит wiki должен быть от 1 до 20.", 400)
        if not isinstance(include_recommendations, bool):
            raise StoreError("Параметр рекомендаций wiki должен быть логическим значением.", 400)
        graph = self._graph(project_id, snapshot_id)
        nodes = {node["id"]: node for node in graph["nodes"]}
        if node_id not in nodes:
            raise StoreError("Узел не найден в выбранном снимке.", 404)
        index = self._call(self.database.published_generation, project_id, snapshot_id)
        neighbors, neighbors_truncated = self._neighbors(graph, node_id, nodes)
        direct = self._call(self.database.documents_for_nodes, project_id, snapshot_id, [node_id], limit, metadata=index)
        neighbor_ids = list(dict.fromkeys(item["node"]["id"] for item in neighbors))
        neighbor_docs = self._call(self.database.documents_for_nodes, project_id, snapshot_id, neighbor_ids, min(100, limit * 2), metadata=index)
        excluded = {document["id"] for document in direct}
        related = []
        for document in neighbor_docs:
            if document["id"] in excluded:
                continue
            matches = {match["nodeId"] for match in document["matches"]}
            document["via"] = [item for item in neighbors if item["node"]["id"] in matches][:6]
            related.append(document)
            excluded.add(document["id"])
            if len(related) >= limit:
                break
        recommendations, status, query_truncated, warnings = [], "not-requested", False, []
        if include_recommendations:
            recommendations, status, query_truncated, warnings = self._recommendations(
                project_id, snapshot_id, nodes[node_id], direct, excluded, index, limit)
        self.store.assert_snapshot(project_id, snapshot_id)
        return {"schemaVersion": 1, "projectId": project_id, "snapshotId": snapshot_id,
                "node": _node_summary(nodes[node_id]), "index": _index_summary(index),
                "directDocuments": direct, "graphNeighbors": neighbors, "neighborDocuments": related,
                "recommendations": recommendations, "recommendationStatus": status,
                "queryTruncated": query_truncated,
                "truncation": {"index": bool(index.get("truncated")), "graphNeighbors": neighbors_truncated,
                               "directDocuments": len(direct) == limit, "neighborDocuments": len(related) == limit},
                "warnings": [*warnings, *index.get("warnings", [])][:10]}

    def document(self, project_id, snapshot_id, document_id, offset=0, max_chars=MAX_DOCUMENT_PAGE_CHARS):
        """Пагинация immutable captured text по character offsets; live source не читается."""
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise StoreError("Некорректное смещение документа.", 400)
        if not isinstance(max_chars, int) or isinstance(max_chars, bool) or not 1 <= max_chars <= MAX_DOCUMENT_PAGE_CHARS:
            raise StoreError("Страница документа превышает 12000 символов.", 400)
        self.store.assert_snapshot(project_id, snapshot_id)
        index = self._call(self.database.published_generation, project_id, snapshot_id)
        document = self._call(self.database.document, project_id, snapshot_id, document_id, metadata=index)
        text = document.pop("text")
        if offset > len(text):
            raise StoreError("Смещение находится за концом документа.", 400)
        end = min(len(text), offset + max_chars)
        # Связи — metadata, не разрешение запросу открывать файл или URL.
        source_links = document.get("sourceLinks", [])
        document["sourceLinksTruncated"] = len(source_links) > 40
        document["sourceLinks"] = source_links[:40]
        for link in document["sourceLinks"]:
            identities = link.get("nodeIds", [])
            link["nodeIdsTruncated"] = bool(link.get("nodeIdsTruncated")) or len(identities) > 12
            link["nodeIds"] = identities[:12]
        for field in ("nodeIds", "fileNodeIds"):
            identities = document.get(field, [])
            truncated_field = field + "Truncated"
            document[truncated_field] = bool(document.get(truncated_field)) or len(identities) > 12
            document[field] = identities[:12]
        self.store.assert_snapshot(project_id, snapshot_id)
        return {"projectId": project_id, "snapshotId": snapshot_id, "document": document,
                "text": text[offset:end], "textOffsetStart": offset, "textOffsetEnd": end,
                "totalChars": len(text), "hasMore": end < len(text), "nextOffset": end if end < len(text) else None,
                "index": _index_summary(index)}
