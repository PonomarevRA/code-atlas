"""Статические interface receiver calls и совместимые реализации, без выбора DI/runtime."""
from collections import defaultdict, deque
from datetime import datetime, timezone

from .interface_source import SourceModel, name_of, text, type_parts, walk, TYPE_KINDS, METHOD_KINDS, MAX_FILES, MAX_BYTES, MAX_AST_NODES, interface_source_stamp
from .source_structure import enrich_source_structure

MODULE_VERSION = "csharp-interface-dispatch-v1"
MAX_DERIVED_EDGES = 12000
MAX_INHERITANCE_DEPTH = 48


def graph_dispatch_edges(graph: dict, nodes: dict | None = None) -> list[dict]:
    """Old snapshots: exact implements/inherits paths plus unambiguous owner-local method name.

    Enriched snapshots already contain signature-checked AST edges, so this fallback cannot
    reintroduce a candidate rejected by their overload/type checks.
    """
    if graph.get("metadata", {}).get("interfaceDispatch", {}).get("version") == MODULE_VERSION:
        return []
    nodes = nodes or {node["id"]: node for node in graph.get("nodes", [])}
    methods, bases, implements = defaultdict(list), defaultdict(list), defaultdict(list)
    existing = {(edge.get("source"), edge.get("target")) for edge in graph.get("edges", []) if edge.get("relation") == "dispatches_to"}
    for edge in graph.get("edges", []):
        source, target = edge.get("source"), edge.get("target")
        if source not in nodes or target not in nodes: continue
        relation = edge.get("relation")
        if relation == "method": methods[source].append(target)
        elif relation in {"inherits", "inherits_from", "extends"}: bases[source].append(target)
        elif relation == "implements": implements[source].append(target)
    result = []
    for implementation in sorted(set(implements) | set(bases)):
        ancestry, pending = [], deque([(implementation, 0)])
        seen = set()
        interfaces = set()
        while pending:
            owner, depth = pending.popleft()
            if owner in seen or depth >= MAX_INHERITANCE_DEPTH: continue
            seen.add(owner); ancestry.append(owner)
            interfaces.update(implements.get(owner, []))
            pending.extend((base, depth + 1) for base in bases.get(owner, []))
        pending = deque(interfaces)
        while pending:
            interface = pending.popleft()
            for base in bases.get(interface, []):
                if base not in interfaces: interfaces.add(base); pending.append(base)
        for interface in sorted(interfaces):
            for method in methods.get(interface, []):
                name = name_of(nodes[method].get("label", ""))
                same_interface = [item for item in methods[interface] if name_of(nodes[item].get("label", "")) == name]
                if not name or len(same_interface) != 1: continue
                for owner in ancestry:
                    candidates = [item for item in methods.get(owner, []) if name_of(nodes[item].get("label", "")) == name]
                    if len(candidates) != 1:
                        if candidates: break
                        continue
                    candidate = candidates[0]
                    if candidate != method and (method, candidate) not in existing:
                        existing.add((method, candidate))
                        result.append({"source": method, "target": candidate, "relation": "dispatches_to", "confidence": "INFERRED", "_origin": "interface-dispatch", "lineKind": "snapshot", "evidence": {"kind": "owner-local-implements", "interfaceType": str(nodes[interface].get("label", ""))[:300], "implementationType": str(nodes[implementation].get("label", ""))[:300], "sourceKind": "snapshot", "signatureKnown": False}})
                    break
    return result[:MAX_DERIVED_EDGES]


def _scope(node, boundary):
    """Ближайший lexical scope не позволяет локальной переменной протечь в соседний блок."""
    while node and node != boundary:
        if node.type in {"block", "foreach_statement", "for_statement", "lambda_expression", "switch_section"}: return node
        node = node.parent
    return boundary


def _within(node, scope) -> bool:
    """Проверяет диапазон AST, включая scope самого foreach/for."""
    return scope.start_byte <= node.start_byte and node.end_byte <= scope.end_byte


