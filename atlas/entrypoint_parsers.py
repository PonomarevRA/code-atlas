"""Ограниченные статические шаблоны входящих HTTP/Kafka/Rabbit регистраций C#."""

from dataclasses import dataclass
import re


_STRING = r'@?"(?:""|\\.|[^"\\])*"'
_TOKEN = re.compile(r'(?P<raw>"{3,})[\s\S]*?(?P=raw)|//[^\r\n]*|/\*[\s\S]*?\*/|' + _STRING + r"|'(?:\\.|[^'\\])*'")
_HTTP = re.compile(r'\[Http(Get|Post|Put|Delete|Patch|Head|Options)\s*(?:\(\s*(' + _STRING + r')?[^\]]*\))?\s*\]')
_CLASS = re.compile(r'\bclass\s+(\w+)')
_METHOD = re.compile(r'\bpublic\s+(?:(?:async|virtual|override|static)\s+)*[\w<>,?.\[\] ]+\s+(\w+)\s*\(')
_ROUTE = re.compile(r'\[Route\s*\(\s*(' + _STRING + r')\s*\)\]')
_KAFKA = re.compile(r'\.AddPortfolioBatchConsumer\s*<\s*([\w.]+)\s*,\s*([\w.]+)\s*>\s*\(\s*consumerSettings\.(\w+)')
_BASIC_CONSUME = re.compile(r'\.BasicConsume(?:Async)?\s*\(\s*(?:queue\s*:\s*)?(' + _STRING + r')\s*,')
_RABBIT = re.compile(r'\.ReceiveEndpoint\s*\(\s*(' + _STRING + r')\s*,')


def without_comments(text: str) -> str:
    """Убирает комментарии, сохраняя строки, смещения и номера строк."""
    def replace(match: re.Match) -> str:
        value = match.group()
        if not value.startswith(("//", "/*")):
            return value
        return "".join("\n" if char == "\n" else " " for char in value)
    return _TOKEN.sub(replace, text)


def code_mask(text: str) -> str:
    """Маскирует строки и комментарии для поиска синтаксиса, сохраняя смещения."""
    return _TOKEN.sub(lambda match: "".join("\n" if char == "\n" else " " for char in match.group()), text)


@dataclass(frozen=True)
class PreparedSource:
    """Две неизменяемые проекции одного файла с исходными смещениями символов."""

    clean: str
    mask: str


def prepare_source(text: str | PreparedSource) -> PreparedSource:
    """Переиспользует лексические проекции в трёх парсерах в пределах запроса."""
    if isinstance(text, PreparedSource):
        return text
    return PreparedSource(without_comments(text), code_mask(text))


def invocation_end(mask: str, opening: int) -> int:
    """Находит закрывающую скобку вызова; строки и комментарии уже замаскированы."""
    depth = 0
    for position in range(opening, len(mask)):
        if mask[position] == "(":
            depth += 1
        elif mask[position] == ")":
            depth -= 1
            if depth == 0:
                return position
    return opening


def literal(value: str | None) -> str | None:
    """Принимает только обычную или verbatim строку без вычисляемых выражений."""
    if value is None:
        return None
    if value.startswith('@"'):
        return value[2:-1].replace('""', '"')
    # Для маршрутов и имён очередей обратные слеши неоднозначны: не вычисляем C#.
    if "\\" in value:
        return None
    return value[1:-1]


def line_at(text: str, position: int) -> int:
    """Переводит смещение в исходнике в номер строки, начиная с единицы."""
    return text.count("\n", 0, position) + 1


def controller_routes(text: str | PreparedSource) -> dict[str, str]:
    """Возвращает буквальные префиксы классов, в том числе partial контроллеров."""
    prepared = prepare_source(text)
    clean, mask = prepared.clean, prepared.mask
    result = {}
    for match in _CLASS.finditer(mask):
        start = max(mask.rfind(char, 0, match.start()) for char in ";{}") + 1
        routes = [route for route in _ROUTE.finditer(clean, start, match.start()) if mask[route.start()] == "["]
        if routes:
            value = literal(routes[-1][1])
            if value is not None:
                result[match[1]] = value
    return result


