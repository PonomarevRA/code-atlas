"""AST-диапазоны C# и ограниченные наблюдения внутри выбранного метода."""

from pathlib import Path
from copy import deepcopy
import hashlib
import re

from .source_viewer import SourceError, read_node_source
from .control_flow import analyze_control_flow
from .method_flow import analyze_method_flow
from .node_documentation import declaration_facts, empty_documentation
from .parameter_facts import empty_parameter_facts, invocation_arguments, parameter_facts
from .attribute_facts import attribute_facts, empty_attribute_facts
from .source_readability import analyze_readability, empty_readability_recommendations


_DECLARATIONS = {"method_declaration", "constructor_declaration", "local_function_statement"}
MAX_AST_NODES = 20_000
_CLASSES = {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration"}
_BRANCHES = {"if_statement", "switch_statement", "switch_expression", "conditional_expression",
             "for_statement", "foreach_statement", "while_statement", "do_statement", "catch_clause",
             "lambda_expression", "anonymous_method_expression"}
_STRINGS = {"string_literal", "verbatim_string_literal", "interpolated_string_expression", "raw_string_literal"}
_TRACE_OPERATIONS = {"SetTag", "AddTag", "AddEvent", "SetStatus"}
_LOG_NAMES = {"Log", "LogTrace", "LogDebug", "LogInformation", "LogWarning", "LogError", "LogCritical"}


def _walk(root):
    """Обходит AST в исходном порядке без рекурсии Python."""
    pending = [root]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.named_children))


def _text(node, content: bytes) -> str:
    """Декодирует только выбранный AST-фрагмент UTF-8."""
    return content[node.start_byte:node.end_byte].decode("utf-8") if node else ""


def _name(node, content: bytes) -> str:
    """Получает простое имя объявления или вызова без предположения о типах."""
    if node is None:
        return ""
    named = node.child_by_field_name("name")
    if named is not None:
        return _text(named, content).split("<", 1)[0]
    return _text(node, content).split(".")[-1].split("<", 1)[0]


def _context(node, boundary) -> tuple[bool, bool, bool]:
    """Отмечает условность, await и Task.WhenAll как синтаксис, а не runtime-порядок."""
    conditional = awaited = parallel = False
    current = node
    while current is not None and current != boundary:
        if current.type == "conditional_expression":
            condition = current.child_by_field_name("condition")
            # Условие ?: вычисляется до выбора arm, а не только при истинном результате.
            conditional |= condition is None or not condition.start_byte <= node.start_byte < condition.end_byte
        else:
            conditional |= current.type in _BRANCHES
        awaited |= current.type == "await_expression"
        if current.type == "invocation_expression":
            function = current.child_by_field_name("function")
            parallel |= function is not None and function.text.endswith(b"WhenAll")
        current = current.parent
    return conditional, awaited, parallel


def _templates(node, content: bytes) -> str:
    """Возвращает только первый буквальный шаблон; вычисление аргументов не выполняет."""
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        arguments = next((child for child in node.named_children
                          if child.type in {"argument_list", "attribute_argument_list"}), None)
    candidates = [node]
    if arguments is not None:
        parameters = arguments.named_children
        function = node.child_by_field_name("function")
        parameter_name = "name" if _name(function, content) in {"StartActivity", "StartSpan"} else "message"
        named = [argument for argument in parameters
                 if re.match(r"\s*" + parameter_name + r"\s*:", _text(argument, content))]
        # Не выбираем следующий строковый structured-аргумент после динамического
        # шаблона. Перегрузки с exception/EventId без семантики типов не угадываем.
        candidates = named[:1] or parameters[:1]
    for argument in candidates:
        for expression in argument.named_children:
            if expression.type in _STRINGS:
                return _text(expression, content)[:600]
    return "Шаблон вычисляется во время выполнения."


