"""Method groups восстанавливают формулы без глобальных совпадений имени Calculate."""

from copy import deepcopy

from atlas.delegate_dispatch import enrich_delegate_dispatch


def callback_fixture(tmp_path, factory_argument="new Formula().Calculate", formula_parameter="int", formula_returns="int"):
    """Создаёт существующие raw IDs для primary constructor, его вызова и формул."""
    sources = {
        "Strategy.cs": "namespace Example;\npublic class Strategy<T>(Func<T,int> calculate) {\n public int Run(T value) => calculate(value);\n}\n",
        "Formula.cs": f"namespace Example;\npublic class Formula {{\n public {formula_returns} Calculate({formula_parameter} value) => 1;\n}}\n",
        "Factory.cs": f"namespace Example.Sub;\npublic class Factory {{\n public object Make() => new Strategy<int>({factory_argument});\n}}\n",
        "Unrelated.cs": "namespace Elsewhere;\npublic class Unrelated {\n public int Calculate(int value) => 0;\n}\n",
    }
    nodes, edges = [], []
    for filename, source in sources.items():
        (tmp_path / filename).write_text(source, encoding="utf-8")
        type_name = filename.removesuffix(".cs")
        method_name = {"Strategy": "Run", "Formula": "Calculate", "Factory": "Make", "Unrelated": "Calculate"}[type_name]
        nodes.extend([{"id": type_name, "label": type_name, "file": filename, "line": 2},
                      {"id": f"{type_name}.{method_name}", "label": f".{method_name}()", "file": filename, "line": 3}])
        edges.append({"source": type_name, "target": f"{type_name}.{method_name}", "relation": "method"})
    return {"nodes": nodes, "edges": edges, "metadata": {}}


def test_explicit_primary_constructor_method_group_is_candidate_with_binding_evidence(tmp_path):
    """Формула появляется только через типизированную регистрацию; исходный граф неизменён."""
    graph = callback_fixture(tmp_path)
    original = deepcopy(graph)
    result = enrich_delegate_dispatch(graph, tmp_path)
    edges = [edge for edge in result["edges"] if edge["relation"] == "dispatches_to"]
    assert [(edge["source"], edge["target"]) for edge in edges] == [("Strategy.Run", "Formula.Calculate")]
    assert edges[0]["confidence"] == "INFERRED"
    assert edges[0]["evidence"]["bindingFile"] == "Factory.cs"
    assert edges[0]["evidence"]["bindingLine"] == 3
    assert edges[0]["evidence"]["runtimeSelectionKnown"] is False
    assert edges[0]["source_location"] == "L3"
    assert graph == original


def test_incompatible_parameter_type_has_no_delegate_edge(tmp_path):
    """Func<int,int> не связывается с Calculate(string) при совпадающем имени."""
    graph = callback_fixture(tmp_path, formula_parameter="string")
    assert enrich_delegate_dispatch(graph, tmp_path)["metadata"]["delegateDispatch"]["derivedDispatch"] == 0


def test_lambda_and_unknown_receiver_remain_unresolved(tmp_path):
    """Не вычисляем lambda или переменную из неизвестного runtime-контекста."""
    for argument in ["value => value + 1", "unknown.Calculate"]:
        graph = callback_fixture(tmp_path, factory_argument=argument)
        assert enrich_delegate_dispatch(graph, tmp_path)["metadata"]["delegateDispatch"]["derivedDispatch"] == 0


def test_incompatible_return_type_has_no_delegate_edge(tmp_path):
    """Совпадения параметра недостаточно при несовместимом возвращаемом типе."""
    graph = callback_fixture(tmp_path, formula_returns="string")
    assert enrich_delegate_dispatch(graph, tmp_path)["metadata"]["delegateDispatch"]["derivedDispatch"] == 0


def test_custom_delegate_and_alias_do_not_become_system_func(tmp_path):
    """Короткое имя Func не доказывает BCL delegate при пользовательском объявлении или alias."""
    for prefix in ["public delegate R Func<T,R>(T value);", "using Func = Custom.Function;"]:
        graph = callback_fixture(tmp_path)
        path = tmp_path / "Strategy.cs"
        path.write_text(path.read_text().replace("namespace Example;", "namespace Example; " + prefix), encoding="utf-8")
        assert enrich_delegate_dispatch(graph, tmp_path)["metadata"]["delegateDispatch"]["derivedDispatch"] == 0


def test_explicit_system_func_survives_unrelated_custom_name(tmp_path):
    """Квалифицированное System.Func разрешено, когда bare Func затенён custom delegate."""
    graph = callback_fixture(tmp_path)
    path = tmp_path / "Strategy.cs"
    path.write_text(path.read_text().replace("namespace Example;", "namespace Example; public delegate R Func<T,R>(T value);").replace("(Func<T,int>", "(System.Func<T,int>"), encoding="utf-8")
    assert enrich_delegate_dispatch(graph, tmp_path)["metadata"]["delegateDispatch"]["derivedDispatch"] == 1


def test_source_declared_system_func_remains_unknown(tmp_path):
    """Даже qualified System.Func не считается BCL, если корпус сам объявляет такой delegate."""
    graph = callback_fixture(tmp_path)
    path = tmp_path / "Strategy.cs"
    path.write_text(path.read_text().replace("(Func<T,int>", "(System.Func<T,int>"), encoding="utf-8")
    (tmp_path / "Shadow.cs").write_text("namespace System; public delegate R Func<T,R>(T value);", encoding="utf-8")
    graph["nodes"].append({"id": "Shadow", "label": "Shadow.cs", "file": "Shadow.cs", "line": 1})
    assert enrich_delegate_dispatch(graph, tmp_path)["metadata"]["delegateDispatch"]["derivedDispatch"] == 0
