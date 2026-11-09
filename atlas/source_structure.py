"""Связи наследования и чтения полей и исходных данных в уже разобранном C# corpus."""
from copy import deepcopy
import hashlib

from .interface_source import METHOD_KINDS, TYPE_KINDS, text, type_parts, walk
from .static_data import static_field_data
from .constructor_structure import capture_constructors
from .member_access import access_relations
from .member_schema import member_facts, member_id, initializer_data
from .code_complexity import analyze_complexity
from .code_health import capture_health_facts

MAX_FIELDS = 12000
MAX_EDGES = 16000
MAX_DATA_CHARS = 8 * 1024 * 1024
MAX_TOTAL_ENTRIES = 12000
VERSION = "csharp-source-structure-v3-all-members"


def _definition(model, value, owner):
    """Имя и generic arity определяют объявление; аргументы сохраняются буквально, constraints не проверяются."""
    base, arguments, array = type_parts(value)
    if array or not base:
        return None
    lookup = base + ("<" + ",".join("object" for _ in arguments) + ">" if arguments else "")
    canonical = model.resolve(lookup, owner)
    return model.definition(canonical) if canonical else None


def _references(ast, source, owner, model, fields, field_names, *, method=False):
    """Сопоставляет видимые имена с полями; локальное затенение и неоднозначные типы не угадываются."""
    shadowed = set()
    if method:
        for child in walk(ast):
            if child.type in {"variable_declarator", "parameter"}:
                shadowed.add(text(child.child_by_field_name("name"), source))
            elif child.type == "foreach_statement":
                shadowed.add(text(child.child_by_field_name("left"), source))
    pending = [ast]
    while pending:
        child = pending.pop()
        if child != ast and child.type in TYPE_KINDS | METHOD_KINDS | {"local_function_statement", "lambda_expression", "anonymous_method_expression"}:
            continue
        target_owner, name = None, ""
        if child.type == "member_access_expression":
            receiver = text(child.child_by_field_name("expression"), source)
            name = text(child.child_by_field_name("name"), source)
            if name not in field_names:
                target_owner = None
            elif receiver in {"this", "base"}:
                target_owner = owner if receiver == "this" else None
            elif receiver not in shadowed:
                target_owner = _definition(model, receiver, owner)
        elif child.type == "identifier":
            name = text(child, source)
            parent = child.parent
            receiver_field = parent is not None and parent.type == 'member_access_expression' and child == parent.child_by_field_name('expression')
            if name not in shadowed and (receiver_field or parent is None or parent.type not in {"member_access_expression", "qualified_name", "variable_declarator", "parameter"}):
                target_owner = owner
        if target_owner and (target_owner["id"], name) in fields:
            target = fields[(target_owner["id"], name)]
            for relation in access_relations(child):
                yield target, relation, child.start_point.row + 1
        pending.extend(reversed(child.named_children))