class _ReceiverResolver:
    """Типизация только известных fields/parameters/locals/collection elements, без исполнения выражений."""

    def __init__(self, model, method):
        """Формирует lexical bindings метода; неоднозначный var initializer остаётся неизвестным."""
        self.model, self.method = model, method
        self.owner = method["owner"]
        self.source = model.files[self.owner["file"]]
        self.bindings = []
        generic = {name: f"!{index}" for index, name in enumerate(method["genericParameters"])}
        for parameter in method["parameters"]:
            self.bindings.append((parameter["name"], model.resolve(parameter["type"], self.owner, generic), method["ast"], -1))
        self.calls = []
        pending = [method["ast"]]
        while pending:
            node = pending.pop()
            if node != method["ast"] and node.type in TYPE_KINDS | METHOD_KINDS | {"local_function_statement", "lambda_expression", "anonymous_method_expression"}: continue
            if node.type == "variable_declaration":
                declared = text(node.child_by_field_name("type"), self.source)
                for variable in node.named_children:
                    if variable.type != "variable_declarator": continue
                    name = text(variable.child_by_field_name("name"), self.source)
                    initializer = next((child for child in variable.named_children if child != variable.child_by_field_name("name")), None)
                    value = self.infer(initializer, node) if declared == "var" else model.resolve(declared, self.owner, generic)
                    self.bindings.append((name, value, _scope(node.parent, method["ast"]), variable.end_byte))
            elif node.type == "foreach_statement":
                declared = text(node.child_by_field_name("type"), self.source)
                value = model.element_type(self.infer(node.child_by_field_name("right"), node)) if declared == "var" else model.resolve(declared, self.owner, generic)
                self.bindings.append((text(node.child_by_field_name("left"), self.source), value, node, node.start_byte))
            elif node.type == "invocation_expression": self.calls.append(node)
            pending.extend(reversed(node.named_children))

    def lookup(self, name, at):
        """Сначала ближайшая visible local binding, затем объявленный instance field."""
        values = [binding for binding in self.bindings if binding[0] == name and binding[3] <= at.start_byte and _within(at, binding[2])]
        if values:
            return min(values, key=lambda value: (value[2].end_byte - value[2].start_byte, -value[3]))[1]
        return self.model.resolve(self.owner["fields"].get(name, ""), self.owner)

    def infer(self, expression, at):
        """Локальные returns/aliases/array indexing/LINQ materializers имеют консервативные правила."""
        if expression is None: return None
        kind = expression.type
        if kind == "identifier": return self.lookup(text(expression, self.source), at)
        if kind == "element_access_expression": return self.model.element_type(self.infer(expression.child_by_field_name("expression"), at))
        if kind == "member_access_expression":
            receiver = expression.child_by_field_name("expression")
            if receiver and receiver.type in {"this_expression", "this"}:
                field = text(expression.child_by_field_name("name"), self.source)
                return self.model.resolve(self.owner["fields"].get(field, ""), self.owner)
            return None
        if kind == "parenthesized_expression" and expression.named_children: return self.infer(expression.named_children[0], at)
        if kind == "cast_expression": return self.model.resolve(text(expression.child_by_field_name("type"), self.source), self.owner)
        if kind == "object_creation_expression": return self.model.resolve(text(expression.child_by_field_name("type"), self.source), self.owner)
        if kind == "integer_literal": return "System.Int32"
        if kind in {"string_literal", "verbatim_string_literal", "interpolated_string_expression"}: return "System.String"
        if kind == "boolean_literal": return "System.Boolean"
        if kind != "invocation_expression": return None
        function = expression.child_by_field_name("function")
        if function is None: return None
        name = name_of(text(function.child_by_field_name("name") or function, self.source))
        if function.type == "member_access_expression":
            receiver = function.child_by_field_name("expression")
            if receiver and receiver.type in {"this_expression", "this"}:
                arguments = expression.child_by_field_name("arguments")
                count = len(arguments.named_children) if arguments else 0
                returns = {self.model.resolve(method["returns"], self.owner) for method in self.owner["methods"] if method["name"] == name and len(method["parameters"]) == count}
                return returns.pop() if len(returns) == 1 else None
            receiver_type = self.infer(receiver, at)
            element = self.model.element_type(receiver_type)
            if element and name in {"ToArray", "ToList", "Where", "OrderBy", "OrderByDescending", "Distinct"}:
                return element + "[]" if name == "ToArray" else "collection:List<" + element + ">"
            return None
        arguments = expression.child_by_field_name("arguments")
        count = len(arguments.named_children) if arguments else 0
        candidates = [method for method in self.owner["methods"] if method["name"] == name and len(method["parameters"]) == count]
        returns = {self.model.resolve(method["returns"], self.owner) for method in candidates}
        return returns.pop() if len(returns) == 1 else None

    def interface_calls(self):
        """Связывает invocation только с единственным подходящим overload доказанного interface receiver."""
        for invocation in self.calls:
            function = invocation.child_by_field_name("function")
            if not function or function.type != "member_access_expression": continue
            receiver = self.infer(function.child_by_field_name("expression"), invocation)
            definition = self.model.definition(receiver) if receiver else None
            if not definition or definition["kind"] != "interface": continue
            name_node = function.child_by_field_name("name")
            name = name_of(text(name_node, self.source))
            generic_arguments = next((child for child in name_node.named_children if child.type == "type_argument_list"), None) if name_node else None
            arity = len(generic_arguments.named_children) if generic_arguments else 0
            arguments = invocation.child_by_field_name("arguments")
            argument_nodes = arguments.named_children if arguments else []
            candidates = []
            for interface, substitution in _interface_tree(self.model, definition, receiver):
                for method in interface["methods"]:
                    if method["name"] != name or len(method["genericParameters"]) != arity or len(method["parameters"]) != len(argument_nodes) or not method["id"]: continue
                    signature = self.model.signature(method, substitution)
                    if signature is None: continue
                    compatible = True
                    for argument, (modifier, expected) in zip(argument_nodes, signature[2]):
                        argument_text = text(argument, self.source).lstrip()
                        actual_modifier = next((value for value in ("ref", "out", "in") if argument_text.startswith(value + " ")), "")
                        expression = argument.named_children[-1] if argument.named_children else argument
                        actual = self.infer(expression, invocation)
                        if actual_modifier != modifier or (actual is not None and actual != expected and not expected.startswith("!")): compatible = False; break
                    if compatible: candidates.append((method, signature))
            if len(candidates) == 1: yield invocation, receiver, candidates[0][0], candidates[0][1]


