"""Статические кандидаты формул из явной передачи method group в primary constructor."""

from copy import deepcopy
from datetime import datetime, timezone
import re

from .interface_source import SourceModel, text, type_parts, walk, MAX_FILES, MAX_BYTES, MAX_AST_NODES

MODULE_VERSION = "csharp-method-group-v1"
MAX_BINDINGS = 2000


def _shadowed_delegate_names(model):
    """Отказывается от короткого BCL имени при custom delegate или using alias в прочитанном корпусе."""
    names = set()
    for source in model.files.values():
        # AST проверяет объявления; строки и комментарии не становятся type facts.
        if b"delegate" not in source and not re.search(rb"using\s+(?:Func|Action)\s*=", source):
            continue
        root = model.parser.parse(source).root_node
        file_namespace = next((text(node.child_by_field_name("name"), source) for node in root.named_children
                               if node.type == "file_scoped_namespace_declaration"), "")
        for node in walk(root):
            if node.type == "delegate_declaration":
                name = text(node.child_by_field_name("name"), source)
                if name in {"Func", "Action"}:
                    names.add(name)
                    namespaces = []
                    ancestor = node.parent
                    while ancestor:
                        if ancestor.type == "namespace_declaration":
                            namespaces.append(text(ancestor.child_by_field_name("name"), source))
                        ancestor = ancestor.parent
                    namespace = ".".join([file_namespace, *reversed(namespaces)]).strip(".")
                    if namespace == "System":
                        names.add("System." + name)
            elif node.type == "using_directive":
                match = re.match(r"(?:global\s+)?using\s+(Func|Action)\s*=", text(node, source))
                if match:
                    names.add(match[1])
    return names


def _member_syntax(root):
    """Обходит только тело текущего члена: вложенные функции имеют другой лексический контекст."""
    pending = [root]
    boundaries = {"lambda_expression", "anonymous_method_expression", "local_function_statement", "class_declaration"}
    while pending:
        node = pending.pop()
        if node != root and node.type in boundaries:
            continue
        yield node
        pending.extend(reversed(node.named_children))


def _primary_parameters(owner, source):
    """Получает упорядоченные параметры primary constructor без поиска соседнего конструктора."""
    parameters = next((child for child in owner["ast"].named_children if child.type == "parameter_list"), None)
    result = []
    for parameter in parameters.named_children if parameters else []:
        if parameter.type == "parameter":
            result.append({"name": text(parameter.child_by_field_name("name"), source),
                           "type": text(parameter.child_by_field_name("type"), source)})
    return result


def _callback_signature(parameter, owner, substitution, model):
    """Разрешает только Func/Action с известными типами; пользовательский одноимённый тип исключается."""
    base, arguments, array = type_parts(parameter["type"])
    if array or base not in {"Func", "System.Func", "Action", "System.Action"}:
        return None
    if base in getattr(model, "callback_shadows", set()):
        return None
    if base in {"Func", "Action"} and model.resolve(parameter["type"], owner, substitution) is not None:
        return None
    resolved = [model.resolve(argument, owner, substitution) for argument in arguments]
    if any(value is None for value in resolved):
        return None
    if base.endswith("Func"):
        return (resolved[:-1], resolved[-1]) if resolved else None
    return resolved, "System.Void"


def _callback_callers(owner, parameter_name, model):
    """Находит прямой вызов constructor-параметра, исключая его локальное затенение."""
    source = model.files[owner["file"]]
    callers = []
    # Присваивание captured параметру разрушает прямую привязку constructor→call.
    for node in walk(owner["ast"]):
        if node.type == "assignment_expression" and text(node.child_by_field_name("left"), source) == parameter_name:
            return callers
    for method in owner["methods"]:
        if not method["id"] or method["constructor"] or any(p["name"] == parameter_name for p in method["parameters"]):
            continue
        syntax = list(_member_syntax(method["ast"]))
        if any(node.type == "variable_declarator" and text(node.child_by_field_name("name"), source) == parameter_name for node in syntax):
            continue
        for node in syntax:
            if node.type != "invocation_expression":
                continue
            function = node.child_by_field_name("function")
            if function and function.type == "identifier" and text(function, source) == parameter_name:
                callers.append((method, node.start_point.row + 1))
    return callers


