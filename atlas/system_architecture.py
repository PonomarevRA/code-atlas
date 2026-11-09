"""Проектная архитектура: декларации и настройки внешних систем без runtime-догадок."""

from collections import defaultdict, deque
import hashlib
import html
import json
from itertools import islice
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .project_scope_context import ProjectScopeContext, absolute_path
from .source_viewer import SourceError, read_node_source
from .system_configuration import configured_systems, logical_name

MAX_FILES = 80
MAX_BYTES = 8 * 1024 * 1024
MAX_EVIDENCE = 12


def _identifier(kind: str, *parts) -> str:
    """Выводит ID из разрешённой идентичности; скрытые URL-части в ID не участвуют."""
    value = json.dumps([kind, *parts], ensure_ascii=False, sort_keys=True)
    return "system-" + hashlib.sha256(value.encode()).hexdigest()[:20]


def _absolute(root: Path, filename: str) -> str:
    """Нормализует путь лексически: внешний ProjectReference не читается с диска."""
    return absolute_path(root, filename)


def _sources(root: Path, projects: list[dict], snapshot_id: str) -> tuple[list, dict]:
    """Читает манифесты и прямые appsettings из известных каталогов с общим бюджетом."""
    candidates = {}
    root = root.resolve()
    directories = defaultdict(list)
    coverage = {"candidateFiles": 0, "readFiles": 0, "skippedFiles": 0,
                "limited": False, "parseErrors": 0, "unresolvedReferences": 0,
                "ambiguousConfigDirectories": 0, "ambiguousFiles": 0, "discoveryLimited": False}
    for project in projects:
        if project.get("manifest"):
            candidates[project["manifest"]] = project["id"]
            directories[project["path"]].append(project["id"])
    for directory_name, owners in sorted(directories.items()):
        if len(owners) != 1:
            coverage["ambiguousConfigDirectories"] += 1
        try:
            directory = (root / directory_name).resolve(strict=True)
            directory.relative_to(root)
            entries = list(islice(directory.iterdir(), 501))
            if len(entries) > 500:
                # Не выбираем произвольные первые файлы ОС: весь неизвестный
                # набор конфигураций этой директории остаётся явно неполным.
                coverage["limited"] = coverage["discoveryLimited"] = True
                continue
            for path in sorted(entries):
                if path.name.startswith("appsettings") and path.suffix.lower() == ".json":
                    if len(owners) != 1:
                        coverage["ambiguousFiles"] += 1
                        coverage["skippedFiles"] += 1
                    else:
                        candidates[path.relative_to(root).as_posix()] = owners[0]
        except (OSError, ValueError, RuntimeError):
            continue
    coverage["candidateFiles"] = len(candidates) + coverage["ambiguousFiles"]
    total, sources = 0, []
    for index, (filename, project_id) in enumerate(sorted(candidates.items())):
        if index >= MAX_FILES:
            coverage["limited"] = True
            coverage["skippedFiles"] += len(candidates) - index
            break
        try:
            source = read_node_source(root, {"file": filename}, snapshot_id)
        except SourceError:
            coverage["skippedFiles"] += 1
            continue
        size = len(source["text"].encode("utf-8"))
        if total + size > MAX_BYTES:
            coverage["limited"] = True
            coverage["skippedFiles"] += 1
            continue
        total += size
        source["projectId"] = project_id
        sources.append(source)
        coverage["readFiles"] += 1
    return sources, coverage


def _manifest_references(text: str) -> list[dict]:
    """Разбирает только PackageReference/ProjectReference, не выполняя MSBuild или XML DTD."""
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML declarations unsupported")
    root = ET.fromstring(text)
    clean = re.sub(r"<!--[\s\S]*?-->", lambda match: "".join("\n" if char == "\n" else " " for char in match.group()), text)
    positions = defaultdict(deque)
    for match in re.finditer(r'<(?:\w+:)?(PackageReference|ProjectReference)\b[^>]*>', clean):
        include = re.search(r'\b(?:Include|Update)\s*=\s*([\'"])(.*?)\1', match.group())
        if include:
            positions[(match[1], html.unescape(include[2]))].append(clean.count("\n", 0, match.start()) + 1)
    result = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in {"PackageReference", "ProjectReference"}:
            continue
        include = node.get("Include") or node.get("Update") or ""
        lines = positions.get((tag, include))
        result.append({"kind": tag, "include": include, "line": lines.popleft() if lines else 1})
    return result