def _base_tree(model, definition, resolved=None):
    """Обходит exact type hierarchy с generic substitutions, циклами и бюджетом глубины."""
    initial = resolved or definition["qualifiedName"] + ("<" + ",".join("$" + name for name in definition["typeParameters"]) + ">" if definition["typeParameters"] else "")
    pending = deque([(definition, initial, 0)])
    seen = set()
    while pending:
        current, value, depth = pending.popleft()
        if value in seen or depth >= MAX_INHERITANCE_DEPTH: continue
        seen.add(value)
        _, arguments, _ = type_parts(value)
        substitutions = dict(zip(current["typeParameters"], arguments))
        yield current, substitutions
        for base in current["baseTypes"]:
            canonical = model.resolve(base, current, substitutions)
            target = model.definition(canonical) if canonical else None
            if target: pending.append((target, canonical, depth + 1))


def _interface_tree(model, definition, resolved=None):
    """Выбирает только интерфейсы в exact inheritance hierarchy."""
    for current, substitution in _base_tree(model, definition, resolved):
        if current["kind"] == "interface": yield current, substitution


def _signature_text(signature):
    """Компактное текстовое свидетельство сохраняет arity/ref/type без AST объектов в JSON."""
    return (signature[0] + f"`{signature[1]}(" + ",".join((modifier + " " if modifier else "") + value for modifier, value in signature[2]) + ")")[:700]


