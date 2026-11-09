"""Proof-boundary regressions for offline test inventory and counters."""
import copy
import json
import os
from pathlib import Path
import pytest
from atlas.test_coverage import build_test_coverage, TestCoverageError, LIMITS
from atlas.test_coverage_reports import line_counter
import xml.etree.ElementTree as ET


def graph(*nodes, edges=()):
    """Creates raw graph records with explicit source positions."""
    return {"nodes": [{"id": id, "label": name + "()", "kind": "method", "file": file, "line": line} for id, name, file, line in nodes], "edges": list(edges)}


def write(root, file, text):
    """Writes only isolated pytest fixtures, never the financial repository."""
    path = root / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def report(root, rows, name="coverage.xml", timestamp=""):
    """Provides aggregate Cobertura; duplicates emulate method/class rows."""
    xml = f'<coverage {timestamp}><packages><package><classes><class filename="Code.cs"><lines>{rows}</lines></class></classes></package></packages></coverage>'
    path = write(root, name, xml)
    future = (root / "Code.cs").stat().st_mtime_ns + 10000000000
    os.utime(path, ns=(future, future))
    return path


def fixture(root):
    """A real declaration range and direct graph call avoid method-name guessing."""
    write(root, "Code.cs", 'namespace Calc;\nclass Code {\n int Calculate(bool yes) {\n if (yes) return 1;\n return 0;\n }\n}\n')
    write(root, "Unit.cs", 'using Xunit;\nnamespace Specs; class Tests {\n [Fact(Skip="legacy")] void Test() {}\n [Theory] void Theory() {}\n}\n')
    return graph(("calc", "Calculate", "Code.cs", 3), ("unit", "Test", "Unit.cs", 3), ("theory", "Theory", "Unit.cs", 4), edges=[{"source": "unit", "target": "calc", "relation": "calls", "confidence": "HIGH"}])


def test_inventory_graph_read_only_and_unknown_runtime(tmp_path):
    data = fixture(tmp_path)
    before = copy.deepcopy(data)
    result = build_test_coverage(tmp_path, data, "snapshot", "calc")
    assert data == before
    assert result["scope"]["mode"] == "node-chain"
    assert result["summary"]["conditionsByStatus"] == {"covered": 0, "partial": 0, "uncovered": 0, "unknown": 1}
    condition = result["conditions"][0]
    assert [item["kind"] for item in condition["objectives"]] == ["true", "false"]
    assert condition["objectives"][1]["implicit"]
    assert condition["testLinks"][0]["path"] == ["unit", "calc"]
    assert all(test["runtime"]["status"] == "unknown" for test in result["tests"])
    assert {test["declaredStatus"] for test in result["tests"]} == {"enabled", "disabled"}
    assert condition["ownerNodeId"] == "calc"


def test_conditional_objectives_keep_distinct_same_line_positions(tmp_path):
    """Несколько ?: на одной строке имеют независимые IDs и две неизвестные runtime-цели."""
    write(tmp_path, "Code.cs", 'class Code { int Calculate(bool yes) => yes ? (yes ? 1 : 2) : 3; }')
    data = graph(("calc", "Calculate", "Code.cs", 1))
    result = build_test_coverage(tmp_path, data, "snapshot", "calc")
    conditions = result['conditions']
    assert len(conditions) == 2
    assert len({condition['id'] for condition in conditions}) == 2
    for condition in conditions:
        assert condition['kind'] == 'conditional'
        assert [item['kind'] for item in condition['objectives']] == ['true', 'false']
        assert all(not item['implicit'] and item['runtimeStatus'] == 'unknown' for item in condition['objectives'])


@pytest.mark.parametrize("hits,coverage,status", [("1", "50% (1/2)", "partial"), ("0", "0% (0/2)", "uncovered"), ("5", "100% (2/2)", "covered"), ("1", "", "unknown"), ("1", "0% (2/2)", "unknown")])
def test_report_counters_never_assign_objective_or_test_result(tmp_path, hits, coverage, status):
    data = fixture(tmp_path)
    report(tmp_path, f'<line number="4" hits="{hits}" branch="true" condition-coverage="{coverage}"/>')
    result = build_test_coverage(tmp_path, data, "snapshot", "calc")
    condition = result["conditions"][0]
    assert condition["runtime"]["status"] == status
    assert all(objective["runtimeStatus"] == "unknown" for objective in condition["objectives"])
    assert all(test["runtime"]["status"] == "unknown" for test in result["tests"])


