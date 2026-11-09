"""Ограниченная модель C# типов и receiver expressions; исходники никогда не изменяются."""
from collections import defaultdict
import hashlib
from pathlib import Path
import re

from .source_viewer import _source_path, _read_regular_file, SourceError

MAX_FILES = 1200
MAX_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_AST_NODES = 1500000
TYPE_KINDS = {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration"}
METHOD_KINDS = {"method_declaration", "constructor_declaration"}
COLLECTIONS = {"IEnumerable", "IReadOnlyList", "IList", "ICollection", "IReadOnlyCollection", "List", "HashSet", "ImmutableArray"}
BUILTINS = {"int": "System.Int32", "string": "System.String", "bool": "System.Boolean", "object": "System.Object", "void": "System.Void", "long": "System.Int64", "double": "System.Double", "decimal": "System.Decimal", "byte": "System.Byte", "CancellationToken": "System.Threading.CancellationToken", "Task": "System.Threading.Tasks.Task", "ValueTask": "System.Threading.Tasks.ValueTask"}


def walk(root):
    """Итеративный обход AST в исходном порядке."""
    pending = [root]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.named_children))


def text(node, source: bytes) -> str:
    """Читает только диапазон выбранного AST-узла."""
    return source[node.start_byte:node.end_byte].decode("utf-8") if node else ""


def name_of(label: str) -> str:
    """Имя метода без владельца и параметров; применяется только внутри доказанного владельца."""
    return str(label).split("(", 1)[0].rsplit(".", 1)[-1].split("<", 1)[0].lstrip("@")


def type_parts(value: str) -> tuple[str, list[str], bool]:
    """Разбирает вложенные generic arguments без разделения запятых внутреннего типа."""
    value = re.sub(r"\s+", "", value).replace("global::", "").removesuffix("?")
    array = value.endswith("[]")
    if array:
        value = value[:-2]
    if "<" not in value:
        return value, [], array
    base, rest = value.split("<", 1)
    if not rest.endswith(">"):
        return "", [], array
    arguments, start, depth = [], 0, 0
    for index, char in enumerate(rest[:-1]):
        if char == "<": depth += 1
        elif char == ">": depth -= 1
        elif char == "," and depth == 0:
            arguments.append(rest[start:index]); start = index + 1
    arguments.append(rest[start:-1])
    return base, arguments, array


def source_files(graph: dict) -> list[str]:
    """Только файлы уже существующих raw nodes входят в бюджет анализа."""
    return sorted({str(node.get("file") or node.get("source_file")) for node in graph.get("nodes", []) if str(node.get("file") or node.get("source_file") or "").lower().endswith(".cs")})


def interface_source_stamp(root: Path, graph: dict) -> list[list]:
    """Дешёвый stat stamp для внешнего cache; не заменяет fingerprint прочитанного содержимого."""
    result = []
    for file in source_files(graph)[:MAX_FILES]:
        try:
            source_root, relative = _source_path(root, {"file": file})
            stat = (source_root / relative).stat()
            result.append([file, stat.st_mtime_ns, stat.st_size])
        except (OSError, SourceError):
            result.append([file, None, None])
    return result