def enrich_source_structure(graph: dict, model, ambiguous_ids: set) -> dict:
    """Добавляет bounded факты к копии графа, переиспользуя AST dispatch-pass; не переписывает исходные связи."""
    nodes = deepcopy(graph.get("nodes", []))
    by_id = {node["id"]: node for node in nodes}
    additions = []
    seen = {(edge.get("source"), edge.get("target"), edge.get("relation")) for edge in graph.get("edges", [])}
    fields, declarations = {}, []
    limited = False
    data_chars = total_entries = 0

    def edge(source, target, relation, owner, line, **details):
        """Не дублирует факты и сохраняет источник декларации/использования."""
        nonlocal limited
        if source == target or (source, target, relation) in seen:
            return
        if len(additions) >= MAX_EDGES:
            limited = True
            return
        seen.add((source, target, relation))
        additions.append({"source": source, "target": target, "relation": relation, "confidence": "EXTRACTED",
                          "_origin": "csharp-source-structure", "source_file": owner["file"], "source_location": f"L{line}",
                          "evidence": {"kind": relation, "sourceKind": "snapshot-captured-source", "file": owner["file"],
                                       "line": line, "runtimeSelectionKnown": False, **details}})

    # Пропущенные private/public методы становятся source nodes до связывания конструктора.
    for method in model.methods:
        owner = method['owner']
        if method['constructor'] or method['id'] or not owner['id'] or owner['id'] in ambiguous_ids:
            continue
        parameters = text(method['ast'].child_by_field_name('parameters'), model.files[owner['file']])
        identifier = member_id('csharp', owner['file'], owner['qualifiedName'], 'method', method['name'], parameters)
        if identifier in by_id or len(nodes) - len(graph.get('nodes', [])) >= MAX_FIELDS:
            limited = True
            continue
        method['id'] = identifier
        node = {'id': identifier, 'label': '.' + method['name'] + '()', 'kind': 'method', '_callable': True,
                'file': owner['file'], 'line': method['ast'].start_point.row + 1, '_origin': 'csharp-source-structure'}
        nodes.append(node); by_id[identifier] = node
        edge(owner['id'], identifier, 'method', owner, node['line'])
    constructors, constructor_limited = capture_constructors(model, nodes, by_id, ambiguous_ids, edge, _definition)
    limited |= constructor_limited
    source_hashes = {filename: hashlib.sha256(source).hexdigest() for filename, source in model.files.items()}
    for method in model.methods:
        if method['id'] in by_id and method['owner']['id'] not in ambiguous_ids:
            by_id[method['id']]['memberFacts'] = member_facts('csharp', 'constructor' if method['constructor'] else 'method',
                method['name'], method['owner']['id'], method['modifiers'], owner_kind=method['owner']['kind'])
            filename = method['owner']['file']
            by_id[method['id']]['complexity'] = analyze_complexity(method['ast'], model.files[filename], 'csharp',
                source_kind='snapshot-captured-source', source_hash=source_hashes[filename])
            capture_health_facts(by_id[method['id']], method['ast'], model.files[filename], 'csharp',
                filename=filename, source_hash=source_hashes[filename])

    for owner in model.types:
        identifier = owner["id"]
        if not identifier or identifier in ambiguous_ids:
            continue
        source = model.files[owner["file"]]
        for base in owner["baseTypes"]:
            parent = _definition(model, base, owner)
            if parent is None or not parent["id"] or parent["id"] in ambiguous_ids:
                continue
            relation = "implements" if parent["kind"] == "interface" and owner["kind"] != "interface" else "inherits"
            # Graphify versions can spell the same inheritance fact differently.
            if any((identifier, parent["id"], kind) in seen for kind in (relation, "inherits_from", "extends") if relation != "implements" or kind == relation):
                continue
            edge(identifier, parent["id"], relation, owner, owner["ast"].start_point.row + 1, declaredType=base)
        body = owner["ast"].child_by_field_name("body")
        for declaration in body.named_children if body else []:
            if declaration.type not in {"field_declaration", "property_declaration"}:
                continue
            variable = next((child for child in declaration.named_children if child.type == "variable_declaration"), None)
            declarators = variable.named_children if variable else [declaration]
            for item in declarators:
                if item.type not in {"variable_declarator", "property_declaration"}:
                    continue
                name = text(item.child_by_field_name("name"), source)
                facts = static_field_data(declaration, name, source)
                initializer = item.child_by_field_name('value') or next((child for child in item.named_children if child.type in {'equals_value_clause', 'arrow_expression_clause'}), None)
                if initializer is None and item.type == 'variable_declarator':
                    initializer = next((child for child in item.named_children if child != item.child_by_field_name('name')), None)
                data = facts or static_field_data(declaration, name, source, include_instance=True) or initializer_data(initializer, source, 'csharp')
                modifiers = [text(child, source) for child in declaration.named_children if child.type == 'modifier']
                member = member_facts('csharp', 'property' if declaration.type == 'property_declaration' else 'field', name, identifier, modifiers, owner_kind=owner['kind'])
                facts_for_budget = data or {'initializer': '', 'entries': []}
                fact_chars = len(facts_for_budget['initializer']) + sum(len(e['key']) + len(e['value']) for e in facts_for_budget['entries'])
                if len(fields) >= MAX_FIELDS or data_chars + fact_chars > MAX_DATA_CHARS or total_entries + len(facts_for_budget['entries']) > MAX_TOTAL_ENTRIES:
                    limited = True
                    continue
                data_chars += fact_chars
                total_entries += len(facts_for_budget['entries'])
                field_id = model._raw_id(owner["file"], declaration, name, owner_id=identifier)
                if not field_id:
                    identity = f"{owner['file']}:{owner['qualifiedName']}`{len(owner['typeParameters'])}:{name}"
                    field_id = "csharp-static:" + hashlib.sha256(identity.encode()).hexdigest()[:24] if facts else member_id("csharp", owner["file"], owner["qualifiedName"], member["kind"], name)
                    if field_id in by_id:
                        limited = True
                        continue
                    node = {"id": field_id, "label": name, "kind": member["kind"], "file": owner["file"], "line": declaration.start_point.row + 1,
                            "_origin": "csharp-source-structure", "architectureRole": "data"}
                    nodes.append(node); by_id[field_id] = node
                by_id[field_id]['memberFacts'] = member
                if data:
                    by_id[field_id]['dataFacts'] = data
                if facts:
                    by_id[field_id]['staticData'] = facts
                fields[(identifier, name)] = field_id
                declarations.append((field_id, owner, item))
                edge(identifier, field_id, member["kind"], owner, declaration.start_point.row + 1)

    # Полный индекс полей готов до связывания ссылок из соседнего класса/файла.
    field_names = {name for _, name in fields}
    for field_id, owner, declarator in declarations:
        source = model.files[owner["file"]]
        initializer = next((child for child in declarator.named_children if child != declarator.child_by_field_name("name")), None)
        if initializer is None:
            continue
        for target, relation, line in _references(initializer, source, owner, model, fields, field_names):
            edge(field_id, target, relation, owner, line)
        if initializer.type == "invocation_expression":
            function = initializer.child_by_field_name("function")
            arguments = initializer.child_by_field_name("arguments")
            name = text(function, source)
            factories = [method for method in owner["methods"] if method["name"] == name and method["id"]
                         and not method["parameters"] and "static" in method.get("modifiers", [])]
            if arguments is not None and not arguments.named_children and len(factories) == 1:
                edge(field_id, factories[0]["id"], "initializes_from", owner, initializer.start_point.row + 1)
    for method in model.methods if fields else []:
        if not method["id"] or method["owner"]["id"] in ambiguous_ids:
            continue
        owner = method["owner"]
        for target, relation, line in _references(method["ast"], model.files[owner["file"]], owner, model, fields, field_names, method=True):
            edge(method["id"], target, relation, owner, line)
    metadata = {**graph.get("metadata", {}), "sourceStructure": {"version": VERSION, "sourceKind": "snapshot-captured-source",
                "sourceFingerprint": model.fingerprint, "constructors": constructors, "fields": len(fields), "staticFields": sum(bool(by_id[i].get("staticData")) for i in fields.values()), "addedEdges": len(additions),
                "truncated": limited or model.truncated, "limits": {"maxFields": MAX_FIELDS, "maxEdges": MAX_EDGES,
                "maxDataChars": MAX_DATA_CHARS, "maxTotalEntries": MAX_TOTAL_ENTRIES},
                "warning": "Лексические объявления и ссылки. Динамические значения, aliases, using static, наследуемое затенение и runtime binding не вычисляются."}}
    return {**graph, "nodes": nodes, "edges": [*graph.get("edges", []), *additions], "metadata": metadata}
