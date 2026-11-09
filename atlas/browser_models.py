"""Strict, bounded browser observations; none of these fields grants execution authority."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CAPTURE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 1024 * 1024


class CaptureModel(BaseModel):
    """Reject unknown fields and implicit string/number conversions at ingestion."""
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class BrowserPage(CaptureModel):
    """Identify one top-frame observation; query strings and fragments are not retained."""
    url: str = Field(max_length=4096)
    title: str = Field(default='', max_length=256)
    capturedAt: str = Field(max_length=64)

    @field_validator('url')
    @classmethod
    def page_url(cls, value):
        """Accept HTTP(S) pages without credentials, keeping only origin and path."""
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Expected an HTTP(S) browser page')
        return parsed._replace(query='', fragment='').geturl()


class BrowserElement(CaptureModel):
    """Normalized DOM element with optional layout; parent IDs refer to this capture only."""
    id: str = Field(min_length=1, max_length=128)
    parentId: str | None = Field(default=None, max_length=128)
    tag: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9:_-]+$')
    text: str = Field(default='', max_length=512)
    attributes: dict[str, str] = Field(default_factory=dict, max_length=16)
    bounds: list[float] = Field(default_factory=list, max_length=4)
    styles: dict[str, str] = Field(default_factory=dict, max_length=12)

    @field_validator('attributes', 'styles')
    @classmethod
    def bounded_pairs(cls, value):
        """Bound both names and values independently of the overall request size."""
        if any(len(key) > 128 or len(item) > 1024 for key, item in value.items()):
            raise ValueError('Oversized DOM property')
        return value

    @field_validator('bounds')
    @classmethod
    def rectangle(cls, value):
        """Layout uses x/y/width/height in CSS pixels, with finite bounded coordinates."""
        if value and (len(value) != 4 or any(abs(item) > 10_000_000 for item in value)
                      or value[2] < 0 or value[3] < 0):
            raise ValueError('Invalid DOM rectangle')
        return value


class BrowserScript(CaptureModel):
    """An observed script, with absence and size rejection distinct from an empty source."""
    id: str = Field(min_length=1, max_length=128)
    url: str = Field(default='', max_length=4096)
    source: str | None = Field(default=None, max_length=MAX_SOURCE_BYTES)
    status: Literal['captured', 'unavailable', 'oversized']

    @model_validator(mode='after')
    def source_state(self):
        """Require exact captured text; unavailable entries must not smuggle retained source."""
        if (self.status == 'captured') != (self.source is not None):
            raise ValueError('Script status and source disagree')
        if self.source is not None and len(self.source.encode('utf-8')) > MAX_SOURCE_BYTES:
            raise ValueError('Script exceeds UTF-8 byte budget')
        if self.url.startswith(('http:', 'https:')):
            parsed = urlsplit(self.url)
            self.url = parsed._replace(netloc=parsed.netloc.rsplit('@', 1)[-1], query='', fragment='').geturl()
        return self


class BrowserCapture(CaptureModel):
    """Top-frame DOM and loaded scripts; no claim of an atomic or complete runtime trace."""
    schemaVersion: Literal['atlas-browser-capture-v1']
    page: BrowserPage
    nodes: list[BrowserElement] = Field(max_length=4000)
    scripts: list[BrowserScript] = Field(default_factory=list, max_length=64)
    warnings: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def inventory(self):
        """Reject duplicate/cyclic trees and cap source bytes across all scripts."""
        seen = set()
        depth = {}
        for node in self.nodes:
            if node.id in seen or node.parentId is not None and node.parentId not in seen:
                raise ValueError('DOM must have unique IDs in parent-before-child order')
            depth[node.id] = depth.get(node.parentId, 0) + 1
            if depth[node.id] > 128:
                raise ValueError('DOM exceeds depth budget')
            seen.add(node.id)
            if any(name.lower().startswith('on') or name.lower() == 'value' for name in node.attributes):
                raise ValueError('DOM event attributes and form values are not captured')
        if len({script.id for script in self.scripts}) != len(self.scripts):
            raise ValueError('Duplicate script identity')
        if sum(len((script.source or '').encode('utf-8')) for script in self.scripts) > 4 * MAX_SOURCE_BYTES:
            raise ValueError('Total script source exceeds budget')
        if any(len(item) > 512 for item in self.warnings):
            raise ValueError('Oversized capture warning')
        if len(self.model_dump_json().encode('utf-8')) > MAX_CAPTURE_BYTES:
            raise ValueError('Capture exceeds encoded byte budget')
        return self


class BrowserCreate(CaptureModel):
    """A task ends on explicit finish/cancel/error; both modes expire within one hour."""
    capture: BrowserCapture
    retention: Literal['task', 'hour'] = 'task'
    ttlSeconds: int = Field(default=3600, ge=1, le=3600)


class BrowserQuery(CaptureModel):
    """A bounded text query over already captured facts, without rebuilding the graph."""
    query: str = Field(default='', max_length=256)
    kind: Literal['dom', 'script', 'function'] | None = None
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=20, ge=1, le=50)


class BrowserNode(CaptureModel):
    """Select an exact session-owned graph node and a bounded neighbor page."""
    nodeId: str = Field(min_length=1, max_length=256)
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=20, ge=1, le=50)


class BrowserSource(CaptureModel):
    """Read script characters on demand; offsets are Unicode codepoints, not UTF-8 bytes."""
    scriptId: str = Field(min_length=1, max_length=128)
    offset: int = Field(default=0, ge=0, le=MAX_SOURCE_BYTES)
    limit: int = Field(default=4000, ge=1, le=8000)


class BrowserFinish(CaptureModel):
    """An explicit task outcome releases captured data in either retention mode."""
    reason: Literal['completed', 'cancelled', 'error'] = 'completed'


class BrowserNodeSource(CaptureModel):
    """Read codepoints relative to a lexical function or script, without byte conversion by clients."""
    nodeId: str = Field(min_length=1, max_length=256)
    offset: int = Field(default=0, ge=0, le=MAX_SOURCE_BYTES)
    limit: int = Field(default=4000, ge=1, le=8000)


class BrowserProperty(CaptureModel):
    """Select one DOM property by stable insertion index, with codepoint value pagination."""
    nodeId: str = Field(min_length=1, max_length=256)
    field: Literal['attributes', 'styles', 'text']
    propertyIndex: int = Field(default=0, ge=0, le=15)
    offset: int = Field(default=0, ge=0, le=1024)
    limit: int = Field(default=512, ge=1, le=1024)
