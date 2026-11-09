"""Typed C# interfaces preserve raw IDs and reject name/arity/ref/generic collisions."""
import copy

from atlas.interface_dispatch import MODULE_VERSION, enrich_interface_dispatch, graph_dispatch_edges
from atlas.interface_source import SourceModel


def fixture(tmp_path, source):
    """AST declarations create independent raw IDs, including separate overload symbols."""
    from tree_sitter import Language, Parser
    import tree_sitter_c_sharp
    path = tmp_path / "Types.cs"
    path.write_text(source)
    content = source.encode()
    tree = Parser(Language(tree_sitter_c_sharp.language())).parse(content)
    nodes, edges = [], []

    def visit(ast, owner=None):
        current = owner
        if ast.type in {"class_declaration", "interface_declaration"}:
            name = ast.child_by_field_name("name").text.decode()
            current = f"type:{ast.start_byte}"
            nodes.append({"id": current, "label": name, "kind": ast.type.removesuffix("_declaration"), "file": "Types.cs", "line": ast.start_point.row + 1})
        if ast.type == "method_declaration":
            identifier = f"method:{ast.start_byte}"
            nodes.append({"id": identifier, "label": "." + ast.child_by_field_name("name").text.decode() + "()", "kind": "method", "file": "Types.cs", "line": ast.start_point.row + 1})
            edges.append({"source": current, "target": identifier, "relation": "method"})
        for child in ast.named_children:
            visit(child, current)

    visit(tree.root_node)
    return {"nodes": nodes, "edges": edges, "metadata": {}}, path


def derived(graph, relation):
    """Select only owned derived facts, never an unrelated original Graphify edge."""
    return [edge for edge in graph["edges"] if edge.get("_origin") == "interface-dispatch" and edge["relation"] == relation]


def test_collection_field_foreach_local_and_method_return_chain(tmp_path):
    source = """using System.Collections.Generic;
namespace N;
interface IModule { void Load(int value); }
class A : IModule { public void Load(int value) {} }
class B : IModule { public void Load(int value) {} }
class Other { public void Load(int value) {} }
class Loader {
 IModule[] modules;
 IModule[] Select(int value) => modules;
 void Run(int value) {
  var active = Select(value);
  active[0].Load(value);
  foreach(var module in modules) module.Load(value);
  IModule local = modules[0]; local.Load(value);
 }
}
"""
    graph, _ = fixture(tmp_path, source)
    before = copy.deepcopy(graph)
    result = enrich_interface_dispatch(graph, tmp_path)
    assert graph == before
    original = {node["id"]: node for node in graph["nodes"]}
    for node in result["nodes"]:
        if node["id"] in original:
            assert {key: node[key] for key in original[node["id"]]} == original[node["id"]]
    fields = [node for node in result["nodes"] if node.get("memberFacts", {}).get("kind") == "field"]
    assert len(fields) == 1 and fields[0]["label"] == "modules"
    assert fields[0]["memberFacts"]["visibility"] == "private"
    assert all(n["callableFacts"]["kind"] == "method" for n in result["nodes"] if "callableFacts" in n)
    assert result["metadata"]["interfaceDispatch"]["version"] == MODULE_VERSION
    calls = derived(result, "calls")
    assert len(calls) == 3
    assert {edge["evidence"]["receiverType"] for edge in calls} == {"N.IModule"}
    assert all(edge["confidence"] == "INFERRED" and edge["lineKind"] == "working-tree-ast" for edge in calls)
    assert {edge["evidence"]["implementationType"] for edge in derived(result, "dispatches_to")} == {"N.A", "N.B"}
    assert graph_dispatch_edges(result) == [], "Enriched snapshots never add name-only fallback over AST decisions"
    raw_ids = {node["id"] for node in graph["nodes"]}
    assert all(edge["source"] in raw_ids and edge["target"] in raw_ids for edge in calls)
    published_ids = {node["id"] for node in result["nodes"]}
    assert all(edge["source"] in published_ids and edge["target"] in published_ids for edge in result["edges"])


def test_namespace_same_name_types_and_generic_arity_are_not_guessed(tmp_path):
    source = """namespace One {
 interface ITask { void Go(int value); }
 class Good : ITask { public void Go(int value) {} }
 class Wrong { public void Go(int value) {} }
}
namespace Two {
 interface ITask { void Go(int value); }
 class Good : ITask { public void Go(int value) {} }
 class Generic<T> : ITask { public void Go<TArg>(int value) {} }
 class Client { One.ITask worker; void Run(){ worker.Go(1); } }
}
"""
    graph, _ = fixture(tmp_path, source)
    result = enrich_interface_dispatch(graph, tmp_path)
    calls = derived(result, "calls")
    assert len(calls) == 1 and calls[0]["evidence"]["interfaceType"] == "One.ITask"
    candidates = derived(result, "dispatches_to")
    assert {(edge["evidence"]["interfaceType"], edge["evidence"]["implementationType"]) for edge in candidates} == {("One.ITask", "One.Good"), ("Two.ITask", "Two.Good")}


