"""Captured lexical attributes preserve identity, explicit owners and incomplete-source boundaries."""
from copy import deepcopy

import pytest

from atlas.attribute_graph import enrich_attribute_graph, MODULE_VERSION
from atlas.interface_source import walk


def project(tmp_path, source, filename="Types.cs"):
    """Real AST supplies exact raw declaration IDs and ownership, including overloads/nested types."""
    from tree_sitter import Language, Parser
    import tree_sitter_c_sharp
    (tmp_path / filename).write_text(source)
    tree = Parser(Language(tree_sitter_c_sharp.language())).parse(source.encode())
    nodes, edges, ids = [], [], {}
    kinds = {"class_declaration": "class", "method_declaration": "method", "property_declaration": "property", "variable_declarator": "field"}
    for ast in walk(tree.root_node):
        if ast.type not in kinds: continue
        name = ast.child_by_field_name("name")
        if name is None: continue
        identifier = "raw:" + str(ast.start_byte)
        ids[(ast.start_byte, ast.end_byte, ast.type)] = identifier
        nodes.append({"id": identifier, "label": name.text.decode(), "kind": kinds[ast.type], "file": filename, "line": name.start_point.row + 1})
        parent = ast.parent
        while parent is not None:
            owner = ids.get((parent.start_byte, parent.end_byte, parent.type))
            if owner:
                edges.append({"source": owner, "target": identifier, "relation": "method" if ast.type == "method_declaration" else "contains"})
                break
            parent = parent.parent
    return {"nodes": nodes, "edges": edges, "metadata": {}}, tree


def attributes(graph):
    return [node for node in graph["nodes"] if node["kind"] == "attribute"]


def test_deterministic_repeated_occurrences_preserve_graph_and_raw_calls(tmp_path):
    graph, _ = project(tmp_path, '[Type] class C { [Mark(1), Mark(1)] void Go() {} }')
    graph["edges"].append({"source": graph["nodes"][0]["id"], "target": graph["nodes"][1]["id"], "relation": "calls", "confidence": "INFERRED"})
    before = deepcopy(graph)
    first = enrich_attribute_graph(graph, tmp_path)
    again = enrich_attribute_graph(first, tmp_path)
    assert graph == before
    assert [node["id"] for node in attributes(first)] == [node["id"] for node in attributes(again)]
    assert len(attributes(first)) == len(attributes(again)) == 3
    repeats = [node for node in attributes(first) if node["name"] == "Mark"]
    assert repeats[0]["id"] != repeats[1]["id"]
    assert repeats[0]["arguments"][0]["expression"] == "1"
    assert all(node["sourceKind"] == "snapshot-captured-source" and len(node["sourceHash"]) == 64 for node in attributes(first))
    assert first["metadata"]["attributeGraph"]["version"] == MODULE_VERSION
    assert [edge for edge in first["edges"] if edge.get("_origin") != "attribute-graph"] == graph["edges"]
    assert [edge for edge in again["edges"] if edge.get("_origin") == "attribute-graph"] == [edge for edge in first["edges"] if edge.get("_origin") == "attribute-graph"]


def test_containing_type_parameter_and_global_owners_are_distinct(tmp_path):
    graph, _ = project(tmp_path, '[assembly: Build("app")]\n[Outer] class C { [Inner] class D { [Method] void Go([Input] int input) {} } }')
    result = enrich_attribute_graph(graph, tmp_path)
    by_name = {node["name"]: node for node in attributes(result)}
    go = next(node for node in result["nodes"] if node.get("label") == "Go")
    c = next(node for node in result["nodes"] if node.get("label") == "C")
    d = next(node for node in result["nodes"] if node.get("label") == "D")
    assert by_name["Method"]["ownerNodeId"] == go["id"]
    assert by_name["Outer"]["ownerNodeId"] == c["id"]
    assert by_name["Inner"]["ownerNodeId"] == d["id"]
    assert by_name["Input"]["ownerNodeId"] is None and by_name["Input"]["declarationNodeId"] == go["id"]
    assert by_name["Input"]["owner"]["position"] == 1
    assert any(edge["source"] == go["id"] and edge["target"] == by_name["Input"]["id"] and edge["relation"] == "has_parameter_attribute" for edge in result["edges"])
    scope = next(node for node in result["nodes"] if node["kind"] == "attribute_scope")
    assert by_name["Build"]["ownerNodeId"] == scope["id"]
    assert not any(edge["source"] == go["id"] and edge["target"] in {by_name["Outer"]["id"], by_name["Inner"]["id"], by_name["Build"]["id"]} for edge in result["edges"])
    groups = go["attributeFacts"]["groups"]
    assert {group["scope"] for group in groups} == {"declaration", "type", "parameter", "file"}
    assert all(group["attrNodeIds"] for group in groups)


