"""HTTP-доступ к captured wiki и локальному производному индексу документов."""
from fastapi import FastAPI, Query

from .api_models import DocumentationIndexInput, SemanticSearchInput, NodeWikiInput, WikiDocumentInput
from .semantic_service import SemanticService
from .wiki_service import WikiService


def register_semantic_routes(app: FastAPI, semantic: SemanticService, wiki: WikiService) -> None:
    """Подключает существующие сервисы; синхронные операции выполняются в HTTP worker.

    Writer, модель и соединение с базой принадлежат composition root: регистрация
    маршрутов не создаёт второй индекс или отдельную очередь.
    """

    @app.get("/api/projects/{project_id}/documentation-index")
    def documentation_index_status(project_id: str, snapshotId: str = Query(min_length=1, max_length=128)):
        """Проверяет локальный производный индекс без запуска модели или сети."""
        return semantic.status(project_id, snapshotId)

    @app.post("/api/projects/{project_id}/documentation-index")
    def build_documentation_index(project_id: str, body: DocumentationIndexInput):
        """Ставит ограниченную индексацию документов в отдельный фон."""
        return semantic.submit(project_id, body.snapshotId, body.force)

    @app.get("/api/documentation-index/jobs/{job_id}")
    def documentation_index_job(job_id: str):
        """Отдаёт прогресс локального writer без опроса исходников."""
        return semantic.get_job(job_id)

    @app.post("/api/projects/{project_id}/semantic-search")
    def semantic_search(project_id: str, body: SemanticSearchInput):
        """Возвращает небольшую выборку документов с точными ссылками на код."""
        return semantic.search(project_id, body.snapshotId, body.query, body.limit, body.kind)

    @app.post("/api/projects/{project_id}/node-wiki")
    def node_wiki(project_id: str, body: NodeWikiInput):
        """Отделяет документы узла и соседей графа от смысловых рекомендаций."""
        return wiki.node_wiki(project_id, body.snapshotId, body.nodeId, body.limit,
                              include_recommendations=body.includeRecommendations)

    @app.post("/api/projects/{project_id}/wiki-document")
    def wiki_document(project_id: str, body: WikiDocumentInput):
        """Читает страницу опубликованного документа, не рабочий исходник."""
        return wiki.document(project_id, body.snapshotId, body.documentId, body.offset, body.maxChars)