def enrich_interface_dispatch(graph: dict, project_root) -> dict:
    """Возвращает новый граф с existing raw IDs; captured AST — кандидаты, не доказательство DI."""
    model = SourceModel(project_root, graph)
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    additions = []
    existing = {(edge.get("source"), edge.get("target"), edge.get("relation"), edge.get("source_location")) for edge in graph.get("edges", [])}
    seen_dispatch = {(edge.get("source"), edge.get("target")) for edge in graph.get("edges", []) if edge.get("relation") == "dispatches_to"}
    ambiguous_ids = {identifier for identifier in {definition["id"] for definition in model.types if definition["id"]} if len({(definition["qualifiedName"], len(definition["typeParameters"])) for definition in model.types if definition["id"] == identifier}) > 1}
    collapsed_types = defaultdict(list)
    for definition in model.types:
        collapsed_types[(definition["file"], definition["qualifiedName"])].append(definition)
    for definitions in collapsed_types.values():
        if len({len(definition["typeParameters"]) for definition in definitions}) > 1 and len({definition["id"] for definition in definitions if definition["id"]}) < len(definitions):
            ambiguous_ids.update(definition["id"] for definition in definitions if definition["id"])
    for implementation in model.types:
        if implementation["kind"] != "class" or not implementation["id"] or implementation["id"] in ambiguous_ids: continue
        hierarchy = list(_base_tree(model, implementation))
        for interface, substitution in hierarchy:
            if interface["kind"] != "interface" or not interface["id"] or interface["id"] in ambiguous_ids: continue
            for method in interface["methods"]:
                expected = model.signature(method, substitution)
                if not expected or not method["id"]: continue
                contract_modifiers = method.get("modifiers", [])
                # Concrete static helpers are not interface implementation contracts.
                if "static" in contract_modifiers and not {"abstract", "virtual"}.intersection(contract_modifiers): continue
                selected = None
                for owner, owner_substitution in hierarchy:
                    if owner["kind"] != "class": continue
                    candidates = []
                    for candidate in owner["methods"]:
                        if not candidate["id"] or candidate["constructor"] or model.signature(candidate, owner_substitution) != expected: continue
                        modifiers = candidate.get("modifiers", [])
                        if ("static" in modifiers) != ("static" in contract_modifiers): continue
                        explicit = candidate.get("explicitInterface")
                        if explicit:
                            target = model.resolve(explicit, owner, owner_substitution)
                            target_definition = model.definition(target) if target else None
                            if target_definition != interface: continue
                        elif "public" not in modifiers:
                            # Private/new hiding members do not replace inherited public implementations.
                            continue
                        candidates.append(candidate)
                    if len(candidates) == 1: selected = candidates[0]; break
                    if candidates: break
                if not selected or selected["id"] == method["id"] or (method["id"], selected["id"]) in seen_dispatch: continue
                seen_dispatch.add((method["id"], selected["id"]))
                additions.append({"source": method["id"], "target": selected["id"], "relation": "dispatches_to", "confidence": "INFERRED", "_origin": "interface-dispatch", "source_file": interface["file"], "source_location": f"L{method['ast'].start_point.row + 1}", "lineKind": "working-tree-ast", "evidence": {"kind": "interface-implementation", "interfaceType": interface["qualifiedName"], "implementationType": implementation["qualifiedName"], "signature": _signature_text(expected), "sourceKind": "working-tree-ast", "file": selected["owner"]["file"], "line": selected["ast"].start_point.row + 1, "implementationProjectId": nodes.get(implementation["id"], {}).get("codeProjectId"), "runtimeSelectionKnown": False}})
    for method in model.methods:
        if not method["id"] or method["constructor"]: continue
        resolver = _ReceiverResolver(model, method)
        for invocation, receiver, target, signature in resolver.interface_calls():
            if target["owner"]["id"] in ambiguous_ids: continue
            line = invocation.start_point.row + 1
            key = (method["id"], target["id"], "calls", f"L{line}")
            if key in existing: continue
            existing.add(key)
            additions.append({"source": method["id"], "target": target["id"], "relation": "calls", "confidence": "INFERRED", "_origin": "interface-dispatch", "source_file": method["owner"]["file"], "source_location": f"L{line}", "lineKind": "working-tree-ast", "evidence": {"kind": "typed-interface-call", "receiverType": receiver, "interfaceType": target["owner"]["qualifiedName"], "signature": _signature_text(signature), "sourceKind": "working-tree-ast", "file": method["owner"]["file"], "line": line, "runtimeSelectionKnown": False}})
    truncated = model.truncated or len(additions) > MAX_DERIVED_EDGES
    additions = additions[:MAX_DERIVED_EDGES]
    metadata = {**graph.get("metadata", {}), "interfaceDispatch": {"version": MODULE_VERSION, "sourceKind": "working-tree-ast", "capturedAt": datetime.now(timezone.utc).isoformat(), "sourceFingerprint": model.fingerprint, "files": len(model.files), "bytes": model.bytes, "astNodes": model.ast_nodes, "derivedCalls": sum(edge["relation"] == "calls" for edge in additions), "derivedDispatch": sum(edge["relation"] == "dispatches_to" for edge in additions), "ambiguousTypeIds": len(ambiguous_ids), "truncated": truncated, "warnings": ((["Достигнут бюджет AST/derived edges, исходник содержит ошибки или часть файлов недоступна."] if truncated else []) + (["Merged raw IDs разных generic types не разрешены по догадке."] if ambiguous_ids else []) + model.warnings)[:10], "limits": {"maxFiles": MAX_FILES, "maxBytes": MAX_BYTES, "maxAstNodes": MAX_AST_NODES, "maxDerivedEdges": MAX_DERIVED_EDGES}}}
    result = {**graph, "nodes": list(graph.get("nodes", [])), "edges": [*graph.get("edges", []), *additions], "metadata": metadata}
    return enrich_source_structure(result, model, ambiguous_ids)
