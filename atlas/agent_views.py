"""Неизменяемые виды графа: исходные факты отдельно от заметок, написанных агентом."""

from collections import deque
from copy import deepcopy
from .analysis_diagnostics import diagnostic_summary
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from .map_storage import checked_path
from .agent_view_layout import build_layout, node_color, project_color
from .project_scopes import assign_code_projects
from .node_comments import COMMENT_FIELDS
from .member_preview import member_preview
from .analysis_facts import matches_analysis_facts, analysis_context


class AgentViewError(RuntimeError):
    """Контролируемая ошибка выбора или чтения сохранённого вида."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def search_nodes(graph: dict, query: str, limit: int = 120, exclude_kinds: list[str] | None = None,
                 exclude_ids: set[str] | None = None, load_fact_index=None) -> dict:
    """
    Return a stable ID-sorted page matching the folded query, after kind/ID exclusions.

    The optional loader returns fact rows aligned with this immutable graph, or
    None for the original bounded traversal. Only selected previews are copied;
    source files and executable plugins are never read during search.

    Args:
        graph: Закреплённый граф; не изменяется.
        query: Подстрока имени, пути или захваченных фактов.
        limit: Максимальное число карточек в странице.
        exclude_kinds: Необязательные типы, исключаемые до поиска.
        exclude_ids: Необязательные raw-ID, исключаемые до поиска.
        load_fact_index: Ленивый загрузчик индекса текущего графа либо None.
    Returns:
        Страница с явными счётчиками полноты и диагностическими фактами.
    """
    excluded = {kind.casefold() for kind in exclude_kinds or []}
    excluded_node_ids = exclude_ids or set()
    needle = query.strip().casefold()
    encoded_needle = needle.encode("utf-8", "surrogatepass")
    matches = []
    facts, index_loaded = None, False
    for index, node in enumerate(graph.get("nodes", [])):
        if node["id"] in excluded_node_ids or str(node.get("kind", "")).casefold() in excluded:
            continue
        matched = needle in " ".join(str(node.get(key) or "") for key in ("label", "id", "file", "source_file")).casefold()
        if not matched:
            # A label-only match needs no fact traversal or cold index construction.
            if not index_loaded and load_fact_index is not None:
                facts = load_fact_index()
                index_loaded = True
            matched = encoded_needle in facts[index] if facts is not None else matches_analysis_facts(node, needle)
        if matched:
            matches.append(node)
    # Sorting only matches keeps the same stable ID order without copying every nonmatching node.
    matches.sort(key=lambda node: node["id"])
    return {**diagnostic_summary(graph), "nodes": [{**{key: node.get(key) for key in ("id", "label", "kind", "file", "line")},
                       **({'analysisIssue': deepcopy(node['analysisIssue'])} if node.get('analysisIssue') else {}),
                       **{key: deepcopy(node[key]) for key in COMMENT_FIELDS if key in node}, **member_preview(node)} for node in matches[:limit]],
            "total": len(matches), "truncated": len(matches) > limit, "snapshotId": graph.get("metadata", {}).get("snapshotId"),
            "analysisContext": analysis_context(graph)}


def _selection(graph: dict, request: dict) -> tuple[list[str], dict]:
    """Сохраняет первый seed и проходит ограниченную окрестность по обоим направлениям."""
    by_id = {node["id"]: node for node in graph["nodes"]}
    excluded = {kind.casefold() for kind in request.get("excludeKinds", [])}
    excluded_ids = set(request.get("excludeNodeIds") or [])
    if len(excluded_ids) > 120 or any(node_id not in by_id for node_id in excluded_ids):
        raise AgentViewError("Исключения должны содержать до 120 существующих raw-ID.")
    seeds = list(dict.fromkeys(request.get("nodeIds") or []))
    for node_id in seeds:
        if node_id not in by_id or node_id in excluded_ids or str(by_id[node_id].get("kind", "")).casefold() in excluded:
            raise AgentViewError("Явно выбранный узел отсутствует в снимке или исключён фильтром.")
    search = request.get("search") or ""
    found = search_nodes(graph, search, 120, list(excluded), excluded_ids) if search.strip() else {"nodes": [], "truncated": False}
    for node in found["nodes"]:
        if node["id"] not in seeds and node["id"] not in excluded_ids:
            seeds.append(node["id"])
    if not seeds:
        raise AgentViewError("Выберите хотя бы один существующий узел или непустой поиск.")
    maximum, depth = request.get("maxNodes", 120), request.get("depth", 1)
    if not 1 <= maximum <= 120 or not 0 <= depth <= 3:
        raise AgentViewError("Допустимо до 120 узлов и глубина от 0 до 3.")
    if len(set(request.get("nodeIds") or [])) > maximum:
        raise AgentViewError("Лимит узлов меньше числа явно выбранных символов.")
    neighbors = {node_id: set() for node_id in by_id}
    for edge in graph["edges"]:
        if edge["source"] in by_id and edge["target"] in by_id:
            neighbors[edge["source"]].add(edge["target"])
            neighbors[edge["target"]].add(edge["source"])
    selected, seen = [], set()
    pending = deque((node_id, 0) for node_id in seeds)
    truncated = bool(found["truncated"])
    while pending:
        node_id, distance = pending.popleft()
        if node_id in seen:
            continue
        seen.add(node_id)
        if node_id in excluded_ids or str(by_id[node_id].get("kind", "")).casefold() in excluded:
            continue
        if len(selected) == maximum:
            truncated = True
            continue
        selected.append(node_id)
        if distance < depth:
            for neighbor in sorted(neighbors[node_id]):
                if neighbor not in seen:
                    pending.append((neighbor, distance + 1))
    return selected, {"truncated": truncated, "selection": {"seedNodeIds": seeds, "depth": depth, "maxNodes": maximum,
        "excludeKinds": sorted(excluded), "excludeNodeIds": sorted(excluded_ids), "search": search, "neighborDirection": "both"}}


def _view_nodes(graph: dict, selected: list[str]) -> tuple[list[dict], list[dict]]:
    """Resolve owners against the full snapshot, then copy facts only for selected cards.

    Project membership and counts still include unselected manifests and declarations.
    The projection contains only fields read by assign_code_projects; nested facts are
    read-only there, while published cards receive independent deep copies below.
    """
    fields = ("id", "file", "source_file", "kind", "codeProjectFacts")
    scopes = [{key: node[key] for key in fields if key in node} for node in graph["nodes"]]
    projects = assign_code_projects(scopes)
    owners = {node["id"]: node["codeProjectId"] for node in scopes}
    selected_ids = set(selected)
    by_id = {}
    for node in graph["nodes"]:
        if node["id"] in selected_ids:
            copied = deepcopy(node)
            copied["codeProjectId"] = owners[node["id"]]
            by_id[node["id"]] = copied
    # Captured descriptors may contain nested metadata such as Gradle languages.
    # Isolate that small catalog too; assign_code_projects copies only its top level.
    return deepcopy(projects), [by_id[node_id] for node_id in selected]


def build_view(project_path: Path, graph: dict, request: dict) -> dict:
    """Фиксирует выбранные исходные узлы/связи, размещение и отдельные пояснения агента."""
    if graph.get("metadata", {}).get("snapshotId") not in {None, request.get("snapshotId")}:
        raise AgentViewError("Карта обновилась. Загрузите актуальный снимок.", 409)
    title = request.get("title", "")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise AgentViewError("Укажите название вида длиной до 200 символов.")
    selected, metadata = _selection(graph, request)
    ids = set(selected)
    annotations = deepcopy(request.get("annotations") or [])
    annotation_ids = set()
    raw_ids = {node["id"] for node in graph["nodes"]}
    if len(annotations) > 30:
        raise AgentViewError("Допустимо не более 30 заметок.")
    for note in annotations:
        note_id = note.get("id")
        if not isinstance(note_id, str) or not 1 <= len(note_id) <= 128 or note_id in annotation_ids or note_id in raw_ids:
            raise AgentViewError("Идентификаторы заметок должны быть уникальны и отличаться от узлов.")
        if not isinstance(note.get("title"), str) or not 1 <= len(note["title"]) <= 120 or not isinstance(note.get("text"), str) or len(note["text"]) > 1200:
            raise AgentViewError("Заметка допускает заголовок до 120 и текст до 1200 символов.")
        targets = note.get("nodeIds")
        if not isinstance(targets, list) or not targets or any(target not in ids for target in targets):
            raise AgentViewError("Все ссылки заметки должны указывать на узлы, включённые в вид.")
        annotation_ids.add(note_id)
        note["nodeIds"] = list(dict.fromkeys(targets))
        note.update({"kind": "explanation", "author": "agent"})
    projects, nodes = _view_nodes(graph, selected)
    owners = {node["codeProjectId"] for node in nodes}
    projects = [project for project in projects if project["id"] in owners]
    for project in projects:
        project["color"] = project_color(project)
    project_map = {project["id"]: project for project in projects}
    for node in nodes:
        node["color"] = node_color(node, project_map)
    edges = [deepcopy(edge) for edge in graph["edges"] if edge["source"] in ids and edge["target"] in ids]
    edges.sort(key=lambda edge: (edge["source"], edge["target"], json.dumps(edge, sort_keys=True, ensure_ascii=False)))
    try:
        layout = build_layout(nodes, projects, annotations)
    except ValueError as error:
        raise AgentViewError(str(error)) from None
    identifier = uuid4().hex
    return {"id": identifier, "title": title.strip(), "projectId": request["projectId"], "projectName": project_path.name,
        "snapshotId": request["snapshotId"], "createdAt": datetime.now(timezone.utc).isoformat(), "query": request.get("query") or "",
        "url": f"/view.html?id={identifier}", "pngUrl": f"/api/views/{identifier}/image.png",
        "nodes": nodes, "edges": edges, "codeProjects": projects, "annotations": annotations,
        "annotationEdges": [{"source": note["id"], "target": target, "kind": "explanation"} for note in annotations for target in note["nodeIds"]],
        "layout": layout, "metadata": {**metadata, "sourceSnapshotId": request["snapshotId"], "sourceNodeCount": len(graph["nodes"]),
            "sourceEdgeCount": len(graph["edges"]), "selectedNodeCount": len(nodes), "selectedEdgeCount": len(edges),
            "author": "agent", "schemaVersion": "agent-view-v1",
            "kindFilterWarning": "Фильтр использует сохранённый kind; неразмеченные symbol не классифицируются по имени." if request.get("excludeKinds") and any(node.get("kind") == "symbol" for node in graph["nodes"]) else "", "note": "Пояснения написаны агентом и не являются извлечёнными фактами Graphify."}}


class AgentViewStore:
    """Хранит UUID-файлы только на создание, без API редактирования или перезаписи."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        self.directory = self.data_dir / "maps" / "views"
        self.lock = RLock()

    def _path(self, *parts, directory=True):
        """Reject managed links and file/directory conflicts before any read or write."""
        try:
            return checked_path(self.data_dir, *parts, directory=directory)
        except ValueError:
            raise AgentViewError("Небезопасный путь или конфликт объектов хранилища видов.", 409) from None

    def create(self, project_path: Path, graph: dict, request: dict) -> dict:
        """Создаёт immutable JSON атомарным переименованием после полной валидации."""
        view = build_view(project_path, graph, request)
        payload = json.dumps(view, ensure_ascii=False)
        if len(payload.encode()) > 16 * 1024 * 1024:
            raise AgentViewError("Сохранённый вид превышает 16 МиБ.")
        with self.lock:
            legacy = self._path("views", view["id"] + ".json", directory=False)
            if legacy.exists():
                raise AgentViewError("Идентификатор вида уже существует в старом хранилище.", 409)
            self._path("maps", "views").mkdir(parents=True, exist_ok=True)
            target = self._path("maps", "views", view["id"] + ".json", directory=False)
            temporary = self._path("maps", "views", view["id"] + ".tmp", directory=False)
            temporary_owned = False
            try:
                with temporary.open("x", encoding="utf-8") as handle:
                    temporary_owned = True
                    handle.write(payload)
                # link создаёт имя атомарно и, в отличие от replace, не перезапишет
                # существующий снимок даже при невероятной коллизии UUID.
                os.link(temporary, target)
            except OSError:
                raise AgentViewError("Не удалось сохранить вид графа.", 500) from None
            finally:
                if temporary_owned:
                    temporary.unlink(missing_ok=True)
        return view

    def get(self, identifier: str) -> dict:
        """Читает UUID-снимок без обращения к текущей версии проекта."""
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise AgentViewError("Сохранённый вид не найден.", 404)
        try:
            path = self._path("maps", "views", identifier + ".json", directory=False)
            if not path.exists():
                path = self._path("views", identifier + ".json", directory=False)
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError()
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise AgentViewError("Сохранённый вид не найден.", 404) from None
        except (OSError, ValueError):
            raise AgentViewError("Не удалось прочитать сохранённый вид.", 500) from None

    def list(self) -> dict:
        """Возвращает до ста последних сохранённых видов без полного графа в списке."""
        paths_by_id = {}
        for parts in (("views",), ("maps", "views")):
            directory = self._path(*parts)
            for path in directory.glob("*.json"):
                if re.fullmatch(r"[a-f0-9]{32}", path.stem):
                    paths_by_id[path.stem] = self._path(*parts, path.name, directory=False)
        paths = sorted(paths_by_id.values(), key=lambda path: path.stat().st_mtime, reverse=True)
        views = []
        for path in paths[:100]:
            view = self.get(path.stem)
            views.append({key: view[key] for key in ("id", "title", "projectId", "projectName", "snapshotId", "createdAt", "url", "pngUrl")})
        return {"views": views, "truncated": len(paths) > 100}
