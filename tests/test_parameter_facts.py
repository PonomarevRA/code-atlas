"""Параметры и callsites подтверждаются выбранным AST, без перегрузок и alias/dataflow."""

from copy import deepcopy

from atlas.execution_path import build_execution_path
from atlas.execution_path_source import SourceAnalysis
from atlas.node_description import build_node_description


def _project(tmp_path, text: str, label: str = "Run"):
    """Создаёт отдельный C#-исходник и ровно один выбранный метод графа."""
    project = tmp_path / "source"
    project.mkdir()
    (project / "Handler.cs").write_text(text)
    graph = {"nodes": [{"id": "run", "label": label, "file": "Handler.cs", "kind": "method"}],
             "edges": [], "metadata": {}}
    return project, graph


def test_actual_arguments_keep_nested_expressions_and_named_modifiers(tmp_path):
    """Запятые внутри выражений не делят аргумент; позиция не становится именем параметра callee."""
    project, graph = _project(tmp_path, '''class Handler {
      void Run(Request request, ref int count) {
        Accept(new Value(1, request.X), request.Items.Select(x => x.Id), value: request.X > 0 ? Pair(1, 2) : 0, ref count);
      }
    }''')
    result = SourceAnalysis(project, "s", 1, 1024 * 1024, 160).describe(graph["nodes"][0])
    event = next(event for event in result["events"] if event.get("callee") == "Accept")
    assert [argument["expression"] for argument in event["arguments"]] == [
        "new Value(1, request.X)", "request.Items.Select(x => x.Id)", "request.X > 0 ? Pair(1, 2) : 0", "count"]
    assert [argument["position"] for argument in event["arguments"]] == [1, 2, 3, 4]
    assert event["arguments"][2]["name"] == "value"
    assert event["arguments"][3]["modifier"] == "ref"
    assert event["arguments"][0]["parameterReferences"] == ["request"]
    assert event["arguments"][3]["parameterReferences"] == ["count"]
    assert event["argumentKind"] == "lexical-source"
    assert "formalParameter" not in event["arguments"][0]


def test_formals_include_defaults_modifiers_flat_params_and_escaped_names(tmp_path):
    """params в C# grammar может быть плоским; типы/default сохраняются без вычисления."""
    project, graph = _project(tmp_path, '''class Handler {
      void Run([FromBody] Request @request, in int count, string label = "Cash", params Item[] items) {
        if (@request.Enabled) Accept(items);
      }
    }''')
    result = build_node_description(project, graph, "run", "s")
    facts = result["parameterFacts"]
    assert [parameter["name"] for parameter in facts["parameters"]] == ["request", "count", "label", "items"]
    assert facts["parameters"][1]["modifier"] == "in"
    assert facts["parameters"][2]["defaultValue"] == '"Cash"'
    assert facts["parameters"][3]["modifier"] == "params"
    assert facts["parameters"][3]["type"] == "Item[]"
    assert facts["conditions"][0]["parameterReferences"] == ["request"]


def test_conditions_reference_only_direct_parameter_identifiers(tmp_path):
    """Строки, имена свойств и локальные alias не создают связи параметр→условие."""
    project, graph = _project(tmp_path, '''class Handler {
      void Run(Request request, int count, object items) {
        var alias = request;
        if (request.Enabled) Accept(request);
        if (alias.Enabled) Ignore();
        if (obj.request != null) Ignore();
        if ("request" == text) Ignore();
        foreach (var item in request.Products) Accept(item);
        for (var i = count; i > 0; i--) Accept(i);
        while (count > 0) break;
        do { } while (request.Enabled);
        switch (request.Kind) { case 1 when count > 0: break; }
        var result = count switch { > 0 => 1, _ => 0 };
        void Nested(Request request) { if (request.Hidden) Ignore(request); }
        System.Func<Request, bool> lambda = request => { if (request.Hidden) return true; return false; };
      }
    }''')
    result = build_execution_path(project, graph, "run", "s")["steps"][0]
    conditions = result["parameterFacts"]["conditions"]
    expressions = {condition["expression"] for condition in conditions}
    assert expressions == {"request.Enabled", "request.Products", "var i = count", "count > 0", "request.Kind", "when count > 0", "count"}
    assert {condition["kind"] for condition in conditions} == {"if", "foreach", "for", "while", "do", "switch", "when", "switch-expression"}
    branches = {branch["id"] for branch in result["controlFlow"]["branches"]}
    assert all(condition["branchId"] is None or condition["branchId"] in branches for condition in conditions)
    assert all("Hidden" not in expression and "alias" not in expression and "obj" not in expression for expression in expressions)
    assert result["parameterFacts"]["kind"] == "lexical-source"
    assert "влияние через промежуточные переменные" in result["parameterFacts"]["warning"]


def test_lambda_arguments_do_not_claim_outer_parameter_context(tmp_path):
    """Лексические аргументы lambda видны, но её собственный параметр не приписывается методу."""
    project, graph = _project(tmp_path, '''class Handler {
      void Run(Request request) { var action = request => Accept(request); Accept(obj.request, "request"); }
    }''')
    result = SourceAnalysis(project, "s", 1, 1024 * 1024, 160).describe(graph["nodes"][0])
    events = [event for event in result["events"] if event.get("callee") == "Accept"]
    assert all(not argument["parameterReferences"] for event in events for argument in event["arguments"])


