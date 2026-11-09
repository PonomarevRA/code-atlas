"""Ограниченные запросы оценки, прогноза и сравнения здоровья через HTTP/MCP."""

from typing import Literal
from pydantic import BaseModel, Field, model_validator


class HealthRefreshQuery(BaseModel):
    """Refresh only a pinned captured map; working files and model execution are outside this job."""
    snapshotId: str | None = Field(default=None, max_length=128)
    model_config = {"extra": "forbid"}


class HealthQuery(BaseModel):
    """Файлы относятся только к зарегистрированному проекту; чтение не перестраивает карту."""
    snapshotId: str | None = Field(default=None, max_length=128)
    sourceMode: Literal["snapshot", "working-tree"] = "snapshot"
    nodeIds: list[str] = Field(default_factory=list, max_length=120)
    files: list[str] = Field(default_factory=list, max_length=20)
    maxMethods: int = Field(default=120, ge=1, le=120, strict=True)
    locale: Literal["ru", "en"] = "en"
    model_config = {"extra": "forbid"}


class DraftFile(BaseModel):
    """Полный предлагаемый текст файла остаётся в памяти и никогда не записывается в проект."""
    file: str = Field(min_length=1, max_length=4096, description="Project-relative file path for a full proposed draft.")
    content: str = Field(max_length=1048576, description="Complete proposed file text; a change summary or diff is insufficient.")
    model_config = {"extra": "forbid"}


class MetricRange(BaseModel):
    """Явный диапазон сценарного предположения, а не измерение исходника."""
    min: float = Field(ge=0, le=100000, allow_inf_nan=False)
    max: float = Field(ge=0, le=100000, allow_inf_nan=False)
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def ordered(self):
        """Обратный диапазон не может молча изменить смысл прогноза."""
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


class HealthProjection(BaseModel):
    """Целевой профиль существующего метода; все изменения явно названы допущениями."""
    identity: str = Field(min_length=1, max_length=128,
                          description="Copy nodes[].healthIdentity from get_code_health's baseline; not a file path or raw node ID.")
    reason: str = Field(min_length=1, max_length=1000, description="Explain this method's proposed change and assumptions.")
    metrics: dict[str, MetricRange] = Field(default_factory=dict, max_length=8)
    documentationScore: MetricRange | None = None
    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def method_identity_contract(cls, value):
        """Explains a common file/change mix-up before producing missing-field and extra-field errors."""
        if isinstance(value, dict) and ({"file", "change"} & value.keys()):
            raise ValueError(
                'Each projection needs identity and reason: '
                '{"identity":"<nodes[].healthIdentity from get_code_health>","reason":"Proposed method change"}. '
                'file/change objects are not projections. For file-based estimates use '
                'drafts=[{"file":"src/example.py","content":"complete proposed file text"}].'
            )
        return value

    @model_validator(mode="after")
    def supported_metrics(self):
        """Не принимает выдуманные метрики или процент документации вне 0–100."""
        allowed = {"maxNestingDepth", "branchCount", "loopCount", "statementCount", "callTargets", "writes"}
        if set(self.metrics) - allowed:
            raise ValueError("Unsupported projected metric")
        if self.documentationScore and self.documentationScore.max > 100:
            raise ValueError("Documentation score must be between 0 and 100")
        return self


class HealthPlanQuery(BaseModel):
    """План основан на immutable оценке; черновики и сценарии не меняют исходник."""
    baselineAssessmentId: str = Field(pattern=r"^[a-f0-9]{32}$")
    intent: str = Field(min_length=1, max_length=2000)
    drafts: list[DraftFile] = Field(default_factory=list, max_length=20)
    projections: list[HealthProjection] = Field(default_factory=list, max_length=120)
    locale: Literal["ru", "en"] = "en"
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def one_basis(self):
        """Не смешивает факты чернового текста с предположениями пользователя."""
        if bool(self.drafts) == bool(self.projections):
            raise ValueError("Choose drafts or projections")
        return self


class HealthCompareQuery(BaseModel):
    """Переименования задаются явно; автоматический join использует полную identity."""
    beforeAssessmentId: str = Field(pattern=r"^[a-f0-9]{32}$")
    afterAssessmentId: str = Field(pattern=r"^[a-f0-9]{32}$")
    planId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    identityMap: dict[str, str] = Field(default_factory=dict, max_length=120)
    locale: Literal["ru", "en"] = "en"
    model_config = {"extra": "forbid"}
