"""Лексические C# атрибуты снимка: отдельные occurrence nodes и честные scope-владельцы."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .attribute_facts import attribute_facts, empty_attribute_facts, _Collector
from . import attribute_facts as lexical_attributes
from .interface_source import walk, text, name_of
from .source_viewer import _read_regular_file, SourceError
from .field_facts import field_facts, project_nullable
from .node_comments import COMMENT_FIELDS, captured_comments

MODULE_VERSION = "csharp-attribute-graph-v1"
MAX_FILES = 1200
MAX_BYTES = 32 * 1024 * 1024
MAX_AST_NODES = 1500000
MAX_OCCURRENCES = 30000
MAX_OWNERS = 40000
_TYPES = {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration", "enum_declaration"}
_DECLARATIONS = _TYPES | {"method_declaration", "constructor_declaration", "property_declaration", "indexer_declaration", "field_declaration", "event_field_declaration", "event_declaration", "delegate_declaration", "parameter"}
_ORIGIN = "attribute-graph"
CAPTURE_WARNING = ("Лексические атрибуты захвачены из C#-исходника при сборке снимка. "
                   "Containing-type, parameter и assembly/module scopes показаны отдельно. "
                   "Другие partial-части и наследование не объединяются; типы атрибутов, "
                   "значения аргументов и runtime-эффект не вычисляются.")


def _relative(root: Path, filename: str) -> Path:
    """Не разрешает symlink: каждый исходный сегмент проверит nofollow reader."""
    path = Path(filename)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError as exc:
            raise SourceError("Файл вне корня проекта.") from exc
    if not path.parts or any(part in {"..", "."} for part in path.parts):
        raise SourceError("Недопустимый путь исходника.")
    return path


def _line(node: dict):
    """Читает положительную raw-строку или совместимую Graphify source_location."""
    value = node.get("line")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    found = re.match(r"^L?(\d+)", str(node.get("source_location") or ""))
    return int(found[1]) if found and int(found[1]) > 0 else None


def _key(ast):
    """Идентичность объявления внутри одного файла не зависит от Python AST wrapper."""
    return ast.start_byte, ast.end_byte, ast.type


def _group_key(group):
    """Сопоставляет contextual groups с тем же точным AST-владельцем."""
    return group["line"], group["column"], group["kind"], group["name"]


def _entries(tree, content: bytes):
    """Объявления полей разделены по именам, атрибуты остаются общими лексически."""
    entries = []
    for ast in walk(tree):
        if ast.type not in _DECLARATIONS:
            continue
        named = ast.child_by_field_name("name")
        if ast.type == "indexer_declaration":
            named = next((child for child in ast.children if child.type == "this"), None)
        if ast.type in {"field_declaration", "event_field_declaration"}:
            names = []
            for child in ast.named_children:
                if child.type != "variable_declaration":
                    continue
                for item in child.named_children:
                    if item.type == "variable_declarator":
                        names.append(item.child_by_field_name("name"))
        else:
            names = [named] if named else []
        for name in names:
            if name is not None:
                entries.append({
                    "ast": ast, "name": text(name, content).lstrip("@"),
                    "nameAst": name, "id": None,
                })
    return entries


def _map_owners(entries: list, raw_nodes: list, owners: dict):
    """Точный file/name/range + доступный owner edge; коллизия в любую сторону остаётся unresolved."""
    index = defaultdict(list)
    for node in raw_nodes:
        label = name_of(node.get("label") or node.get("name") or "")
        index[label.removesuffix("[]")].append(node)
    declaration_entries = {_key(entry["ast"]): entry for entry in entries}
    type_ids = set()
    reverse = defaultdict(list)
    for entry in entries:
        ast = entry["ast"]
        current = ast.parent
        while current is not None and current.type not in _DECLARATIONS:
            current = current.parent
        parent = declaration_entries.get(_key(current)) if current is not None else None
        candidates = []
        for node in index[entry["name"]]:
            line = _line(node)
            if line is None or not ast.start_point.row + 1 <= line <= ast.end_point.row + 1:
                continue
            kind = str(node.get("kind") or "")
            label = str(node.get("label") or node.get("name") or "").strip()
            if ast.type in _TYPES and ("(" in label or label.startswith(".")):
                continue
            if ast.type not in _TYPES and node["id"] in type_ids:
                continue
            if ast.type in _TYPES and kind in {"method", "property", "field", "parameter", "file"}:
                continue
            if ast.type not in _TYPES and kind in {"class", "interface", "struct", "record", "enum", "type", "file"}:
                continue
            if parent and parent["id"] and owners.get(node["id"]) and parent["id"] not in owners[node["id"]]:
                continue
            candidates.append(node["id"])
        if len(candidates) == 1:
            entry["id"] = candidates[0]
            reverse[candidates[0]].append(entry)
            if ast.type in _TYPES:
                type_ids.add(candidates[0])
    for matches in reverse.values():
        if len(matches) > 1:
            for entry in matches:
                entry["id"] = None


def _empty_facts(status):
    """Даже отсутствующие факты описывают capture снимка, а не текущую директорию."""
    result = empty_attribute_facts(status)
    result.update(sourceKind="snapshot-captured-source", warning=CAPTURE_WARNING)
    return result


def _lexical_facts(ast, content, filename):
    """Indexer использует bracketed parameters; общий collector ждёт круглые скобки."""
    if ast.type != "indexer_declaration":
        return attribute_facts(ast, content, filename)
    collector = _Collector(content, filename)
    collector.group("declaration", ast, collector.direct_lists(ast), name="this")
    parameters = ast.child_by_field_name("parameters")
    position = 0
    for parameter in collector.children(parameters) if parameters is not None else []:
        if parameter.type != "parameter":
            continue
        position += 1
        if position > lexical_attributes.MAX_PARAMETERS:
            collector.result["truncated"] = True
            break
        lists = collector.direct_lists(parameter)
        if lists:
            name = parameter.child_by_field_name("name")
            collector.group("parameter", name or parameter, lists, name=text(name, content), position=position)
    parent = ast.parent
    ancestors = containing_types = 0
    while parent is not None:
        ancestors += 1
        if ancestors > lexical_attributes.MAX_ANCESTORS:
            collector.result["truncated"] = True
            break
        if parent.type in _TYPES:
            containing_types += 1
            if containing_types <= lexical_attributes.MAX_CONTAINING_TYPES:
                collector.group("type", parent, collector.direct_lists(parent))
            else:
                collector.result["truncated"] = True
        if parent.type == "compilation_unit":
            lists = [child for child in collector.children(parent) if child.type == "global_attribute"]
            if lists:
                collector.group("file", parent, lists, name=filename)
        parent = parent.parent
    if ast.has_error or collector.result["truncated"]:
        collector.result["status"] = "partial"
    return collector.result


class _Capture:
    """Один ограниченный capture; хранит происхождение и scope отдельно от runtime-семантики."""

    def __init__(self, graph, root):
        """Копирует только изменяемые raw nodes и metadata; старые derived nodes заменяются."""
        self.root = Path(root).resolve()
        self.nodes = [deepcopy(node) for node in graph.get("nodes", []) if node.get("_origin") != _ORIGIN]
        self.edges = [deepcopy(edge) for edge in graph.get("edges", []) if edge.get("_origin") != _ORIGIN]
        self.raw_ids = {node["id"] for node in self.nodes}
        self.result = {
            **graph, "nodes": self.nodes, "edges": self.edges,
            "metadata": deepcopy(graph.get("metadata") or {}),
        }
        self.by_file = defaultdict(list)
        self.owners = defaultdict(set)
        self.attributes = {}
        self.scope_nodes = {}
        self.edge_keys = set()
        self.fingerprint = hashlib.sha256()
        self.spans = {}
        self.truncated = False
        self.warnings = []
        self.bytes = 0
        self.ast_nodes = 0
        self.files = 0
        self.resolved = 0
        self.unresolved = 0
        for node in self.nodes:
            filename = node.get("file") or node.get("source_file")
            if isinstance(filename, str) and filename.lower().endswith(".cs"):
                node.pop("attributeFacts", None)
                node.pop("fieldFacts", None)
                for key in COMMENT_FIELDS:
                    node.pop(key, None)
                node["attributeFacts"] = _empty_facts("unavailable")
                try:
                    filename = _relative(self.root, filename).as_posix()
                except SourceError:
                    pass
                self.by_file[filename].append(node)
        for edge in self.edges:
            if edge.get("relation") in {"method", "contains", "declares", "property", "field", "member"}:
                self.owners[edge.get("target")].add(edge.get("source"))

    def warn(self, message):
        """Неполнота всегда явная; сообщения bounded и не содержат посторонних файлов."""
        self.truncated = True
        if len(self.warnings) < 10:
            self.warnings.append(message[:250])

    def edge(self, source, target, relation, scope, declaration=None):
        """Сохраняет отдельную relation для контекста параметра, без ложного method-owned claim."""
        key = source, target, relation
        if key in self.edge_keys:
            return
        self.edge_keys.add(key)
        attribute = self.attributes[target]
        self.edges.append({
            "source": source, "target": target, "relation": relation, "scope": scope,
            "confidence": "SOURCE", "_origin": _ORIGIN,
            "ownerNodeId": source if relation == "has_attribute" else None,
            "declarationNodeId": declaration,
            "source_file": attribute["file"], "source_location": f"L{attribute['line']}",
            "evidence": {
                "kind": "lexical-attribute", "sourceKind": "snapshot-captured-source",
                "file": attribute["file"], "line": attribute["line"],
                "sourceHash": attribute["sourceHash"],
            },
        })

    def occurrence(self, attribute, group, owner_id, declaration_id, source_hash):
        """Детерминированный ID отличает повторные атрибуты одной строки по column."""
        span = self.spans.get((attribute["line"], attribute["column"]))
        identity = [attribute["file"], span, attribute["target"]]
        encoded_identity = json.dumps(identity, ensure_ascii=False).encode()
        identifier = "attr:" + hashlib.sha256(encoded_identity).hexdigest()[:32]
        if identifier in self.raw_ids:
            self.warn("ID атрибута конфликтует с исходным узлом; occurrence не опубликован.")
            return None
        if identifier not in self.attributes:
            if len(self.attributes) >= MAX_OCCURRENCES:
                self.warn("Достигнут бюджет occurrence nodes атрибутов.")
                return None
            owner = {key: group[key] for key in ("kind", "name", "file", "line", "endLine", "column")}
            if "position" in group:
                owner["position"] = group["position"]
            node = {
                **deepcopy(attribute), "id": identifier, "kind": "attribute",
                "label": attribute["text"] or attribute["name"], "scope": group["scope"],
                "ownerNodeId": owner_id, "declarationNodeId": declaration_id, "owner": owner,
                "sourceHash": source_hash, "sourceKind": "snapshot-captured-source",
                "source_file": attribute["file"], "source_location": f"L{attribute['line']}",
                "_origin": _ORIGIN,
            }
            if span:
                node.update(startByte=span[0], endByte=span[1])
            self.attributes[identifier] = node
            self.nodes.append(node)
        elif owner_id and owner_id != self.attributes[identifier]["ownerNodeId"]:
            # One field attribute list can apply to several declarators in the same statement.
            node = self.attributes[identifier]
            owner_ids = node.setdefault("ownerNodeIds", [node["ownerNodeId"]] if node["ownerNodeId"] else [])
            if owner_id not in owner_ids:
                owner_ids.append(owner_id)
            node["ownerNodeId"] = owner_ids[0] if len(owner_ids) == 1 else None
        return identifier

    def file_owner(self, filename, raw_nodes):
        """Assembly/module получают raw file или отдельный synthetic scope, никогда метод."""
        matches = [node["id"] for node in raw_nodes if node.get("kind") == "file"]
        if len(matches) == 1:
            return matches[0]
        if filename not in self.scope_nodes:
            identifier = "attr-scope:" + hashlib.sha256(filename.encode()).hexdigest()[:32]
            if identifier in self.raw_ids:
                self.warn("ID file scope конфликтует с исходным узлом; scope не опубликован.")
                return None
            self.scope_nodes[filename] = identifier
            self.nodes.append({
                "id": identifier, "kind": "attribute_scope",
                "label": filename + " · assembly/module", "file": filename,
                "line": 1, "endLine": 1, "column": 1, "scope": "file",
                "source_file": filename, "source_location": "L1",
                "sourceKind": "snapshot-captured-source", "_origin": _ORIGIN,
            })
        return self.scope_nodes[filename]

    def load_file(self, filename, parser):
        """Одно nofollow-чтение и bounded AST; ошибка не выглядит отсутствием атрибутов."""
        try:
            content = _read_regular_file(self.root, _relative(self.root, filename))
            content.decode("utf-8")
        except (OSError, SourceError, UnicodeDecodeError):
            self.warn(f"Исходник {filename} недоступен для capture атрибутов.")
            return None
        if self.bytes + len(content) > MAX_BYTES:
            self.warn("Достигнут общий бюджет bytes атрибутов.")
            return None
        self.bytes += len(content)
        self.files += 1
        source_hash = hashlib.sha256(content).hexdigest()
        self.fingerprint.update(filename.encode())
        self.fingerprint.update(bytes.fromhex(source_hash))
        tree = parser.parse(content)
        ast_count = 0
        self.spans = {}
        for ast in walk(tree.root_node):
            ast_count += 1
            if self.ast_nodes + ast_count > MAX_AST_NODES:
                self.warn("Достигнут общий бюджет AST атрибутов.")
                return None
            if ast.type == "attribute":
                location = ast.start_point.row + 1, ast.start_point.column + 1
                digest = hashlib.sha256(content[ast.start_byte:ast.end_byte]).hexdigest()
                self.spans[location] = ast.start_byte, ast.end_byte, digest
        self.ast_nodes += ast_count
        if tree.root_node.has_error:
            self.warn(f"Неполный C# AST: {filename}.")
        return content, tree.root_node, source_hash

    def capture_groups(self, facts, entry, group_owners, entry_ids, raw_nodes, source_hash):
        """Публикует occurrence один раз; contextual groups сохраняют собственные scope owners."""
        ast = entry["ast"]
        for group in facts["groups"]:
            if group["scope"] == "declaration":
                group["name"] = entry["name"]
                if ast.type == "parameter":
                    group["scope"] = "parameter"
            owner_id = group_owners.get(_group_key(group))
            if group["scope"] == "declaration":
                owner_id = entry["id"]
            declaration_id = entry["id"] if group["scope"] == "parameter" else None
            if ast.type == "parameter" and group["scope"] == "parameter":
                parent = ast.parent
                while parent is not None and parent.type not in _DECLARATIONS:
                    parent = parent.parent
                declaration_id = entry_ids.get(_key(parent)) if parent is not None else None
            if group["scope"] == "file" and group["attributes"]:
                owner_id = self.file_owner(group["file"], raw_nodes)
            group.update(ownerNodeId=owner_id, declarationNodeId=declaration_id, attrNodeIds=[])
            for attribute in group["attributes"]:
                attribute_owner_id = owner_id
                # A record's property:/field: target does not prove a generated raw member.
                if group["scope"] == "parameter" and attribute["target"] not in {None, "param"}:
                    attribute_owner_id = None
                identifier = self.occurrence(attribute, group, attribute_owner_id, declaration_id, source_hash)
                if identifier is None:
                    facts.update(status="partial", truncated=True)
                    break
                attribute["attributeNodeId"] = identifier
                group["attrNodeIds"].append(identifier)
                if attribute_owner_id:
                    self.edge(attribute_owner_id, identifier, "has_attribute", group["scope"])
                elif declaration_id:
                    self.edge(declaration_id, identifier, "has_parameter_attribute", "parameter", declaration_id)
        self.truncated |= facts["truncated"]

    def capture_globals(self, filename, tree, content, source_hash, raw_nodes):
        """Assembly/module публикуются и для графа, содержащего только raw file node."""
        lists = [child for child in tree.named_children if child.type == "global_attribute"]
        if not lists:
            return
        collector = _Collector(content, filename)
        collector.group("file", tree, lists, name=filename)
        group = collector.result["groups"][0]
        owner_id = self.file_owner(filename, raw_nodes)
        group.update(ownerNodeId=owner_id, declarationNodeId=None, attrNodeIds=[])
        for attribute in group["attributes"]:
            identifier = self.occurrence(attribute, group, owner_id, None, source_hash)
            if identifier is None:
                collector.result.update(status="partial", truncated=True)
                break
            attribute["attributeNodeId"] = identifier
            group["attrNodeIds"].append(identifier)
            if owner_id:
                self.edge(owner_id, identifier, "has_attribute", "file")
        collector.result.update(sourceKind="snapshot-captured-source", sourceHash=source_hash, warning=CAPTURE_WARNING)
        if tree.has_error:
            collector.result.update(status="partial", truncated=True)
        for node in raw_nodes:
            if node["id"] == owner_id:
                node["attributeFacts"] = collector.result
        self.truncated |= collector.result["truncated"]

    def capture_source_only(self, filename, tree, content, source_hash):
        """Unsupported owners сохраняют source-only occurrences вместо молчаливой потери атрибутов."""
        published = set()
        for node in self.attributes.values():
            if node["file"] == filename:
                published.add(node.get("startByte"))
        count = 0
        for ast in walk(tree):
            if ast.type != "attribute_list":
                continue
            collector = _Collector(content, filename)
            attributes = collector.collect_attributes([ast])
            owner = ast.parent
            name = text(owner.child_by_field_name("name"), content)
            group = {
                "scope": "declaration", "name": name, "kind": owner.type,
                "file": filename, "line": owner.start_point.row + 1,
                "endLine": owner.end_point.row + 1, "column": owner.start_point.column + 1,
            }
            for attribute in attributes:
                span = self.spans.get((attribute["line"], attribute["column"]))
                if span is None or span[0] in published:
                    continue
                identifier = self.occurrence(attribute, group, None, None, source_hash)
                if identifier is not None:
                    self.attributes[identifier]["status"] = "unresolved"
                    published.add(span[0])
                    count += 1
            self.truncated |= collector.result["truncated"]
        if count:
            self.warn(f"{filename}: {count} атрибутов сохранены source-only; lexical scope не сопоставлен с raw owner.")

    def capture_file(self, filename, raw_nodes, parser):
        """Связывает AST и raw IDs, затем независимо собирает declaration/context/global facts."""
        loaded = self.load_file(filename, parser)
        if loaded is None:
            return
        content, tree, source_hash = loaded
        for node in raw_nodes:
            node["attributeFacts"] = _empty_facts("unresolved")
        entries = _entries(tree, content)
        _map_owners(entries, raw_nodes, self.owners)
        group_owners = {}
        entry_ids = {}
        for entry in entries:
            ast = entry["ast"]
            entry_ids[_key(ast)] = entry["id"]
            location = ast.start_point.row + 1, ast.start_point.column + 1
            group_owners[(*location, ast.type, entry["name"])] = entry["id"]
            if ast.type == "parameter":
                name_ast = entry["nameAst"]
                location = name_ast.start_point.row + 1, name_ast.start_point.column + 1
                group_owners[(*location, name_ast.type, entry["name"])] = entry["id"]
        raw_by_id = {node["id"]: node for node in raw_nodes}
        nullable_context = project_nullable(self.root, filename)
        nullable_directives = [node for node in walk(tree) if node.type == "preproc_nullable"]
        for entry in entries:
            if self.resolved + self.unresolved >= MAX_OWNERS:
                self.warn("Достигнут бюджет lexical owners атрибутов.")
                break
            if entry["id"]:
                self.resolved += 1
            else:
                self.unresolved += 1
            facts = _lexical_facts(entry["ast"], content, filename)
            facts.update(sourceKind="snapshot-captured-source", sourceHash=source_hash, warning=CAPTURE_WARNING)
            if tree.has_error:
                facts.update(status="partial", truncated=True)
            self.capture_groups(facts, entry, group_owners, entry_ids, raw_nodes, source_hash)
            if entry["id"]:
                raw_by_id[entry["id"]]["attributeFacts"] = facts
                raw_by_id[entry["id"]].update(captured_comments(entry["ast"], content, filename, source_hash))
                field = field_facts(entry["ast"], entry["name"], content, filename, source_hash, nullable_context, nullable_directives)
                if field is not None:
                    # Общий бюджет таблиц уже применён source-structure pass; повторно initializer не читаем.
                    if raw_by_id[entry["id"]].get("staticData"):
                        field["staticData"] = deepcopy(raw_by_id[entry["id"]]["staticData"])
                    raw_by_id[entry["id"]]["fieldFacts"] = field
        self.capture_globals(filename, tree, content, source_hash, raw_nodes)
        if self.resolved + self.unresolved < MAX_OWNERS:
            self.capture_source_only(filename, tree, content, source_hash)
        published = sum(1 for node in self.attributes.values() if node["file"] == filename)
        if published < len(self.spans):
            self.warn(f"Не опубликованы {len(self.spans) - published} атрибутов {filename}: unsupported scope или бюджет capture.")

    def finish(self):
        """Возвращает metadata полноты и бюджетов, даже при отсутствующих исходниках."""
        self.result["metadata"]["attributeGraph"] = {
            "version": MODULE_VERSION, "sourceKind": "snapshot-captured-source",
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "sourceFingerprint": self.fingerprint.hexdigest(),
            "files": self.files, "bytes": self.bytes, "astNodes": self.ast_nodes,
            "occurrences": len(self.attributes), "resolvedOwners": self.resolved,
            "unresolvedOwners": self.unresolved, "truncated": self.truncated,
            "warnings": self.warnings, "warning": CAPTURE_WARNING,
            "limits": {
                "maxFiles": MAX_FILES, "maxBytes": MAX_BYTES, "maxAstNodes": MAX_AST_NODES,
                "maxOccurrences": MAX_OCCURRENCES, "maxOwners": MAX_OWNERS,
            },
        }
        return self.result


def enrich_attribute_graph(graph: dict, root: Path, *, chunk_max_files: int = 32) -> dict:
    """
    Читает целые файлы пакетами с общими владельцами и бюджетом атрибутов.

    chunk_max_files меняет только границы пакетов: raw IDs/calls сохраняются,
    повторный capture заменяет только owned derived facts, а лимиты остаются
    общими для всего снимка.
    """
    from .enrichment_chunks import file_batches
    from tree_sitter import Language, Parser
    import tree_sitter_c_sharp
    capture = _Capture(graph, root)
    parser = Parser(Language(tree_sitter_c_sharp.language()))
    files = sorted(capture.by_file)
    if len(files) > MAX_FILES:
        capture.warn("Достигнут бюджет файлов атрибутов.")
    chunk_count = 0
    for batch in file_batches(files[:MAX_FILES], chunk_max_files):
        chunk_count += 1
        for filename in batch:
            capture.capture_file(filename, capture.by_file[filename], parser)
    result = capture.finish()
    result['metadata']['attributeGraph'].update(executionScope='file-chunks',
                                               chunkCount=chunk_count, chunkMaxFiles=chunk_max_files)
    return result
