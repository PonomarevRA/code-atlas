"""Real-language paired examples keep advisory readability and numeric health distinct."""

from copy import deepcopy

import pytest

from atlas.code_health import enrich_code_health
from atlas.code_health_source import parse_health_source
from atlas.code_health_policy import structural_components
from atlas.mcp_graph import build_mcp_graph
from tests.test_member_graph import capture


def measured(filename, source):
    """Measure actual grammar output without executing any example or analyzed callback."""
    nodes = parse_health_source(filename, source.encode())["methods"]
    return enrich_code_health({"nodes": nodes, "edges": [], "metadata": {}})["nodes"]


@pytest.mark.parametrize("filename,source", [
    ("sample.py", "def choose(a,b,c,d,e):\n if not a and b or not c and d or e: return 1\n return 0\n"),
    ("sample.js", "function choose(a,b,c,d,e){ if (!a && b || !c && d || e) return 1; return 0; }"),
    ("sample.cs", "class C { int Choose(bool a,bool b,bool c,bool d,bool e){ if (!a && b || !c && d || e) return 1; return 0; } }"),
    ("sample.rs", "fn choose(a:bool,b:bool,c:bool,d:bool,e:bool)->i32 { if !a && b || !c && d || e { return 1; } 0 }"),
])
def test_dense_expression_has_same_explainable_signals_across_languages(filename, source):
    node = measured(filename, source)[0]
    metrics = node["complexity"]
    signal = metrics["readability"]
    assert signal["version"] == "local-readability-v1" and signal["status"] == "complete"
    assert signal["maxBooleanOperands"] == 5
    assert signal["mixedBooleanExpressions"] == 1 and signal["booleanNegations"] == 2
    assert signal["expressions"][0]["operators"] == ["and", "or"]
    warning = next(f for f in node["health"]["findings"] if f["rule"] == "dense-boolean-expression")
    assert warning["evidence"]["operandCount"] == 5 and warning["line"] > 0
    assert node["health"]["verdict"] == "REVIEW"
    # New evidence must not silently change the existing numeric policy.
    assert structural_components(metrics, None)["flow"] == 100


@pytest.mark.parametrize("filename,nested,guards", [
    ("a.py", "def choose(a,b,c):\n if a:\n  if b:\n   if c: return 1\n return 0\n",
     "def choose(a,b,c):\n if not a: return 0\n if not b: return 0\n if not c: return 0\n return 1\n"),
    ("a.js", "function choose(a,b,c){ if(a){ if(b){ if(c) return 1; } } return 0; }",
     "function choose(a,b,c){ if(!a)return 0; if(!b)return 0; if(!c)return 0; return 1; }"),
    ("a.cs", "class C{int Choose(bool a,bool b,bool c){if(a){if(b){if(c)return 1;}}return 0;}}",
     "class C{int Choose(bool a,bool b,bool c){if(!a)return 0;if(!b)return 0;if(!c)return 0;return 1;}}"),
    ("a.rs", "fn choose(a:bool,b:bool,c:bool)->i32 {if a {if b {if c {return 1;}}} 0}",
     "fn choose(a:bool,b:bool,c:bool)->i32 {if !a{return 0;}if !b{return 0;}if !c{return 0;}1}"),
])
def test_guard_clauses_reduce_depth_without_negation_penalty(filename, nested, guards):
    before, after = measured(filename, nested)[0], measured(filename, guards)[0]
    assert before["complexity"]["maxNestingDepth"] == 3
    assert after["complexity"]["maxNestingDepth"] == 1
    assert after["health"]["components"]["flow"] > before["health"]["components"]["flow"]
    assert not any(f["rule"] == "dense-boolean-expression" for f in after["health"]["findings"])


