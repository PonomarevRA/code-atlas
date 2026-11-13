"""Small, snapshot-bound overview and drill-down pages for the code map."""
from collections import defaultdict
from contextlib import closing
import json
import logging
import os
from pathlib import Path
import sqlite3
from threading import Lock
from uuid import uuid4

from .graphistry_provider import GraphistryError
from .grouping import GROUPING_VERSION, enrich_graph
from .grouping_providers import DEFAULT_ALGORITHM
from .snapshot_objects import read_snapshot
from .map_storage import checked_path

_FILENAME = 'atlas-map-preview-v3.sqlite'
_MAX_EDGES = 1200
_MAX_PROJECTION_BYTES = 1024 * 1024
_NODE_FIELDS = ('label', 'kind', 'file', 'line', 'x', 'y')
_EDGE_FIELDS = ('relation', 'confidence')
_LOG = logging.getLogger(__name__)


def _encode(value):
    """Encode a compact projection without changing list order."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _bounded_json(value):
    """Reject projections above the same byte budget for every response."""
    if len(value.encode('utf-8')) > _MAX_PROJECTION_BYTES:
        raise ValueError('Progressive map projection exceeds the size limit')
    return json.loads(value)


def _select(row, fields):
    """Copy only fields consumed by the first-level map."""
    return {key: row[key] for key in fields if key in row}


def _preview_path(root, snapshot):
    """Choose the derivative beside a managed snapshot without following links."""
    relative = snapshot.relative_to(root)
    return checked_path(root, *relative.parts[:-1], _FILENAME, directory=False)


def _source_identity(snapshot):
    """Bind the derivative to the canonical snapshot document, including replacement."""
    if snapshot.is_symlink():
        raise ValueError('Snapshot must not be redirected')
    stat = snapshot.stat()
    return _encode([stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])


def _overview(graph, snapshot_id, omitted_edges):
    """Project grouping aggregates and project membership without node payloads."""
    memberships = defaultdict(lambda: defaultdict(int))
    for node in graph['nodes']:
        group_id = node.get('community')
        project_id = node.get('codeProjectId')
        if group_id is not None and project_id is not None:
            memberships[group_id][project_id] += 1
    diagnostics = graph.get('metadata', {}).get('analysisDiagnostics') or {}
    return {
        'snapshotId': snapshot_id,
        'communities': graph.get('communities', []),
        'communityEdges': graph.get('communityEdges', []),
        'codeProjects': graph.get('codeProjects', []),
        'groupProjects': memberships,
        'metadata': {
            'snapshotId': snapshot_id,
            'groupingAlgorithm': DEFAULT_ALGORITHM,
            'analysisStatus': diagnostics.get('analysisStatus', 'unknown'),
            'nodeCount': len(graph['nodes']),
            'edgeCount': len(graph['edges']),
            'sourceKind': 'snapshot-captured',
            'integrityScope': 'snapshot-document-and-preview',
            'omittedEdges': omitted_edges,
        },
    }


def _populate(db, graph, snapshot, identity):
    """Build an immutable index with stable node/edge ordering and covering indexes."""
    db.executescript("""
        CREATE TABLE overview (snapshot TEXT PRIMARY KEY, identity TEXT NOT NULL, data TEXT NOT NULL, metadata TEXT NOT NULL);
        CREATE TABLE nodes (ord INTEGER PRIMARY KEY, id TEXT NOT NULL, community TEXT, project TEXT, data TEXT);
        CREATE TABLE edges (ord INTEGER PRIMARY KEY, source INTEGER NOT NULL, target INTEGER NOT NULL, data TEXT);
    """)
    node_order = {node['id']: index for index, node in enumerate(graph['nodes'])}
    edges = [(index, edge) for index, edge in enumerate(graph['edges'])
             if edge.get('source') in node_order and edge.get('target') in node_order]
    summary = _overview(graph, snapshot.parent.name, len(graph['edges']) - len(edges))
    overview = _encode(summary)
    if len(overview.encode('utf-8')) > _MAX_PROJECTION_BYTES:
        raise ValueError('Progressive map overview exceeds the size limit')
    db.execute('INSERT INTO overview VALUES (?, ?, ?, ?)',
               (snapshot.parent.name, identity, overview, _encode(summary['metadata'])))
    db.executemany('INSERT INTO nodes VALUES (?, ?, ?, ?, ?)', (
        (index, node['id'], node.get('community'), node.get('codeProjectId'), _encode(_select(node, _NODE_FIELDS)))
        for index, node in enumerate(graph['nodes'])
    ))
    db.executemany('INSERT INTO edges VALUES (?, ?, ?, ?)', (
        (index, node_order[edge['source']], node_order[edge['target']], _encode(_select(edge, _EDGE_FIELDS)))
        for index, edge in edges
    ))
    db.executescript("""
        CREATE INDEX nodes_by_group ON nodes(community, ord);
        CREATE INDEX nodes_by_project ON nodes(project, ord);
        CREATE INDEX nodes_by_group_project ON nodes(community, project, ord);
        CREATE INDEX edges_by_endpoints ON edges(source, target, ord);
    """)
    db.commit()


def write_progressive_map(root: Path, snapshot: Path, graph: dict, *, expected_identity: str | None = None):
    """Atomically publish a regenerable overview and detail index for one grouped graph."""
    path = _preview_path(root, snapshot)
    identity = _source_identity(snapshot)
    if expected_identity is not None and identity != expected_identity:
        raise ValueError('Snapshot changed before indexing the map')
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with closing(sqlite3.connect(temporary)) as db:
            _populate(db, graph, snapshot, identity)
        if _source_identity(snapshot) != identity:
            raise ValueError('Snapshot changed while indexing the map')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_progressive_map(root: Path, snapshot: Path, graph: dict):
    """Write the fast path without making the canonical snapshot depend on it."""
    try:
        write_progressive_map(root, snapshot, graph)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        _LOG.warning('Progressive map index unavailable for %s: %s', snapshot.parent.name, error)


def _read_connection(path):
    """Open only an existing, atomically replaced index in read-only mode."""
    return closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True))


def _read_overview(db, snapshot, *, metadata_only=False):
    """Verify the captured source document identity and read the required projection."""
    column = 'metadata' if metadata_only else 'data'
    row = db.execute(f'SELECT identity, {column} FROM overview WHERE snapshot = ? AND length({column}) <= ?',
                     (snapshot.parent.name, _MAX_PROJECTION_BYTES)).fetchone()
    if row is None or row[0] != _source_identity(snapshot):
        raise ValueError('Progressive map index is stale')
    value = _bounded_json(row[1])
    if not isinstance(value, dict) or value.get('snapshotId') != snapshot.parent.name:
        raise ValueError('Invalid progressive map projection')
    return row[0], value


def _read_page(db, metadata, group_id, project_scope, offset, limit):
    """Return one node page and bounded links whose two endpoints are on the page."""
    criteria, args = [], []
    if group_id is not None:
        criteria.append('community = ?')
        args.append(group_id)
    if project_scope is not None:
        criteria.append('project = ?')
        args.append(project_scope)
    where = ' AND '.join(criteria)
    total = db.execute(f'SELECT count(*) FROM nodes WHERE {where}', args).fetchone()[0]
    page = db.execute(f'SELECT ord, id, community, project, data FROM nodes WHERE {where} ORDER BY ord LIMIT ? OFFSET ?',
                      [*args, limit, offset]).fetchall()
    nodes, edges = [], []
    keys = {}
    for ordinal, node_id, community, project, data in page:
        keys[ordinal] = node_id
        nodes.append({'id': node_id, 'community': community, 'codeProjectId': project, **json.loads(data)})
    if keys:
        marks = ','.join('?' for _ in keys)
        rows = db.execute(
            f'SELECT source, target, data FROM edges WHERE source IN ({marks}) AND target IN ({marks}) ORDER BY ord LIMIT ?',
            [*keys, *keys, _MAX_EDGES + 1],
        )
        for source, target, data in rows:
            edges.append({'source': keys[source], 'target': keys[target], **json.loads(data)})
    snapshot_id = metadata['snapshotId']
    return {
        'snapshotId': snapshot_id, 'groupId': group_id, 'projectScope': project_scope,
        'nodes': nodes, 'edges': edges[:_MAX_EDGES],
        'total': total, 'offset': offset, 'nextOffset': offset + len(page) if offset + len(page) < total else None,
        'edgesTruncated': len(edges) > _MAX_EDGES, 'edgeScope': 'page',
        'metadata': metadata,
    }


class ProgressiveMap:
    """Read lightweight projections and build the derivative once for saved legacy maps."""

    def __init__(self, store):
        """Use the application store and serialize lazy index creation per service."""
        self.store = store
        self.bootstrap_lock = Lock()

    def _snapshot(self, project_id, snapshot_id):
        """Resolve only the current canonical path and enforce the requested revision."""
        from .store import StoreError

        snapshot = self.store._snapshot_path(project_id, '_graph')
        if snapshot.parent.name != snapshot_id:
            raise StoreError('Карта обновилась. Откройте актуальный снимок.', 409)
        return snapshot

    def _read(self, path, snapshot, scope):
        """Read one immutable derivative, returning a miss for absent or obsolete data."""
        if not path.is_file():
            return None
        try:
            with _read_connection(path) as db:
                identity, value = _read_overview(db, snapshot, metadata_only=scope is not None)
                result = value if scope is None else _read_page(db, value, *scope)
                if _source_identity(snapshot) != identity:
                    return None
                return result
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            return None

    def _bootstrap(self, project_id, snapshot_id, snapshot, path, scope):
        """Regenerate a missing derivative once, keeping reads outside the registry lock."""
        with self.bootstrap_lock:
            result = self._read(path, snapshot, scope)
            if result is not None:
                return result
            self._snapshot(project_id, snapshot_id)
            identity = _source_identity(snapshot)
            graph = read_snapshot(snapshot)
            if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list) or not isinstance(graph.get('edges'), list):
                raise ValueError('Invalid canonical map')
            metadata = graph.get('metadata', {})
            if (metadata.get('groupingVersion') != GROUPING_VERSION
                    or metadata.get('groupingProvider') != DEFAULT_ALGORITHM):
                graph = enrich_graph(graph)
            self._snapshot(project_id, snapshot_id)
            write_progressive_map(self.store.data_dir, snapshot, graph, expected_identity=identity)
            return self._read(path, snapshot, scope)

    @staticmethod
    def _unavailable():
        """Return a typed failure so the interface can load the canonical map."""
        from .store import StoreError

        error = StoreError('Обзор карты недоступен. Откройте полную карту.', 503)
        error.code = 'MAP_PREVIEW_UNAVAILABLE'
        return error

    def read(self, project_id, snapshot_id, scope=None):
        """Read the current projection, lazily upgrading legacy maps without changing their graph."""

        snapshot = self._snapshot(project_id, snapshot_id)
        try:
            path = _preview_path(self.store.data_dir, snapshot)
            result = self._read(path, snapshot, scope)
            if result is None:
                result = self._bootstrap(project_id, snapshot_id, snapshot, path, scope)
            self._snapshot(project_id, snapshot_id)
            if scope is not None and result is not None and len(_encode(result).encode('utf-8')) > _MAX_PROJECTION_BYTES:
                raise ValueError('Progressive map page exceeds the size limit')
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, GraphistryError) as error:
            raise self._unavailable() from error
        if result is None:
            raise self._unavailable()
        return result