def build_system_architecture(project_path: Path, graph: dict, snapshot_id: str, *,
                              prepared: ProjectScopeContext | None = None) -> dict:
    """Создаёт слой без методов, секретов и self-edge.

    prepared допускается только от того же graph/root внутри текущего запроса;
    он повторно использует владельцев, но не кэширует содержимое конфигураций.
    """
    scope = prepared if prepared is not None else ProjectScopeContext(project_path, graph)
    projects, owners = scope.projects, scope.owners
    project_ids, manifests = scope.project_ids, scope.manifests
    nodes = {}
    for project in projects:
        nodes[project["id"]] = {"id": project["id"], "kind": "project", "dependencyKind": "project",
                                  "label": project["label"], "codeProjectId": project["id"],
                                  "host": None, "logicalName": project["label"]}
    edges = {}

    def connect(source: str, target: str, relation: str, evidence: dict) -> None:
        """Агрегирует направление и число фактов, сохраняя ограниченные доказательства."""
        if source == target:
            return
        key = (source, target, relation)
        edge = edges.setdefault(key, {"source": source, "target": target, "relation": relation,
                                      "weight": 0, "evidence": [], "evidenceTruncated": False})
        edge["weight"] += 1
        if evidence in edge["evidence"]:
            return
        if len(edge["evidence"]) < MAX_EVIDENCE:
            edge["evidence"].append(evidence)
        else:
            edge["evidenceTruncated"] = True

    sources, coverage = _sources(project_path, projects, snapshot_id)
    for source in sources:
        filename, text, owner = source["file"], source["text"], source["projectId"]
        evidence = {"file": filename, "line": 1, "type": "configuration", "confidence": "configured",
                    "configVariant": Path(filename).name if filename.endswith(".json") else None}
        if filename.endswith(".json"):
            try:
                document = json.loads(text)
            except (ValueError, RecursionError):
                coverage["parseErrors"] += 1
                continue
            if not isinstance(document, dict):
                coverage["parseErrors"] += 1
                continue
            for dependency in configured_systems(document, text):
                kind = dependency["kind"]
                target = _identifier(kind, dependency.get("scheme"), dependency.get("host"), dependency["logicalName"],
                                     owner if not dependency.get("host") else None)
                public = {key: value for key, value in dependency.items() if key != "line"}
                nodes[target] = {**public, "id": target, "dependencyKind": kind, "codeProjectId": None}
                connect(owner, target, "configured-" + kind, {**evidence, "line": dependency["line"], "type": kind + "-configuration"})
            continue
        if Path(filename).suffix.lower() not in {".csproj", ".fsproj", ".vbproj"}:
            continue
        try:
            references = _manifest_references(text)
        except (ET.ParseError, ValueError):
            coverage["parseErrors"] += 1
            continue
        for reference in references:
            include = reference["include"]
            if not include or "$" in include or "@(" in include:
                coverage["unresolvedReferences"] += 1
                continue
            if reference["kind"] == "PackageReference":
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", include):
                    coverage["unresolvedReferences"] += 1
                    continue
                target = _identifier("package", include.lower())
                nodes.setdefault(target, {"id": target, "kind": "package", "dependencyKind": "package",
                                          "label": include, "logicalName": include, "host": None, "codeProjectId": None})
                relation = "package-reference"
            else:
                path = _absolute(project_path, str(Path(filename).parent / include.replace("\\", "/")))
                target = manifests.get(path)
                if not target:
                    target = _identifier("external-project", path)
                    label = logical_name(Path(include.replace("\\", "/")).stem, "External project")
                    nodes.setdefault(target, {"id": target, "kind": "external-project", "dependencyKind": "external-project",
                                              "label": label, "logicalName": label, "host": None, "codeProjectId": None})
                relation = "project-reference"
            connect(owner, target, relation, {**evidence, "line": reference["line"], "type": reference["kind"], "confidence": "declared"})
    allowed = {"calls", "references", "imports", "inherits", "implements", "dispatches_to"}
    ordered = sorted(graph.get("edges", []), key=lambda edge: (edge["source"], edge["target"], edge.get("relation", ""), str(edge.get("source_location", ""))))
    for edge in ordered:
        if edge.get("relation") not in allowed:
            continue
        left, right = owners.get(edge["source"], {}), owners.get(edge["target"], {})
        source, target = left.get("codeProjectId"), right.get("codeProjectId")
        if source not in project_ids or target not in project_ids or source == target:
            continue
        filename = str(edge.get("source_file") or left.get("file") or "")
        filename = scope.evidence_file(filename)
        location = re.match(r"L(\d+)", str(edge.get("source_location") or ""))
        connect(source, target, "code-dependency", {"file": filename, "line": int(location[1]) if location else left.get("line"),
                "type": edge["relation"], "confidence": "graph", "configVariant": None})
    return {"snapshotId": snapshot_id, "sourceKind": "working-tree+snapshot", "nodes": [nodes[key] for key in sorted(nodes)],
            "edges": [edges[key] for key in sorted(edges)], "coverage": coverage,
            "counts": {"nodes": len(nodes), "edges": len(edges), "projects": len(projects)},
            "limits": {"maxFiles": MAX_FILES, "maxBytes": MAX_BYTES, "maxEvidencePerEdge": MAX_EVIDENCE},
            "warning": "Стрелки показывают декларации/настройки использования и связи кода, а не направление сообщений или фактический runtime-трафик. Пакет не доказывает работающий сервис. Варианты appsettings показаны отдельно в доказательствах; активное окружение неизвестно. MSBuild-условия, центральные версии пакетов и вычисляемые настройки не исполняются. Для URL оставлен только нормализованный host; пути, userinfo, query и секреты не возвращаются."}