def test_ambiguous_overloads_do_not_supply_formals_or_argument_mapping(tmp_path):
    """Имя без строки не выбирает соседнюю перегрузку и не выдаёт её параметры за доказательство."""
    project, graph = _project(tmp_path, "class Handler { void Run(int x) {} void Run(string x) {} }")
    result = build_node_description(project, graph, "run", "s")
    assert result["sourceStatus"] == "unresolved"
    assert result["parameterFacts"]["parameters"] == []
    assert result["events"] == []


def test_parameter_argument_and_condition_limits_are_explicit(tmp_path):
    """Ограничения не подменяют полноту: длинное выражение, список и условия отмечаются отдельно."""
    text = "class Handler { void Run(int request) { Accept(" + ",".join("request" for _ in range(45)) + ");"
    text += "if (request > 0) Accept();" * 105 + "} }"
    project, graph = _project(tmp_path, text)
    result = build_node_description(project, graph, "run", "s")
    event = next(event for event in result["events"] if event.get("callee") == "Accept")
    assert len(event["arguments"]) == 40 and event["argumentsTruncated"]
    assert len(result["parameterFacts"]["conditions"]) == 100
    assert result["parameterFacts"]["truncated"]
    assert result["truncation"]["parameterFacts"] and result["truncation"]["arguments"]


def test_formal_and_expression_limits_preserve_explicit_truncation(tmp_path):
    """Параметры сверх лимита и длинное выражение не сериализуются как полные списки/значения."""
    parameters = ",".join(f"string p{index}" for index in range(42))
    long_expression = 'p0 + "' + "x" * 700 + '"'
    project, graph = _project(tmp_path, f"class Handler {{ void Run({parameters}) {{ Accept({long_expression}); }} }}")
    result = build_node_description(project, graph, "run", "s")
    assert len(result["parameterFacts"]["parameters"]) == 40
    assert result["parameterFacts"]["truncated"]
    argument = next(event for event in result["events"] if event.get("callee") == "Accept")["arguments"][0]
    assert len(argument["expression"]) == 600 and argument["truncated"]
    assert argument["parameterReferences"] == ["p0"]


def test_incoming_call_sites_keep_source_expressions_and_candidate_confidence(tmp_path):
    """Выбранный callee показывает аргументы caller, без позиционного сопоставления формальных параметров."""
    project, graph = _project(tmp_path, '''class Handler {
      void Run(Request request) { Accept(request.Amount, currency: "RUB"); }
      void Accept(decimal amount, string currency) { }
    }''')
    graph["nodes"].append({"id": "accept", "label": "Accept", "file": "Handler.cs", "line": 3})
    graph["edges"].append({"source": "run", "target": "accept", "relation": "calls"})
    before = deepcopy(graph)
    result = build_node_description(project, graph, "accept", "s")
    assert graph == before
    assert len(result["incomingCallSites"]) == 1
    site = result["incomingCallSites"][0]
    assert site["callerNodeId"] == "run" and site["callerLabel"] == "Run"
    assert site["confidence"] == "candidate", "name-only graph binding stays uncertain"
    assert [argument["expression"] for argument in site["arguments"]] == ["request.Amount", '"RUB"']
    assert site["arguments"][1]["name"] == "currency"
    assert site["arguments"][0]["parameterReferences"] == ["request"]
    assert not result["incomingCallSitesTruncated"]


def test_incoming_call_sites_respect_shared_file_and_caller_budget(tmp_path, monkeypatch):
    """Десятки incoming узлов не вызывают неограниченное чтение; maxFiles включает callee."""
    from atlas import execution_path_source
    project, graph = _project(tmp_path, "class Target { void Run(int value) { } }")
    for index in range(12):
        filename = f"Caller{index}.cs"
        (project / filename).write_text(f"class Caller{index} {{ void Invoke(int value) {{ Run(value); }} }}")
        graph["nodes"].append({"id": f"caller-{index}", "label": "Invoke", "file": filename})
        graph["edges"].append({"source": f"caller-{index}", "target": "run", "relation": "calls"})
    reads = []
    original = execution_path_source.read_node_source

    def record(root, node, snapshot, **kwargs):
        """Считает фактические безопасные чтения, а не попытки AST сопоставления."""
        reads.append(node["id"])
        return original(root, node, snapshot, **kwargs)

    monkeypatch.setattr(execution_path_source, "read_node_source", record)
    result = build_node_description(project, graph, "run", "s")
    assert len(reads) == 5
    assert len(result["incomingCallSites"]) == 4
    assert result["incomingCallSitesTruncated"] and result["truncation"]["files"]
    assert result["limits"]["maxFiles"] == 5 and result["limits"]["maxIncomingCallers"] == 8


def test_incoming_call_sites_cap_repeated_invocations(tmp_path):
    """Один caller с множеством вызовов не обходит лимит возвращённых callsites."""
    project, graph = _project(tmp_path, "class Handler { void Run(int value) {"
                              + "Accept(value);" * 45 + "} void Accept(int value) {} }")
    graph["nodes"].append({"id": "accept", "label": "Accept", "file": "Handler.cs"})
    graph["edges"].append({"source": "run", "target": "accept", "relation": "calls"})
    result = build_node_description(project, graph, "accept", "s")
    assert len(result["incomingCallSites"]) == 40
    assert result["incomingCallSitesTruncated"] and result["truncation"]["incomingCallSites"]
