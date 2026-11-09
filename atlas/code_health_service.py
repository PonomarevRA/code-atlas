"""Оценки выбранных исходников и снимков с ограниченным кэшем и immutable baseline."""

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import threading
import networkx as nx

from .code_health import ANALYZER_VERSION, calculate_health, dependency_profiles, resolve_inherited_documentation
from .code_health_comparison import summarize
from .code_health_policy import policy
from .code_health_records import HealthError, HealthRecords
from .code_health_source import bind_source_methods, parse_health_source
from .source_viewer import _read_regular_file, SourceError


def relative_file(filename: str) -> str:
    """Файлы запроса не могут выходить за проект или содержать Windows traversal."""
    normalized = filename.replace("\\", "/")
    path = Path(normalized)
    if not filename or path.is_absolute() or ":" in normalized or any(part in {".", ".."} for part in normalized.split("/")):
        raise HealthError("Select a relative file inside the project", 403)
    return path.as_posix()


class CodeHealthService:
    """Не запускает Graphify или код проекта; reads явно выбирают snapshot/working-tree."""
    def __init__(self, store, refresh=None):
        self.store = store
        self.refresh = refresh
        self.records = HealthRecords(store.data_dir)
        self.cache = OrderedDict()
        self.lock = threading.RLock()

    def _parsed(self, filename: str, content: bytes, max_methods: int) -> dict:
        """Кэш хранит только небольшие факты, а не AST или черновой текст."""
        key = (filename, hashlib.sha256(content).hexdigest(), max_methods, ANALYZER_VERSION, policy()["version"])
        if key in self.cache:
            self.cache.move_to_end(key)
            return deepcopy(self.cache[key])
        result = parse_health_source(filename, content, max_methods=max_methods)
        self.cache[key] = deepcopy(result)
        while len(self.cache) > 8:
            self.cache.popitem(last=False)
        return result

    def evaluate_contents(self, graph: dict, contents: dict[str, bytes], *, locale: str = "en", source_kind: str = "working-tree",
                          max_methods: int = 120) -> dict:
        """Обрабатывает явный набор файлов; исходник черновика остаётся лишь входным аргументом."""
        if len(contents) > policy()["limits"]["maxFiles"] or sum(len(value) for value in contents.values()) > policy()["limits"]["maxTotalBytes"]:
            raise HealthError("Health source budget exceeded", 413)
        methods, hashes, warnings, truncated, file_analysis = [], {}, [], False, {}
        for filename, content in sorted(contents.items()):
            if len(content) > policy()["limits"]["maxFileBytes"]:
                raise HealthError("Health file exceeds 1 MiB", 413)
            try:
                content.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                raise HealthError("Health source must be UTF-8", 415) from error
            remaining = max_methods - len(methods)
            if remaining <= 0:
                truncated = True
                break
            parsed = self._parsed(filename, content, remaining)
            file_analysis[filename] = {"status": parsed["status"], "truncated": parsed["truncated"], "methodCount": len(parsed["methods"]),
                                       'empty': not content.decode('utf-8-sig').strip()}
            hashes[filename] = parsed["sourceHash"]
            methods.extend(parsed["methods"])
            warnings.extend({"file": filename, "message": item} for item in parsed["warnings"])
            truncated |= parsed["truncated"]
        methods = bind_source_methods(methods, graph)
        original = {node["id"]: node for node in graph.get("nodes", [])}
        current_ids = {node['id'] for node in methods}
        verified_owners = set()
        for method in methods:
            previous = original.get(method['id'], {})
            if method.get('healthSourceStamp') and method['healthSourceStamp'] == previous.get('healthSourceStamp'):
                owner = (method.get('memberFacts') or {}).get('ownerNodeId')
                if owner:
                    verified_owners.add(owner)
        owners = [node for node in graph.get('nodes', []) if node['id'] not in current_ids and node['id'] in verified_owners]
        inherited_edges = [edge for edge in graph.get('edges', []) if edge.get('source') in verified_owners and edge.get('target') in verified_owners]
        resolve_inherited_documentation([*methods, *owners], inherited_edges)
        profiles = dependency_profiles(graph)
        name_index = {}
        for method in methods:
            name_index.setdefault(method["name"], []).append(method)
        def local_matches(caller, expression):
            if not expression.isidentifier():
                return []
            scope = caller['healthBindingScope']
            allowed = []
            for target in name_index.get(expression, []):
                target_scope = target['healthBindingScope']
                if target_scope['file'] != scope['file'] or target_scope['language'] != scope['language']:
                    continue
                if target_scope['owners']:
                    if target_scope['owners'] != scope['owners']:
                        continue
                    if scope['language'] not in {'csharp', 'java'} and not target_scope['functions']:
                        continue  # Python/JS methods require a receiver; bare names search lexical bindings.
                functions = target_scope['functions']
                accessible = not functions or functions == scope['functions'][:len(functions)] or functions == [*scope['functions'], caller['name']]
                if accessible:
                    allowed.append(target)
            if len(allowed) > 1:
                # Choose only a unique binding in the nearest lexical scope.
                nearest = max(len(target['healthBindingScope']['functions']) for target in allowed)
                allowed = [target for target in allowed if len(target['healthBindingScope']['functions']) == nearest]
            return allowed

        local_calls = nx.DiGraph()
        local_calls.add_nodes_from(method['healthIdentity'] for method in methods)
        for method in methods:
            for expression in method['lexicalCalls']:
                matches = local_matches(method, expression)
                if len(matches) == 1:
                    local_calls.add_edge(method['healthIdentity'], matches[0]['healthIdentity'])
        recursive = set()
        for component in nx.strongly_connected_components(local_calls):
            if len(component) > 1 or any(local_calls.has_edge(member, member) for member in component):
                recursive.update(component)
        for method in methods:
            snapshot_node = original.get(method["id"], {})
            if method.get("healthSourceStamp") and snapshot_node.get("healthSourceStamp") == method["healthSourceStamp"]:
                dependency = profiles.get(method["id"])
            else:
                targets, unresolved = set(), []
                for expression in method["lexicalCalls"]:
                    # Только свободное имя однозначного local helper. Receiver
                    # binding и стандартные библиотеки требуют графового факта.
                    matches = local_matches(method, expression)
                    if len(matches) == 1:
                        targets.add(matches[0]["healthIdentity"])
                    else:
                        unresolved.append(expression)
                dependency = {"status": "partial" if unresolved or method.get("callsTruncated") or method.get("writesUnknown") else "complete",
                    "callTargets": len(targets), "unknownTargets": len(unresolved), "writes": method["lexicalWrites"],
                    "recursive": method["healthIdentity"] in recursive, "implementationCandidates": 0,
                    "basis": "bounded-local-lexical-calls-and-write-targets", "runtimeSelectionKnown": False,
                    "unresolvedCalls": unresolved[:20]}
            for fields in (method["complexity"], method["documentationHealth"]):
                fields["sourceKind"] = source_kind
            method['health'] = {'dependencies': dependency}
        incomplete_files = truncated or any(item['status'] != 'complete' for item in file_analysis.values())
        from .code_health_context import apply_source_context
        organization = apply_source_context(methods, graph, local_calls, file_analysis, contents,
                            source_kind=source_kind, locale=locale, complete=not incomplete_files)
        summary = summarize(methods)
        summary['organization'] = organization
        if incomplete_files:
            summary["logicInventory"]["status"] = "partial"
            summary["scopeStatus"] = "partial"
        return {"status": "partial" if incomplete_files else "complete", "fileAnalysis": file_analysis,
                "policyVersion": policy()["version"], "analyzerVersion": ANALYZER_VERSION, "nodes": methods,
                "files": sorted(contents), "sourceHashes": hashes, "sourceMode": source_kind,
                "assessmentKind": "measured" if source_kind == "working-tree" else "draft-analysis",
                "measuredAt": datetime.now(timezone.utc).isoformat(), "snapshotId": graph.get("metadata", {}).get("snapshotId"),
                "graphUpdatedAt": graph.get("metadata", {}).get("graphUpdatedAt"), "truncated": truncated,
                "warnings": warnings, "summary": summary, "behaviourVerification": "not-performed"}

    def assess(self, project_id: str, query) -> dict:
        """Создаёт baseline только по запросу; исходные снимки и рабочие файлы не меняются."""
        with self.lock:
            filenames = [relative_file(name) for name in query.files]
            root, graph = self.store.health_snapshot(project_id, query.snapshotId, query.nodeIds, filenames, query.maxMethods)
            if query.sourceMode == "working-tree":
                filenames = sorted(set(filenames or graph["scopeFiles"]))
                if not filenames:
                    raise HealthError("Choose nodeIds or files for working-tree assessment")
                if len(filenames) > policy()["limits"]["maxFiles"]:
                    raise HealthError("Too many health files", 413)
                contents = {}
                for filename in filenames:
                    try:
                        contents[filename] = _read_regular_file(root.resolve(), Path(filename))
                    except SourceError as error:
                        raise HealthError(str(error), error.status) from error
                result = self.evaluate_contents(graph, contents, locale=query.locale, max_methods=query.maxMethods)
                captured = {node.get("file"): (node.get("complexity") or {}).get("sourceHash") for node in graph["selected"]}
                result["freshness"] = {"status": "current" if all(captured.get(name) == digest for name, digest in result["sourceHashes"].items()) else "changed-or-unverified",
                                       "checkedAt": result["measuredAt"]}
            else:
                nodes = graph["selected"]
                profiles = dependency_profiles(graph) if any(not n.get("health") for n in nodes) else {}
                for node in nodes:
                    node["health"] = calculate_health(node, (node.get("health") or {}).get("dependencies") or profiles.get(node["id"]), locale=query.locale)
                result = {"status": "complete" if graph["metadata"].get("codeHealth") else "unavailable",
                    "policyVersion": policy()["version"], "analyzerVersion": ANALYZER_VERSION,
                    "nodes": nodes, "files": sorted({node.get("file", "") for node in nodes}), "sourceMode": "snapshot",
                    "sourceHashes": {node.get("file", ""): node.get("complexity", {}).get("sourceHash") for node in nodes},
                    "assessmentKind": "measured", "snapshotId": graph["metadata"]["snapshotId"],
                    "graphUpdatedAt": graph["metadata"].get("graphUpdatedAt"), "measuredAt": graph["metadata"].get("codeHealth", {}).get("measuredAt"),
                    "truncated": graph["truncated"], "warnings": [], "summary": summarize(nodes),
                    "freshness": {"status": "not-checked"}, "behaviourVerification": "not-performed"}
            result["scope"] = {"nodeIds": sorted(query.nodeIds), "maxMethods": query.maxMethods}
            return self.records.save(project_id, result)

    def report(self, project_id: str, snapshot_id: str | None, *, limit: int = 40, offset: int = 0, order: str = "health", locale: str = "en") -> dict:
        """Читает готовые snapshot facts и агрегаты; отчёт не сканирует исходники."""
        _, graph = self.store.health_snapshot(project_id, snapshot_id, limit=limit, offset=offset, order=order)
        nodes = graph["selected"]
        for node in nodes:
            if not node.get("health"):
                node["health"] = calculate_health(node, None, locale=locale)
            else:
                from .code_health_policy import messages
                for item in node["health"].get("findings", []):
                    item["message"] = messages(locale).get(item["rule"], item["rule"])
        summary = graph["metadata"].get("codeHealth", {}).get("summary")
        return {"status": "complete" if graph["metadata"].get("codeHealth") else "unavailable",
                "snapshotId": graph["metadata"]["snapshotId"], "graphUpdatedAt": graph["metadata"].get("graphUpdatedAt"),
                "policyVersion": graph["metadata"].get('codeHealth', {}).get('policyVersion', policy()["version"]),
                "mode": "advisory", "sourceMode": "snapshot", "nodes": nodes,
                "summary": summary or {**summarize(nodes), "scope": "page-only; legacy snapshot"},
                "totalMethods": graph["totalMethods"], "truncated": graph["truncated"],
                "measuredAt": graph["metadata"].get('codeHealth', {}).get('measuredAt'),
                "refresh": self._refresh_status(project_id, graph['metadata']['snapshotId']),
                "nextCursor": offset + limit if graph["truncated"] else None, "freshness": {"status": "not-checked"}}

    def _refresh_status(self, project_id: str, snapshot_id: str):
        """Historical reports keep their captured facts; only the current revision accepts refresh jobs."""
        if self.refresh is None:
            return None
        try:
            return self.refresh.status(project_id, snapshot_id)
        except HealthError as error:
            if error.status != 409:
                raise
            return {'status': 'historical', 'snapshotId': snapshot_id}