def test_overload_parameter_types_and_ref_modifiers(tmp_path):
    source = """namespace N;
interface ITask {
 void Go(int value);
 void Go(string value);
 void Ref(ref int value);
}
class Good : ITask {
 public void Go(int value) {}
 public void Go(string value) {}
 public void Ref(ref int value) {}
}
class Wrong : ITask {
 public void Go(bool value) {}
 public void Go(int value, int extra) {}
 public void Ref(int value) {}
}
class Client {
 ITask worker;
 void Run(int value) { worker.Go(value); worker.Go("s"); worker.Ref(ref value); }
}
"""
    graph, _ = fixture(tmp_path, source)
    result = enrich_interface_dispatch(graph, tmp_path)
    candidates = derived(result, "dispatches_to")
    assert len(candidates) == 3
    assert all(edge["evidence"]["implementationType"] == "N.Good" for edge in candidates)
    calls = derived(result, "calls")
    assert len(calls) == 3
    assert len({edge["target"] for edge in calls}) == 3


def test_generic_interface_substitution_and_inherited_implementation(tmp_path):
    source = """namespace N;
interface IWorker<T> { void Work(T value); }
interface IStringWorker : IWorker<string> {}
class Base { public void Work(string value) {} }
class Good : Base, IStringWorker {}
class Wrong : IWorker<string> { public void Work(int value) {} }
class Generic<U> : IWorker<U> { public void Work(U value) {} }
class Client { IStringWorker worker; void Run() { worker.Work("x"); } }
"""
    graph, _ = fixture(tmp_path, source)
    result = enrich_interface_dispatch(graph, tmp_path)
    candidates = derived(result, "dispatches_to")
    assert {edge["evidence"]["implementationType"] for edge in candidates} == {"N.Good", "N.Generic"}
    good = next(edge for edge in candidates if edge["evidence"]["implementationType"] == "N.Good")
    model = SourceModel(tmp_path, graph)
    base_method = next(method for method in model.methods if method["owner"]["name"] == "Base")
    assert good["target"] == base_method["id"]
    calls = derived(result, "calls")
    assert len(calls) == 1 and calls[0]["evidence"]["receiverType"] == "N.IStringWorker"


def test_private_and_static_hiding_preserve_inherited_public_implementation(tmp_path):
    source = """namespace N;
interface ITask { void Go(int value); }
class Base { public void Go(int value) {} }
class PrivateHiding : Base, ITask { private new void Go(int value) {} }
class StaticHiding : Base, ITask { public new static void Go(int value) {} }
class Explicit : ITask { void ITask.Go(int value) {} }
class PrivateOnly : ITask { private void Go(int value) {} }
class StaticOnly : ITask { public static void Go(int value) {} }
"""
    graph, _ = fixture(tmp_path, source)
    model = SourceModel(tmp_path, graph)
    base_method = next(method for method in model.methods if method["owner"]["name"] == "Base")
    explicit = next(method for method in model.methods if method["owner"]["name"] == "Explicit")
    assert explicit["modifiers"] == [] and explicit["explicitInterface"] == "ITask"
    result = enrich_interface_dispatch(graph, tmp_path)
    candidates = derived(result, "dispatches_to")
    # Shared inherited target is one edge, independent of how many derived owners expose it.
    assert {edge["target"] for edge in candidates} == {base_method["id"], explicit["id"]}
    assert next(edge for edge in candidates if edge["target"] == base_method["id"])["evidence"]["line"] == 3
    forbidden = {method["id"] for method in model.methods if method["owner"]["name"] in {"PrivateHiding", "StaticHiding", "PrivateOnly", "StaticOnly"}}
    assert not forbidden.intersection(edge["target"] for edge in candidates)


def test_static_abstract_interface_matches_static_public_member_only(tmp_path):
    source = """namespace N;
interface IFactory { static abstract int Make(); static int Helper() => 1; }
class StaticFactory : IFactory { public static int Make() => 1; public static int Helper() => 2; }
class InstanceFactory : IFactory { public int Make() => 1; }
"""
    graph, _ = fixture(tmp_path, source)
    model = SourceModel(tmp_path, graph)
    expected = next(method for method in model.methods if method["owner"]["name"] == "StaticFactory" and method["name"] == "Make")
    candidates = derived(enrich_interface_dispatch(graph, tmp_path), "dispatches_to")
    assert len(candidates) == 1 and candidates[0]["target"] == expected["id"]


