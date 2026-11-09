"""Снимок комментариев и документации для локального поиска; исходный код не исполняется."""

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from .source_viewer import MAX_SOURCE_BYTES, SourceError, _source_path, read_node_source

SCHEMA_VERSION = 2
EXTRACTOR_VERSION = "documentation-ast-v2-documents"
MAX_CHUNKS = 6000
MAX_FILES = 1500
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_CHUNK_CHARS = 600
CHUNK_OVERLAP_CHARS = 80
MAX_RECORD_CHARS = 32000
MAX_NODE_IDS = 100
MAX_AST_NODES = 200000
MAX_ANCESTORS = 128
MAX_CATALOGUE_FILES = 100
MAX_CATALOGUE_ENTRIES = 4000
MAX_CATALOGUE_DEPTH = 4
MAX_WARNINGS = 50
MAX_DOCUMENTS = 2000
MAX_DOCUMENT_CHARS = 128000
MAX_DOCUMENT_BYTES = 512 * 1024
MAX_DOCUMENT_TOTAL_BYTES = 16 * 1024 * 1024
MAX_SOURCE_LINKS = 40
_MARKDOWN = {".md", ".markdown"}
_DECLARATIONS = {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration",
                 "enum_declaration", "delegate_declaration", "method_declaration", "constructor_declaration",
                 "destructor_declaration", "local_function_statement", "property_declaration",
                 "event_declaration", "indexer_declaration", "field_declaration", "event_field_declaration",
                 "enum_member_declaration", "lambda_expression", "anonymous_method_expression"}


def _markdown_hrefs(value: str):
    """Ограниченный inline-link синтаксис: code examples и images не связываются с кодом."""
    fence = None
    for line_number, line in enumerate(value.splitlines(), start=1):
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        plain = re.sub(r"`[^`]*`", "", line)
        for match in re.finditer(r"(?<!!)\[[^\]\n]{0,200}\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)", plain):
            yield match[1], line_number


