"""Явное принятие исходного списка нарушений без автоматического скрытия новых дефектов."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import Field, model_validator
from .architecture_rules import StrictModel, VERSION, ANALYZER


class BaselineFinding(StrictModel):
    """Идентичность нарушения без нестабильных номеров строк."""
    id: str = Field(pattern=r'^[a-f0-9]{64}$')
    ruleId: str = Field(min_length=1, max_length=80)
    kind: Literal['forbidden', 'cycles']


class Baseline(StrictModel):
    """Совместимость фиксирует анализатор, смысл правил и исходное наблюдение."""
    version: Literal['atlas-architecture-baseline-v1']
    analyzerVersion: Literal['atlas-dependencies-v1']
    rulesVersion: Literal['atlas-architecture-v1']
    rulesFingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    sourceFingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    createdAt: str = Field(min_length=1, max_length=80)
    ruleIds: list[str] = Field(min_length=1, max_length=100)
    findings: list[BaselineFinding] = Field(max_length=1000)

    @model_validator(mode='after')
    def identities(self):
        """Отклоняет дубликаты и ссылки на отсутствующее правило."""
        if len(set(self.ruleIds)) != len(self.ruleIds) or len({item.id for item in self.findings}) != len(self.findings):
            raise ValueError('Duplicate baseline identity')
        if any(item.ruleId not in self.ruleIds for item in self.findings):
            raise ValueError('Unknown baseline rule')
        return self


def create_baseline(result: dict) -> Baseline:
    """Создаёт baseline лишь из полного результата; caller явно решает записать файл."""
    if (result['findingsTruncated'] or result['counts']['unknown']
            or any(item['coverage'] != 'complete' or item['findingsTruncated'] for item in result['rules'])):
        raise ValueError('Baseline requires complete, untruncated analysis; unknown cannot be accepted')
    return Baseline(version='atlas-architecture-baseline-v1', analyzerVersion=ANALYZER, rulesVersion=VERSION,
                    rulesFingerprint=result['rulesFingerprint'], sourceFingerprint=result['sourceFingerprint'],
                    createdAt=datetime.now(timezone.utc).isoformat(),
                    ruleIds=sorted(item['ruleId'] for item in result['rules']),
                    findings=[BaselineFinding(**{key: item[key] for key in ('id', 'ruleId', 'kind')}) for item in result['findings']])


def compare_baseline(result: dict, baseline: Baseline) -> dict:
    """Неподтверждённое отсутствие старого нарушения остаётся unconfirmed, не resolved."""
    rule_ids = sorted(item['ruleId'] for item in result['rules'])
    if (baseline.rulesFingerprint != result['rulesFingerprint'] or sorted(baseline.ruleIds) != rule_ids
            or baseline.analyzerVersion != result['analyzerVersion'] or baseline.rulesVersion != result['version']):
        raise ValueError('Baseline is incompatible with current rules/analyzer/scope; explicitly recreate it')
    previous = {item.id: item for item in baseline.findings}
    current = {item['id']: item for item in result['findings']}
    new, existing, resolved, unconfirmed = [], [], [], []
    by_rule = {item['ruleId']: item for item in result['rules']}
    for identifier in sorted(current):
        (existing if identifier in previous else new).append(identifier)
    for identifier in sorted(previous.keys() - current.keys()):
        rule = by_rule[previous[identifier].ruleId]
        complete = rule['coverage'] == 'complete' and not rule['findingsTruncated'] and not result['findingsTruncated']
        # Смена представителя не доказывает исчезновение прежнего цикла. Без
        # хранения всех прежних путей resolved допустим лишь когда циклов нет.
        if previous[identifier].kind == 'cycles' and rule['findingCount']:
            complete = False
        (resolved if complete else unconfirmed).append(identifier)
    return {'compatible': True, 'new': new, 'existing': existing, 'resolved': resolved,
            'unconfirmed': unconfirmed, 'baselineSourceFingerprint': baseline.sourceFingerprint}
