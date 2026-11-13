"""Share a pinned read-only snapshot and search a bounded worktree declaration layer."""

from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import uuid4

from .checkout_file_search import lexical_declarations
from .code_health_source import language_for
from .mcp_response_budget import json_bytes
from .store import StoreError
from .worktree_overlay_git import capture_delta, configuration_path, conflict, identity


_MAX_OVERLAYS = 16
_TTL = 3600
_DECLARATIONS_PER_FILE = 500
_CACHE_FILES = 24


@dataclass
class Overlay:
    """Process-local overlay identity, activity deadline, and bounded lexical cache."""

    root: Path
    base_session: str
    snapshot: str
    base_project: str
    git_identity: dict
    base_hashes: dict
    deadline: float
    revision: str | None = None
    sources: dict = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)
    cache: OrderedDict = field(default_factory=OrderedDict)


def _captured_identity(root, graph):
    """Expose only build-time provenance attached to the chosen snapshot."""
    stamp = graph.get('metadata', {}).get('worktreeBase')
    if not stamp:
        raise conflict('Use overlayBase=true when building the clean base session.')
    return stamp


def _identities(sources):
    """Derive stable public change identities without including source text."""
    return [dict(sources[name][0]) for name in sorted(sources)]


class WorktreeOverlays:
    """Coordinate short per-overlay refreshes and reuse the base store's snapshot lease."""

    def __init__(self, sessions):
        """Own ephemeral handles separately from persistent temporary checkout maps."""
        self.sessions = sessions
        self.lock = Lock()
        self.overlays = {}

    def _prune(self, now):
        """Expire idle handles under the registry lock without touching Git or the base store."""
        self.overlays = {key: entry for key, entry in self.overlays.items() if entry.deadline > now}

    def open(self, checkout, base_session, snapshot):
        """Bind a sibling Git worktree to one previously certified clean snapshot."""
        try:
            root = Path(checkout).expanduser()
            if not root.is_absolute() or not root.is_dir():
                raise conflict('Select an existing absolute Git worktree path.')
            root = root.resolve(strict=True)
        except (OSError, RuntimeError, UnicodeError) as error:
            raise conflict('The selected Git worktree is unavailable.') from error
        git_identity = identity(root)
        with self.sessions.access(base_session) as entry:
            if root == Path(entry.record['checkout']):
                raise conflict('Select a sibling Git worktree for the overlay.')
            base = entry.store._read_snapshot_analysis(entry.project_id, snapshot, _captured_identity)
            if any(base.get(key) != value for key, value in git_identity.items()):
                raise conflict('The overlay requires the same Git repository and commit as the base.')
            project = entry.project_id
        now = monotonic()
        with self.lock:
            self._prune(now)
            for key, existing in self.overlays.items():
                if (existing.root, existing.base_session, existing.snapshot) == (root, base_session, snapshot):
                    if existing.revision is None:
                        raise conflict('The worktree overlay is opening. Retry.')
                    return {**self._handle(key, existing), 'overlayRevision': existing.revision}
            if len(self.overlays) >= _MAX_OVERLAYS:
                raise StoreError('Too many worktree overlay handles. Retry after an idle handle expires.', 429)
            key = uuid4().hex
            entry = Overlay(root, base_session, snapshot, project, git_identity, base['fileHashes'], now + _TTL)
            self.overlays[key] = entry
        try:
            return self.refresh(key)
        except BaseException:
            with self.lock:
                self.overlays.pop(key, None)
            raise

    @staticmethod
    def _handle(key, entry):
        """Return only scoped identities, never the internal Git directory."""
        return {'overlayId': key, 'baseSessionId': entry.base_session, 'baseSnapshotId': entry.snapshot,
                'baseProjectId': entry.base_project, 'commit': entry.git_identity['commit'], 'experimental': True}

    def _get(self, key):
        """Fetch one active handle and allow unrelated worktrees to run concurrently."""
        with self.lock:
            self._prune(monotonic())
            entry = self.overlays.get(key)
            if entry is None:
                raise StoreError('Worktree overlay expired; open it again.', 410)
            return entry

    @staticmethod
    def _declarations(entry, sources, query, limit):
        """Reuse lexical facts by grammar and content hash within this worktree."""
        hits, total, partial = [], 0, False
        term = query.strip().casefold()
        for name, (item, content) in sorted(sources.items()):
            if item['status'] == 'deleted':
                continue
            language = language_for(name)
            if content is None or language is None:
                item['declarationStatus'] = 'unavailable' if item['issue'] else 'unsupported'
                partial = True
                continue
            key = (language.name, language.module, language.language_function, item['sourceHash'])
            result = entry.cache.get(key)
            if result is None:
                try:
                    result = lexical_declarations(content, language, '', _DECLARATIONS_PER_FILE)
                except StoreError as error:
                    item['declarationStatus'] = 'unavailable'
                    item['issue'] = str(error)
                    partial = True
                    continue
                entry.cache[key] = result
                if len(entry.cache) > _CACHE_FILES:
                    entry.cache.popitem(last=False)
            entry.cache.move_to_end(key)
            incomplete = result['coverage']['status'] != 'complete' or result['truncated']
            item['declarationStatus'] = 'partial' if incomplete else 'complete'
            partial |= incomplete
            for declaration in result['declarations']:
                qualified = f"{declaration['owner']}.{declaration['name']}"
                if term in qualified.casefold() or term in name.casefold():
                    total += 1
                    if len(hits) < limit:
                        hits.append({**declaration, 'file': name, 'sourceHash': item['sourceHash'],
                                     'sourceKind': 'working-tree'})
        return hits, total, partial

    def refresh(self, key):
        """Capture and verify one worktree epoch before exposing it to parallel searches."""
        entry = self._get(key)
        with entry.lock:
            sources = capture_delta(entry.root, entry.git_identity, entry.base_hashes)
            expected = _identities(sources)
            verified = capture_delta(entry.root, entry.git_identity, entry.base_hashes)
            if _identities(verified) != expected:
                raise conflict('The worktree changed during the overlay refresh. Retry.')
            revision = sha256(json.dumps([entry.snapshot, key, expected], sort_keys=True).encode()).hexdigest()
            with self.sessions.access(entry.base_session) as base:
                base.store.assert_snapshot(base.project_id, entry.snapshot)
                entry.sources, entry.revision = sources, revision
                with self.lock:
                    if self.overlays.get(key) is not entry:
                        raise StoreError('Worktree overlay expired; open it again.', 410)
                    entry.deadline = monotonic() + _TTL
            return {**self._handle(key, entry), 'overlayRevision': revision, 'changedFiles': expected}

    def search(self, key, query, limit, revision=None):
        """Search a captured epoch; unpinned reads first refresh from current raw bytes."""
        if revision is None:
            revision = self.refresh(key)['overlayRevision']
        entry = self._get(key)
        with entry.lock:
            if entry.revision != revision:
                raise conflict('The overlay revision changed; refresh or use the latest revision.')
            sources = {name: (dict(item), content) for name, (item, content) in entry.sources.items()}
            declarations, total, partial = self._declarations(entry, sources, query, limit)
        config = any(configuration_path(name) or language_for(name) is None for name in sources)
        with self.sessions.access(entry.base_session) as base:
            result = base.store.search_project_nodes(base.project_id, entry.snapshot, query, limit,
                                                     exclude_files=set(sources), cache_query=True)
            base.store.assert_snapshot(base.project_id, entry.snapshot)
            response = {**result, **self._handle(key, entry), 'declarations': declarations,
                        'declarationTotal': total, 'declarationTruncated': partial or total > limit,
                        'changedFiles': _identities(sources), 'overlayRevision': revision,
                        'sourceKind': 'captured-worktree', 'linkStatus': 'needs-rebind' if sources else 'baseline',
                        'rebindScope': 'repository' if config else ('affected-files' if sources else 'none'),
                        'baselineNodeSource': 'snapshot', 'currentLinksAvailable': False,
                        'rebindMethod': 'full-checkout-build' if sources else None}
            if json_bytes(response) > 64_000:
                raise StoreError('Overlay response is too large; narrow the query or reduce the limit.', 413)
            with self.lock:
                if self.overlays.get(key) is not entry:
                    raise StoreError('Worktree overlay expired; open it again.', 410)
                entry.deadline = monotonic() + _TTL
        return response