def _method_group(argument, context, model, expected):
    """Сопоставляет new Type().Method с единственной совместимой сигнатурой существующего raw метода."""
    source = model.files[context["file"]]
    expressions = [child for child in argument.named_children if child.type == "member_access_expression"]
    if len(expressions) != 1:
        return None
    expression = expressions[0]
    receiver = expression.child_by_field_name("expression")
    if receiver is None or receiver.type != "object_creation_expression":
        return None
    resolved = model.resolve(text(receiver.child_by_field_name("type"), source), context)
    target_owner = model.definition(resolved) if resolved else None
    if target_owner is None:
        return None
    _, type_arguments, _ = type_parts(resolved)
    substitution = dict(zip(target_owner["typeParameters"], type_arguments))
    name = text(expression.child_by_field_name("name"), source)
    arguments, returns = expected
    compatible = []
    for method in target_owner["methods"]:
        if method["name"] != name or not method["id"] or method["genericParameters"]:
            continue
        signature = model.signature(method, substitution)
        return_type = model.resolve(method["returns"], target_owner, substitution)
        if signature and signature[2] == tuple(("", value) for value in arguments) and return_type == returns:
            compatible.append(method)
    return compatible[0] if len(compatible) == 1 else None


def _creation_bindings(creation, factory, model):
    """Доказывает передачу method group конкретному параметру и сохраняет generic substitution."""
    context = factory["owner"]
    source = model.files[context["file"]]
    canonical = model.resolve(text(creation.child_by_field_name("type"), source), context)
    owner = model.definition(canonical) if canonical else None
    if owner is None or not owner["id"]:
        return []
    parameters = _primary_parameters(owner, model.files[owner["file"]])
    argument_list = creation.child_by_field_name("arguments")
    arguments = [child for child in argument_list.named_children if child.type == "argument"] if argument_list else []
    if len(arguments) != len(parameters):
        return []
    _, type_arguments, _ = type_parts(canonical)
    substitution = dict(zip(owner["typeParameters"], type_arguments))
    bindings = []
    for index, parameter in enumerate(parameters):
        named = [argument for argument in arguments if text(argument.child_by_field_name("name"), source) == parameter["name"]]
        argument = named[0] if len(named) == 1 else arguments[index]
        # Не сопоставляем переставленные named arguments по позициям.
        given_name = text(argument.child_by_field_name("name"), source)
        if given_name and given_name != parameter["name"]:
            continue
        expected = _callback_signature(parameter, owner, substitution, model)
        target = _method_group(argument, context, model, expected) if expected else None
        if target is None:
            continue
        for caller, line in _callback_callers(owner, parameter["name"], model):
            bindings.append({"source": caller["id"], "target": target["id"], "relation": "dispatches_to",
                             "confidence": "INFERRED", "_origin": "method-group-dispatch",
                             "source_file": owner["file"], "source_location": f"L{line}", "lineKind": "snapshot-captured-ast",
                             "evidence": {"kind": "method-group-binding", "parameter": parameter["name"],
                                          "receiverType": canonical, "declaringType": target["owner"]["qualifiedName"],
                                          "bindingFile": context["file"], "bindingLine": creation.start_point.row + 1,
                                          "factoryNodeId": factory["id"], "runtimeSelectionKnown": False}})
    return bindings


def enrich_delegate_dispatch(graph, project_root):
    """Дополняет копию снимка кандидатами callbacks; неизвестные выражения остаются неразрешёнными."""
    model = SourceModel(project_root, graph)
    model.callback_shadows = _shadowed_delegate_names(model)
    result = deepcopy(graph)
    edges = result["edges"]
    seen = {(edge["source"], edge["target"], edge.get("relation")) for edge in edges}
    count = 0
    limited = False
    for factory in model.methods:
        if not factory["id"]:
            continue
        for creation in _member_syntax(factory["ast"]):
            if creation.type != "object_creation_expression":
                continue
            for edge in _creation_bindings(creation, factory, model):
                key = edge["source"], edge["target"], edge["relation"]
                if key in seen:
                    continue
                if count >= MAX_BINDINGS:
                    limited = True
                    break
                seen.add(key)
                edges.append(edge)
                count += 1
            if limited:
                break
        if limited:
            break
    result.setdefault("metadata", {})["delegateDispatch"] = {
        "version": MODULE_VERSION, "sourceKind": "snapshot-captured-ast", "capturedAt": datetime.now(timezone.utc).isoformat(),
        "sourceFingerprint": model.fingerprint, "derivedDispatch": count, "truncated": limited or model.truncated,
        "files": len(model.files), "bytes": model.bytes, "warnings": list(model.warnings)[:6],
        "limits": {"bindings": MAX_BINDINGS, "files": MAX_FILES, "bytes": MAX_BYTES, "astNodes": MAX_AST_NODES},
        "scope": "Explicit new Type().Method passed to a Func/Action primary-constructor parameter; runtime instance choice is unknown.",
    }
    result["metadata"]["edgeCount"] = len(edges)
    return result