def _events(declaration, content: bytes, limit: int, parameter_names: set[str]) -> tuple[list[dict], bool]:
    """Извлекает события и исходные аргументы выбранного объявления, исключая тела вложенных методов."""
    events = []
    dictionaries = _local_dictionaries(declaration, content)
    pending = [declaration]
    while pending:
        node = pending.pop()
        if node != declaration and node.type in _DECLARATIONS | _CLASSES:
            continue
        event = None
        if node.type == "comment":
            event = {"kind": "comment", "text": _text(node, content)[:600], "callee": None}
        elif node.type == "assignment_expression":
            left, right = node.child_by_field_name('left'), node.child_by_field_name('right')
            receiver = left.child_by_field_name('expression') if left and left.type == 'element_access_expression' else None
            if receiver is not None and _text(receiver, content) in dictionaries:
                event = {"kind": "data-write", "text": _text(node, content)[:600], "callee": None,
                         "dataKey": _text(left, content)[:300], "dataValue": _text(right, content)[:600]}
        elif node.type == "constructor_initializer":
            arguments = next((child for child in node.named_children if child.type == "argument_list"), None)
            callee = "base" if any(child.type == "base" for child in node.children) else "this"
            event = {"kind": "call", "text": _text(node, content).lstrip(": ")[:600], "callee": callee,
                     "callKind": "constructor-initializer", "arguments": [], "argumentsTruncated": False}
        elif node.type == "invocation_expression":
            function = node.child_by_field_name("function")
            name = _name(function, content)
            kind = "call"
            if name in _LOG_NAMES:
                kind = "log"
            elif name in {"StartActivity", "StartSpan"} | _TRACE_OPERATIONS:
                kind = "span"
            text = _templates(node, content) if kind != "call" else _text(function, content)[:300] + "()"
            event = {"kind": kind, "text": text, "callee": name}
            event.update(invocation_arguments(node, content, parameter_names))
            if name in _TRACE_OPERATIONS:
                # Одно имя SetTag не доказывает Activity/OpenTelemetry: сохраняем
                # выражение как синтаксического кандидата, не вычисляя значения.
                event.update(text=f"Возможный trace {name}: " + _text(node, content)[:600],
                             traceOperation=name, confidence="candidate")
        elif node.type == "attribute" and _name(node, content) in {"LoggerMessage", "LoggerMessageAttribute"}:
            message = None
            for child in node.named_children:
                for argument in child.named_children:
                    if re.match(r"Message\s*=", _text(argument, content)):
                        message = _templates(argument, content)
            event = {"kind": "log", "text": message or _templates(node, content), "callee": "LoggerMessage"}
        if event is not None:
            source_expression = _text(node, content)
            event["sourceExpression"] = source_expression[:2000]
            event["sourceExpressionTruncated"] = len(source_expression) > 2000
            conditional, awaited, parallel = _context(node, declaration)
            ancestor, deferred = node.parent, False
            while ancestor is not None and ancestor != declaration:
                if ancestor.type in {'lambda_expression', 'anonymous_method_expression'}:
                    deferred = True
                    break
                ancestor = ancestor.parent
            event.update(line=node.start_point.row + 1, column=node.start_point.column + 1,
                         endLine=node.end_point.row + 1, endColumn=node.end_point.column + 1,
                         targetNodeIds=[], confidence=event.get("confidence", "source"), conditional=conditional,
                         awaited=awaited, parallel=parallel, deferred=deferred)
            events.append(event)
            if len(events) > limit:
                return events[:limit], True
        pending.extend(reversed(node.named_children))
    events = sorted(_leading_comments(declaration, content) + events, key=lambda event: (event["line"], event["column"]))
    return events[:limit], len(events) > limit


def _local_dictionaries(declaration, content):
    """Находит явно объявленные локальные Dictionary; неоднозначное затенение исключается целиком."""
    definitions = {}
    pending = [declaration]
    while pending:
        node = pending.pop()
        if node != declaration and node.type in _DECLARATIONS | _CLASSES | {'lambda_expression', 'anonymous_method_expression'}:
            continue
        if node.type == 'variable_declaration':
            declared = _text(node.child_by_field_name('type'), content)
            for item in node.named_children:
                if item.type != 'variable_declarator':
                    continue
                initializer = next((child for child in item.named_children if child != item.child_by_field_name('name')), None)
                inferred = _text(initializer.child_by_field_name('type'), content) if initializer and initializer.type == 'object_creation_expression' else ''
                value = inferred if declared == 'var' else declared
                name = _text(item.child_by_field_name('name'), content)
                definitions.setdefault(name, []).append(bool(re.search(r'(?:^|\.)\w*Dictionary\s*<', value)))
        pending.extend(reversed(node.named_children))
    return {name for name, values in definitions.items() if len(values) == 1 and values[0]}


def _leading_comments(declaration, content: bytes) -> list[dict]:
    """Берёт только непосредственно прилегающий комментарий выбранного объявления."""
    previous = declaration.prev_named_sibling
    comments = []
    next_row = declaration.start_point.row
    while previous is not None and previous.type == "comment" and previous.end_point.row >= next_row - 1:
        comments.append({"kind": "comment", "text": _text(previous, content)[:600], "callee": None,
                         "line": previous.start_point.row + 1, "column": previous.start_point.column + 1,
                         "targetNodeIds": [], "confidence": "source", "conditional": False,
                         "awaited": False, "parallel": False})
        next_row = previous.start_point.row
        previous = previous.prev_named_sibling
    return list(reversed(comments))



