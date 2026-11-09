"""Portable static-analysis contracts shared by optional engines and graph projection.

These records describe declarations and static bindings. They never establish a
runtime invocation or turn unavailable analysis into a successful metric.
"""
from dataclasses import asdict, dataclass, field
import math
import re
from typing import Literal

SEMANTIC_CONTRACT_VERSION = 'atlas-semantic-v1'
SEMANTIC_CAPABILITIES = ('definition', 'references', 'types', 'calls')
AVAILABILITY_STATUSES = ('unchecked', 'checking', 'available', 'missing', 'incompatible', 'failed', 'disabled')
RESULT_STATUSES = ('complete', 'partial', 'failed', 'cancelled', 'unsupported')


def _text(value, name, *, empty=False, maximum=2048):
    """Reject coerced identifiers and oversized evidence at the serialization boundary."""
    if not isinstance(value, str):
        raise ValueError(f'Invalid semantic {name}')
    if len(value) > maximum or (not empty and not value):
        raise ValueError(f'Invalid semantic {name}')


def _integer(value, name):
    """Counters and source coordinates are nonnegative integers; booleans are not counts."""
    if type(value) is not int or value < 0:
        raise ValueError(f'Invalid semantic {name}')


def _relative_path(path):
    """Use normalized repository-relative names; filesystem containment is checked by the reader."""
    _text(path, 'source path')
    if path.startswith('/') or '\\' in path or ':' in path:
        raise ValueError('Expected a repository-relative semantic source path')
    if any(part in {'', '.', '..'} for part in path.split('/')):
        raise ValueError('Invalid semantic source path segment')


def _fingerprint(value, name, *, empty=False):
    """Hashes have one lowercase SHA-256 spelling to prevent ambiguous cache identities."""
    if empty and value == '':
        return
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError(f'Invalid semantic {name}')


@dataclass(frozen=True)
class SourceSpan:
    """Zero-based half-open source coordinates; utf-8 columns count bytes, utf-16 code units."""
    path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    coordinate_kind: Literal['utf-16', 'utf-8', 'codepoints'] = 'utf-16'

    def __post_init__(self):
        """Reject traversal and reversed/coerced spans before matching graph identities."""
        _relative_path(self.path)
        for name in ('start_line', 'start_column', 'end_line', 'end_column'):
            _integer(getattr(self, name), name)
        if (self.end_line, self.end_column) < (self.start_line, self.start_column):
            raise ValueError('Semantic span ends before its start')
        if self.coordinate_kind not in {'utf-16', 'utf-8', 'codepoints'}:
            raise ValueError('Unsupported semantic coordinate kind')


@dataclass(frozen=True)
class SemanticContext:
    """Pin one snapshot, workspace and build target without publishing local engine paths."""
    snapshot_id: str
    workspace_id: str
    workspace_root: str
    target_id: str
    config_fingerprint: str
    source_hashes: dict[str, str]
    provider_fingerprint: str = ''

    def __post_init__(self):
        """Require explicit target/config identities and exact hashes for analyzed source files."""
        for name in ('snapshot_id', 'workspace_id', 'workspace_root', 'target_id'):
            _text(getattr(self, name), name)
        _fingerprint(self.config_fingerprint, 'configuration fingerprint')
        _fingerprint(self.provider_fingerprint, 'provider fingerprint', empty=True)
        if not isinstance(self.source_hashes, dict) or len(self.source_hashes) > 20000:
            raise ValueError('Invalid semantic source inventory')
        for path, digest in self.source_hashes.items():
            _relative_path(path)
            _fingerprint(digest, 'source hash')
        # A caller changing its input dictionary must not change this pinned context.
        object.__setattr__(self, 'source_hashes', dict(self.source_hashes))


