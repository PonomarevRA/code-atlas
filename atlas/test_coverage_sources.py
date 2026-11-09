"""C# inventory и лексические цели условий: ограниченное чтение без запуска тестов."""
from collections import defaultdict
import hashlib
from pathlib import Path
import re

from .control_flow import analyze_control_flow
from .source_viewer import SourceError, _source_path, read_node_source, MAX_SOURCE_BYTES

METHOD_TYPES = {"method_declaration", "constructor_declaration", "local_function_statement"}
TYPE_TYPES = {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration"}
BRANCH_TYPES = {"if_statement": "if", "switch_statement": "switch", "switch_expression": "switch-expression",
                "conditional_expression": "conditional",
                "for_statement": "for", "foreach_statement": "foreach", "while_statement": "while", "do_statement": "do"}
UNIT_ATTRIBUTES = {"Fact": ("xunit", "fact"), "Theory": ("xunit", "theory"),
                   "Test": ("nunit", "test"), "TestCase": ("nunit", "test-case"),
                   "TestMethod": ("mstest", "test-method"), "DataTestMethod": ("mstest", "data-test-method")}
FRAMEWORK_NAMESPACES = {"xunit": "Xunit", "nunit": "NUnit.Framework", "mstest": "Microsoft.VisualStudio.TestTools.UnitTesting"}


def stable_id(prefix: str, *parts) -> str:
    """Создаёт собственный ID из расположения, не заменяя raw-ID карты."""
    payload = "\0".join(str(part) for part in parts)
    return prefix + ":" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def source_text(node, content: bytes) -> str:
    """Читает буквальный диапазон AST; не вычисляет выражение."""
    return content[node.start_byte:node.end_byte].decode("utf-8") if node is not None else ""


def literal_string(value: str) -> str | None:
    """Распознаёт обычную/verbatim строку; interpolation/вычисленные значения остаются неизвестны."""
    value = value.strip()
    if value.startswith('@"') and value.endswith('"'):
        return value[2:-1].replace('""', '"')
    if not value.startswith('"') or not value.endswith('"'):
        return None
    core = value[1:-1]
    result, index = [], 0
    escapes = {'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}
    while index < len(core):
        char = core[index]
        if char == "\\":
            index += 1
            if index >= len(core) or core[index] not in escapes:
                return None
            char = escapes[core[index]]
        result.append(char)
        index += 1
    return "".join(result)


class SourceBudget:
    """Один safe read на canonical файл; общий бюджет учитывает и неудачные попытки."""
    def __init__(self, root: Path, snapshot_id: str, limits: dict):
        self.root, self.snapshot_id, self.limits = root, snapshot_id, limits
        self.files, self.bytes, self.warnings = {}, 0, []
        self.truncated = {"files": False, "bytes": False, "astNodes": False}

    def read(self, filename: str) -> dict | None:
        """Читает только регулярный UTF-8 файл внутри root; резерв 1MiB предотвращает overshoot."""
        try:
            root, relative = _source_path(self.root, {"file": filename})
            key = relative.as_posix()
        except SourceError as error:
            if len(self.warnings) < 100:
                self.warnings.append(f"Файл недоступен: {filename[:200]} ({error.status}).")
            return None
        if key in self.files:
            return self.files[key]
        if len(self.files) >= self.limits["maxSourceFiles"]:
            self.truncated["files"] = True
            return None
        if self.bytes + MAX_SOURCE_BYTES > self.limits["maxSourceBytes"]:
            self.truncated["bytes"] = True
            return None
        self.files[key] = None
        try:
            before = (root / relative).stat()
            source = read_node_source(self.root, {"file": key}, self.snapshot_id)
            after = (root / relative).stat()
        except (SourceError, OSError) as error:
            self.bytes += MAX_SOURCE_BYTES
            self.warnings.append(f"Файл не прочитан: {key[:200]} ({getattr(error, 'status', 404)}).")
            return None
        content = source["text"].encode("utf-8")
        self.bytes += len(content)
        result = {"file": key, "text": source["text"], "content": content,
                  "sourceHash": hashlib.sha256(content).hexdigest(), "mtimeNs": after.st_mtime_ns,
                  "stableRead": (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)}
        self.files[key] = result
        return result


def walk(node, *, skip_nested=False):
    """Итеративно обходит named AST; вложенный метод имеет собственный список условий."""
    pending = [node]
    while pending:
        current = pending.pop()
        if skip_nested and current != node and current.type in METHOD_TYPES | TYPE_TYPES:
            continue
        yield current
        pending.extend(reversed(current.named_children))


def attributes(declaration, content: bytes) -> list[dict]:
    """Извлекает только attribute lists данного объявления, без наследования/reflection."""
    result = []
    for listing in declaration.named_children:
        if listing.type != "attribute_list":
            continue
        for attribute in listing.named_children:
            if attribute.type != "attribute":
                continue
            name = source_text(attribute.child_by_field_name("name"), content)
            args = []
            for child in attribute.named_children:
                if child.type == "attribute_argument_list":
                    args = [source_text(argument, content) for argument in child.named_children if argument.type == "attribute_argument"]
            result.append({"name": name, "shortName": name.rsplit(".", 1)[-1].removesuffix("Attribute"),
                           "arguments": args, "line": attribute.start_point.row + 1})
    return result


def declaration_status(attrs: list[dict], type_resolved=True) -> tuple[str, list[str]]:
    """Skip/Ignore — объявленная конфигурация, не результат запуска; expression не вычисляется."""
    reasons, unknown = [], not type_resolved
    for attr in attrs:
        if attr["shortName"] in {"Ignore", "Explicit"}:
            reasons.append(f"[{attr['shortName']}] объявлен в исходнике.")
            unknown |= attr["shortName"] == "Explicit"
        for argument in attr["arguments"]:
            match = re.match(r"\s*(Skip|Ignore|IgnoreReason|Explicit)\s*=\s*(.*)\Z", argument, re.S)
            if not match:
                continue
            name, value = match.groups()
            literal = literal_string(value)
            if value.strip() == "null" or (name == "Explicit" and value.strip() == "false"):
                continue
            if literal is not None or (name == "Explicit" and value.strip() == "true"):
                reasons.append(f"{name}: {literal if literal is not None else value.strip()}")
            else:
                reasons.append(f"{name}: выражение не вычислено ({value[:200]}).")
                unknown = True
    if reasons:
        return ("unknown" if unknown else "disabled"), reasons
    return ("unknown" if unknown else "enabled"), []


def unit_attributes(attrs: list[dict], local_attributes: set[str], aliases: dict[str, str]) -> list[dict]:
    """Bare known names are hints; qualified custom names cannot masquerade as framework attributes."""
    units = []
    for attr in attrs:
        if attr["name"] in aliases:
            name = aliases[attr["name"]]
            attr = dict(attr, originalName=attr["name"], name=name, shortName=name.rsplit(".", 1)[-1].removesuffix("Attribute"))
        if attr["shortName"] not in UNIT_ATTRIBUTES:
            continue
        name = attr["name"].removeprefix("global::")
        namespace, _, _ = name.rpartition(".")
        expected = FRAMEWORK_NAMESPACES[UNIT_ATTRIBUTES[attr["shortName"]][0]]
        if namespace:
            if namespace == expected:
                units.append(attr)
        elif attr["shortName"] not in local_attributes:
            units.append(attr)
    return units


def skip_attributes(attrs: list[dict], framework: str, imports: set[str], local_attributes: set[str], aliases: dict) -> list[dict]:
    """Only this runner's unit/Ignore/Explicit attributes affect its declared skip configuration."""
    selected = [attr for attr in unit_attributes(attrs, local_attributes, aliases) if UNIT_ATTRIBUTES[attr["shortName"]][0] == framework]
    expected = FRAMEWORK_NAMESPACES[framework]
    for attr in attrs:
        name = aliases.get(attr["name"], attr["name"]).removeprefix("global::")
        namespace, _, short = name.rpartition(".")
        short = (short or name).removesuffix("Attribute")
        if short not in ({"Ignore", "Explicit"} if framework == "nunit" else {"Ignore"} if framework == "mstest" else set()):
            continue
        resolved = namespace == expected if namespace else expected in imports and short not in local_attributes
        if resolved:
            selected.append(dict(attr, shortName=short))
    return selected


def namespace_of(declaration, content: bytes, file_namespace: str) -> str:
    """Сохраняет namespace источника; папка или имя теста его не подменяют."""
    names, current = [], declaration.parent
    while current is not None:
        if current.type == "namespace_declaration":
            names.append(source_text(current.child_by_field_name("name"), content))
        current = current.parent
    return ".".join(reversed(names)) or file_namespace or "<global>"


def raw_line(node: dict) -> int | None:
    """Использует явную строку карты или Lnn; не угадывает метод по имени отчёта."""
    line = node.get("line")
    if isinstance(line, int) and not isinstance(line, bool):
        return line
    match = re.match(r"^L(\d+)", str(node.get("source_location") or ""))
    return int(match[1]) if match else None


def raw_name(node: dict) -> str:
    """Нормализует только синтаксическое имя сохранённого символа для exact AST mapping."""
    return str(node.get("label") or "").split("(", 1)[0].rsplit(".", 1)[-1].lstrip("@").split("<", 1)[0]


def index_raw_files(root: Path, graph: dict) -> dict:
    """Группирует raw-узлы по файлу, проверяя повторяющийся путь один раз за анализ.

    Здесь нет чтения или долгоживущего кэша. SourceBudget повторно проверяет путь
    при фактическом чтении и открывает каждый сегмент без symlink.
    """
    raw_files = defaultdict(list)
    paths = {}
    for raw in graph.get("nodes", []):
        filename = str(raw.get("file") or raw.get("source_file") or "")
        if filename not in paths:
            try:
                _, relative = _source_path(root, {"file": filename})
                paths[filename] = relative.as_posix()
            except SourceError:
                paths[filename] = None
        key = paths[filename]
        if key is not None:
            raw_files[key].append(raw)
    return raw_files


def scan_csharp(budget: SourceBudget, graph: dict, files: list[str], limits: dict) -> dict:
    """Собирает namespace/method/test/condition inventory; AST ошибки явно дают partial."""
    from tree_sitter import Language, Parser
    import tree_sitter_c_sharp
    parser = Parser(Language(tree_sitter_c_sharp.language()))
    result = {"methods": [], "conditions": [], "tests": [], "bindings": [], "fileFacts": {},
              "truncation": {"methods": False, "conditions": False, "tests": False}}
    raw_files = index_raw_files(budget.root, graph)
    inspected = 0
    for filename in files:
        source = budget.read(filename)
        if source is None:
            continue
        if source["file"] in result["fileFacts"]:
            continue
        content = source["content"]
        tree = parser.parse(content)
        declarations, file_namespace, local_attributes, imports, aliases = [], "", set(), set(), {}
        for item in walk(tree.root_node):
            inspected += 1
            if inspected > limits["maxAstNodes"]:
                budget.truncated["astNodes"] = True
                break
            if item.type == "file_scoped_namespace_declaration":
                file_namespace = source_text(item.child_by_field_name("name"), content)
            if item.type == "using_directive":
                match = re.fullmatch(r"(?:global\s+)?using\s+([\w.]+)\s*;", source_text(item, content).strip())
                if match:
                    imports.add(match[1])
                alias = re.fullmatch(r"(?:global\s+)?using\s+(\w+)\s*=\s*([\w.:]+)\s*;", source_text(item, content).strip())
                if alias:
                    aliases[alias[1]] = alias[2]
            if item.type in TYPE_TYPES:
                local_attributes.add(source_text(item.child_by_field_name("name"), content).removesuffix("Attribute"))
            if item.type in METHOD_TYPES:
                declarations.append(item)
        result["fileFacts"][source["file"]] = {"mtimeNs": source["mtimeNs"], "stableRead": source["stableRead"], "sourceHash": source["sourceHash"]}
        by_name = defaultdict(list)
        for decl in declarations:
            by_name[source_text(decl.child_by_field_name("name"), content)].append(decl)
        raw_owners = defaultdict(list)
        for raw in raw_files[source["file"]]:
            candidates = by_name.get(raw_name(raw), [])
            line = raw_line(raw)
            if line is not None:
                candidates = [decl for decl in candidates if decl.start_point.row + 1 <= line <= decl.end_point.row + 1]
            if len(candidates) == 1:
                raw_owners[candidates[0].start_byte].append(raw["id"])
        for decl in declarations:
            if len(result["methods"]) >= limits["maxMethods"]:
                result["truncation"]["methods"] = True
                break
            name = source_text(decl.child_by_field_name("name"), content)
            line, column = decl.start_point.row + 1, decl.start_point.column + 1
            identifier = stable_id("coverage-method", source["file"], line, column, name)
            owners = raw_owners[decl.start_byte]
            owner = owners[0] if len(owners) == 1 else None
            status = "partial" if decl.has_error or not source["stableRead"] else "parsed"
            namespace = namespace_of(decl, content, file_namespace)
            method = {"id": identifier, "ownerNodeId": owner, "name": name, "namespace": namespace,
                      "file": source["file"], "line": line, "column": column, "endLine": decl.end_point.row + 1,
                      "sourceStatus": status, "conditionIds": [], "testIds": [],
                      "runtime": {"status": "unknown", "reason": "Отчёт не сопоставлен.", "reportPaths": []}}
            result["methods"].append(method)
            attrs = attributes(decl, content)
            units = unit_attributes(attrs, local_attributes, aliases)
            if units and len(result["tests"]) < limits["maxTests"]:
                unit = units[0]
                framework, subtype = UNIT_ATTRIBUTES[unit["shortName"]]
                expected = FRAMEWORK_NAMESPACES[framework]
                qualified = unit["name"].removeprefix("global::")
                resolved = qualified.startswith(expected + ".") or ("." not in qualified and expected in imports)
                inherited = list(attrs)
                parent = decl.parent
                while parent is not None:
                    if parent.type in TYPE_TYPES:
                        inherited.extend(attributes(parent, content))
                    parent = parent.parent
                config = skip_attributes(inherited, framework, imports, local_attributes, aliases)
                declared, reasons = declaration_status(config, resolved)
                result["tests"].append({"id": stable_id("coverage-test", source["file"], line, column),
                    "nodeId": owner, "kind": "unit", "subtype": subtype, "framework": framework,
                    "frameworkResolution": "lexical-namespace" if resolved else "attribute-name-only",
                    "name": name, "namespace": namespace, "file": source["file"], "line": line,
                    "column": column, "endLine": method["endLine"], "sourceStatus": status,
                    "declaredStatus": declared, "skipReasons": reasons, "tags": [], "exampleRows": 0,
                    "exampleRowsTruncated": False, "runtime": {"status": "unknown", "reason": "Агрегированный отчёт не сообщает выполнение отдельного теста."}, "staticTargetIds": []})
            elif units:
                result["truncation"]["tests"] = True
            for attr in attrs:
                binding_namespace = "." not in attr["name"] and attr["name"] not in aliases and any(name in imports for name in {"Reqnroll", "TechTalk.SpecFlow"})
                binding_qualified = attr["name"].removeprefix("global::").rpartition(".")[0] in {"Reqnroll", "TechTalk.SpecFlow"}
                if attr["shortName"] in {"Given", "When", "Then", "StepDefinition"} and attr["arguments"] and owner and (binding_qualified or attr["shortName"] not in local_attributes) and (binding_namespace or binding_qualified):
                    pattern = literal_string(attr["arguments"][0])
                    if pattern is not None:
                        result["bindings"].append({"nodeId": owner, "pattern": pattern, "file": source["file"], "line": attr["line"]})
            flow = analyze_control_flow(decl, content, identifier, [])
            result["truncation"]["conditions"] |= flow["truncated"]
            points = defaultdict(list)
            for item in walk(decl, skip_nested=True):
                if item.type in BRANCH_TYPES:
                    points[(item.start_point.row + 1, BRANCH_TYPES[item.type])].append(item)
            for branch in flow["branches"]:
                if len(result["conditions"]) >= limits["maxConditions"]:
                    result["truncation"]["conditions"] = True
                    break
                items = points[(branch["line"], branch["kind"])]
                ast = items.pop(0) if items else None
                col = ast.start_point.column + 1 if ast is not None else 1
                cond_id = stable_id("coverage-condition", source["file"], branch["line"], col, branch["kind"])
                expression = branch.get("condition") or branch.get("selector") or branch.get("header") or branch.get("collection") or ""
                objectives = [{"id": cond_id + f":{index}", "kind": arm["kind"], "label": arm["label"], "implicit": False, "runtimeStatus": "unknown"} for index, arm in enumerate(branch["arms"])]
                if branch["kind"] == "if" and not any(item["kind"] == "false" for item in objectives):
                    objectives.append({"id": cond_id + ":false", "kind": "false", "label": "false (без else)", "implicit": True, "runtimeStatus": "unknown"})
                if branch["kind"] in {"for", "foreach", "while", "do"}:
                    exit_label = "выход после тела" if branch["kind"] == "do" else "выход / ноль итераций"
                    objectives.append({"id": cond_id + ":exit", "kind": "exit", "label": exit_label, "implicit": True, "runtimeStatus": "unknown"})
                expression_node = ast.child_by_field_name("condition") if ast is not None else None
                expression_end = expression_node.end_point.row + 1 if expression_node is not None else branch["line"]
                header_end = ast.start_byte if ast is not None else 0
                # Conditions/selectors can span lines even when the body spans many more: inspect only header tokens.
                if ast is not None:
                    for child in ast.children:
                        if child.type in {"block", "switch_body", "switch_expression_arm"} or child == ast.child_by_field_name("body"):
                            break
                        header_end = child.end_byte
                        expression_end = max(expression_end, child.end_point.row + 1)
                        if child.type in {")", "{"}:
                            break
                full_expression = source_text(expression_node, content) if expression_node is not None else (content[ast.start_byte:header_end].decode("utf-8") if ast is not None else expression)
                expression_truncated = len(full_expression) > 1000
                nested_lambda = False
                ancestor = ast.parent if ast is not None else None
                while ancestor is not None and ancestor != decl:
                    nested_lambda |= ancestor.type in {"lambda_expression", "anonymous_method_expression"}
                    ancestor = ancestor.parent
                method["conditionIds"].append(cond_id)
                result["conditions"].append({"id": cond_id, "ownerNodeId": owner, "methodId": identifier,
                    "methodLabel": name, "namespace": namespace, "file": source["file"], "line": branch["line"], "column": col,
                    "endLine": branch["endLine"], "kind": branch["kind"], "condition": expression,
                    "sourceStatus": status, "objectives": objectives, "runtime": {"status": "unknown", "lineHits": None,
                        "branchesCovered": None, "branchesValid": None, "reportPaths": [], "reason": "Нет однозначных счётчиков ветвей.", "freshness": "unknown"},
                    "testLinks": [], "conditionTruncated": expression_truncated,
                    "runtimeAmbiguous": expression_end != branch["line"] or "&&" in full_expression or "||" in full_expression or "?" in full_expression or nested_lambda or expression_truncated})
        if budget.truncated["astNodes"]:
            break
    return result
