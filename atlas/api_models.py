"""Публичные DTO HTTP API; ограничения не зависят от транспортной регистрации маршрутов."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field
from .execution_path_limits import MAX_PATH_NODES


class ProjectInput(BaseModel):
    """Абсолютный локальный путь без команды оболочки."""
    path: str = Field(min_length=1, max_length=4096)


class RemoteRepositoryInput(BaseModel):
    """HTTPS/SSH репозиторий и необязательная ветка/тег; не shell-команда."""
    url: str = Field(min_length=1, max_length=2048)
    ref: str = Field(default="", max_length=200)

    model_config = {"extra": "forbid"}


class NodeInput(BaseModel):
    """Идентификатор существующего узла текущего снимка."""
    nodeId: str = Field(min_length=1, max_length=4096)
    snapshotId: str = Field(min_length=1, max_length=128)


class QueryInput(BaseModel):
    """Поисковый запрос к графу, а не команда и не LLM-промпт."""
    question: str = Field(min_length=1, max_length=2000)
    snapshotId: str = Field(min_length=1, max_length=128)


class SourceInput(NodeInput):
    """Узел и ревизия открытой карты; произвольный путь не принимается."""
    snapshotId: str = Field(min_length=1, max_length=128)

    model_config = {"extra": "forbid"}


class MethodFlowChunkInput(SourceInput):
    """Закреплённый узел и ссылка на страницу исходного синтаксического дерева."""
    chunkRef: str | None = Field(default=None, min_length=1, max_length=8192)
    limit: int = Field(default=40, ge=1, le=64, strict=True)
    contextId: str | None = Field(default=None, min_length=1, max_length=128)


class AnalysisContextInput(BaseModel):
    """An explicit temporary analysis scope within one currently published snapshot."""
    snapshotId: str = Field(min_length=1, max_length=128)
    model_config = {'extra': 'forbid'}


class AnalysisSectionInput(SourceInput):
    """Address one bounded description section or its returned child/page reference."""
    contextId: str = Field(min_length=1, max_length=128)
    section: Literal['methodFlow', 'documentation', 'calls', 'controlFlow', 'parameterFacts'] = 'methodFlow'
    sectionRef: str | None = Field(default=None, min_length=1, max_length=8192)
    limit: int = Field(default=40, ge=1, le=64, strict=True)


class ExecutionPathInput(NodeInput):
    """Корень текущего снимка и ограниченные бюджеты статического пути."""
    maxDepth: int = Field(default=8, ge=1, le=30, strict=True)
    maxNodes: int = Field(default=MAX_PATH_NODES, ge=1, le=MAX_PATH_NODES, strict=True)

    model_config = {"extra": "forbid"}


class GraphNeighborsInput(SourceInput):
    """Окрестность опубликованного узла; без чтения рабочего исходника."""
    direction: Literal["incoming", "outgoing", "both"] = "both"
    limit: int = Field(default=40, ge=1, le=120, strict=True)
    cursor: str | None = Field(default=None, min_length=1, max_length=8192)


class NodeWikiInput(SourceInput):
    """Документы и связи узла; смысловые рекомендации можно запросить отдельно."""
    limit: int = Field(default=8, ge=1, le=12, strict=True)
    includeRecommendations: bool = Field(default=True, strict=True)


class WikiDocumentInput(BaseModel):
    """Страница сохранённого текста; путь к рабочему файлу не принимается."""
    snapshotId: str = Field(min_length=1, max_length=128)
    documentId: str = Field(min_length=1, max_length=256)
    offset: int = Field(default=0, ge=0, le=131072, strict=True)
    maxChars: int = Field(default=12000, ge=1, le=12000, strict=True)
    model_config = {"extra": "forbid"}


class ViewAnnotationInput(BaseModel):
    """Пояснение агента с явными ссылками только на выбранные исходные узлы."""
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(default="", max_length=1200)
    nodeIds: list[str] = Field(min_length=1, max_length=120)
    model_config = {"extra": "forbid"}


class AgentViewInput(BaseModel):
    """Ограниченная воспроизводимая выборка снимка и отдельно авторские заметки."""
    projectId: str = Field(min_length=1, max_length=128)
    snapshotId: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    query: str = Field(default="", max_length=4000)
    nodeIds: list[str] = Field(default_factory=list, max_length=120)
    search: str = Field(default="", max_length=500)
    excludeKinds: list[str] = Field(default_factory=list, max_length=30)
    excludeNodeIds: list[str] = Field(default_factory=list, max_length=120)
    maxNodes: int = Field(default=120, ge=1, le=120, strict=True)
    depth: int = Field(default=1, ge=0, le=3, strict=True)
    annotations: list[ViewAnnotationInput] = Field(default_factory=list, max_length=30)
    model_config = {"extra": "forbid"}


class AgentSearchInput(BaseModel):
    """Поиск существующих символов для последующего построения агентского вида."""
    query: str = Field(min_length=1, max_length=500)
    snapshotId: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=120, ge=1, le=120, strict=True)
    excludeKinds: list[str] = Field(default_factory=list, max_length=30)
    model_config = {"extra": "forbid"}


class DocumentationURLInput(BaseModel):
    """Локальный MCP endpoint для обнаружения инструментов."""
    url: str = Field(min_length=1, max_length=2048)
    model_config = {"extra": "forbid"}


class DocumentationConfigInput(DocumentationURLInput):
    """Явно выбранный инструмент и статические JSON-аргументы поиска."""
    tool: str = Field(min_length=1, max_length=200)
    queryArgument: str = Field(min_length=1, max_length=200)
    arguments: dict = Field(default_factory=dict)


class DocumentationSearchInput(BaseModel):
    """Только поисковая строка и версия настройки; raw-код не прикладывается."""
    query: str = Field(min_length=1, max_length=500)
    configVersion: str | None = Field(default=None, min_length=1, max_length=128)
    model_config = {"extra": "forbid"}


class DocumentationIndexInput(BaseModel):
    """Индекс локальных документов для конкретной опубликованной карты."""
    snapshotId: str = Field(min_length=1, max_length=128)
    force: bool = False
    model_config = {"extra": "forbid"}


class SemanticSearchInput(BaseModel):
    """Компактный векторный запрос без загрузки кода во внешнюю модель."""
    snapshotId: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=8, ge=1, le=20, strict=True)
    kind: Literal["all", "comment", "documentation"] = "all"
    model_config = {"extra": "forbid"}


class FederationInput(BaseModel):
    """Текущий основной снимок и явно выбранные зарегистрированные репозитории."""
    snapshotId: str = Field(min_length=1, max_length=128)
    relatedProjectIds: list[str] = Field(default_factory=list, max_length=8)
    visitedNodeIds: list[Annotated[str, Field(min_length=1, max_length=4096)]] = Field(default_factory=list, max_length=64)

    model_config = {"extra": "forbid"}


class CompareInput(BaseModel):
    """Две локальные Git-ревизии и явная семантика сравнения их деревьев."""
    baseRef: str = Field(min_length=1, max_length=512)
    targetRef: str = Field(min_length=1, max_length=512)
    mode: Literal["direct", "merge-base"] = "direct"
