"""Объединение локальных архитектур с явной принадлежностью репозиториям."""

from collections import Counter
import ipaddress
from urllib.parse import urlsplit

from .project_scope_context import ProjectScopeContext
from .system_architecture import MAX_EVIDENCE, _absolute, _identifier, build_system_architecture


def _shared_host(node: dict) -> bool:
    """Разрешает объединение известных удалённых адресов, исключая локальные заглушки."""
    hosts = node.get("hosts") or ([node["host"]] if node.get("host") else [])
    if not hosts:
        return False
    for endpoint in hosts:
        try:
            host = urlsplit("//" + endpoint).hostname
            if not host or host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
                return False
            try:
                address = ipaddress.ip_address(host)
                if address.is_loopback or address.is_unspecified or address.is_link_local:
                    return False
            except ValueError:
                pass
        except ValueError:
            return False
    return True


def build_federated_architecture(snapshots: list[dict], primary_snapshot_id: str) -> dict:
    """Объединяет факты выбранных снимков без поиска сервисов по именам проектов."""
    ordered = sorted(snapshots, key=lambda item: item["id"])
    manifests = {}
    scopes = {}
    for snapshot in ordered:
        scope = ProjectScopeContext(snapshot["path"], snapshot["graph"])
        scopes[snapshot["id"]] = scope
        for project in scope.projects:
            if project.get("manifest"):
                path = _absolute(snapshot["path"], project["manifest"])
                key = _identifier("external-project", path)
                manifests.setdefault(key, []).append(_identifier("repository-node", snapshot["id"], project["id"]))
    nodes, edges = {}, {}
    per_repository, repositories = [], []
    last_view = None
    for snapshot in ordered:
        repository_id, repository_name = snapshot["id"], snapshot["name"]
        repositories.append({"id": repository_id, "name": repository_name, "snapshotId": snapshot["snapshotId"]})
        view = build_system_architecture(snapshot["path"], snapshot["graph"], snapshot["snapshotId"], prepared=scopes[repository_id])
        last_view = view
        per_repository.append({"repositoryId": repository_id, **view["coverage"]})
        mapping = {}
        original_nodes = {node["id"]: node for node in view["nodes"]}
        for node in view["nodes"]:
            kind = node["kind"]
            exact_targets = manifests.get(node["id"], []) if kind == "external-project" else []
            if len(exact_targets) == 1:
                mapping[node["id"]] = exact_targets[0]
                continue
            shared = kind == "package" or (kind not in {"project", "external-project"} and _shared_host(node))
            if kind == "package":
                target = _identifier("shared-package", node["logicalName"].casefold())
            elif shared:
                target = _identifier("shared-system", kind, node.get("scheme"), node.get("databaseType"), node.get("host"))
            else:
                target = _identifier("repository-node", repository_id, node["id"])
            mapping[node["id"]] = target
            item = nodes.setdefault(target, {**node, "id": target,
                "originalCodeProjectId": node.get("codeProjectId"),
                "repositoryId": None if shared else repository_id,
                "repositoryName": None if shared else repository_name,
                "repositoryIds": [], "repositoryNames": [], "logicalNames": [],
                "identityScope": "package-name" if kind == "package" else "endpoint-host" if shared else "repository"})
            if shared and kind != "package":
                item["label"] = f"{kind} · {node['host']}"
            if repository_id not in item["repositoryIds"]:
                item["repositoryIds"].append(repository_id)
                item["repositoryNames"].append(repository_name)
            if node.get("logicalName") not in item["logicalNames"]:
                item["logicalNames"].append(node.get("logicalName"))
        for edge in view["edges"]:
            source, target = mapping[edge["source"]], mapping[edge["target"]]
            if source == target:
                continue
            key = (source, target, edge["relation"])
            item = edges.setdefault(key, {"source": source, "target": target, "relation": edge["relation"],
                "weight": 0, "evidence": [], "evidenceTruncated": False})
            item["weight"] += edge["weight"]
            item["evidenceTruncated"] |= edge.get("evidenceTruncated", False)
            for evidence in edge["evidence"]:
                tagged = {**evidence, "repositoryId": repository_id, "repositoryName": repository_name,
                          "logicalName": original_nodes[edge["target"]].get("logicalName")}
                if tagged in item["evidence"]:
                    continue
                if len(item["evidence"]) < MAX_EVIDENCE:
                    item["evidence"].append(tagged)
                else:
                    item["evidenceTruncated"] = True
    coverage = {}
    for item in per_repository:
        for key, value in item.items():
            if isinstance(value, bool):
                coverage[key] = coverage.get(key, False) or value
            elif isinstance(value, int):
                coverage[key] = coverage.get(key, 0) + value
    coverage["perRepository"] = per_repository
    for node in nodes.values():
        node["logicalNames"].sort()
    kinds = Counter(node["kind"] for node in nodes.values())
    return {"snapshotId": primary_snapshot_id, "sourceKind": "working-tree+snapshot",
        "nodes": [nodes[key] for key in sorted(nodes)], "edges": [edges[key] for key in sorted(edges)],
        "repositories": repositories, "coverage": coverage,
        "counts": {"nodes": len(nodes), "edges": len(edges), "projects": kinds["project"], "repositories": len(repositories)},
        "limits": {**(last_view["limits"] if last_view else {}), "maxRelatedRepositories": 8, "fileBudgetScope": "per-repository"},
        "warning": (last_view["warning"] if last_view else "") + " Общие удалённые адреса объединены по типу, схеме, типу БД и адресу. Общий адрес не доказывает один сервис/БД или поток между репозиториями. Локальные и неизвестные адреса разделены по репозиториям. Межрепозиторные ProjectReference разрешены только по точному пути манифеста."}