def test_overload_collision_is_unresolved_not_guessed(tmp_path):
    graph, _ = project(tmp_path, 'class C { [First] void Go() {} [Second] void Go(int x) {} }')
    methods = [node for node in graph["nodes"] if node["kind"] == "method"]
    # Graphify sometimes collapses overloads to one raw ID on the same source line.
    graph["nodes"].remove(methods[1])
    graph["edges"] = [edge for edge in graph["edges"] if edge["target"] != methods[1]["id"]]
    result = enrich_attribute_graph(graph, tmp_path)
    assert all(node["ownerNodeId"] is None for node in attributes(result))
    assert result["nodes"][1]["attributeFacts"]["status"] == "unresolved"
    assert not [edge for edge in result["edges"] if edge.get("_origin") == "attribute-graph"]


def test_partial_parts_and_fields_properties_keep_exact_raw_owners(tmp_path):
    graph, _ = project(tmp_path, '[One] partial class C { [Field] int value; [Property] int Value {get;set;} }')
    second, _ = project(tmp_path, '[Two] partial class C {}', "Other.cs")
    for node in second["nodes"]: node["id"] = "other:" + node["id"]
    graph["nodes"].extend(second["nodes"])
    result = enrich_attribute_graph(graph, tmp_path)
    by_name = {node["name"]: node for node in attributes(result)}
    assert by_name["One"]["ownerNodeId"] != by_name["Two"]["ownerNodeId"]
    assert by_name["One"]["file"] == "Types.cs" and by_name["Two"]["file"] == "Other.cs"
    for attribute, label in (("Field", "value"), ("Property", "Value")):
        raw = next(node for node in result["nodes"] if node.get("label") == label)
        assert by_name[attribute]["ownerNodeId"] == raw["id"]


def test_missing_source_syntax_errors_and_orphans_are_truthful(tmp_path):
    graph, _ = project(tmp_path, 'class C { [Mark] void Go() { }')
    graph["nodes"] = [{"id": "file", "label": "Types.cs", "kind": "file", "file": "Types.cs"}]
    graph["edges"] = []
    result = enrich_attribute_graph(graph, tmp_path)
    assert result["metadata"]["attributeGraph"]["truncated"]
    assert result["metadata"]["attributeGraph"]["warnings"]
    assert attributes(result)[0]["ownerNodeId"] is None
    (tmp_path / "Types.cs").unlink()
    missing = enrich_attribute_graph(graph, tmp_path)
    assert not attributes(missing) and missing["nodes"][0]["attributeFacts"]["status"] == "unavailable"
    assert missing["metadata"]["attributeGraph"]["truncated"]


def test_symlinks_escape_and_limits_do_not_claim_complete(tmp_path, monkeypatch):
    from atlas import attribute_graph
    graph, _ = project(tmp_path, '[Mark] class C {}')
    (tmp_path / "Link.cs").symlink_to(tmp_path / "Types.cs")
    graph["nodes"][0]["file"] = "Link.cs"
    assert not attributes(enrich_attribute_graph(graph, tmp_path))
    graph["nodes"][0]["file"] = "../Outside.cs"
    assert not attributes(enrich_attribute_graph(graph, tmp_path))
    graph["nodes"][0]["file"] = "Types.cs"
    for budget in ("MAX_FILES", "MAX_BYTES", "MAX_AST_NODES", "MAX_OCCURRENCES", "MAX_OWNERS"):
        with monkeypatch.context() as patch:
            patch.setattr(attribute_graph, budget, 0)
            result = enrich_attribute_graph(graph, tmp_path)
            assert result["metadata"]["attributeGraph"]["truncated"], budget
            assert not attributes(result), budget


