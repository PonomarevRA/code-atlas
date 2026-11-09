"""Typed receiver facts must not become spurious self-recursion in reader call targets."""

from atlas.execution_path import _bind_events


def test_exact_typed_interface_call_overrides_same_name_guess():
    """The same line/name may have a heuristic self-edge and an independently typed interface edge."""
    nodes = {name: {"id": name, "label": ".LoadAsync()"} for name in ["caller", "interface"]}
    step = {"events": [{"kind": "call", "callee": "LoadAsync", "line": 37, "confidence": "source"}], "unresolved": []}
    common = {"source": "caller", "relation": "calls", "sourceLine": 37, "confidence": "graph"}
    guessed = {**common, "target": "caller"}
    typed = {**common, "target": "interface", "evidence": {"kind": "typed-interface-call"}}
    _bind_events(step, [guessed, typed], nodes)
    assert step["events"][0]["targetNodeIds"] == ["interface"]


def test_unqualified_recursion_remains_visible_without_typed_receiver():
    """An actual recursive call must remain available when no stronger receiver fact exists."""
    nodes = {"caller": {"id": "caller", "label": ".LoadAsync()"}}
    step = {"events": [{"kind": "call", "callee": "LoadAsync", "line": 37, "confidence": "source"}], "unresolved": []}
    _bind_events(step, [{"source": "caller", "target": "caller", "relation": "calls", "sourceLine": 37, "confidence": "graph"}], nodes)
    assert step["events"][0]["targetNodeIds"] == ["caller"]


def test_same_line_calls_keep_ambiguity_without_column_evidence():
    """Line-only typed evidence cannot separate an interface call from recursion on the same line."""
    nodes = {name: {"id": name, "label": ".LoadAsync()"} for name in ["caller", "interface"]}
    step = {"events": [{"kind": "call", "callee": "LoadAsync", "line": 37, "column": column, "confidence": "source"}
                       for column in [1, 25]], "unresolved": []}
    common = {"source": "caller", "relation": "calls", "sourceLine": 37, "confidence": "graph"}
    _bind_events(step, [{**common, "target": "caller"},
                        {**common, "target": "interface", "evidence": {"kind": "typed-interface-call"}}], nodes)
    for event in step["events"]:
        assert event["targetNodeIds"] == ["caller", "interface"]
