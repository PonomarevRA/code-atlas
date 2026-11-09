"""Gherkin inheritance and lexical binding regressions."""
from atlas.test_coverage import build_test_coverage, LIMITS


def make_source(root, text):
    """Creates an isolated feature fixture."""
    (root / "Cases.feature").write_text(text)


def test_ru_outline_background_tags_examples_are_declared_not_executed(tmp_path):
    make_source(tmp_path, '''# language: ru
@SkipLegacy
Функционал: Расчёт
  Предыстория:
    Дано начальные данные
  @slow
  Правило: Валюта
    Контекст:
      И курс валюты
    @unit
    Структура сценария: Доход <value>
      Когда рассчитываю <value>
      То получаю результат
      @ignore
      Примеры: Старые
        | value |
        | 10 |
        | 20 |
''')
    result = build_test_coverage(tmp_path, {"nodes": [], "edges": []}, "s")
    test = result["tests"][0]
    assert test["kind"] == "gherkin" and test["subtype"] == "scenario-outline"
    assert test["tags"] == ["SkipLegacy", "slow", "unit"]
    assert test["declaredStatus"] == "disabled"
    assert test["exampleRows"] == 2
    assert test["examples"][0]["tags"] == ["SkipLegacy", "slow", "unit", "ignore"]
    assert [step["text"] for step in test["steps"]] == ["начальные данные", "курс валюты", "рассчитываю <value>", "получаю результат"]
    assert test["runtime"]["status"] == "unknown"


def test_background_overflow_and_combined_inheritance_explicit(tmp_path, monkeypatch):
    monkeypatch.setitem(LIMITS, "maxStepsPerTest", 2)
    make_source(tmp_path, '''Feature: F
Background:
Given one
And two
And omitted
Scenario: T
''')
    result = build_test_coverage(tmp_path, {"nodes": [], "edges": []}, "s")
    assert result["truncation"]["gherkin"]
    assert result["tests"][0]["sourceStatus"] == "partial"
    assert len(result["tests"][0]["steps"]) == 2
    make_source(tmp_path, '''Feature: F
Background:
Given one
And two
Rule: R
Background:
Given three
Scenario: T
''')
    result = build_test_coverage(tmp_path, {"nodes": [], "edges": []}, "s")
    assert result["truncation"]["gherkin"]
    assert result["tests"][0]["sourceStatus"] == "partial"
    assert len(result["tests"][0]["steps"]) == 2


def test_exact_boundary_background_is_complete(tmp_path, monkeypatch):
    monkeypatch.setitem(LIMITS, "maxStepsPerTest", 2)
    make_source(tmp_path, 'Feature: F\nBackground:\nGiven one\nAnd two\nScenario: T\n')
    result = build_test_coverage(tmp_path, {"nodes": [], "edges": []}, "s")
    assert result["tests"][0]["sourceStatus"] == "parsed"
    assert not result["truncation"]["gherkin"]


def test_exact_qualified_binding_links_ambiguous_and_regex_unresolved(tmp_path):
    (tmp_path / "Code.cs").write_text('''using Reqnroll;
namespace Steps; class Code {
 [Given("initial data")] void GivenData() { Calculate(); }
 [Given("initial data")] void Duplicate() { }
 [When("run (.*)")] void Regex() { }
 void Calculate() { if(true) {} }
}''')
    make_source(tmp_path, 'Feature: F\nScenario: Test\nGiven initial data\nWhen run 12\n')
    nodes = [{"id": id, "label": name + "()", "file": "Code.cs", "kind": "method", "line": line} for id, name, line in [("given", "GivenData", 3), ("dupe", "Duplicate", 4), ("regex", "Regex", 5), ("calc", "Calculate", 6)]]
    data = {"nodes": nodes, "edges": [{"source": "given", "target": "calc", "relation": "calls"}]}
    result = build_test_coverage(tmp_path, data, "s", "calc")
    test = result["tests"][0]
    assert test["steps"][0]["bindingStatus"] == "ambiguous"
    assert test["steps"][1]["bindingStatus"] == "unresolved"
    link = result["conditions"][0]["testLinks"][0]
    assert link["confidence"] == "candidate" and link["bindingEvidence"]["ambiguous"]
    assert link["path"] == ["given", "calc"]
    assert link["bindingEvidence"]["file"] == "Code.cs"


def test_docstring_keywords_do_not_create_fake_scenarios(tmp_path):
    make_source(tmp_path, 'Feature: F\nScenario: Real\nGiven payload\n"""\nScenario: Fake\n"""\n')
    result = build_test_coverage(tmp_path, {"nodes": [], "edges": []}, "s")
    assert [test["name"] for test in result["tests"]] == ["Real"]