def test_calls_and_deferred_bodies_do_not_merge_unrelated_boolean_groups():
    nodes = measured("a.py", "def configure(a,b,c,d):\n def callback():\n  return a and b and c and d\n return check(a and b, c or d)\n")
    caller, callback = nodes
    signal = caller["complexity"]["readability"]
    assert signal["maxBooleanOperands"] == 2 and signal["mixedBooleanExpressions"] == 0
    assert callback["complexity"]["readability"]["maxBooleanOperands"] == 4
    assert signal["deferredBodyCount"] == 1
    item = next(f for f in caller["health"]["findings"] if f["rule"] == "deferred-logic-inventory")
    assert item["severity"] == "info" and item["evidence"]["excludedDeferredBodies"] == 1


def test_boolean_evidence_budget_prioritizes_warning_and_unknown_stays_unknown(monkeypatch):
    simple = "\n".join(" x = a and b and c and d" for _ in range(10))
    node = measured("a.py", "def choose(a,b,c,d,e):\n" + simple + "\n return a and b and c and d and e\n")[0]
    signal = node["complexity"]["readability"]
    assert len(signal["expressions"]) == 8 and signal["evidenceTruncated"]
    assert signal["expressions"][0]["operandCount"] == 5
    assert any(f["rule"] == "dense-boolean-expression" for f in node["health"]["findings"])
    import atlas.code_complexity as complexity
    monkeypatch.setattr(complexity, "MAX_AST_NODES", 4)
    partial = measured("a.py", "def choose(a,b,c):\n return a and b and c\n")[0]
    assert partial["complexity"]["readability"]["status"] == "partial"
    assert partial["health"]["score"] is None
    macro = measured("a.rs", "fn run(a: bool) { if a { println!(\"test\"); } }")[0]
    assert macro["complexity"]["readability"]["status"] == "partial"
    assert macro["health"]["score"] is None


@pytest.mark.parametrize("suffix,signature,prefix", [
    (suffix, signature, prefix)
    for suffix, signature in [
        (".js", "function doubled(x) { return x * 2; }"),
        (".ts", "function doubled(x: number): number { return x * 2; }"),
        (".js", "const doubled = (x) => x * 2;"),
        (".ts", "const doubled = (x: number): number => x * 2;"),
    ]
    for prefix in ("", "export ", "export default ")
    if not (prefix == "export default " and signature.startswith("const"))
])
def test_exported_declaration_owns_jsdoc(suffix, signature, prefix):
    docs = "/** Calculate a doubled amount.\n * @param x Input amount.\n * @returns Doubled amount. */\n"
    node = measured("a" + suffix, docs + prefix + signature)[0]
    assert node["documentationHealth"]["score"] == 100
    assert node["documentationHealth"]["semanticReview"]["status"] == "not-reviewed"
    unrelated = measured("a" + suffix, docs + "const unrelated = 1;\n" + prefix + signature)[0]
    assert unrelated["documentationHealth"]["ownDescription"] is False


@pytest.mark.parametrize("filename,source", [
    ("a.py", 'def calculate_total(x):\n """Calculate total."""\n return x * 2\n'),
    ("a.js", '/** Calculate total. */\nexport const calculateTotal = (x) => x * 2;'),
])
def test_syntax_only_documentation_does_not_get_credit_from_name_style(filename, source):
    node = measured(filename, source)[0]
    assert node["documentationHealth"]["ownDescription"] is False
    assert any(f["rule"] == "placeholder-documentation" for f in node["health"]["findings"])


def test_source_card_and_snapshot_mcp_publish_same_readability_without_mutation(tmp_path):
    docs = "/** Selects values that satisfy the configured acceptance rules. */\n"
    graph = capture(tmp_path, ".js", docs + "export function choose(a,b,c,d,e){return a && b || c && d || e;}")
    node = next(n for n in graph["nodes"] if n.get("memberFacts", {}).get("name") == "choose")
    assert "acceptance rules" in node["callableFacts"]["description"]
    saved = deepcopy(graph)
    published = next(n for n in build_mcp_graph(graph)["nodes"] if n["id"] == node["id"])
    assert published["complexity"]["readability"] == node["complexity"]["readability"]
    assert graph == saved