def test_multiline_overloads_and_raw_parameters_resolve_without_name_guessing(tmp_path):
    graph, tree = project(tmp_path, '''class C {
 [First] void Go([Input] int x) {}
 [Second] void Go(string x) {}
}''')
    method = next(node for node in graph["nodes"] if node["kind"] == "method")
    parameter = next(ast for ast in walk(tree.root_node) if ast.type == "parameter")
    name = parameter.child_by_field_name("name")
    graph["nodes"].append({"id": "raw-param", "label": "x", "kind": "parameter", "file": "Types.cs", "line": name.start_point.row + 1})
    graph["edges"].append({"source": method["id"], "target": "raw-param", "relation": "contains"})
    result = enrich_attribute_graph(graph, tmp_path)
    names = {node["name"]: node for node in attributes(result)}
    assert names["First"]["ownerNodeId"] != names["Second"]["ownerNodeId"]
    assert names["Input"]["ownerNodeId"] == "raw-param"
    assert names["Input"]["declarationNodeId"] == method["id"]
    assert any(edge["source"] == "raw-param" and edge["target"] == names["Input"]["id"] and edge["relation"] == "has_attribute" for edge in result["edges"])
    assert "текущ" not in next(node for node in result["nodes"] if node["id"] == method["id"])["attributeFacts"]["warning"]


def test_shared_field_attribute_has_multiple_real_owners_and_raw_file_capture(tmp_path):
    graph, _ = project(tmp_path, '[assembly: Build]\nclass C { [Shared] int a, b; }')
    graph["nodes"].append({"id": "raw-file", "label": "Types.cs", "kind": "file", "file": "Types.cs"})
    result = enrich_attribute_graph(graph, tmp_path)
    shared = next(node for node in attributes(result) if node["name"] == "Shared")
    field_ids = {node["id"] for node in graph["nodes"] if node["kind"] == "field"}
    assert shared["ownerNodeId"] is None and set(shared["ownerNodeIds"]) == field_ids
    assert {edge["source"] for edge in result["edges"] if edge["target"] == shared["id"]} == field_ids
    assert not [node for node in result["nodes"] if node["kind"] == "attribute_scope"]
    raw_file = next(node for node in result["nodes"] if node["id"] == "raw-file")
    assert raw_file["attributeFacts"]["groups"][0]["scope"] == "file"
    assert raw_file["attributeFacts"]["groups"][0]["attributes"][0]["name"] == "Build"
    assert raw_file["attributeFacts"]["groups"][0]["attributes"][0]["attributeNodeId"]


def test_indexer_declaration_and_parameter_are_captured_with_explicit_owners(tmp_path):
    graph, _ = project(tmp_path, '''class C {
 [IndexerMark] public int this[[Input] int index] { get => 0; }
}''')
    graph["nodes"].append({"id": "indexer", "label": "this[]", "kind": "property", "file": "Types.cs", "line": 2})
    graph["edges"].append({"source": graph["nodes"][0]["id"], "target": "indexer", "relation": "contains"})
    result = enrich_attribute_graph(graph, tmp_path)
    names = {node["name"]: node for node in attributes(result)}
    assert names["IndexerMark"]["ownerNodeId"] == "indexer"
    assert names["Input"]["ownerNodeId"] is None
    assert names["Input"]["declarationNodeId"] == "indexer"
    assert any(edge["source"] == "indexer" and edge["target"] == names["Input"]["id"] and edge["relation"] == "has_parameter_attribute" for edge in result["edges"])
    assert not result["metadata"]["attributeGraph"]["truncated"]


@pytest.mark.parametrize('kind', ['attribute', 'attribute_scope'])
def test_synthetic_namespace_collision_never_replaces_or_duplicates_raw_nodes(tmp_path, kind):
    graph, _ = project(tmp_path, '[assembly: Build]\n[Mark] class C {}')
    first = enrich_attribute_graph(graph, tmp_path)
    foreign = deepcopy(next(node for node in first["nodes"] if node["kind"] == kind))
    foreign.pop('_origin')
    foreign['label'] = 'Foreign node'
    graph['nodes'].append(foreign)
    result = enrich_attribute_graph(graph, tmp_path)
    assert sum(node['id'] == foreign['id'] for node in result['nodes']) == 1
    assert next(node for node in result['nodes'] if node['id'] == foreign['id'])['label'] == 'Foreign node'
    assert result['metadata']['attributeGraph']['truncated']
    assert any('конфликт' in warning for warning in result['metadata']['attributeGraph']['warnings'])
    assert not any(edge.get('_origin') == 'attribute-graph' and foreign['id'] in {edge['source'], edge['target']} for edge in result['edges'])
    assert all(edge['source'] is not None and edge['target'] is not None for edge in result['edges'])