def _line(value) -> int | None:
    """Оставляет только подтверждённый положительный номер строки снимка."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _name(node, content: bytes) -> str:
    """Читает имя AST-объявления без разрешения типов и перегрузок."""
    child = node.child_by_field_name("name")
    return content[child.start_byte:child.end_byte].decode("utf-8").removeprefix("@") if child is not None else ""


def _comment_text(raw: str, xml: bool) -> str:
    """Убирает маркеры комментария; безопасный XML превращает в подписанные поля документации."""
    lines = []
    if not xml:
        raw = raw.removeprefix("/*").removesuffix("*/")
    for line in raw.splitlines():
        value = line.strip()
        if xml and value.startswith("///"):
            value = value[3:].strip()
        elif value.startswith("//"):
            value = value[2:].strip()
        elif value.startswith("*"):
            value = value[1:].strip()
        lines.append(value)
    text = "\n".join(lines).strip()
    if not xml or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        return text
    try:
        root = ET.fromstring("<documentation>" + text + "</documentation>")
    except ET.ParseError:
        return text
    fields = []
    for child in root:
        value = " ".join(" ".join(child.itertext()).split())
        label = child.tag + (" " + child.attrib["name"] if "name" in child.attrib else "")
        if value:
            fields.append(f"{label}: {value}")
    return "\n".join(fields) or text


class _Capture:
    """Владеет общими бюджетами, безопасным чтением и детерминированной сборкой одного корпуса."""

    def __init__(self, root: Path, graph: dict, snapshot_id: str):
        """Фиксирует raw ID снимка; коллекции графа и файлы проекта остаются неизменными."""
        self.root, self.graph, self.snapshot_id = root.resolve(), graph, snapshot_id
        self.nodes = {node["id"]: node for node in graph.get("nodes", []) if isinstance(node.get("id"), str)}
        self.chunks, self.seen, self.warnings = [], set(), []
        self.full_documents = []
        self.document_ids = set()
        self.document_bytes = 0
        self.captured_at = datetime.now(timezone.utc).isoformat()
        self.truncated = False
        self.consumed_bytes = 0
        self.canonical_paths = {}
        self.stats = {"candidateFiles": 0, "readFiles": 0, "readBytes": 0, "skippedFiles": 0,
                      "duplicateFiles": 0, "astFiles": 0, "comments": 0, "markdownFiles": 0,
                      "snapshotRecords": 0, "ambiguousOwners": 0, "chunks": 0}
        self.documents, self.rationale_targets = defaultdict(set), defaultdict(set)
        self.node_paths = defaultdict(list)
        for node in self.nodes.values():
            filename = node.get("file") or node.get("source_file")
            if isinstance(filename, str) and not Path(filename).is_absolute() and ".." not in Path(filename).parts:
                self.node_paths[Path(filename).as_posix()].append(node["id"])
        for edge in graph.get("edges", []):
            source, target = edge.get("source"), edge.get("target")
            if source not in self.nodes or target not in self.nodes:
                continue
            if edge.get("relation") == "documents":
                self.documents[source].add(target)
            elif edge.get("relation") == "rationale_for":
                self.rationale_targets[source].add(target)
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_c_sharp
            self.parser = Parser(Language(tree_sitter_c_sharp.language()))
        except ImportError:
            self.parser = None
            self.warn("Локальная грамматика C# недоступна; AST-комментарии не захвачены.")

    def warn(self, message: str, *, limited: bool = True) -> None:
        """Ограничивает сообщения и явно снижает полноту при недоступном источнике или лимите."""
        self.truncated |= limited
        if len(self.warnings) < MAX_WARNINGS and message[:400] not in self.warnings:
            self.warnings.append(message[:400])

    def add(self, text: str, *, kind: str, file: str, line: int | None, end_line: int | None,
            source_hash: str, node_ids: list[str], association: str, source_kind: str,
            truncated: bool = False, exact_lines: bool = False) -> None:
        """Делит только текст документа; каждый фрагмент сохраняет источник и доказанный scope.

        600 символов не гарантируют 128 токенов MiniLM: локальный индекс обязан проверить
        tokenizer и разделить фрагмент дополнительно. Для очищенного XML/comment диапазон
        относится ко всему исходному блоку, а не к выдуманным строкам очищенного текста.
        """
        identities = sorted({node_id for node_id in node_ids if node_id in self.nodes})
        truncated |= len(identities) > MAX_NODE_IDS
        identities = identities[:MAX_NODE_IDS]
        document = self.add_document(text, kind, file, line, end_line, source_hash,
                                     identities, association, source_kind, truncated, exact_lines)
        if document is None:
            return
        original_length = len(text)
        text = document["text"][:MAX_RECORD_CHARS]
        truncated |= original_length > len(text)
        document["embeddingTruncated"] = truncated
        self.truncated |= truncated
        start = 0
        while start < len(text):
            end = min(start + MAX_CHUNK_CHARS, len(text))
            chunk_text = text[start:end]
            if chunk_text.strip():
                chunk_line, chunk_end = line, end_line
                if exact_lines and line is not None:
                    chunk_line = line + text[:start].count("\n")
                    chunk_end = chunk_line + chunk_text.count("\n")
                signature = [self.snapshot_id, source_kind, file, chunk_line, chunk_end,
                             source_hash, identities, association, kind, start, chunk_text]
                digest = hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode("utf-8")).hexdigest()
                if digest not in self.seen:
                    if len(self.chunks) >= MAX_CHUNKS:
                        self.warn("Достигнут лимит фрагментов документации.")
                        return
                    self.seen.add(digest)
                    self.chunks.append({"id": "doc:" + digest[:24], "text": chunk_text, "kind": kind,
                                        "file": file, "line": chunk_line, "endLine": chunk_end,
                                        "sourceHash": source_hash, "nodeIds": identities,
                                        "association": association, "sourceKind": source_kind,
                                        "documentId": document["id"], "documentOffsetStart": start,
                                        "documentOffsetEnd": end,
                                        "rangeKind": "text-slice" if exact_lines else "source-block",
                                        "truncated": truncated})
            if end == len(text):
                break
            start = max(start + 1, end - CHUNK_OVERLAP_CHARS)

    def source_links(self, document_file: str, text: str) -> list[dict]:
        """Только явные Markdown href; упоминание имени/пути не становится связью с символом."""
        links = []
        for href, document_line in _markdown_hrefs(text):
            try:
                parsed = urlsplit(href)
            except ValueError:
                continue
            if parsed.scheme or parsed.netloc:
                continue
            path = unquote(parsed.path)
            if not path or "\\" in path or any(ord(char) < 32 for char in path):
                continue
            filename = posixpath.normpath(posixpath.join(posixpath.dirname(document_file), path))
            if path.startswith("/"):
                filename = posixpath.normpath(path.lstrip("/"))
            if filename.startswith("../") or filename in {"..", "."}:
                continue
            line_match = re.fullmatch(r"L(\d+)(?:-L?\d+)?", parsed.fragment)
            all_node_ids = sorted(self.node_paths.get(filename, []))
            link = {
                "file": filename,
                "line": int(line_match[1]) if line_match else None,
                "scope": "file",
                "nodeIds": all_node_ids[:MAX_NODE_IDS],
                "association": "source-link",
                "documentLine": document_line,
            }
            if len(all_node_ids) > MAX_NODE_IDS:
                link["nodeIdsTruncated"] = True
                self.warn("Привязки явной ссылки к узлам файла усечены по лимиту node IDs.")
            links.append(link)
            if len(links) >= MAX_SOURCE_LINKS:
                self.warn("Не все явные ссылки документа сохранены из-за лимита source links.")
                break
        return links

    def add_document(self, text, kind, file, line, end_line, source_hash, identities,
                     association, source_kind, truncated, exact_lines):
        """Сохраняет bounded полный текст до chunking; его можно читать без файлов проекта."""
        if not text.strip():
            return None
        captured = text[:MAX_DOCUMENT_CHARS]
        encoded = captured.encode("utf-8")[:MAX_DOCUMENT_BYTES]
        captured = encoded.decode("utf-8", errors="ignore")
        truncated |= len(captured) != len(text)
        signature = [source_kind, file, line, end_line, source_hash, identities, association, kind, captured]
        identifier = "document:" + hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()[:32]
        if identifier in self.document_ids:
            return next(document for document in self.full_documents if document["id"] == identifier)
        if len(self.full_documents) >= MAX_DOCUMENTS or self.document_bytes + len(encoded) > MAX_DOCUMENT_TOTAL_BYTES:
            self.warn("Достигнут бюджет сохранённых полных документов.")
            return None
        format_name = "markdown" if exact_lines and Path(file).suffix.lower() in _MARKDOWN else "text"
        title = next((value.strip().lstrip("#").strip() for value in captured.splitlines() if value.strip()), Path(file).name)[:200]
        document = {"id": identifier, "title": title, "text": captured, "kind": kind,
                    "format": format_name, "file": file, "line": line, "endLine": end_line,
                    "sourceHash": source_hash, "sourceKind": source_kind, "capturedAt": self.captured_at,
                    "nodeIds": identities, "association": association, "truncated": truncated,
                    "sourceLinks": self.source_links(file, captured) if format_name == "markdown" else []}
        if association == "file":
            file_nodes = sorted(set(self.node_paths.get(file, [])))
            document["fileNodeIds"] = file_nodes[:MAX_NODE_IDS]
            document["fileNodeIdsTruncated"] = len(file_nodes) > MAX_NODE_IDS
            if document["fileNodeIdsTruncated"]:
                self.warn("Не все file-scope привязки документа сохранены из-за лимита raw IDs.")
        self.document_bytes += len(encoded)
        self.document_ids.add(identifier)
        self.full_documents.append(document)
        self.truncated |= truncated
        return document

    def catalogue(self) -> list[str]:
        """Находит root README и Markdown в docs; ссылки и вложения глубже лимита не обходятся."""
        found, inspected = [], 0
        pending = [(self.root, 0)]
        while pending:
            directory, depth = pending.pop(0)
            try:
                with os.scandir(directory) as stream:
                    entries = []
                    for entry in stream:
                        inspected += 1
                        if inspected > MAX_CATALOGUE_ENTRIES:
                            self.warn("Каталог документации усечён по числу просмотренных записей.")
                            return sorted(found)
                        entries.append(entry)
                for entry in sorted(entries, key=lambda item: item.name):
                    if entry.is_symlink():
                        continue
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False) and (depth > 0 or entry.name == "docs"):
                        if depth < MAX_CATALOGUE_DEPTH:
                            pending.append((path, depth + 1))
                        else:
                            self.warn("Каталог docs усечён по глубине обхода.")
                    elif entry.is_file(follow_symlinks=False) and path.suffix.lower() in _MARKDOWN:
                        if depth == 0 and not entry.name.lower().startswith("readme"):
                            continue
                        if len(found) >= MAX_CATALOGUE_FILES:
                            self.warn("Достигнут лимит Markdown-файлов каталога документации.")
                            return sorted(found)
                        found.append(path.relative_to(self.root).as_posix())
            except OSError:
                self.warn("Каталог документации недоступен.")
        return sorted(found)

    def files(self) -> dict[str, list[dict]]:
        """Объединяет aliases до чтения; каждый канонический файл читается не более одного раза."""
        candidates = defaultdict(list)
        for node in self.nodes.values():
            filename = node.get("file") or node.get("source_file")
            if not isinstance(filename, str) or node.get("kind") == "external":
                continue
            if Path(filename).suffix.lower() in _MARKDOWN | {".cs"}:
                candidates[filename].append(node)
        for filename in self.catalogue():
            candidates.setdefault(filename, [])
        self.stats["candidateFiles"] = len(candidates)
        canonical = {}
        ordered = sorted(candidates, key=lambda filename: (Path(filename).suffix.lower() != ".cs", filename))
        for index, filename in enumerate(ordered):
            if index >= MAX_FILES:
                self.stats["skippedFiles"] += len(ordered) - index
                self.warn("Достигнут лимит файлов корпуса документации.")
                break
            try:
                _, relative = _source_path(self.root, {"file": filename})
            except SourceError:
                self.stats["skippedFiles"] += 1
                self.warn("Исходный файл документации отсутствует или его путь недопустим.")
                continue
            key = relative.as_posix()
            self.canonical_paths[filename] = key
            if key in canonical:
                self.stats["duplicateFiles"] += 1
            canonical.setdefault(key, []).extend(candidates[filename])
        for filename, nodes in canonical.items():
            self.node_paths[filename] = sorted({node["id"] for node in nodes})
        return canonical

    def owners(self, declarations: list, nodes: list[dict], content: bytes) -> dict[int, list[str]]:
        """Сопоставляет имя и при неоднозначности диапазон; чужие overload/соседи не угадываются."""
        by_name, owners = defaultdict(list), defaultdict(list)
        for declaration in declarations:
            name = _name(declaration, content)
            if name:
                by_name[name].append(declaration)
        for node in nodes:
            name = str(node.get("label") or "").strip(".").split("(", 1)[0].split("<", 1)[0].removeprefix("@")
            matches = by_name.get(name, [])
            line = _line(node.get("line"))
            if len(matches) > 1 and line is not None:
                matches = [declaration for declaration in matches
                           if declaration.start_point.row + 1 <= line <= declaration.end_point.row + 1]
            if len(matches) == 1:
                owners[matches[0].start_byte].append(node["id"])
            elif matches:
                self.stats["ambiguousOwners"] += 1
        return owners

    def csharp(self, filename: str, text: str, nodes: list[dict], source_hash: str) -> bool:
        """Обходит AST один раз и сохраняет только comment nodes, включая XML перед декларацией."""
        if self.parser is None:
            return False
        content = text.encode("utf-8")
        tree = self.parser.parse(content)
        declarations, comments, pending, visited = [], [], [tree.root_node], 0
        while pending:
            node = pending.pop()
            visited += 1
            if visited > MAX_AST_NODES:
                self.warn("Обход C# AST усечён по числу узлов.")
                break
            if node.type in _DECLARATIONS:
                declarations.append(node)
            elif node.type == "comment":
                comments.append(node)
            pending.extend(reversed(node.named_children))
        self.stats["astFiles"] += 1
        self.stats["comments"] += len(comments)
        if tree.root_node.has_error:
            self.warn("C# AST содержит ошибки; полнота комментариев не гарантируется.")
        owners = self.owners(declarations, nodes, content)
        leading = {}
        for declaration in declarations:
            previous, row = declaration.prev_named_sibling, declaration.start_point.row
            while previous is not None and previous.type == "comment" and previous.end_point.row >= row - 1:
                leading[previous.start_byte] = declaration
                row, previous = previous.start_point.row, previous.prev_named_sibling
        groups = []
        for comment in comments:
            owner = leading.get(comment.start_byte)
            if owner is None:
                owner, ancestors = comment.parent, 0
                while owner is not None and owner.type not in _DECLARATIONS:
                    ancestors += 1
                    if ancestors > MAX_ANCESTORS:
                        owner = None
                        self.warn("Поиск владельца комментария усечён по глубине AST.")
                        break
                    owner = owner.parent
            ids = owners.get(owner.start_byte, []) if owner is not None else []
            raw = content[comment.start_byte:comment.end_byte].decode("utf-8")
            xml = raw.lstrip().startswith("///")
            key = (tuple(sorted(ids)), owner.start_byte if owner is not None else None, xml)
            if groups and groups[-1][0] == key and not content[groups[-1][2]:comment.start_byte].strip():
                groups[-1][2] = comment.end_byte
                groups[-1][4] = comment.end_point.row + 1
            else:
                groups.append([key, comment.start_byte, comment.end_byte,
                               comment.start_point.row + 1, comment.end_point.row + 1])
        for index, (key, start, end, line, end_line) in enumerate(groups):
            raw = content[start:end].decode("utf-8")
            limited = len(raw) > MAX_RECORD_CHARS
            self.add(_comment_text(raw[:MAX_RECORD_CHARS], key[2]), kind="documentation" if key[2] else "comment",
                     file=filename, line=line, end_line=end_line, source_hash=source_hash,
                     node_ids=list(key[0]), association="declaration" if key[0] else "file",
                     source_kind="working-tree", truncated=limited)
            if len(self.chunks) >= MAX_CHUNKS:
                if index + 1 < len(groups):
                    self.warn("Не все C#-комментарии захвачены из-за лимита фрагментов.")
                break
        return True

    def capture_files(self) -> set[str]:
        """Захватывает файл безопасным reader; резерв 1 МиБ сохраняет строгий общий бюджет чтения."""
        captured = set()
        files = self.files()
        for index, (filename, nodes) in enumerate(files.items()):
            if len(self.chunks) >= MAX_CHUNKS:
                self.stats["skippedFiles"] += len(files) - index
                self.warn("Не все файлы обработаны из-за лимита фрагментов.")
                break
            # Reader читает максимум MAX_SOURCE_BYTES, включая BOM. Резерв до чтения
            # не допускает физического overshoot; маленький остаток бюджета не используется.
            if self.consumed_bytes + MAX_SOURCE_BYTES > MAX_TOTAL_BYTES:
                self.stats["skippedFiles"] += len(files) - index
                self.warn("Достигнут общий бюджет чтения; остаток меньше максимального файла.")
                break
            try:
                source = read_node_source(self.root, {"file": filename}, self.snapshot_id)
            except SourceError:
                self.stats["skippedFiles"] += 1
                self.warn("Файл документации недоступен, не является UTF-8-текстом или превышает 1 МиБ.")
                # Неизвестный прочитанный объём консервативно занимает один максимальный файл.
                self.consumed_bytes += MAX_SOURCE_BYTES
                continue
            text = source["text"]
            size = len(text.encode("utf-8"))
            self.consumed_bytes += min(MAX_SOURCE_BYTES, size + 3)
            if size > MAX_FILE_BYTES:
                self.stats["skippedFiles"] += 1
                self.warn("Файл превышает лимит документации.")
                continue
            self.stats["readFiles"] += 1
            self.stats["readBytes"] += size
            source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if Path(filename).suffix.lower() == ".cs":
                if self.csharp(filename, text, nodes, source_hash):
                    captured.add(filename)
            else:
                ids = {node["id"] for node in nodes}
                related = set(ids)
                for node_id in ids:
                    related.update(self.documents[node_id])
                self.add(text, kind="documentation", file=filename, line=1, end_line=source["totalLines"],
                         source_hash=source_hash, node_ids=list(related),
                         association="documents" if related != ids else "file", source_kind="working-tree",
                         exact_lines=True)
                self.stats["markdownFiles"] += 1
                captured.add(filename)
        return captured

    def snapshot_text(self, captured: set[str]) -> None:
        """Использует только сохранённые rationale и documents descriptions, не произвольные code bodies."""
        for node_id, node in sorted(self.nodes.items()):
            filename = str(node.get("file") or node.get("source_file") or "")
            if self.canonical_paths.get(filename, filename) in captured:
                continue
            records = []
            rationale = node.get("rationale")
            if isinstance(rationale, str) and rationale.strip():
                records.append((rationale, [node_id], "snapshot-rationale"))
            if node_id in self.rationale_targets or node.get("file_type") == "rationale":
                text = node.get("label") or node.get("description")
                if isinstance(text, str) and text.strip():
                    records.append((text, [node_id, *self.rationale_targets[node_id]], "snapshot-rationale"))
            description = node.get("description")
            if (node.get("kind") in {"skill", "memory", "note"} or node_id in self.documents):
                if isinstance(description, str) and description.strip():
                    records.append((description, [node_id, *self.documents[node_id]], "documents"))
            for text, ids, association in records:
                if len(self.chunks) >= MAX_CHUNKS:
                    self.warn("Тексты снимка усечены по лимиту фрагментов.")
                    return
                limited = len(text) > MAX_RECORD_CHARS
                text = text[:MAX_RECORD_CHARS]
                source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                self.add(text, kind="documentation", file=filename, line=_line(node.get("line")),
                         end_line=_line(node.get("line")), source_hash=source_hash, node_ids=ids,
                         association=association, source_kind="graph-snapshot", truncated=limited)
                self.stats["snapshotRecords"] += 1


def capture_documentation(root: Path, graph: dict, snapshot_id: str) -> dict:
    """Создаёт ограниченный корпус при сборке индекса; запросы поиска не читают рабочую копию.

    Источники смешаны только с явной provenance: свежие C# AST/Markdown и сохранённый
    rationale/описания снимка. Не строит рёбра, не связывает упоминания с raw ID и не
    исполняет исходники. Точная токенизация MiniLM выполняется отдельно локальным индексом.
    """
    capture = _Capture(root, graph, snapshot_id)
    captured = capture.capture_files()
    capture.snapshot_text(captured)
    markdown_by_file = {document["file"]: document["id"] for document in capture.full_documents
                        if document["format"] == "markdown"}
    for document in capture.full_documents:
        for link in document["sourceLinks"]:
            if link["file"] in markdown_by_file:
                link["documentId"] = markdown_by_file[link["file"]]
    capture.stats["chunks"] = len(capture.chunks)
    capture.stats["documents"] = len(capture.full_documents)
    capture.stats["documentBytes"] = capture.document_bytes
    capture.stats["reservedReadBytes"] = capture.consumed_bytes
    return {"schemaVersion": SCHEMA_VERSION, "extractorVersion": EXTRACTOR_VERSION,
            "sourceKind": "captured-working-tree-and-snapshot", "snapshotId": snapshot_id,
            "capturedAt": capture.captured_at, "chunks": capture.chunks, "documents": capture.full_documents,
            "truncated": capture.truncated, "stats": capture.stats, "warnings": capture.warnings,
            "limits": {"maxChunks": MAX_CHUNKS, "maxFiles": MAX_FILES, "maxFileBytes": MAX_FILE_BYTES,
                       "maxTotalBytes": MAX_TOTAL_BYTES, "chunkMaxChars": MAX_CHUNK_CHARS,
                       "chunkOverlapChars": CHUNK_OVERLAP_CHARS, "maxRecordChars": MAX_RECORD_CHARS,
                       "maxNodeIdsPerChunk": MAX_NODE_IDS, "maxAstNodesPerFile": MAX_AST_NODES,
                       "maxAncestors": MAX_ANCESTORS, "maxCatalogueFiles": MAX_CATALOGUE_FILES,
                       "maxDocuments": MAX_DOCUMENTS, "maxDocumentChars": MAX_DOCUMENT_CHARS,
                       "maxDocumentBytes": MAX_DOCUMENT_BYTES, "maxDocumentTotalBytes": MAX_DOCUMENT_TOTAL_BYTES,
                       "maxCatalogueEntries": MAX_CATALOGUE_ENTRIES, "maxCatalogueDepth": MAX_CATALOGUE_DEPTH,
                       "tokenLimitGuaranteed": False}}