def test_duplicates_identical_are_not_added_conflicts_are_unknown(tmp_path):
    data = fixture(tmp_path)
    row = '<line number="4" hits="1" branch="true" condition-coverage="50% (1/2)"/>'
    report(tmp_path, row + row)
    result = build_test_coverage(tmp_path, data, "s")
    assert result["conditions"][0]["runtime"]["branchesCovered"] == 1
    report(tmp_path, '<line number="4" hits="1" branch="true" condition-coverage="100% (2/2)"/>', "coverage.cobertura.xml")
    result = build_test_coverage(tmp_path, data, "s")
    assert result["conditions"][0]["runtime"]["status"] == "unknown"
    assert "конфликт" in result["conditions"][0]["runtime"]["reason"]


def test_report_copied_with_new_mtime_old_run_timestamp_is_stale(tmp_path):
    data = fixture(tmp_path)
    report(tmp_path, '<line number="4" hits="2" branch="true" condition-coverage="100% (2/2)"/>', timestamp='timestamp="100"')
    result = build_test_coverage(tmp_path, data, "s")
    assert result["conditions"][0]["runtime"]["freshness"] == "stale"
    assert result["conditions"][0]["runtime"]["status"] == "unknown"


def test_source_newer_report_stale(tmp_path):
    data = fixture(tmp_path)
    path = report(tmp_path, '<line number="4" hits="2" branch="true" condition-coverage="100% (2/2)"/>')
    newer = path.stat().st_mtime_ns + 1
    os.utime(tmp_path / "Code.cs", ns=(newer, newer))
    result = build_test_coverage(tmp_path, data, "s")
    assert result["conditions"][0]["runtime"]["status"] == "unknown"


@pytest.mark.parametrize("source", [
 'class Code { int Calculate(bool yes) { if(yes) return 1; return 0; } int Other(bool yes) { if(yes) return 1; return 0; } }',
 'class Code { int Calculate(bool yes) {\n if(yes && yes) return 1;\n return 0; } }',
 'class Code { int Calculate(bool yes) {\n if(\n yes) return 1;\n return 0; } }',
])
def test_same_line_other_method_compound_multiline_unknown(tmp_path, source):
    write(tmp_path, "Code.cs", source)
    line = 1 if source.startswith('class Code { int Calculate(bool yes) { if') else 2
    data = graph(("calc", "Calculate", "Code.cs", 1), ("other", "Other", "Code.cs", 1))
    report(tmp_path, f'<line number="{line}" hits="2" branch="true" condition-coverage="100% (4/4)"/>')
    result = build_test_coverage(tmp_path, data, "s", "calc")
    assert result["conditions"]
    assert all(condition["runtime"]["status"] == "unknown" for condition in result["conditions"])


def test_loops_switch_expression_implicit_objectives(tmp_path):
    write(tmp_path, "Code.cs", 'class Code { void Calculate(int n) {\n for(int i=0;i<n;i++) {}\n foreach(var i in new int[0]) {}\n while(n>0) n--;\n do { n++; } while(n<1);\n switch(n) { case 1: break; default: break; }\n var x=n switch { 1=>2, _=>3 };\n} }')
    result = build_test_coverage(tmp_path, graph(("calc", "Calculate", "Code.cs", 1)), "s")
    assert {condition["kind"] for condition in result["conditions"]} == {"for", "foreach", "while", "do", "switch", "switch-expression"}
    loop = next(condition for condition in result["conditions"] if condition["kind"] == "do")
    assert "ноль" not in loop["objectives"][-1]["label"]
    assert all(condition["runtime"]["status"] == "unknown" for condition in result["conditions"])


def test_attribute_shadow_and_qualified_framework(tmp_path):
    write(tmp_path, "Code.cs", 'using Xunit; class FactAttribute:System.Attribute {} class Tests { [Fact] void Fake(){} [Xunit.Fact] void Real(){} [Other.Fact] void Foreign(){} }')
    result = build_test_coverage(tmp_path, graph(("real", "Real", "Code.cs", 1)), "s")
    assert [test["name"] for test in result["tests"]] == ["Real"]


