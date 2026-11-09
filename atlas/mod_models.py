"""Versioned mod manifest: presentation and grouping never replace extracted source facts."""
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator
from .mod_analysis_models import ModAnalysis
from .color_scheme_models import ColorScheme

ID = r'^[a-z][a-z0-9-]{0,47}$'
COLOR = r'^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$'


class CardTheme(BaseModel):
    """Small declarative themes remain valid CSS/SVG values, not injected markup."""
    fill: str | None = Field(default=None, pattern=COLOR)
    headerFill: str | None = Field(default=None, pattern=COLOR)
    stroke: str | None = Field(default=None, pattern=COLOR)
    text: str | None = Field(default=None, pattern=COLOR)
    radius: float | None = Field(default=None, ge=0, le=24, allow_inf_nan=False)
    borderWidth: float | None = Field(default=None, ge=.5, le=5, allow_inf_nan=False)
    fontSize: float | None = Field(default=None, ge=9, le=20, allow_inf_nan=False)
    model_config = {'extra':'forbid'}


class ModGrouping(BaseModel):
    """A declaration groups existing IDs by data or delegates to an explicitly enabled Python worker."""
    id: str = Field(pattern=ID)
    label: str = Field(min_length=1, max_length=100)
    mode: Literal['field', 'directory', 'components', 'python'] = 'field'
    field: Literal['kind','language','codeProjectId','health.level'] = 'kind'
    depth: int = Field(default=2, ge=1, le=8, strict=True)
    worker: str | None = Field(default=None, max_length=200)
    function: str = Field(default='group', pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    model_config = {'extra':'forbid'}


class ModWorkflow(BaseModel):
    """Declarative local workflows select a host engine; they cannot import server-side mod code."""
    id: str = Field(pattern=ID)
    label: str = Field(min_length=1, max_length=100)
    engine: Literal['langgraph'] = 'langgraph'
    kind: Literal['research', 'documentation']
    model_config = {'extra': 'forbid'}


class AnalysisAutoActivation(BaseModel):
    """Только точные расширения и имена файлов выбирают host-owned анализ, без скриптов."""
    extensions: list[str] = Field(default_factory=list, max_length=16)
    manifestNames: list[str] = Field(default_factory=list, max_length=16)
    model_config = {'extra': 'forbid'}

    @field_validator('extensions')
    @classmethod
    def extensions_valid(cls, values):
        """Расширения не являются glob или регулярными выражениями."""
        from .mod_analysis_models import _suffix_names
        return _suffix_names(values)

    @field_validator('manifestNames')
    @classmethod
    def names_valid(cls, values):
        """Маркеры — только bounded basename, не пути и команды."""
        from .mod_analysis_models import _relative_names
        _relative_names(values)
        if any('/' in value for value in values):
            raise ValueError('Activation markers must be basenames')
        return values


class ModWikiPage(BaseModel):
    """Страница документации с локальным Markdown-путём, без загрузки её текста."""
    id: str = Field(pattern=ID)
    title: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=200)
    model_config = {'extra': 'forbid'}

    @field_validator('path')
    @classmethod
    def markdown_path(cls, value):
        """
        Проверяет относительный Markdown-путь внутри пакета.

        cls — модель страницы; value — заявленный путь длиной до 200 символов.
        Возвращает исходный путь. ValueError отвергает обход каталогов и не-.md.
        """
        if ('\\' in value or ':' in value or '\0' in value or value.startswith('/')
                or any(part in {'', '.', '..'} for part in value.split('/'))
                or not value.lower().endswith('.md')):
            raise ValueError('Wiki pages require a relative Markdown .md path')
        return value


class ModWiki(BaseModel):
    """Необязательные страницы подключаются к существующему разделу руководства Atlas."""
    section: Literal['Explore-Code', 'Agents-and-MCP', 'Data-and-Updates', 'Development-Guide', 'Mods-and-Wiki']
    pages: list[ModWikiPage] = Field(max_length=16)
    model_config = {'extra': 'forbid'}

    @field_validator('pages')
    @classmethod
    def unique_pages(cls, pages):
        """
        Проверяет однозначность ID в пространстве конкретного мода.

        cls — модель wiki; pages — проверенные объявления в исходном порядке.
        Возвращает список без изменений; ValueError означает повторяющийся ID.
        """
        if len({page.id for page in pages}) != len(pages):
            raise ValueError('Duplicate wiki page id')
        return pages


class ModManifest(BaseModel):
    """Only this application-owned catalog is inspected; repository manifests never activate code."""
    apiVersion: Literal[1]
    id: str = Field(pattern=ID)
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(default='', max_length=1000)
    requires: list[str] = Field(default_factory=list, max_length=8)
    theme: CardTheme | None = None
    colorSchemes: list[ColorScheme] = Field(default_factory=list, max_length=8)
    behavior: dict[Literal['doubleClick'], Literal['select','expand','enter','source','isolate']] = Field(default_factory=dict)
    frontend: str | None = Field(default=None, max_length=200)
    grouping: list[ModGrouping] = Field(default_factory=list, max_length=8)
    workflows: list[ModWorkflow] = Field(default_factory=list, max_length=8)
    analysis: ModAnalysis | None = None
    autoActivate: AnalysisAutoActivation | Literal[False] | None = None
    wiki: ModWiki | None = None
    model_config = {'extra':'forbid'}

    @model_validator(mode='after')
    def automatic_analysis_only(self):
        """Автоматика не может включить оформление, workflow или исполняемый мод."""
        if self.autoActivate is not None and self.autoActivate is not False:
            if not self.analysis or not (self.autoActivate.extensions or self.autoActivate.manifestNames):
                raise ValueError('Automatic activation requires analysis and file selectors')
            if self.frontend or self.grouping or self.workflows or self.theme or self.colorSchemes or self.behavior:
                raise ValueError('Only declarative analysis mods support automatic activation')
        return self

    @field_validator('colorSchemes')
    @classmethod
    def unique_color_schemes(cls, schemes):
        """Scheme IDs are unique within a package; other packages have separate namespaces."""
        if len({scheme.id for scheme in schemes}) != len(schemes):
            raise ValueError('Duplicate color scheme id')
        return schemes

    @field_validator('apiVersion', mode='before')
    @classmethod
    def strict_api_version(cls, value):
        """A boolean or coerced version cannot select an executable SDK contract."""
        if type(value) is not int:
            raise ValueError('apiVersion must be an integer')
        return value
