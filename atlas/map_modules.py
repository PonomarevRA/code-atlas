"""Подключаемые модули карты: навыки и записи agent-memory рядом с кодом."""

from pathlib import Path
from posixpath import normpath


SKILL_ROOTS = ("skills", ".cursor/skills", ".agents/skills", ".claude/skills")
_DESCRIPTION_LIMIT = 1200
_EDGE_LIMIT = 8
_ERROR_LIMIT = 4000


def attach_map_modules(graph: dict, root: Path) -> None:
    """Добавляет узлы модулей после Graphify и до группировки.

    Сбой одного модуля остаётся в metadata и не отменяет уже собранный граф кода.
    """
    metadata = graph.setdefault("metadata", {})
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])
    known_ids = {node.get("id") for node in nodes}
    files = _files_by_path(nodes)
    records = []
    for module in _modules():
        try:
            contributed = module["contribute"](root) or {}
            accepted = _accept_nodes(contributed.get("nodes") or [], known_ids, files)
            nodes.extend(accepted)
            accepted_ids = {node["id"] for node in accepted}
            edges.extend(_resolve_edges(contributed.get("edges") or [], accepted_ids, files))
            records.append({"id": module["id"], "label": module["label"], "status": "ok"})
        except Exception as error:
            message = str(error)[-_ERROR_LIMIT:] or type(error).__name__
            records.append({"id": module["id"], "label": module["label"], "status": "error", "error": message})
    metadata["mapModules"] = records
    if "nodeCount" in metadata:
        metadata["nodeCount"] = len(nodes)
    if "edgeCount" in metadata:
        metadata["edgeCount"] = len(edges)


def contribute_skills(root: Path) -> dict:
    """Собирает SKILL.md из известных каталогов навыков. Одинаковое имя даёт один узел."""
    found = []
    for base in SKILL_ROOTS:
        directory = root / base
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("SKILL.md")):
            if path.is_file():
                found.append(path)
    groups: dict[str, list[tuple[str, dict]]] = {}
    order = []
    for path in found:
        relative = path.relative_to(root).as_posix()
        matter = _front_matter(path.read_text(encoding="utf-8"))
        name = matter.get("name") or _skill_label(path)
        label = name
        description = matter.get("description") or ""
        key = matter.get("name") or relative
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append((relative, {"label": label, "description": description}))
    nodes = []
    edges = []
    for key in order:
        copies = groups[key]
        copies.sort(key=lambda item: (_skill_rank(item[0]), item[0]))
        primary, primary_fields = copies[0]
        description = primary_fields["description"]
        others = [path for path, _ in copies[1:]]
        if others:
            suffix = "; " + "; ".join(others)
            description = f"{description}{suffix}".strip()
        node_id = f"skill:{primary}"
        nodes.append(_node(node_id, primary_fields["label"], "skill", primary, description[:_DESCRIPTION_LIMIT]))
        for token in _path_tokens(primary_fields["description"]):
            edges.append({"source": node_id, "target": token, "relation": "documents"})
    return {"nodes": nodes, "edges": edges}


