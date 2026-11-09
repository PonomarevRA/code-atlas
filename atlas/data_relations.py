"""Проверяемые совпадения ключей отдельно от заявленного бизнес-смысла."""

from pathlib import Path
import re

from .data_models import require
from .data_storage import identity


def key_name(field: str) -> str:
    """Нормализует только имя для предложения кандидата, не значения данных."""
    return re.sub(r"[^a-zа-я0-9]", "", field.rsplit("/", 1)[-1].casefold())


def eligible(source: dict, field: str, target: dict, other: str) -> bool:
    """Предлагает одноимённые ключи или entityId → id; не делает all-pairs строк."""
    name, remote = key_name(field), key_name(other)
    entity = re.sub(r"[^a-zа-я0-9]", "", Path(target["file"]).stem.casefold()).rstrip("s")
    return name == remote or (remote == "id" and name in {entity + "id", entity + "guid"})


def validate_relation(connection, rule: dict, collections: dict, declared: bool) -> dict:
    """Считает охват через EXISTS, не материализуя декартово произведение дублей."""
    source, target = rule["sourceCollection"], rule["targetCollection"]
    left, right = rule["sourceField"], rule["targetField"]
    mode = rule.get("compareAs", "typed")
    require(mode in {"typed", "string"}, "INVALID_RELATION", "compareAs должен быть typed или string.")
    for cid, field in ((source, left), (target, right)):
        require(cid in collections, "INVALID_RELATION", "Коллекция правила отсутствует.")
        require(any(item["name"] == field and item["indexed"] for item in collections[cid]["fields"]),
                "UNINDEXED_FIELD", "Для правила нужно импортировать индексируемые поля.")
    compare = "t.type=s.type AND t.value=s.value" if mode == "typed" else "t.raw=s.raw"
    distinct_key = "type||':'||value" if mode == "typed" else "raw"
    present, distinct = connection.execute(f"SELECT count(*), count(DISTINCT {distinct_key}) FROM cells WHERE collection=? AND field=? AND type!='null'",
                                          (source, left)).fetchone()
    remote, remote_distinct = connection.execute(f"SELECT count(*), count(DISTINCT {distinct_key}) FROM cells WHERE collection=? AND field=? AND type!='null'",
                                                 (target, right)).fetchone()
    matched = connection.execute(f"""SELECT count(*) FROM cells s WHERE s.collection=? AND s.field=? AND s.type!='null'
          AND EXISTS (SELECT 1 FROM cells t WHERE t.collection=? AND t.field=? AND t.type!='null' AND {compare})""",
                                 (source, left, target, right)).fetchone()[0]
    total = collections[source]["count"]
    result = {**rule, "id": "relation:" + identity(source, left, target, right, mode),
              "sourceFile": collections[source]["file"], "targetFile": collections[target]["file"],
              "status": "declared" if declared else "candidate", "evidence": "full-snapshot-value-match",
              "semanticStatus": "user-declared" if declared else "inferred", "compareAs": mode,
              "coverage": {"complete": True, "records": total, "nonNullKeys": present, "matched": matched,
                           "unmatched": present - matched, "missingOrNull": total - present,
                           "duplicateSourceKeys": present - distinct, "duplicateTargetKeys": remote - remote_distinct},
              "cardinality": ("many" if present != distinct else "one") + "-to-" + ("many" if remote != remote_distinct else "one")}
    return result


def build_relations(connection, collections: dict, rules: list[dict], cancelled) -> dict:
    """Проверяет явные правила и до 500 кандидатов из максимум 64 индексных полей."""
    indexed = [(cid, field["name"]) for cid, item in collections.items() for field in item["fields"] if field["indexed"]]
    result, used, tested, truncated = [], set(), 0, False

    def resolve(rule, side):
        """Ссылка по файлу допустима только при единственной подходящей коллекции."""
        selected = rule.get(side + "Collection")
        if selected:
            return selected
        matches = [cid for cid, item in collections.items() if item["file"] == rule.get(side + "File")
                   and any(field["name"] == rule.get(side + "Field") for field in item["fields"])]
        require(len(matches) == 1, "AMBIGUOUS_RELATION", "Укажите точную коллекцию правила; путь файла неоднозначен или поле отсутствует.")
        return matches[0]

    for supplied in rules:
        require(isinstance(supplied, dict), "INVALID_RELATION", "Правило должно быть объектом.")
        require(set(supplied) <= {"sourceCollection", "targetCollection", "sourceFile", "targetFile", "sourceField", "targetField", "compareAs"}
                and all(isinstance(value, str) and len(value) <= 1024 for value in supplied.values()),
                "INVALID_RELATION", "Проверьте поля правила связи.")
        rule = {**supplied, "sourceCollection": resolve(supplied, "source"), "targetCollection": resolve(supplied, "target")}
        require("sourceField" in rule and "targetField" in rule, "INVALID_RELATION", "В правиле нужны sourceField и targetField.")
        value = validate_relation(connection, rule, collections, True)
        result.append(value)
        used.add(value["id"])
    for source, field in indexed:
        for target, other in indexed:
            require(not cancelled(), "CANCELLED", "Импорт отменён.", 409)
            if source == target or not eligible(collections[source], field, collections[target], other):
                continue
            if tested >= 500:
                truncated = True
                break
            tested += 1
            value = validate_relation(connection, {"sourceCollection": source, "targetCollection": target,
                                                  "sourceField": field, "targetField": other}, collections, False)
            if value["coverage"]["matched"] and value["id"] not in used:
                result.append(value)
                used.add(value["id"])
        if truncated:
            break
    return {"items": result, "candidatePairsChecked": tested, "truncated": truncated,
            "indexedFields": len(indexed), "searchCoverage": "limited-candidate-fields"}