class SourceAnalysis:
    """Кэш AST только в пределах запроса; все чтения проходят существующий safe reader."""

    def __init__(self, root: Path, snapshot_id: str, max_files: int, max_bytes: int, max_events: int):
        """Фиксирует общий бюджет; грамматика создаётся только для выбранного языка."""
        self.parser = None
        self.parser_warning = ''
        self.root, self.snapshot_id = root, snapshot_id
        self.max_files, self.max_bytes, self.max_events = max_files, max_bytes, max_events
        self.files = {}
        self.ast_limited_files = set()
        self.total_bytes = 0
        self.truncation = {"files": False, "bytes": False, "events": False,
                           "parameterFacts": False, "arguments": False, "attributeFacts": False, "ast": False}

    def _load(self, node: dict):
        """Читает и разбирает каждый допустимый файл один раз, учитывая все попытки."""
        filename = node.get("file") or node.get("source_file") or ""
        if filename in self.files:
            return self.files[filename]
        if len(self.files) >= self.max_files:
            self.truncation["files"] = True
            return None
        self.files[filename] = None
        if not str(filename).lower().endswith(".cs"):
            return None
        try:
            source = read_node_source(self.root, node, self.snapshot_id, include_syntax=False)
        except SourceError:
            return None
        content = source["text"].encode("utf-8")
        if self.total_bytes + len(content) > self.max_bytes:
            self.truncation["bytes"] = True
            return None
        self.total_bytes += len(content)
        if self.parser is None:
            try:
                from tree_sitter import Language, Parser
                import tree_sitter_c_sharp
                self.parser = Parser(Language(tree_sitter_c_sharp.language()))
            except ImportError:
                self.parser_warning = 'Локальная грамматика C# недоступна.'
                return None
        tree = self.parser.parse(content)
        declarations = []
        for index, item in enumerate(_walk(tree.root_node)):
            if index >= MAX_AST_NODES:
                # A prefix cannot prove unique declaration ownership; discard it as a whole.
                self.truncation['ast'] = True
                self.ast_limited_files.add(filename)
                return None
            if item.type in _DECLARATIONS | _CLASSES:
                declarations.append(item)
        result = (source["file"], content, declarations, hashlib.sha256(content).hexdigest(),
                  source.get("sourceByteHash"), source.get("sourceReadAt"))
        self.files[filename] = result
        return result

    def describe(self, node: dict) -> dict:
        """Сопоставляет узел с точным AST-объявлением; неоднозначность остаётся явной."""
        result = {"kind": "external" if node.get("kind") == "external" else "unknown",
                  "file": node.get("file") or node.get("source_file") or "", "line": node.get("line"),
                  "endLine": None, "sourceStatus": "unavailable", "events": [], "unresolved": [],
                  "signature": "", "parameters": [], "returnType": "", "documentation": empty_documentation(),
                  "typeContext": None, "declarationTruncated": False, "sourceHash": None,
                  "analyzerVersion": "source-structure-v1",
                  "readabilityRecommendations": empty_readability_recommendations(),
                  "parameterFacts": empty_parameter_facts(),
                  "attributeFacts": empty_attribute_facts(),
                  "controlFlow": {"branches": [], "terminals": [], "truncated": False, "unsupported": []}}
        result["callableFacts"] = deepcopy(node.get("callableFacts"))
        from .code_complexity import unavailable_complexity
        result['complexity'] = deepcopy(node.get('complexity')) or unavailable_complexity()
        result['health'] = deepcopy(node.get('health'))
        if (node.get("fieldFacts") or node.get("staticData") or node.get("dataFacts")) and not node.get('callableFacts'):
            # Значения принадлежат опубликованному снимку; не выдаём их за вычисления текущего метода.
            facts = node.get("fieldFacts") or {}
            result.update(kind="field", sourceStatus="captured", sourceKind="snapshot-captured-source",
                          staticData=deepcopy(node.get("staticData") or node.get("dataFacts")), signature=(node.get("staticData") or node.get("dataFacts") or {}).get("initializer", facts.get("type", "")),
                          sourceHash=facts.get("sourceHash"))
            result["documentation"]["summary"] = facts.get("description", "")
            return result
        from .flow_languages.host import describe_language
        language_result = describe_language(self, node, result)
        if language_result is not None:
            return language_result
        if (node.get('memberFacts') or {}).get('language') not in {None, 'csharp'} and node.get('callableFacts'):
            captured = node['callableFacts']
            result.update(kind='method', sourceStatus='captured', sourceKind='snapshot-captured-source',
                          signature=captured.get('signature', ''), sourceHash=captured.get('sourceHash'))
            result['documentation']['summary'] = captured.get('description', '')
            result['unresolved'].append('Method declaration is captured; detailed control-flow analysis is not supported for this language.')
            return result
        return self._describe_csharp(node, result)

    def _describe_csharp(self, node: dict, result: dict) -> dict:
        """Established C# parser behind the common provider interface; preserve its source semantics."""
        from .code_complexity import analyze_complexity
        loaded = self._load(node)
        if loaded is None:
            filename = node.get('file') or node.get('source_file') or ''
            if filename in self.ast_limited_files:
                result['declarationTruncated'] = True
                result['unresolved'].append('Исчерпан лимит узлов AST C#; принадлежность объявления не подтверждена.')
            else:
                result["unresolved"].append(self.parser_warning or "Текущий C#-исходник недоступен либо исчерпан лимит чтения.")
            return result
        filename, content, declarations, source_hash, source_byte_hash, source_read_at = loaded
        result["sourceHash"] = source_hash
        result["sourceByteHash"] = source_byte_hash
        result["sourceReadAt"] = source_read_at
        name = str(node.get("label") or "").strip(".").split("(", 1)[0]
        matches = [declaration for declaration in declarations if _name(declaration, content) == name]
        if len(matches) > 1 and (node.get('_callable_class') or node.get('kind') in {'class', 'interface', 'struct', 'record'}):
            matches = [declaration for declaration in matches if declaration.type in _CLASSES]
        elif len(matches) > 1 and '(' in str(node.get('label', '')):
            # Конструктор имеет имя класса и лежит внутри его диапазона; сигнатура raw-узла отличает метод от типа.
            matches = [declaration for declaration in matches if declaration.type in _DECLARATIONS]
        callable_facts = node.get('callableFacts') or {}
        if len(matches) > 1 and callable_facts.get('kind') == 'constructor':
            matches = [declaration for declaration in matches if declaration.type == 'constructor_declaration'
                       and _text(declaration.child_by_field_name('parameters'), content) == callable_facts.get('parameterList')
                       and ('static' in callable_facts.get('modifiers', [])) == any(child.type == 'modifier' and _text(child, content) == 'static' for child in declaration.named_children)]
        line = node.get("line")
        if len(matches) > 1 and isinstance(line, int):
            matches = [declaration for declaration in matches if declaration.start_point.row + 1 <= line <= declaration.end_point.row + 1]
        if len(matches) != 1:
            result.update(file=filename, sourceStatus="unresolved")
            result["attributeFacts"]["status"] = "unresolved"
            result["unresolved"].append("Точное объявление не найдено или имя неоднозначно; содержимое соседних методов не используется.")
            return result
        declaration = matches[0]
        result.update(kind="class" if declaration.type in _CLASSES else "method", file=filename,
                      line=declaration.start_point.row + 1, endLine=declaration.end_point.row + 1, sourceStatus="parsed")
        result.update(declaration_facts(declaration, content))
        result["attributeFacts"] = attribute_facts(declaration, content, filename)
        self.truncation["attributeFacts"] |= result["attributeFacts"]["truncated"]
        if declaration.has_error:
            result["unresolved"].append("AST содержит ошибки разбора; показанные события могут быть неполными.")
        if result["kind"] == "class":
            comments = _leading_comments(declaration, content)
            result["events"] = comments[:self.max_events]
            self.truncation["events"] |= len(comments) > self.max_events
        if result["kind"] == "method":
            result['complexity'] = analyze_complexity(declaration, content, 'csharp',
                source_kind='working-tree', source_hash=source_hash)
            if result.get('health') and result['health'].get('sourceHash') != source_hash:
                result['health'] = {'status': 'unavailable', 'score': None, 'documentationScore': None, 'sourceKind': 'working-tree'}
            result["readabilityRecommendations"] = analyze_readability(declaration, content, node["id"])
            result["parameterFacts"] = parameter_facts(declaration, content, node["id"])
            self.truncation["parameterFacts"] |= result["parameterFacts"]["truncated"]
            parameter_names = {parameter["name"].removeprefix("@") for parameter in result["parameterFacts"]["parameters"]}
            result["events"], limited = _events(declaration, content, self.max_events, parameter_names)
            self.truncation["events"] |= limited
            self.truncation["arguments"] |= any(event.get("argumentsTruncated", False) for event in result["events"])
            result["controlFlow"] = analyze_control_flow(declaration, content, node["id"], result["events"])
            result["methodFlow"] = analyze_method_flow(declaration, content, result["events"])
        for event in result["events"]:
            event.setdefault("branchIds", [])
        return result
