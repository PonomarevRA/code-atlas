"""Gherkin AST и таблицы как данные; шаги/ожидания никогда не исполняются."""

from gherkin.parser import Parser

from .data_models import MAX_FEATURE_BYTES, MAX_RECORD_BYTES, require
from .data_storage import identity


def import_gherkin(path, file, writer):
    """Импортирует ограниченный AST, один шаблон на сценарий и записи таблиц."""
    require(path.stat().st_size <= MAX_FEATURE_BYTES, "FEATURE_LIMIT", "Один .feature превышает 8 MiB; разделите файл.")
    text = path.read_text(encoding="utf-8-sig")
    require(all(len(line.encode()) <= MAX_RECORD_BYTES for line in text.splitlines()), "TOKEN_LIMIT", "Строка Gherkin превышает 1 MiB.")
    document = Parser().parse(text)
    feature = document.get("feature")
    require(feature is not None, "FEATURE_EMPTY", "В .feature отсутствует Feature.")
    nodes, count = [], 0
    comments = document.get("comments", [])

    def tags(value):
        """Возвращает исходные теги без домыслов о настройках test runner."""
        return [item["name"] for item in value.get("tags", [])]

    def source(value):
        """Координаты принадлежат захваченному файлу, не текущей рабочей копии."""
        return {"file": file["path"], "sourceHash": file["sha256"], **value.get("location", {}),
                "sourceKind": "snapshot-captured-source"}

    def node(value, kind, owner, position):
        """Создаёт собственные ID, независимо от генератора AST-парсера."""
        nonlocal count
        count += 1
        require(count <= 20_000, "SCHEMA_LIMIT", "Gherkin AST превышает 20000 узлов.")
        result = {"id": "data:" + writer.map_id + ":gherkin:" + identity(file["path"], file["sha256"], position),
                  "kind": kind, "label": value.get("name", value.get("text", kind)), "owner": owner,
                  "description": value.get("description", ""), "source": source(value), "tags": tags(value)}
        nodes.append(result)
        return result

    feature_node = node(feature, "feature", None, "feature")
    feature_node["language"] = feature.get("language", "en")
    feature_node["comments"] = comments

    def steps(step_values, owner, position, scenario_id=None):
        """Шаги сценария и фона используют один контракт аргументов и источников."""
        result = []
        for number, step in enumerate(step_values):
            step_node = node(step, "step", owner, position + f"/step/{number}")
            saved = {**step_node, "text": step["text"], "keyword": step["keyword"],
                     "role": step.get("keywordType", "Unknown")}
            if "docString" in step:
                saved["docString"] = step["docString"]
            if "dataTable" in step:
                cid = writer.collection(file, (position, number, "table"), "Аргументы: " + step["text"], "data-table", owner)
                for row in step["dataTable"].get("rows", []):
                    table_values = {f"column{index + 1}": cell["value"] for index, cell in enumerate(row["cells"])}
                    writer.add(cid, table_values, {**source(row), "role": saved["role"], "scenarioId": scenario_id,
                               "backgroundOwner": owner if scenario_id is None else None,
                               "cells": [cell["location"] for cell in row["cells"]]})
                saved["dataTableId"] = cid
            result.append(saved)
        return result

    def children(container, owner, inherited_tags, inherited_background, prefix):
        """Обходит Feature/Rule, сохраняя владельца фона и порядок сценариев."""
        background = list(inherited_background)
        for number, child in enumerate(container.get("children", [])):
            location = f"{prefix}/{number}"
            if "background" in child:
                value = child["background"]
                current = node(value, "background", owner, location)
                current["steps"] = steps(value.get("steps", []), current["id"], location)
                background.append({"owner": current["id"], "steps": current["steps"], "source": current["source"]})
            elif "rule" in child:
                value = child["rule"]
                current = node(value, "rule", owner, location)
                children(value, current["id"], inherited_tags + tags(value), background, location)
            elif "scenario" in child:
                value = child["scenario"]
                kind = "scenario-outline" if value.get("examples") else "scenario"
                current = node(value, kind, owner, location)
                scenario = {**current, "name": value.get("name", ""), "keyword": value.get("keyword", ""),
                            "ownTags": tags(value), "tags": list(dict.fromkeys(inherited_tags + tags(value))),
                            "background": background, "steps": [], "examples": [], "runtimeStatus": "unknown"}
                start = value["location"]["line"]
                subsequent = container.get("children", [])[number + 1:]
                end = next(iter(subsequent[0].values())).get("location", {}).get("line", len(text.splitlines()) + 1) if subsequent else len(text.splitlines()) + 1
                scenario["comments"] = [item for item in comments if start <= item["location"]["line"] < end]
                scenario["commentScope"] = "source-range"
                scenario["steps"] = steps(value.get("steps", []), current["id"], location, current["id"])
                for example_number, example in enumerate(value.get("examples", [])):
                    header = example.get("tableHeader")
                    if header is None:
                        continue
                    names = [cell["value"] for cell in header["cells"]]
                    require(all(names) and len(names) == len(set(names)), "EXAMPLES_HEADER", "Examples содержит пустые или повторяющиеся заголовки.")
                    cid = writer.collection(file, (location, example_number, "examples"), "Examples: " + (example.get("name") or current["label"]), "examples", current["id"])
                    example_tags = list(dict.fromkeys(scenario["tags"] + tags(example)))
                    for row in example.get("tableBody", []):
                        values = [cell["value"] for cell in row["cells"]]
                        require(len(values) == len(names), "EXAMPLES_WIDTH", "Examples содержит строку неверной ширины.")
                        writer.add(cid, dict(zip(names, values)), {**source(row), "scenarioId": current["id"], "tags": example_tags,
                                   "role": "scenario-parameters", "cells": [cell["location"] for cell in row["cells"]]})
                    scenario["examples"].append({"collectionId": cid, "tags": example_tags, "source": source(example)})
                writer.scenario(scenario)
    children(feature, feature_node["id"], tags(feature), [], "feature")
    return nodes
