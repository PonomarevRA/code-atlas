"""Ограниченные декларации атрибутов C#: исходные выражения без reflection и наследования."""

MAX_GROUPS = 54
MAX_CONTAINING_TYPES = 12
MAX_PARAMETERS = 40
MAX_ATTRIBUTES = 200
MAX_ATTRIBUTES_PER_GROUP = 40
MAX_ARGUMENTS = 20
MAX_EXPRESSION_CHARS = 600
MAX_TEXT_CHARS = 1600
MAX_NAME_CHARS = 300
MAX_INSPECTED_NODES = 4000
MAX_ANCESTORS = 128
_TYPES = {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration"}
WARNING = ("Показаны атрибуты выбранного объявления, содержащих типов, его параметров и файла "
           "из текущего C#-исходника. Атрибуты типов и assembly/module показаны отдельно, "
           "не как атрибуты метода. Унаследованные атрибуты и другие части partial-объявлений "
           "не ищутся. Тип атрибута, значения аргументов и влияние на выполнение не вычисляются.")


def empty_attribute_facts(status: str = "unavailable") -> dict:
    """Создаёт независимую пустую модель; unavailable не означает отсутствие атрибутов."""
    return {"kind": "lexical-source", "status": status, "groups": [], "truncated": False,
            "limits": {"maxGroups": MAX_GROUPS, "maxContainingTypes": MAX_CONTAINING_TYPES,
                       "maxParameters": MAX_PARAMETERS, "maxAttributes": MAX_ATTRIBUTES,
                       "maxAttributesPerGroup": MAX_ATTRIBUTES_PER_GROUP,
                       "maxArgumentsPerAttribute": MAX_ARGUMENTS,
                       "maxExpressionChars": MAX_EXPRESSION_CHARS, "maxTextChars": MAX_TEXT_CHARS,
                       "maxNameChars": MAX_NAME_CHARS, "maxInspectedNodes": MAX_INSPECTED_NODES,
                       "maxAncestors": MAX_ANCESTORS}, "warning": WARNING}


class _Collector:
    """Собирает только непосредственные attribute lists с общим бюджетом одного объявления."""

    def __init__(self, content: bytes, filename: str):
        """Привязывает факты к уже безопасно прочитанному файлу; файлового I/O здесь нет."""
        self.content, self.filename = content, filename
        self.result = empty_attribute_facts("parsed")
        self.inspected = self.attributes = 0

    def children(self, node):
        """Ограничивает осмотр прямых AST-детей; тела методов и выражений не обходятся."""
        for child in node.children:
            if self.inspected >= MAX_INSPECTED_NODES:
                self.result["truncated"] = True
                return
            self.inspected += 1
            yield child

    def text(self, node, limit: int) -> tuple[str, bool]:
        """Возвращает буквальный UTF-8-фрагмент и признак сокращения сериализованного текста."""
        value = self.content[node.start_byte:node.end_byte].decode("utf-8") if node is not None else ""
        return value[:limit], len(value) > limit

    def location(self, node) -> dict:
        """Использует строки и столбец исходника с нумерацией от единицы."""
        return {"file": self.filename, "line": node.start_point.row + 1,
                "endLine": node.end_point.row + 1, "column": node.start_point.column + 1}

    def arguments(self, attribute) -> tuple[list[dict], bool]:
        """Различает позицию, name: конструктора и Property=; значения не вычисляет."""
        argument_list = next((child for child in self.children(attribute)
                              if child.type == "attribute_argument_list"), None)
        if argument_list is None:
            return [], False
        arguments, truncated = [], False
        for argument in self.children(argument_list):
            if argument.type != "attribute_argument":
                continue
            if len(arguments) >= MAX_ARGUMENTS:
                truncated = True
                break
            name_node = argument.child_by_field_name("name")
            expression, name_kind = None, None
            for child in self.children(argument):
                if child.type == ":":
                    name_kind = "constructor"
                elif child.type == "=":
                    name_kind = "property"
                elif child.is_named and child != name_node and child.type != "comment":
                    expression = child
            name, name_shortened = self.text(name_node, MAX_NAME_CHARS)
            text, shortened = self.text(expression, MAX_EXPRESSION_CHARS)
            shortened |= name_shortened or argument.has_error or expression is None
            arguments.append({"position": len(arguments) + 1, "name": name or None,
                              "nameKind": name_kind, "expression": text,
                              **self.location(argument), "truncated": shortened})
            truncated |= shortened
        return arguments, truncated

    def collect_attributes(self, lists: list) -> list[dict]:
        """Сохраняет порядок и повторы атрибутов; бюджет общий для всех scope-групп."""
        attributes = []
        for attribute_list in lists:
            target, target_shortened = None, False
            children = list(self.children(attribute_list))
            for child in children:
                if child.type == "attribute_target_specifier":
                    text, target_shortened = self.text(child, MAX_NAME_CHARS)
                    target = text.rstrip(":").strip()
                elif attribute_list.type == "global_attribute" and child.type in {"assembly", "module"}:
                    target = child.type
            for attribute in children:
                if attribute.type != "attribute":
                    continue
                if len(attributes) >= MAX_ATTRIBUTES_PER_GROUP or self.attributes >= MAX_ATTRIBUTES:
                    self.result["truncated"] = True
                    return attributes
                name, name_shortened = self.text(attribute.child_by_field_name("name"), MAX_NAME_CHARS)
                text, text_shortened = self.text(attribute, MAX_TEXT_CHARS)
                arguments, arguments_truncated = self.arguments(attribute)
                shortened = (target_shortened or name_shortened or text_shortened or arguments_truncated
                             or attribute.has_error or not name)
                attributes.append({"name": name, "target": target, "arguments": arguments,
                                   "argumentsTruncated": arguments_truncated, "text": text,
                                   **self.location(attribute), "truncated": shortened})
                self.attributes += 1
                self.result["truncated"] |= shortened
        return attributes

    def group(self, scope: str, owner, lists: list, name: str | None = None,
              position: int | None = None) -> None:
        """Добавляет scope с отдельным владельцем; containing type не становится декларацией метода."""
        if len(self.result["groups"]) >= MAX_GROUPS:
            self.result["truncated"] = True
            return
        if name is None:
            name, shortened = self.text(owner.child_by_field_name("name"), MAX_NAME_CHARS)
            self.result["truncated"] |= shortened
        group = {"scope": scope, "name": name, "kind": owner.type, **self.location(owner),
                 "attributes": self.collect_attributes(lists)}
        if position is not None:
            group["position"] = position
        self.result["groups"].append(group)

    def direct_lists(self, owner) -> list:
        """Берёт только attribute lists самого владельца, исключая соседей и вложенные объявления."""
        return [child for child in self.children(owner) if child.type == "attribute_list"]

    def parameter_groups(self, declaration) -> None:
        """Делит внешние параметры по запятым, учитывая плоский AST последнего params-параметра."""
        parameters = declaration.child_by_field_name("parameters")
        if parameters is None:
            # Позиционный record хранит parameter_list без имени поля в текущей grammar.
            parameters = next((child for child in self.children(declaration)
                               if child.type == "parameter_list"), None)
        if parameters is None:
            return
        segment, position = [], 0
        for index, child in enumerate(self.children(parameters)):
            if child.type == "(":
                continue
            if child.type in {",", ")"}:
                if not segment:
                    continue
                position += 1
                if position > MAX_PARAMETERS:
                    self.result["truncated"] = True
                    return
                name_node = next((item for field, item in segment if field == "name"), None)
                lists = [item for _, item in segment if item.type == "attribute_list"]
                if lists:
                    name, shortened = self.text(name_node, MAX_NAME_CHARS)
                    self.result["truncated"] |= shortened or name_node is None
                    self.group("parameter", name_node or segment[0][1], lists,
                               name=name.removeprefix("@"), position=position)
                segment = []
            elif child.type == "parameter":
                for part, item in enumerate(self.children(child)):
                    segment.append((child.field_name_for_child(part), item))
            else:
                segment.append((parameters.field_name_for_child(index), child))
        if segment:
            # Незавершённый список не выдаёт выдуманный параметр, но явно снижает полноту.
            self.result["truncated"] = True


def attribute_facts(declaration, content: bytes, filename: str) -> dict:
    """Показывает выбранную декларацию, ближайшие типы, параметры и global attrs текущего файла.

    Внешние partial-части, базовые типы и разрешение имён не читаются. Обход предков
    ограничен отдельно; global attributes доступны только при достижении compilation unit.
    """
    collector = _Collector(content, filename)
    collector.group("declaration", declaration, collector.direct_lists(declaration))
    collector.parameter_groups(declaration)
    current, ancestors, types = declaration.parent, 0, 0
    while current is not None:
        ancestors += 1
        if ancestors > MAX_ANCESTORS:
            collector.result["truncated"] = True
            break
        if current.type in _TYPES:
            types += 1
            if types <= MAX_CONTAINING_TYPES:
                collector.group("type", current, collector.direct_lists(current))
            else:
                collector.result["truncated"] = True
        if current.type == "compilation_unit":
            lists = [child for child in collector.children(current) if child.type == "global_attribute"]
            if lists:
                collector.group("file", current, lists, name=filename)
        current = current.parent
    if declaration.has_error or collector.result["truncated"]:
        collector.result["status"] = "partial"
    return collector.result
