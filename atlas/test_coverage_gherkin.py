"""Ограниченный EN/RU inventory Gherkin; шаги и tags не считаются результатами запуска."""
import re

from .test_coverage_sources import stable_id

KEYWORDS = {
    "Feature": "feature", "Функционал": "feature", "Функция": "feature", "Свойство": "feature",
    "Rule": "rule", "Правило": "rule", "Background": "background", "Предыстория": "background", "Контекст": "background",
    "Scenario": "scenario", "Сценарий": "scenario", "Example": "scenario", "Пример": "scenario",
    "Scenario Outline": "scenario-outline", "Scenario Template": "scenario-outline", "Структура сценария": "scenario-outline",
    "Шаблон сценария": "scenario-outline", "Examples": "examples", "Сценарии": "examples", "Примеры": "examples",
}
STEP = re.compile(r"^(?:Given|When|Then|And|But|Допустим|Дано|Пусть|Когда|Если|То|Тогда|И|Но|А|\*)\s+(.+)$")
DISABLED_TAGS = {"ignore", "ignored", "skip", "skipped", "disabled", "skiplegacy"}


def scan_gherkin(budget, files: list[str], limits: dict) -> dict:
    """Сохраняет scenario templates, inherited tags, background и число строк Examples; не разворачивает тесты."""
    tests, truncated = [], False
    for filename in files[:limits["maxFeatureFiles"]]:
        source = budget.read(filename)
        if source is None:
            continue
        feature_tags, rule_tags, pending_tags = [], [], []
        feature_name, rule_name = "", ""
        background, rule_background, current = [], [], None
        background_partial, rule_background_partial = False, False
        section, table_header, examples_tags = "", False, []
        doc_string = False
        lines = source["text"].splitlines()
        for number, raw in enumerate(lines, 1):
            text = raw.strip()
            if text.startswith(('"""', "```")):
                doc_string = not doc_string
                continue
            if doc_string or not text or text.startswith("#"):
                continue
            if text.startswith("@"):
                pending_tags.extend(re.findall(r"@([^\s#]+)", text.split("#", 1)[0]))
                continue
            keyword, separator, name = text.partition(":")
            kind = KEYWORDS.get(keyword.strip()) if separator else None
            if kind:
                if current and kind != "examples":
                    current["endLine"] = number - 1
                    current = None
                section = kind
                if kind == "feature":
                    feature_tags, rule_tags = pending_tags, []
                    feature_name, rule_name = name.strip(), ""
                    background, rule_background = [], []
                    background_partial, rule_background_partial = False, False
                elif kind == "rule":
                    rule_tags, rule_name, rule_background = pending_tags, name.strip(), []
                    rule_background_partial = False
                elif kind == "background":
                    if rule_name:
                        rule_background = []
                        rule_background_partial = False
                    else:
                        background = []
                        background_partial = False
                elif kind in {"scenario", "scenario-outline"}:
                    if len(tests) >= limits["maxTests"]:
                        truncated = True
                        break
                    tags = list(dict.fromkeys(feature_tags + rule_tags + pending_tags))
                    disabled = [tag for tag in tags if tag.casefold() in DISABLED_TAGS]
                    inherited_steps = background + rule_background
                    inherited_partial = background_partial or rule_background_partial or len(inherited_steps) > limits["maxStepsPerTest"]
                    truncated |= inherited_partial
                    current = {"id": stable_id("coverage-gherkin", source["file"], number, name.strip()),
                        "nodeId": None, "kind": "gherkin", "subtype": kind, "framework": "gherkin", "name": name.strip(),
                        "namespace": feature_name or "<feature>", "feature": feature_name, "rule": rule_name,
                        "file": source["file"], "line": number, "column": len(raw) - len(raw.lstrip()) + 1, "endLine": len(lines),
                        "sourceStatus": "parsed" if source["stableRead"] and not inherited_partial else "partial",
                        "declaredStatus": "disabled" if disabled else "enabled", "skipReasons": ["Объявлен tag @" + tag for tag in disabled],
                        "tags": tags, "exampleRows": 0, "exampleRowsTruncated": False, "examples": [],
                        "steps": list(inherited_steps[:limits["maxStepsPerTest"]]), "staticTargetIds": [],
                        "runtime": {"status": "unknown", "reason": "Feature описывает сценарий, но не подтверждает его запуск."}}
                    tests.append(current)
                elif kind == "examples" and current:
                    table_header = False
                    examples_tags = list(dict.fromkeys(current["tags"] + pending_tags))
                    current["examples"].append({"name": name.strip(), "line": number, "tags": examples_tags,
                        "declaredStatus": "disabled" if any(tag.casefold() in DISABLED_TAGS for tag in examples_tags) else "enabled", "rows": 0})
                pending_tags = []
                continue
            step = STEP.match(text)
            if step:
                fact = {"text": step[1], "file": source["file"], "line": number, "bindings": []}
                if section == "background":
                    target = rule_background if rule_name else background
                    if len(target) < limits["maxStepsPerTest"]:
                        target.append(fact)
                    else:
                        truncated = True
                        if rule_name:
                            rule_background_partial = True
                        else:
                            background_partial = True
                elif current and section in {"scenario", "scenario-outline"}:
                    if len(current["steps"]) < limits["maxStepsPerTest"]:
                        current["steps"].append(fact)
                    else:
                        current["sourceStatus"] = "partial"
                        truncated = True
            elif text.startswith("|") and section == "examples" and current and current["examples"]:
                if not table_header:
                    table_header = True
                elif current["exampleRows"] < limits["maxExampleRows"]:
                    current["exampleRows"] += 1
                    current["examples"][-1]["rows"] += 1
                else:
                    current["exampleRowsTruncated"] = truncated = True
        # Background steps are copied as source facts; no regex or step code is executed.
    return {"tests": tests, "truncated": truncated or len(files) > limits["maxFeatureFiles"]}


def bind_steps(tests: list[dict], bindings: list[dict]) -> None:
    """Связывает только буквальный pattern со всем step text; regex/outline substitution намеренно не вычисляет."""
    exact = {}
    for binding in bindings:
        pattern = binding["pattern"]
        if re.search(r"[\[\]().*+?{}\\^$|]", pattern):
            continue
        exact.setdefault(pattern, []).append(binding)
    for test in tests:
        for step in test.get("steps", []):
            matches = exact.get(step["text"], [])
            step["bindings"] = [dict(binding, ambiguous=len(matches) > 1, sourceKind="working-tree") for binding in matches[:8]]
            step["bindingStatus"] = "ambiguous" if len(matches) > 1 else "exact-literal" if matches else "unresolved"
