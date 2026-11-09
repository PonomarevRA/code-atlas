"""Bounded source-analysis declarations; packages select host capabilities, never project code."""
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

LANGUAGES = Literal['csharp', 'python', 'java', 'javascript', 'typescript', 'go', 'cpp', 'kotlin', 'php', 'rust', 'swift', 'html']
RULE_ID = r'^[a-z][a-z0-9-]{0,47}$'


def _relative_names(values: list[str]) -> list[str]:
    """Reject traversal, executable patterns and duplicate relative path declarations."""
    for value in values:
        if not value or len(value) > 200 or value.startswith('/') or '\\' in value or ':' in value:
            raise ValueError('Expected a bounded relative path')
        if any(part in {'', '.', '..'} for part in value.split('/')):
            raise ValueError('Invalid relative path segment')
        if any(char in value for char in '*?[]{}'):
            raise ValueError('Analysis rules use exact names, not glob expressions')
    if len(set(values)) != len(values):
        raise ValueError('Duplicate analysis declaration')
    return values


def _suffix_names(values: list[str]) -> list[str]:
    """Validate exact short file suffixes shared by language and build providers."""
    for value in values:
        if not value.startswith('.') or len(value) > 24 or not value[1:].isalnum():
            raise ValueError('Invalid file suffix')
    if len(set(values)) != len(values):
        raise ValueError('Duplicate file suffix')
    return values


class AnalysisProjectRule(BaseModel):
    """Language-owned build markers and explicitly heuristic folder decomposition."""
    id: str = Field(pattern=RULE_ID)
    language: LANGUAGES
    extensions: list[str] = Field(min_length=1, max_length=12)
    manifestNames: list[str] = Field(default_factory=list, max_length=12)
    manifestSuffixes: list[str] = Field(default_factory=list, max_length=8)
    sourceRootMarkers: list[str] = Field(default_factory=list, max_length=12)
    logicalFolders: bool = Field(default=False, strict=True)
    model_config = {'extra': 'forbid'}

    @field_validator('manifestNames', 'sourceRootMarkers')
    @classmethod
    def relative_names(cls, values):
        """Manifest basenames and source-root segments are data, not scan commands."""
        return _relative_names(values)

    @field_validator('manifestNames')
    @classmethod
    def manifest_basenames(cls, values):
        """Markers are exact basenames probed in each source ancestor, not arbitrary paths."""
        if any('/' in value for value in values):
            raise ValueError('Manifest names must be basenames')
        return values

    @field_validator('extensions', 'manifestSuffixes')
    @classmethod
    def suffixes(cls, values):
        """Only short dot-prefixed suffixes participate in host path matching."""
        return _suffix_names(values)


class AnalysisBuildRule(BaseModel):
    """A separately activated build-system provider serves selected language adapters."""
    id: str = Field(pattern=RULE_ID)
    languages: list[LANGUAGES] = Field(min_length=1, max_length=11)
    manifestNames: list[str] = Field(default_factory=list, max_length=12)
    manifestSuffixes: list[str] = Field(default_factory=list, max_length=8)
    model_config = {'extra': 'forbid'}

    @field_validator('manifestNames')
    @classmethod
    def marker_names(cls, values):
        """Build markers are exact relative basenames, never commands or patterns."""
        _relative_names(values)
        if any('/' in value for value in values):
            raise ValueError('Manifest names must be basenames')
        return values

    @field_validator('manifestSuffixes')
    @classmethod
    def suffixes(cls, values):
        """Reuse the language-provider suffix contract without broadening its grammar."""
        return _suffix_names(values)

    @model_validator(mode='after')
    def bounded_markers(self):
        """Reject duplicate languages and empty build-provider declarations."""
        if len(set(self.languages)) != len(self.languages):
            raise ValueError('Duplicate build-provider language')
        if not self.manifestNames and not self.manifestSuffixes:
            raise ValueError('Build provider requires a manifest name or suffix')
        return self