def test_unknown_var_index_and_lexical_shadowing_do_not_add_calls(tmp_path):
    source = """namespace N;
interface IModule { void Load(int value); }
class Implementation : IModule { public void Load(int value) {} }
class Client {
 IModule[] modules;
 void Run() {
  var active = UnknownFactory(); active[0].Load(1);
  foreach(var module in UnknownFactory()) module.Load(1);
  { int modules = 1; modules.Load(1); }
  modules[0].Load(1);
 }
}
"""
    graph, _ = fixture(tmp_path, source)
    calls = derived(enrich_interface_dispatch(graph, tmp_path), "calls")
    assert len(calls) == 1
    assert calls[0]["source_location"] == "L10"


def test_inheritance_cycles_are_bounded_and_graph_fallback_is_owner_local():
    graph = {"nodes": [{"id": identifier, "label": label} for identifier, label in [("i", "IFoo"), ("im", "Work"), ("base", "Base"), ("bm", "Work"), ("child", "Child"), ("unrelated", "Work")]], "edges": [
        {"source": "i", "target": "im", "relation": "method"},
        {"source": "base", "target": "bm", "relation": "method"},
        {"source": "child", "target": "base", "relation": "inherits"},
        {"source": "base", "target": "child", "relation": "inherits"},
        {"source": "child", "target": "i", "relation": "implements"},
    ]}
    before = copy.deepcopy(graph)
    result = graph_dispatch_edges(graph)
    assert [(edge["source"], edge["target"]) for edge in result] == [("im", "bm")]
    assert graph == before


def test_ast_errors_do_not_publish_implementation_candidates(tmp_path):
    source = """namespace N;
interface IModule { void Load(int value); }
class Broken : IModule { public void Load(int value) { !!! } }
class Client { IModule module; void Run(){ module.Load(1); } }
"""
    graph, _ = fixture(tmp_path, source)
    result = enrich_interface_dispatch(graph, tmp_path)
    assert derived(result, "dispatches_to") == []
    assert result["metadata"]["interfaceDispatch"]["truncated"] is True
    assert any("AST-ошибкой" in message for message in result["metadata"]["interfaceDispatch"]["warnings"])


def test_custom_collection_name_is_not_treated_as_framework_collection(tmp_path):
    source = """namespace N;
interface IModule { void Load(int value); }
class List<T> {}
class Client { List<IModule> modules; void Run(){ modules[0].Load(1); } }
"""
    graph, _ = fixture(tmp_path, source)
    result = enrich_interface_dispatch(graph, tmp_path)
    assert derived(result, "calls") == []


def test_merged_generic_type_ids_are_explicitly_unresolved(tmp_path):
    source = """namespace N;
interface IModule { void Load(int value); }
interface IModule<T> { void Load(T value); }
class Impl : IModule { public void Load(int value) {} }
class Client { IModule module; void Run(){ module.Load(1); } }
"""
    graph, _ = fixture(tmp_path, source)
    model = SourceModel(tmp_path, graph)
    generic = next(definition for definition in model.types if definition["name"] == "IModule" and definition["typeParameters"])
    removed = {generic["id"], *(method["id"] for method in generic["methods"])}
    graph["nodes"] = [node for node in graph["nodes"] if node["id"] not in removed]
    result = enrich_interface_dispatch(graph, tmp_path)
    assert result["metadata"]["interfaceDispatch"]["ambiguousTypeIds"] == 1
    assert derived(result, "dispatches_to") == []
    assert derived(result, "calls") == []


def test_explicit_this_keeps_field_type_when_parameter_shadows_name(tmp_path):
    source = """namespace N;
interface IOne { void Go(int value); }
interface ITwo { void Go(int value); }
class Client {
 IOne worker;
 void Run(ITwo worker) { this.worker.Go(1); worker.Go(1); }
}
"""
    graph, _ = fixture(tmp_path, source)
    calls = derived(enrich_interface_dispatch(graph, tmp_path), "calls")
    assert len(calls) == 2
    assert {edge["evidence"]["receiverType"] for edge in calls} == {"N.IOne", "N.ITwo"}


def test_partial_ast_marks_source_coverage_incomplete(tmp_path):
    graph, _ = fixture(tmp_path, "namespace N; interface IOne { void Go(int value); } class Broken { void Go(){ ")
    result = enrich_interface_dispatch(graph, tmp_path)
    metadata = result["metadata"]["interfaceDispatch"]
    assert metadata["truncated"] is True
    assert any("AST-ошибки" in warning for warning in metadata["warnings"])