def contribute_memory(root: Path) -> dict:
    """Берёт только object-файлы agent-memory. Реестры в карту не попадают."""
    directory = root / "docs" / "agent-memory"
    nodes = []
    edges = []
    if not directory.is_dir():
        return {"nodes": nodes, "edges": edges}
    for path in sorted(directory.rglob("*.a2a.yaml")):
        if not path.is_file() or path.name == "registry.a2a.yaml":
            continue
        document = _parse_yaml(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not _is_memory_object(document):
            continue
        object_id = document.get("id")
        if not isinstance(object_id, str) or not object_id.strip():
            continue
        title = document.get("title")
        label = title.strip() if isinstance(title, str) and title.strip() else object_id
        summary = document.get("summary") if isinstance(document.get("summary"), str) else ""
        payload = " ".join(_strings(document.get("payload")))
        description = " ".join(part for part in (summary.strip(), payload.strip()) if part)
        relative = path.relative_to(root).as_posix()
        node_id = f"memory:{object_id}"
        nodes.append(_node(node_id, label, "memory", relative, description[:_DESCRIPTION_LIMIT]))
        for ref in _source_refs(document.get("sources")):
            edges.append({"source": node_id, "target": ref, "relation": "documents"})
    return {"nodes": nodes, "edges": edges}


def _modules() -> tuple:
    """Подставляет функции после их объявления, чтобы список оставался единственной точкой регистрации."""
    return (
        {"id": "skills", "label": "skills", "contribute": contribute_skills},
        {"id": "agent-memory", "label": "agent-memory", "contribute": contribute_memory},
    )


def _node(node_id: str, label: str, kind: str, file: str, description: str) -> dict:
    return {
        "id": node_id,
        "label": label,
        "kind": kind,
        "file": file,
        "line": None,
        "community": None,
        "description": description,
    }


def _accept_nodes(candidates: list, known_ids: set, files: dict) -> list:
    """Пропускает узел без id и узел, который уже есть в графе кода."""
    accepted = []
    for node in candidates:
        node_id = node.get("id")
        if not node_id or node_id in known_ids:
            continue
        known_ids.add(node_id)
        accepted.append(node)
        file = _normalize_path(node.get("file") or "")
        if file and file not in files:
            files[file] = node_id
    return accepted


def _resolve_edges(candidates: list, sources: set, files: dict) -> list:
    """Оставляет не больше восьми связей на узел и только к уже существующим файлам."""
    resolved = []
    used: dict[str, set] = {}
    for edge in candidates:
        source = edge.get("source")
        if source not in sources:
            continue
        target_path = _normalize_path(str(edge.get("target") or ""))
        target = files.get(target_path)
        if not target or target == source:
            continue
        targets = used.setdefault(source, set())
        if target in targets or len(targets) >= _EDGE_LIMIT:
            continue
        targets.add(target)
        resolved.append({"source": source, "target": target, "relation": "documents"})
    return resolved


def _files_by_path(nodes: list) -> dict:
    files = {}
    for node in nodes:
        file = _normalize_path(str(node.get("file") or ""))
        if file and file not in files:
            files[file] = node.get("id")
    return files


def _normalize_path(value: str) -> str:
    """Приводит ссылку к repo-relative пути и отбрасывает выход за корень."""
    text = value.strip().strip("`\"'")
    text = text.replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    text = text.rstrip(".,;:)]")
    if not text or ".." in text.split("/"):
        return ""
    return normpath(text)


def _skill_rank(relative: str) -> int:
    """Каталог skills важнее копий, затем порядок .cursor, .agents, .claude."""
    for index, base in enumerate(SKILL_ROOTS):
        if relative == base or relative.startswith(base + "/"):
            return index
    return len(SKILL_ROOTS)


def _skill_label(path: Path) -> str:
    """Имя каталога навыка понятнее, чем общий stem файла SKILL.md."""
    if path.name == "SKILL.md" and path.parent.name:
        return path.parent.name
    return path.stem


def _front_matter(text: str) -> dict:
    """Читает name и description из первой шапки Markdown."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    block = text[4:end]
    parsed = _parse_yaml(block)
    if not isinstance(parsed, dict):
        return {}
    result = {}
    for key in ("name", "description"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def _path_tokens(text: str) -> list[str]:
    """Достаёт из описания токены, похожие на путь к файлу репозитория."""
    tokens = []
    for raw in text.split():
        token = _normalize_path(raw)
        if token and ("/" in token or "." in Path(token).suffix):
            tokens.append(token)
    return tokens


def _is_memory_object(document: dict) -> bool:
    if document.get("schema") == "agent-memory-object/v1":
        return True
    return "kind" in document


def _source_refs(sources) -> list[str]:
    if not isinstance(sources, list):
        return []
    refs = []
    for item in sources:
        if isinstance(item, dict) and isinstance(item.get("ref"), str):
            refs.append(item["ref"])
    return refs


def _strings(value) -> list[str]:
    """Собирает текстовые значения payload, без ключей, чисел и логических флагов."""
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        found = []
        for item in value.values():
            found.extend(_strings(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_strings(item))
        return found
    return []


def _parse_yaml(text: str):
    """Читает подмножество YAML из навыков и agent-memory без внешней библиотеки."""
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append((len(raw) - len(raw.lstrip(" ")), stripped))
    if not lines:
        return {}
    value, index = _parse_block(lines, 0, lines[0][0])
    if index < len(lines):
        raise ValueError("не удалось разобрать YAML модуля карты")
    return value


def _parse_block(lines, index, indent):
    """Разбирает словарь или список на одном уровне отступа."""
    if index >= len(lines) or lines[index][0] < indent:
        return {}, index
    if lines[index][1].startswith("- "):
        return _parse_list(lines, index, indent)
    result = {}
    while index < len(lines) and lines[index][0] == indent and not lines[index][1].startswith("- "):
        key, _, raw_value = lines[index][1].partition(":")
        key = key.strip()
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            result[key] = _scalar(raw_value)
            continue
        if index < len(lines) and lines[index][0] > indent:
            child, index = _parse_block(lines, index, lines[index][0])
            result[key] = child
        else:
            result[key] = ""
    return result, index


def _parse_list(lines, index, indent):
    """Разбирает элементы списка: строку или набор полей одного объекта."""
    items = []
    while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
        content = lines[index][1][2:].strip()
        index += 1
        if content.startswith(("'", '"')) or ":" not in content:
            items.append(_scalar(content))
            continue
        key, _, raw_value = content.partition(":")
        item = {}
        raw_value = raw_value.strip()
        if raw_value:
            item[key.strip()] = _scalar(raw_value)
        elif index < len(lines) and lines[index][0] > indent:
            child, index = _parse_block(lines, index, lines[index][0])
            item[key.strip()] = child
        else:
            item[key.strip()] = ""
        while index < len(lines) and lines[index][0] > indent and not lines[index][1].startswith("- "):
            nested_key, _, nested_value = lines[index][1].partition(":")
            nested_indent = lines[index][0]
            nested_value = nested_value.strip()
            index += 1
            if nested_value:
                item[nested_key.strip()] = _scalar(nested_value)
            elif index < len(lines) and lines[index][0] > nested_indent:
                child, index = _parse_block(lines, index, lines[index][0])
                item[nested_key.strip()] = child
            else:
                item[nested_key.strip()] = ""
        items.append(item)
    return items, index


def _scalar(value: str):
    """Оставляет текст. Числа и логические значения не попадают в описание."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        body = value[1:-1]
        return body.replace('\\"', '"').replace("\\'", "'")
    if value in {"true", "false", "null", "~"} or _is_number(value):
        return None
    return value


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True