class AnalysisAnnotationRule(BaseModel):
    """An exact resolved annotation name supplies one lexical framework role."""
    name: str = Field(min_length=1, max_length=200, pattern=r'^[A-Za-z_][\w.$]*(?:::[A-Za-z_][\w.$]*)?$')
    role: Literal['test', 'parameter-source', 'lifecycle', 'metadata', 'step', 'disabled']
    argument: str | None = Field(default=None, max_length=80, pattern=r'^[A-Za-z_][\w]*$')
    sourceArgument: str | None = Field(default=None, max_length=80, pattern=r'^[A-Za-z_][\w]*$')
    displayArgument: str | None = Field(default=None, max_length=80, pattern=r'^[A-Za-z_][\w]*$')
    disabledArgument: str | None = Field(default=None, max_length=80, pattern=r'^[A-Za-z_][\w]*$')
    datasetStrategy: Literal['junit-single-values-v1', 'junit-positional-csv-v1',
                             'junit-literal-provider-v1', 'junit-enum-constants-v1',
                             'resource-reference-v1', 'unresolved-v1'] | None = None
    model_config = {'extra': 'forbid'}

    @model_validator(mode='after')
    def source_strategy_role(self):
        """Only a parameter-source declaration may select a host dataset parser."""
        if self.datasetStrategy is not None and self.role != 'parameter-source':
            raise ValueError('Dataset strategies require the parameter-source role')
        return self


class AnalysisTestFramework(BaseModel):
    """A library interprets normalized lexical facts without replacing language parsers."""
    id: str = Field(pattern=RULE_ID)
    language: LANGUAGES
    annotationRules: list[AnalysisAnnotationRule] = Field(min_length=1, max_length=64)
    supportsComposed: bool = Field(default=False, strict=True)
    model_config = {'extra': 'forbid'}

    @model_validator(mode='after')
    def unique_annotations(self):
        """Reject conflicting roles for the same annotation inside one library package."""
        names = [rule.name for rule in self.annotationRules]
        if len(set(names)) != len(names):
            raise ValueError('Duplicate framework annotation name')
        return self


class AnalysisEntrypointProvider(BaseModel):
    """A library selects a bounded host parser, never executable repository code."""
    id: str = Field(pattern=RULE_ID)
    language: Literal['python']
    strategy: Literal['python-fastapi-v1', 'python-fastmcp-v1']
    model_config = {'extra': 'forbid'}


class AnalysisMarkupProvider(BaseModel):
    """Декларация выбирает поддержанный HTML parser хоста, а не код пакета."""
    id: str = Field(pattern=RULE_ID)
    language: Literal['html'] = 'html'
    strategy: Literal['html-structure-v1'] = 'html-structure-v1'
    model_config = {'extra': 'forbid'}


class AnalysisDocumentOptions(BaseModel):
    """Только ограниченные host options; содержимое mod не задаёт код или пути."""
    maxInputBytes: int = Field(default=5 * 1024 * 1024, ge=1, le=50 * 1024 * 1024, strict=True)
    maxNodes: int = Field(default=100000, ge=1, le=100000, strict=True)
    maxDepth: int = Field(default=128, ge=1, le=128, strict=True)
    maxTokens: int = Field(default=300000, ge=1, le=300000, strict=True)
    maxAliases: int = Field(default=10000, ge=0, le=10000, strict=True)
    dialect: Literal['YAML-1.1'] = 'YAML-1.1'
    max_source_bytes: int | None = Field(default=None, ge=1, le=50 * 1024 * 1024, strict=True)
    max_nodes: int | None = Field(default=None, ge=1, le=100000, strict=True)
    max_text_bytes: int | None = Field(default=None, ge=1, le=16 * 1024 * 1024, strict=True)
    max_zip_entries: int | None = Field(default=None, ge=1, le=10000, strict=True)
    max_expanded_bytes: int | None = Field(default=None, ge=1, le=250 * 1024 * 1024, strict=True)
    max_compression_ratio: float | None = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
    max_xml_elements: int | None = Field(default=None, ge=1, le=1000000, strict=True)
    max_depth: int | None = Field(default=None, ge=1, le=128, strict=True)
    max_cells: int | None = Field(default=None, ge=1, le=100000, strict=True)
    max_pdf_objects: int | None = Field(default=None, ge=1, le=100000, strict=True)
    max_pdf_pages: int | None = Field(default=None, ge=1, le=300, strict=True)
    max_pdf_content_bytes: int | None = Field(default=None, ge=1, le=16 * 1024 * 1024, strict=True)
    extract_tables: bool | None = Field(default=None, strict=True)
    framing: Literal['jsonl', 'lines', 'envelope'] | None = None
    start_pattern: str | None = Field(default=None, min_length=1, max_length=1024)
    eof_policy: Literal['complete', 'incomplete'] | None = None
    encoding: Literal['utf-8'] | None = None
    max_clusters: int | None = Field(default=None, ge=1, le=10000, strict=True)
    drain_depth: int | None = Field(default=None, ge=3, le=128, strict=True)
    max_children: int | None = Field(default=None, ge=1, le=1000, strict=True)
    similarity_threshold: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    model_config = {'extra': 'forbid'}