def parse_http(text: str | PreparedSource, shared_routes: dict[str, str]) -> list[dict]:
    """Находит атрибутные действия MVC с буквальными маршрутами, без запуска ASP.NET."""
    prepared = prepare_source(text)
    clean, mask = prepared.clean, prepared.mask
    classes = list(_CLASS.finditer(mask))
    result = []
    for match in _HTTP.finditer(clean):
        if mask[match.start()] != "[":
            continue
        preceding = [item for item in classes if item.start() < match.start()]
        if not preceding:
            continue
        controller = preceding[-1][1]
        after = mask[match.end():match.end() + 1800]
        method = _METHOD.search(after)
        if not method or any(char in after[:method.start()] for char in ";{}"):
            continue
        method_name = method[1]
        suffix = literal(match[2]) if match[2] else ""
        # Не превращаем HttpGet(CONSTANT) в корневой маршрут.
        if match[2] is None and "(" in match.group() and not re.search(r'\(\s*\)', match.group()):
            suffix = None
        if match[2]:
            remainder = clean[match.end(2):match.end()].strip()
            if not remainder.startswith((")", ",")):
                suffix = None
        attribute_start = max(mask.rfind(char, 0, match.start()) for char in ";{}") + 1
        attribute_end = match.end() + method.start()
        method_routes = [route for route in _ROUTE.finditer(clean, attribute_start, attribute_end)
                         if mask[route.start()] == "["]
        route_attributes = list(re.finditer(r'\[Route\s*\(', mask[attribute_start:attribute_end]))
        if route_attributes:
            if len(method_routes) == 1 and len(route_attributes) == 1 and match[2] is None:
                suffix = literal(method_routes[0][1])
            else:
                suffix = None
        prefix = shared_routes.get(controller)
        route = None
        if suffix is not None and (prefix is not None or suffix.startswith(("/", "~/"))):
            route = suffix[1:] if suffix.startswith("~/") else suffix
            if not suffix.startswith(("/", "~/")):
                route = "/".join(part.strip("/") for part in (prefix or "", suffix) if part)
            route = "/" + route.lstrip("/")
            route = route.replace("[controller]", controller.removesuffix("Controller"))
            route = route.replace("[action]", method_name)
        result.append({"kind": "http", "handler": method_name, "controller": controller,
                       "httpMethod": match[1].upper(), "route": route,
                       "line": line_at(clean, match.start())})
    return result


def parse_messaging(text: str | PreparedSource) -> list[dict]:
    """Распознаёт batch-регистрации проекта и явный MassTransit ReceiveEndpoint."""
    prepared = prepare_source(text)
    clean, mask = prepared.clean, prepared.mask
    result = []
    for match in _KAFKA.finditer(clean):
        if mask[match.start()] != ".":
            continue
        result.append({"kind": "kafka", "handler": match[2].split(".")[-1],
                       "settingsKey": match[3], "messageType": match[1],
                       "line": line_at(clean, match.start())})
    for match in _RABBIT.finditer(clean):
        if mask[match.start()] != ".":
            continue
        opening = mask.find("(", match.start())
        end = invocation_end(mask, opening)
        body = mask[match.end():end]
        handler = re.search(r'\b(?:ConfigureConsumer|Consumer)\s*<\s*([\w.]+)', body)
        result.append({"kind": "rabbit", "handler": handler[1].split(".")[-1] if handler else None,
                       "queue": literal(match[1]), "line": line_at(clean, match.start())})
    for match in _BASIC_CONSUME.finditer(clean):
        if mask[match.start()] != ".":
            continue
        result.append({"kind": "rabbit", "handler": None, "queue": literal(match[1]),
                       "line": line_at(clean, match.start())})
    return result