def test_overloaded_methods_without_line_have_no_raw_mapping(tmp_path):
    write(tmp_path, "Code.cs", 'class Code { int Calculate(int n) { if(n>0)return 1; return 0;} int Calculate(bool n) {if(n)return 2;return 0;} }')
    data = {"nodes": [{"id": "calc", "label": "Calculate()", "file": "Code.cs", "kind": "method"}], "edges": []}
    result = build_test_coverage(tmp_path, data, "s")
    assert all(condition["ownerNodeId"] is None for condition in result["conditions"])


def test_ast_error_partial_not_complete(tmp_path):
    write(tmp_path, "Code.cs", 'class Code { void Calculate(bool yes) { if(yes) { broken syntax syntax; } } }')
    result = build_test_coverage(tmp_path, graph(("calc", "Calculate", "Code.cs", 1)), "s")
    assert result["status"] == "partial"
    assert result["methods"][0]["sourceStatus"] == "partial"


def test_unsafe_xml_paths_and_fifo_never_read(tmp_path):
    data = fixture(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside.xml")
    outside.write_text('<coverage/>')
    (tmp_path / "link.xml").symlink_to(outside)
    os.mkfifo(tmp_path / "fifo.xml")
    write(tmp_path, "evil.xml", '<!DOCTYPE coverage [<!ENTITY x SYSTEM "file:///etc/passwd">]><coverage>&x;</coverage>')
    result = build_test_coverage(tmp_path, data, "s", report_paths=["../outside.xml", "link.xml", "fifo.xml", "evil.xml"])
    assert all(report["status"] == "rejected" for report in result["reports"])
    assert result["conditions"][0]["runtime"]["status"] == "unknown"


def test_source_fifo_and_oversize_degrade(tmp_path):
    os.mkfifo(tmp_path / "Code.cs")
    result = build_test_coverage(tmp_path, graph(("calc", "Calculate", "Code.cs", 1)), "s")
    assert not result["methods"] and result["warnings"]
    (tmp_path / "Code.cs").unlink()
    write(tmp_path, "Code.cs", 'a' * (1024 * 1024 + 1))
    result = build_test_coverage(tmp_path, graph(("calc", "Calculate", "Code.cs", 1)), "s")
    assert result["status"] == "partial"


def test_limits_explicit_and_unknown_node(tmp_path, monkeypatch):
    data = fixture(tmp_path)
    monkeypatch.setitem(LIMITS, "maxMethods", 1)
    result = build_test_coverage(tmp_path, data, "s")
    assert result["status"] == "partial" and result["truncation"]["methods"]
    with pytest.raises(TestCoverageError) as error:
        build_test_coverage(tmp_path, data, "s", "absent")
    assert error.value.status == 404
    with pytest.raises(TestCoverageError):
        build_test_coverage(tmp_path, data, "s", report_paths=["a"] * 9)


def test_static_dispatch_links_preserve_candidate_and_ignore_attributes(tmp_path):
    data = fixture(tmp_path)
    data["edges"][0]["relation"] = "dispatches_to"
    data["edges"][0]["evidence"] = {"kind": "interface-implementation"}
    data["edges"].append({"source": "theory", "target": "calc", "relation": "has_attribute"})
    result = build_test_coverage(tmp_path, data, "s", "calc")
    links = result["conditions"][0]["testLinks"]
    assert len(links) == 1 and links[0]["confidence"] == "candidate"
    assert links[0]["edgeEvidence"][0]["evidence"]["kind"] == "interface-implementation"


def test_no_reflection_string_name_test_mapping(tmp_path):
    data = fixture(tmp_path)
    data["edges"] = []
    write(tmp_path, "Unit.cs", 'using Xunit; class Tests { [Fact] void Test(){ typeof(object).GetMethod("Calculate"); } }')
    result = build_test_coverage(tmp_path, data, "s", "calc")
    assert not result["conditions"][0]["testLinks"]


@pytest.mark.parametrize("timestamp", ['not-a-date', '0', '999999999999'])
def test_invalid_declared_report_timestamp_cannot_fallback_to_fresh_mtime(tmp_path, timestamp):
    data = fixture(tmp_path)
    report(tmp_path, '<line number="4" hits="2" branch="true" condition-coverage="100% (2/2)"/>', timestamp=f'timestamp="{timestamp}"')
    result = build_test_coverage(tmp_path, data, "s")
    runtime = result["conditions"][0]["runtime"]
    assert runtime["status"] == "unknown" and runtime["freshness"] == "unknown"
    assert result["reports"][0]["timestampInvalid"]


def test_invalid_duplicate_hits_poison_same_line_not_unrelated_line(tmp_path):
    data = fixture(tmp_path)
    good = '<line number="4" hits="2" branch="true" condition-coverage="100% (2/2)"/>'
    report(tmp_path, good + '<line number="4" hits="garbled" branch="true" condition-coverage="0% (0/2)"/>')
    result = build_test_coverage(tmp_path, data, "s")
    assert result["conditions"][0]["runtime"]["status"] == "unknown"
    report(tmp_path, good + '<line number="99" hits="garbled"/>')
    result = build_test_coverage(tmp_path, data, "s")
    assert result["conditions"][0]["runtime"]["status"] == "covered"


def test_explicit_using_alias_overrides_framework_import_and_known_alias_resolves(tmp_path):
    write(tmp_path, "Code.cs", 'using Xunit; using Fact = Other.MarkerAttribute; namespace Other { class MarkerAttribute:System.Attribute {} } class Tests { [Fact] void Fake(){} [Xunit.Fact] void Real(){} }')
    data = graph(("real", "Real", "Code.cs", 1))
    result = build_test_coverage(tmp_path, data, "s")
    assert [test["name"] for test in result["tests"]] == ["Real"]
    write(tmp_path, "Code.cs", 'using Fact = Xunit.FactAttribute; class Tests { [Fact] void Real(){} }')
    result = build_test_coverage(tmp_path, data, "s")
    assert result["tests"][0]["declaredStatus"] == "enabled"


def test_source_facts_identify_current_read_not_report_hash(tmp_path):
    data = fixture(tmp_path)
    result = build_test_coverage(tmp_path, data, "s")
    fact = next(item for item in result["sourceFacts"] if item["file"] == "Code.cs")
    assert len(fact["sourceHash"]) == 64 and fact["mtimeNs"] > 0 and fact["stableRead"]


def test_positive_branch_counter_with_zero_line_hits_unknown(tmp_path):
    data = fixture(tmp_path)
    report(tmp_path, '<line number="4" hits="0" branch="true" condition-coverage="100% (2/2)"/>')
    result = build_test_coverage(tmp_path, data, "s")
    assert result["conditions"][0]["runtime"]["status"] == "unknown"


@pytest.mark.parametrize('source', ['class Code { void Calculate(int n) {\n switch(\n n) { case 1:break;default:break; }\n} }', 'class Code { void Calculate(int[] n) {\n foreach(var x in\n n) {}\n} }'])
def test_multiline_switch_selector_and_foreach_header_unknown(tmp_path, source):
    write(tmp_path, 'Code.cs', source)
    report(tmp_path, '<line number="2" hits="2" branch="true" condition-coverage="100% (2/2)"/>')
    result = build_test_coverage(tmp_path, graph(('calc','Calculate','Code.cs',1)), 's')
    assert result['conditions'][0]['runtimeAmbiguous']
    assert result['conditions'][0]['runtime']['status'] == 'unknown'


def test_compound_operator_beyond_display_limit_is_not_silently_covered(tmp_path):
    write(tmp_path, 'Code.cs', 'class Code { void Calculate(string s,bool b) {\n if(s=="' + 'a'*1200 + '" || b) {}\n} }')
    report(tmp_path, '<line number="2" hits="2" branch="true" condition-coverage="100% (4/4)"/>')
    result = build_test_coverage(tmp_path, graph(('calc','Calculate','Code.cs',1)), 's')
    assert result['conditions'][0]['conditionTruncated']
    assert result['conditions'][0]['runtimeAmbiguous']
    assert result['conditions'][0]['runtime']['status'] == 'unknown'


def test_long_paths_omitted_explicitly_and_both_test_kinds_retained(tmp_path):
    from atlas.test_coverage import static_links, graph_index
    nodes = [{'id': id} for id in ['u1','u2','u3','g','a','b','c']]
    edges = [{'source': start, 'target':'a','relation':'calls'} for start in ['u1','u2','u3','g']]
    edges += [{'source':'a','target':'b','relation':'calls'},{'source':'b','target':'c','relation':'calls'}]
    tests = [{'id':id,'nodeId':id if id!='g' else None,'kind':'unit' if id!='g' else 'gherkin','steps':[] if id!='g' else [{'bindings':[{'nodeId':'g','file':'Steps.cs','line':1}]}]} for id in ['u1','u2','u3','g']]
    conditions = [{'id':'condition','ownerNodeId':'c','testLinks':[]}]
    methods = [{'ownerNodeId':'c','testIds':[]}]
    index = graph_index({'nodes':nodes,'edges':edges},LIMITS)
    assert static_links(tests,conditions,methods,index,LIMITS,{'steps':0})
    assert conditions[0]['testKinds'] == {'unit':3,'gherkin':1}
    assert {link['testKind'] for link in conditions[0]['testLinks']} == {'unit','gherkin'}
    assert conditions[0]['testLinksTotal'] == 4 and conditions[0]['testLinksTruncated']
    link = conditions[0]['testLinks'][0]
    assert link['path'] == [] and link['pathTruncated'] and link['pathLength'] == 4
    assert link['pathPrefix'] == ['u1'] and link['pathSuffix'] == ['c']


def test_unrelated_skip_and_ignore_attributes_do_not_disable_xunit(tmp_path):
    write(tmp_path, 'Code.cs', 'using Xunit; class OtherAttribute:System.Attribute { public string Skip{get;set;} } class IgnoreAttribute:System.Attribute {} class Tests { [Fact][Other(Skip="custom")][Ignore] void Test(){} }')
    result = build_test_coverage(tmp_path, graph(('unit','Test','Code.cs',1)), 's')
    assert result['tests'][0]['declaredStatus'] == 'enabled'
    assert result['tests'][0]['skipReasons'] == []


def test_nunit_type_ignore_is_declared_skip_mstest_ignore_supported(tmp_path):
    write(tmp_path, 'Code.cs', 'using NUnit.Framework; [Ignore("later")] class Tests { [Test] void Test(){} }')
    result = build_test_coverage(tmp_path, graph(('unit','Test','Code.cs',1)), 's')
    assert result['tests'][0]['declaredStatus'] == 'disabled'
    write(tmp_path, 'Code.cs', 'using Microsoft.VisualStudio.TestTools.UnitTesting; class Tests { [TestMethod][Ignore] void Test(){} }')
    result = build_test_coverage(tmp_path, graph(('unit','Test','Code.cs',1)), 's')
    assert result['tests'][0]['declaredStatus'] == 'disabled'


def test_report_and_xml_element_budgets_explicit(tmp_path, monkeypatch):
    data = fixture(tmp_path)
    write(tmp_path, 'coverage.xml', '<coverage>' + ' ' * 1000 + '</coverage>')
    monkeypatch.setitem(LIMITS, 'maxReportBytes', 128)
    result = build_test_coverage(tmp_path, data, 's')
    assert result['reports'][0]['status'] == 'rejected'
    assert result['stats']['reportBytes'] == 128
    monkeypatch.setitem(LIMITS, 'maxReportBytes', 8*1024*1024)
    monkeypatch.setitem(LIMITS, 'maxReportElements', 2)
    report(tmp_path, '<line number="4" hits="1"/>')
    result = build_test_coverage(tmp_path, data, 's')
    assert result['truncation']['reports']
    assert result['conditions'][0]['runtime']['status'] == 'unknown'


def test_response_budget_explicit_and_actual_serialized_size_bounded(tmp_path, monkeypatch):
    data = fixture(tmp_path)
    for index in range(60):
        name='method'+str(index)
        write(tmp_path, name+'.cs', 'class Code { void '+name+'(bool yes){ if(yes){} } }')
        data['nodes'].append({'id':name,'label':name+'()','file':name+'.cs','line':1,'kind':'method'})
    monkeypatch.setitem(LIMITS,'maxResponseBytes',16000)
    result=build_test_coverage(tmp_path,data,'s')
    assert result['status']=='partial' and result['truncation']['response']
    assert len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode())<=16000
    assert result['returned']['conditions'] < result['summary']['conditions']