@dataclass(frozen=True)
class SemanticAvailability:
    """Environment discovery state, independent of whether enrichment has been applied."""
    status: str
    reason: str = ''
    engine_version: str | None = None
    engine_fingerprint: str | None = None
    capabilities: tuple[str, ...] = ()
    checked_at: float | None = None
    next_check_at: float | None = None

    def __post_init__(self):
        """Validate persisted status and UTC epoch timestamps without discovering an engine."""
        if self.status not in AVAILABILITY_STATUSES:
            raise ValueError('Unknown semantic availability status')
        _text(self.reason, 'availability reason', empty=True, maximum=4096)
        if self.engine_version is not None:
            _text(self.engine_version, 'engine version')
        if self.engine_fingerprint is not None:
            _fingerprint(self.engine_fingerprint, 'engine fingerprint')
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError('Duplicate semantic capability')
        if any(value not in SEMANTIC_CAPABILITIES for value in self.capabilities):
            raise ValueError('Unknown semantic capability')
        object.__setattr__(self, 'capabilities', tuple(self.capabilities))
        for value in (self.checked_at, self.next_check_at):
            if value is None:
                continue
            if type(value) not in {int, float}:
                raise ValueError('Invalid semantic discovery timestamp')
            if not math.isfinite(value) or value < 0:
                raise ValueError('Invalid semantic discovery timestamp')
        if self.checked_at is not None and self.next_check_at is not None:
            if self.next_check_at < self.checked_at:
                raise ValueError('Semantic discovery expiration precedes its check')


@dataclass(frozen=True)
class SemanticSymbol:
    """A bounded symbol reference; graph_node_id is assigned only after a unique source match."""
    symbol_id: str
    kind: str
    label: str
    source_span: SourceSpan | None = None
    uri: str | None = None
    graph_node_id: str | None = None

    def __post_init__(self):
        """Keep external references separate from graph nodes and reject malformed spans."""
        for name in ('symbol_id', 'kind', 'label'):
            _text(getattr(self, name), name)
        if self.source_span is not None and not isinstance(self.source_span, SourceSpan):
            raise ValueError('Expected a semantic source span')
        for name in ('uri', 'graph_node_id'):
            value = getattr(self, name)
            if value is not None:
                _text(value, name, maximum=4096)


@dataclass(frozen=True)
class SemanticFact:
    """Static call/reference evidence, including unresolved targets and ambiguity candidates."""
    kind: Literal['call', 'reference', 'type', 'definition']
    source: SemanticSymbol
    target: SemanticSymbol | None
    confidence: Literal['unknown', 'candidate', 'confirmed-static']
    callsite: SourceSpan | None = None
    evidence: str = ''

    def __post_init__(self):
        """A confirmed static binding requires a target; no record claims runtime selection."""
        if self.kind not in {'call', 'reference', 'type', 'definition'}:
            raise ValueError('Unknown semantic fact kind')
        if self.confidence not in {'unknown', 'candidate', 'confirmed-static'}:
            raise ValueError('Unknown semantic confidence')
        if not isinstance(self.source, SemanticSymbol):
            raise ValueError('Expected a semantic source symbol')
        if self.target is not None and not isinstance(self.target, SemanticSymbol):
            raise ValueError('Expected a semantic target symbol')
        if self.confidence == 'confirmed-static' and self.target is None:
            raise ValueError('Confirmed static evidence requires a target')
        if self.callsite is not None and not isinstance(self.callsite, SourceSpan):
            raise ValueError('Expected a semantic callsite span')
        _text(self.evidence, 'evidence', empty=True, maximum=4096)


@dataclass(frozen=True)
class SemanticCoverage:
    """Completeness of one capability and scope, independent of other available metrics."""
    status: Literal['complete', 'partial', 'unknown', 'unsupported']
    analyzed: int = 0
    total: int | None = None
    reasons: tuple[str, ...] = ()

    def __post_init__(self):
        """Unknown totals remain null; missing scopes cannot be relabeled complete."""
        if self.status not in {'complete', 'partial', 'unknown', 'unsupported'}:
            raise ValueError('Unknown semantic coverage status')
        _integer(self.analyzed, 'analyzed count')
        if self.total is not None:
            _integer(self.total, 'total count')
            if self.analyzed > self.total:
                raise ValueError('Semantic analyzed count exceeds total')
        if self.status == 'complete' and self.total != self.analyzed:
            raise ValueError('Complete semantic coverage requires an exact total')
        if len(self.reasons) > 64:
            raise ValueError('Too many semantic coverage reasons')
        for reason in self.reasons:
            _text(reason, 'coverage reason', maximum=1024)
        object.__setattr__(self, 'reasons', tuple(self.reasons))


