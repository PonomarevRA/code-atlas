"""Версионируемые правила здоровья отделены от локализации и исходных фактов."""

from functools import lru_cache
from decimal import Decimal, ROUND_HALF_UP
import json

from .runtime_paths import resource_root


@lru_cache(maxsize=1)
def policy() -> dict:
    """Читает поставляемую политику; проект не может подменить её комментариями."""
    path = resource_root() / "resources/code-health/policies/atlas-health-v3.json"
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def messages(locale: str = "en") -> dict:
    """Неподдержанная локаль использует английский текст с неизменными rule ID."""
    root = resource_root() / "resources/code-health/locales"
    name = locale if locale in {"ru", "en"} else "en"
    return json.loads((root / (name + ".json")).read_text(encoding="utf-8"))


def finding(rule: str, *, file: str = "", line: int | None = None,
            severity: str = "warning", evidence=None, locale: str = "en") -> dict:
    """Возвращает стабильное правило с локализованным советом и проверяемым местом."""
    result = {"rule": rule, "severity": severity, "message": messages(locale).get(rule, rule),
              "file": file, "line": line}
    if evidence is not None:
        result["evidence"] = evidence
    return result


def structural_components(metrics: dict, dependencies: dict | None) -> dict:
    """Преобразует полные измерения в баллы; частичные данные не заполняются нулями."""
    result = {"flow": None, "size": None, "dependencies": None}
    rules = policy()
    if metrics.get("status") == "complete" and all(metrics.get(k) is not None for k in ("maxNestingDepth", "loopCount", "statementCount")) and (metrics.get("branchCount") is not None or metrics.get("branchingCount") is not None):
        flow = rules["flow"]
        penalty = min(flow["nestingCap"], max(0, metrics["maxNestingDepth"] - flow["nestingFree"]) * flow["nestingPenalty"])
        penalty += min(flow["branchCap"], max(0, metrics.get("branchCount", metrics.get("branchingCount")) - flow["branchesFree"]) * flow["branchPenalty"])
        penalty += min(flow["loopCap"], max(0, metrics["loopCount"] - flow["loopsFree"]) * flow["loopPenalty"])
        result["flow"] = max(0, 100 - penalty)
        size = rules["size"]
        result["size"] = 100 - min(size["cap"], max(0, metrics["statementCount"] - size["statementsFree"]) * size["statementPenalty"])
    if dependencies and dependencies.get("status") == "complete":
        coupling = rules["dependencies"]
        penalty = min(coupling["targetCap"], max(0, dependencies["callTargets"] - coupling["targetsFree"]) * coupling["targetPenalty"])
        penalty += min(coupling["writeCap"], max(0, dependencies["writes"] - coupling["writesFree"]) * coupling["writePenalty"])
        penalty += coupling["recursionPenalty"] if dependencies.get("recursive") else 0
        result["dependencies"] = max(0, 100 - penalty)
    return result


def round_score(value: Decimal) -> float:
    """Округляет балл до десятых одинаково для обеих границ малого бонуса.

    :param value: Точно вычисленный десятичный балл до округления.
    :returns: Балл с округлением половины вверх; без банковских скачков на 0,1.
    """
    return float(value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def weighted_score(components: dict, weights: dict) -> float | None:
    """Считает применимую взвешенную оценку, не подставляя значения за UNKNOWN.

    :param components: Измеренные баллы компонентов; None означает неизвестность.
    :param weights: Веса только применимых компонентов из версионированной политики.
    :returns: Балл до десятых либо None при неизвестных фактах/пустом знаменателе.
    """
    total = denominator = Decimal(0)
    for name, weight in weights.items():
        value = components.get(name)
        if value is None:
            return None
        decimal_weight = Decimal(str(weight))
        total += Decimal(str(value)) * decimal_weight
        denominator += decimal_weight
    return round_score(total / denominator) if denominator else None


def mean_score(values: list) -> float | None:
    """Усредняет известную выборку с тем же округлением, что и оценки методов.

    :param values: Уже отобранные известные баллы; полноту проверяет вызывающий код.
    :returns: Среднее до десятых либо None для пустой выборки.
    """
    total = sum((Decimal(str(value)) for value in values), Decimal(0))
    return round_score(total / len(values)) if values else None