def test_unsupported_enum_members_and_accessors_survive_as_source_only(tmp_path):
    (tmp_path / 'Types.cs').write_text('''enum E { [ValueMark] A }
class C { public int Value { [GetterMark] get => 0; } }
''')
    graph = {'nodes': [{'id': 'file', 'kind': 'file', 'label': 'Types.cs', 'file': 'Types.cs'}], 'edges': []}
    result = enrich_attribute_graph(graph, tmp_path)
    names = {node['name']: node for node in attributes(result)}
    assert set(names) == {'ValueMark', 'GetterMark'}
    assert all(node['ownerNodeId'] is None and node['status'] == 'unresolved' for node in names.values())
    assert not [edge for edge in result['edges'] if edge.get('_origin') == 'attribute-graph']
    assert result['metadata']['attributeGraph']['truncated']
    assert any('source-only' in warning for warning in result['metadata']['attributeGraph']['warnings'])


def test_record_property_target_is_parameter_context_not_a_generated_property_claim(tmp_path):
    (tmp_path / 'Types.cs').write_text('record R([property: JsonName("id")] int Id);')
    graph = {'nodes': [{'id': 'record', 'kind': 'record', 'label': 'R', 'file': 'Types.cs', 'line': 1}], 'edges': []}
    result = enrich_attribute_graph(graph, tmp_path)
    node = attributes(result)[0]
    assert node['target'] == 'property' and node['scope'] == 'parameter'
    assert node['ownerNodeId'] is None and node['declarationNodeId'] == 'record'
    assert result['edges'][0]['relation'] == 'has_parameter_attribute'


def test_graphify_symbol_type_is_not_confused_with_constructor_name(tmp_path):
    (tmp_path / 'Types.cs').write_text('[Mark]\nclass C {\n public C() {}\n}')
    graph = {
        'nodes': [
            {'id': 'type', 'kind': 'symbol', 'label': 'C', 'file': 'Types.cs', 'line': 2},
            {'id': 'ctor', 'kind': 'symbol', 'label': '.C()', 'file': 'Types.cs', 'line': 3},
        ],
        'edges': [{'source': 'type', 'target': 'ctor', 'relation': 'method'}],
    }
    result = enrich_attribute_graph(graph, tmp_path)
    assert attributes(result)[0]['ownerNodeId'] == 'type'
    raw_type = next(node for node in result['nodes'] if node['id'] == 'type')
    ctor = next(node for node in result['nodes'] if node['id'] == 'ctor')
    assert raw_type['attributeFacts']['status'] == ctor['attributeFacts']['status'] == 'parsed'
    assert next(group for group in ctor['attributeFacts']['groups'] if group['scope'] == 'type')['ownerNodeId'] == 'type'
    assert not any(edge['source'] == 'ctor' and edge['relation'] == 'has_attribute' for edge in result['edges'])


def test_flat_params_source_aliases_and_non_csharp_facts_are_preserved(tmp_path):
    (tmp_path / 'Types.cs').write_text('''class C {
 void Go([Input] int input, [Last] params int[] rest) {}
}''')
    graph = {
        'nodes': [
            {'id': 'go', 'kind': 'symbol', 'label': '.Go()', 'source_file': 'Types.cs', 'source_location': 'L2'},
            {'id': 'doc', 'kind': 'documentation', 'file': 'README.md', 'attributeFacts': {'other': 'preserved'}},
        ],
        'edges': [],
    }
    result = enrich_attribute_graph(graph, tmp_path)
    attrs = {node['name']: node for node in attributes(result)}
    assert set(attrs) == {'Input', 'Last'}
    assert attrs['Last']['owner']['position'] == 2
    assert attrs['Last']['declarationNodeId'] == 'go'
    assert all(node['source_file'] == 'Types.cs' and node['source_location'] == 'L2' for node in attrs.values())
    assert next(node for node in result['nodes'] if node['id'] == 'doc')['attributeFacts'] == {'other': 'preserved'}
    assert not result['metadata']['attributeGraph']['truncated']