@dataclass(frozen=True)
class SemanticResult:
    """One bounded provider result anchored to captured files, configuration and engine identity."""
    context: SemanticContext
    provider_id: str
    provider_fingerprint: str
    engine_fingerprint: str
    status: str = 'partial'
    facts: tuple[SemanticFact, ...] = ()
    coverage: dict[str, SemanticCoverage] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self):
        """Reject unanchored facts or inflated completeness before optional overlay publication."""
        if not isinstance(self.context, SemanticContext):
            raise ValueError('Expected a semantic context')
        _text(self.provider_id, 'provider id')
        _fingerprint(self.provider_fingerprint, 'provider fingerprint')
        _fingerprint(self.engine_fingerprint, 'engine fingerprint')
        if self.context.provider_fingerprint and self.context.provider_fingerprint != self.provider_fingerprint:
            raise ValueError('Semantic provider fingerprint does not match context')
        if self.status not in RESULT_STATUSES:
            raise ValueError('Unknown semantic enrichment status')
        if len(self.facts) > 50000 or any(not isinstance(fact, SemanticFact) for fact in self.facts):
            raise ValueError('Invalid semantic facts')
        for fact in self.facts:
            # Targets may refer to external source; only local source/callsite is mandatory.
            for span in (fact.source.source_span, fact.callsite):
                if span is not None and span.path not in self.context.source_hashes:
                    raise ValueError('Semantic fact source is not pinned by context')
        if not isinstance(self.coverage, dict) or len(self.coverage) > 4:
            raise ValueError('Invalid semantic coverage')
        for capability, coverage in self.coverage.items():
            if capability not in SEMANTIC_CAPABILITIES or not isinstance(coverage, SemanticCoverage):
                raise ValueError('Invalid semantic capability coverage')
        if self.status == 'complete':
            if not self.coverage or any(item.status != 'complete' for item in self.coverage.values()):
                raise ValueError('Complete semantic result requires complete capability coverage')
        if len(self.diagnostics) > 64:
            raise ValueError('Too many semantic diagnostics')
        for diagnostic in self.diagnostics:
            _text(diagnostic, 'diagnostic', maximum=4096)
        object.__setattr__(self, 'facts', tuple(self.facts))
        object.__setattr__(self, 'coverage', dict(self.coverage))
        object.__setattr__(self, 'diagnostics', tuple(self.diagnostics))

    def to_dict(self) -> dict:
        """Return a detached portable payload; callers must omit workspace_root in public summaries."""
        return asdict(self)


def semantic_availability_from_dict(payload: dict) -> SemanticAvailability:
    """Decode persisted discovery data; unknown fields and invalid values fail closed."""
    return SemanticAvailability(**payload)


def _symbol_from_dict(payload: dict) -> SemanticSymbol:
    """Restore one symbol and its optional coordinates without accepting arbitrary extra fields."""
    values = dict(payload)
    if values.get('source_span') is not None:
        values['source_span'] = SourceSpan(**values['source_span'])
    return SemanticSymbol(**values)


def semantic_result_from_dict(payload: dict) -> SemanticResult:
    """Validate a persisted result before graph projection, including every nested source anchor."""
    values = dict(payload)
    values['context'] = SemanticContext(**values['context'])
    facts = []
    for fact in values.get('facts', []):
        item = dict(fact)
        item['source'] = _symbol_from_dict(item['source'])
        if item.get('target') is not None:
            item['target'] = _symbol_from_dict(item['target'])
        if item.get('callsite') is not None:
            item['callsite'] = SourceSpan(**item['callsite'])
        facts.append(SemanticFact(**item))
    values['facts'] = tuple(facts)
    values['coverage'] = {key: SemanticCoverage(**item) for key, item in values.get('coverage', {}).items()}
    return SemanticResult(**values)