class AnalysisDocumentProvider(BaseModel):
    """Мод выбирает allowlisted host adapter, не импортирует repository Python."""
    id: str = Field(pattern=RULE_ID)
    strategy: Literal['document-markdown-v1', 'document-yaml-v1', 'document-helm-v1',
                      'document-docx-v1', 'document-xlsx-v1', 'document-pptx-v1',
                      'document-pdf-basic-v1', 'logs-structured-v1', 'logs-drain3-v1', 'logs-lnav-v1']
    extensions: list[str] = Field(min_length=1, max_length=8)
    options: AnalysisDocumentOptions = Field(default_factory=AnalysisDocumentOptions)
    model_config = {'extra': 'forbid'}

    @field_validator('extensions')
    @classmethod
    def suffixes(cls, values):
        """Provider claims используют те же точные расширения, что и inventory."""
        return _suffix_names(values)


class AnalysisSemanticProvider(BaseModel):
    """Select an optional host-owned engine adapter without executable package settings."""
    id: str = Field(pattern=RULE_ID)
    language: Literal['csharp', 'python', 'java', 'javascript', 'typescript', 'go',
                      'cpp', 'kotlin', 'php', 'rust', 'swift']
    strategy: Literal['roslyn-local-v1', 'typescript-local-v1', 'jedi-local-v1',
                      'jdt-local-v1', 'gopls-local-v1', 'clangd-local-v1',
                      'kotlin-local-v1', 'phpactor-local-v1',
                      'rust-analyzer-local-v1', 'sourcekit-local-v1']
    capabilities: list[Literal['definition', 'references', 'types', 'calls']] = Field(
        default_factory=lambda: ['definition'], min_length=1, max_length=4)
    model_config = {'extra': 'forbid'}

    @model_validator(mode='after')
    def supported_profile(self):
        """Reject mismatched language adapters and duplicate requested capabilities."""
        languages = {
            'roslyn-local-v1': {'csharp'}, 'typescript-local-v1': {'javascript', 'typescript'},
            'jedi-local-v1': {'python'}, 'jdt-local-v1': {'java'}, 'gopls-local-v1': {'go'},
            'clangd-local-v1': {'cpp'}, 'kotlin-local-v1': {'kotlin'},
            'phpactor-local-v1': {'php'}, 'rust-analyzer-local-v1': {'rust'},
            'sourcekit-local-v1': {'swift'},
        }
        if self.language not in languages[self.strategy]:
            raise ValueError('Semantic strategy does not support this language')
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError('Duplicate semantic capability')
        return self


class ModAnalysis(BaseModel):
    """Optional v1 analysis capability; discovery and activation retain existing mod rules."""
    apiVersion: Literal[1]
    projectRules: list[AnalysisProjectRule] = Field(default_factory=list, max_length=8)
    buildRules: list[AnalysisBuildRule] = Field(default_factory=list, max_length=8)
    testFrameworks: list[AnalysisTestFramework] = Field(default_factory=list, max_length=8)
    entrypointProviders: list[AnalysisEntrypointProvider] = Field(default_factory=list, max_length=8)
    markupProviders: list[AnalysisMarkupProvider] = Field(default_factory=list, max_length=1)
    documentProviders: list[AnalysisDocumentProvider] = Field(default_factory=list, max_length=8)
    semanticProviders: list[AnalysisSemanticProvider] = Field(default_factory=list, max_length=8)
    model_config = {'extra': 'forbid'}

    @field_validator('apiVersion', mode='before')
    @classmethod
    def strict_version(cls, value):
        """Reject coerced or boolean versions instead of silently selecting a host contract."""
        if type(value) is not int:
            raise ValueError('Analysis apiVersion must be an integer')
        return value

    @model_validator(mode='after')
    def unique_rules(self):
        """Rule IDs are unique within each analysis capability list."""
        for rules in (self.projectRules, self.buildRules, self.testFrameworks, self.entrypointProviders,
                      self.markupProviders, self.documentProviders, self.semanticProviders):
            identifiers = [rule.id for rule in rules]
            if len(set(identifiers)) != len(identifiers):
                raise ValueError('Duplicate analysis rule id')
        return self
