"""Строгий версионируемый контракт архитектурных правил без исполняемых выражений."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = 'atlas-architecture-v1'
ANALYZER = 'atlas-dependencies-v1'
Relation = Literal['python-import', 'project-reference', 'code-dependency']


class StrictModel(BaseModel):
    """Отклоняет неизвестные поля и неявные преобразования конфигурации."""
    model_config = ConfigDict(extra='forbid', strict=True)


class Layer(StrictModel):
    """Именованная область: относительные fnmatch-пути либо точные ID проектов снимка."""
    id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_.-]+$')
    paths: list[str] = Field(default_factory=list, max_length=32)
    projects: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def selector(self):
        """Требует один способ выбора и запрещает выход пути за корень."""
        if bool(self.paths) == bool(self.projects):
            raise ValueError('Layer requires exactly one of paths/projects')
        for path in self.paths:
            if not path or len(path) > 300 or path.startswith('/') or '\\' in path or '..' in path.split('/'):
                raise ValueError('Layer paths must be bounded relative POSIX patterns')
        if any(not item or len(item) > 128 for item in self.projects):
            raise ValueError('Invalid project ID')
        return self


class Rule(StrictModel):
    """Запрет направленного ребра или цикла внутри выбранных слоёв."""
    id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_.-]+$')
    kind: Literal['forbidden', 'cycles']
    layers: list[str] = Field(min_length=1, max_length=32)
    target: list[str] = Field(default_factory=list, max_length=32)
    relations: list[Relation] = Field(min_length=1, max_length=3)

    @model_validator(mode='after')
    def direction(self):
        """Не допускает неявную целевую область или лишнюю цель для циклов."""
        if (self.kind == 'forbidden') != bool(self.target):
            raise ValueError('Only forbidden rules require target layers')
        return self


class Rules(StrictModel):
    """Полный набор правил; порядок элементов не влияет на идентичность конфигурации."""
    version: Literal['atlas-architecture-v1']
    exclude: list[str] = Field(default_factory=list, max_length=32)
    pythonRoot: str | None = Field(default=None, min_length=1, max_length=300)
    layers: list[Layer] = Field(min_length=1, max_length=32)
    rules: list[Rule] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def references(self):
        """Проверяет уникальность имён и существование всех ссылок на слои."""
        if any('python-import' in rule.relations for rule in self.rules) and self.pythonRoot is None:
            raise ValueError('python-import requires an explicit pythonRoot')
        if self.pythonRoot is not None and (self.pythonRoot.startswith('/') or '\\' in self.pythonRoot
                or any(part in {'', '..'} for part in self.pythonRoot.split('/'))):
            raise ValueError('pythonRoot must be a relative source directory')
        for path in self.exclude:
            if (not path or len(path) > 300 or path.startswith('/') or '\\' in path
                    or any(part in {'', '.', '..'} for part in path.split('/'))
                    or any(char in path for char in '*?[]')):
                raise ValueError('exclude requires literal relative POSIX paths')
        layer_ids = {item.id for item in self.layers}
        if len(layer_ids) != len(self.layers) or len({item.id for item in self.rules}) != len(self.rules):
            raise ValueError('Duplicate layer/rule ID')
        for rule in self.rules:
            if not set(rule.layers + rule.target) <= layer_ids:
                raise ValueError('Unknown layer reference')
        return self