class SourceModel:
    """Сохраняет AST объявления и связывает их только с подтверждёнными raw IDs снимка."""

    def __init__(self, root: Path, graph: dict):
        """Читает bounded C# corpus; same-name тип без namespace/import доказательства не разрешается."""
        from tree_sitter import Language, Parser
        import tree_sitter_c_sharp
        self.parser = Parser(Language(tree_sitter_c_sharp.language()))
        self.types, self.methods = [], []
        self.type_index = defaultdict(list)
        self.files = {}; self.bytes = 0; self.ast_nodes = 0; self.truncated = False; self.warnings = []
        self.nodes_by_file = defaultdict(list)
        self.method_owners = defaultdict(set)
        for edge in graph.get("edges", []):
            if edge.get("relation") == "method": self.method_owners[edge.get("target")].add(edge.get("source"))
        for node in graph.get("nodes", []):
            self.nodes_by_file[str(node.get("file") or node.get("source_file") or "")].append(node)
        digest = hashlib.sha256()
        files = source_files(graph)
        self.truncated |= len(files) > MAX_FILES
        for file in files[:MAX_FILES]:
            try:
                source_root, relative = _source_path(root, {"file": file})
                source = _read_regular_file(source_root, relative)
            except (OSError, SourceError):
                self.truncated = True; continue
            if self.bytes + len(source) > MAX_BYTES:
                self.truncated = True; break
            self.bytes += len(source)
            try:
                source.decode("utf-8-sig")
            except UnicodeDecodeError:
                self.truncated = True
                self.warnings.append(f"Файл {file} не является UTF-8 C# исходником.")
                continue
            digest.update(file.encode()); digest.update(hashlib.sha256(source).digest())
            tree = self.parser.parse(source)
            if tree.root_node.has_error:
                self.truncated = True
                if len(self.warnings) < 10: self.warnings.append(f"Файл содержит AST-ошибки; повреждённые объявления пропущены: {file}.")
            count = sum(1 for _ in walk(tree.root_node))
            if self.ast_nodes + count > MAX_AST_NODES:
                self.truncated = True; break
            self.ast_nodes += count
            self.files[file] = source
            self._collect(file, source, tree.root_node)
        self.fingerprint = digest.hexdigest()
        for definition in self.types:
            self.type_index[(definition["qualifiedName"], len(definition["typeParameters"]))].append(definition)

    def _raw_id(self, file, declaration, name, *, is_type=False, owner_id=None):
        """Точное имя плюс строка внутри объявления исключают глобальные suffix-совпадения."""
        candidates = []
        for node in self.nodes_by_file[file]:
            label = str(node.get("label", ""))
            if name_of(label) != name or label.endswith(".cs"):
                continue
            if is_type and (label.startswith(".") or "(" in label):
                continue
            if owner_id and self.method_owners.get(node["id"]) and owner_id not in self.method_owners[node["id"]]:
                continue
            line = node.get("line")
            if line is None:
                match = re.match(r"L(\d+)", str(node.get("source_location", "")))
                line = int(match[1]) if match else None
            if isinstance(line, int) and declaration.start_point.row + 1 <= line <= declaration.end_point.row + 1:
                candidates.append(node["id"])
        return candidates[0] if len(set(candidates)) == 1 else None

    def _collect(self, file, source, ast):
        """Собирает namespace, imports, base types, methods и field declarations без исполнения кода."""
        usings = [text(node, source).removeprefix("global ").removeprefix("using ").removesuffix(";").strip() for node in ast.named_children if node.type == "using_directive"]
        file_namespace = next((text(node.child_by_field_name("name"), source) for node in ast.named_children if node.type == "file_scoped_namespace_declaration"), "")
        for declaration in walk(ast):
            if declaration.type not in TYPE_KINDS:
                continue
            if declaration.has_error:
                self.truncated = True
                if len(self.warnings) < 10: self.warnings.append(f"Объявление с AST-ошибкой не разрешено: {file}:{declaration.start_point.row + 1}.")
                continue
            namespaces, parents = [], []
            ancestor = declaration.parent
            while ancestor:
                if ancestor.type == "namespace_declaration": namespaces.append(text(ancestor.child_by_field_name("name"), source))
                if ancestor.type in TYPE_KINDS: parents.append(text(ancestor.child_by_field_name("name"), source))
                ancestor = ancestor.parent
            namespace = ".".join([file_namespace, *reversed(namespaces)]).strip(".")
            name = text(declaration.child_by_field_name("name"), source)
            parameters = next((child for child in declaration.named_children if child.type == "type_parameter_list"), None)
            type_parameters = [text(child.child_by_field_name("name") or child, source).strip() for child in parameters.named_children] if parameters else []
            base_list = next((child for child in declaration.named_children if child.type == "base_list"), None)
            definition = {"id": self._raw_id(file, declaration, name, is_type=True), "name": name, "qualifiedName": ".".join([namespace, *reversed(parents), name]).strip("."), "namespace": namespace, "usings": usings, "typeParameters": type_parameters, "baseTypes": [text(child, source) for child in base_list.named_children] if base_list else [], "kind": "interface" if declaration.type == "interface_declaration" else "class", "file": file, "ast": declaration, "fields": {}, "methods": []}
            self.types.append(definition)
            body = declaration.child_by_field_name("body")
            for child in body.named_children if body else []:
                if child.type == "field_declaration":
                    variable = next((item for item in child.named_children if item.type == "variable_declaration"), None)
                    if variable:
                        field_type = text(variable.child_by_field_name("type"), source)
                        for item in variable.named_children:
                            if item.type == "variable_declarator": definition["fields"][text(item.child_by_field_name("name"), source)] = field_type
                if child.type in METHOD_KINDS:
                    self._method(definition, child, source)
            # C# primary-constructor parameters are lexically accessible in instance members.
            primary = declaration.child_by_field_name("parameters") or next((child for child in declaration.named_children if child.type == "parameter_list"), None)
            for parameter in primary.named_children if primary else []:
                if parameter.type == "parameter": definition["fields"][text(parameter.child_by_field_name("name"), source)] = text(parameter.child_by_field_name("type"), source)

    def _method(self, owner, declaration, source):
        """Сохраняет сигнатуру и modifiers: dispatch проверяет доступность, delegate сохраняет свои правила."""
        name = text(declaration.child_by_field_name("name"), source)
        type_parameters = next((child for child in declaration.named_children if child.type == "type_parameter_list"), None)
        generic = [text(child.child_by_field_name("name") or child, source) for child in type_parameters.named_children] if type_parameters else []
        parameters = declaration.child_by_field_name("parameters")
        values = []
        for parameter in parameters.named_children if parameters else []:
            if parameter.type != "parameter": continue
            modifiers = [text(child, source) for child in parameter.named_children if child.type == "modifier"]
            values.append({"name": text(parameter.child_by_field_name("name"), source), "type": text(parameter.child_by_field_name("type"), source), "modifier": next((value for value in modifiers if value in {"ref", "out", "in"}), ""), "optional": any(child.type == "equals_value_clause" for child in parameter.named_children)})
        explicit = next((child for child in declaration.named_children if child.type == "explicit_interface_specifier"), None)
        method_modifiers = [text(child, source) for child in declaration.named_children if child.type == "modifier"]
        record = {"id": self._raw_id(owner["file"], declaration, name, owner_id=owner["id"]), "owner": owner, "name": name, "parameters": values, "genericParameters": generic, "returns": text(declaration.child_by_field_name("returns"), source), "explicitInterface": text(explicit, source).rstrip("."), "modifiers": method_modifiers, "ast": declaration, "constructor": declaration.type == "constructor_declaration"}
        owner["methods"].append(record); self.methods.append(record)

    def resolve(self, value: str, owner: dict, substitution=None) -> str | None:
        """Разрешает квалифицированный тип по лексическому namespace/import; неоднозначность остаётся None."""
        substitution = substitution or {}
        base, arguments, array = type_parts(value)
        if base in substitution:
            resolved = substitution[base]
        elif base in owner["typeParameters"]:
            resolved = "$" + base
        elif base in BUILTINS and base[0].islower():
            resolved = BUILTINS[base]
        else:
            if "." in base:
                groups = [[base]]
            else:
                # C# сначала ищет текущий и родительские namespace, затем imports.
                namespaces = owner["namespace"].split(".") if owner["namespace"] else []
                groups = [[owner["qualifiedName"] + "." + base]]
                for depth in range(len(namespaces), -1, -1):
                    groups.append([".".join([*namespaces[:depth], base])])
                groups.append([using + "." + base for using in owner["usings"] if "=" not in using and not using.startswith("static ")])
            resolved = None
            for names in groups:
                matches = list(dict.fromkeys(name for name in names if (name, len(arguments)) in self.type_index))
                if len(matches) > 1: return None
                if matches:
                    resolved = matches[0]; break
            if resolved is None:
                if base in BUILTINS: resolved = BUILTINS[base]
                elif base in BUILTINS.values(): resolved = base
                elif base in COLLECTIONS or (base.startswith("System.Collections.Generic.") and base.rsplit(".", 1)[-1] in COLLECTIONS): resolved = "collection:" + base.rsplit(".", 1)[-1]
                else: return None
        if arguments:
            resolved_arguments = [self.resolve(argument, owner, substitution) for argument in arguments]
            if any(argument is None for argument in resolved_arguments): return None
            resolved += "<" + ",".join(resolved_arguments) + ">"
        return resolved + ("[]" if array else "")

    def definition(self, resolved: str) -> dict | None:
        """Возвращает единственное объявление точной qualified name/generic arity."""
        base, arguments, array = type_parts(resolved)
        matches = self.type_index.get((base, len(arguments)), [])
        return matches[0] if not array and len(matches) == 1 else None

    def signature(self, method: dict, substitution=None):
        """Каноническая сигнатура включает типы и модификаторы; неизвестный тип не угадывается."""
        substitutions = {name: f"!{index}" for index, name in enumerate(method["genericParameters"])}
        substitutions.update(substitution or {})
        parameters = []
        for parameter in method["parameters"]:
            value = self.resolve(parameter["type"], method["owner"], substitutions)
            if value is None: return None
            parameters.append((parameter["modifier"], value))
        return (method["name"], len(method["genericParameters"]), tuple(parameters))

    def element_type(self, value):
        """Только известные collection/array формы имеют извлекаемый тип элемента."""
        if not value: return None
        base, arguments, array = type_parts(value)
        if array: return value[:-2]
        return arguments[0] if base.startswith("collection:") and len(arguments) == 1 else None
