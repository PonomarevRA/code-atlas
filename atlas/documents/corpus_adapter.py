"""Schema-2 search corpus из captured Markdown blobs и уже сохранённых link nodes."""
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
from urllib.parse import unquote, urlsplit

from ..document_corpus import (SCHEMA_VERSION, MAX_CHUNKS, MAX_DOCUMENTS, MAX_DOCUMENT_CHARS,
    MAX_DOCUMENT_BYTES, MAX_DOCUMENT_TOTAL_BYTES, MAX_RECORD_CHARS, MAX_CHUNK_CHARS,
    CHUNK_OVERLAP_CHARS, MAX_NODE_IDS, MAX_SOURCE_LINKS, MAX_TOTAL_BYTES)
from .models import require, identity
from .queries import database

LIMITS = {'maxChunks': MAX_CHUNKS, 'maxDocuments': MAX_DOCUMENTS,
          'maxDocumentChars': MAX_DOCUMENT_CHARS, 'maxDocumentBytes': MAX_DOCUMENT_BYTES,
          'maxDocumentTotalBytes': MAX_DOCUMENT_TOTAL_BYTES, 'maxReadBytes': MAX_TOTAL_BYTES}


def _artifact_name(value):
    """Manifest не может вывести чтение за пределы hash-addressed captured storage."""
    require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value),
            'INVALID_ARTIFACT', 'Некорректный captured artifact hash.')
    return value


class _Corpus:
    """Владеет детерминированными budgets одного экспорта; исходники не открывает."""

    def __init__(self, root, manifest, graph, code_snapshot_id, limits):
        """Фиксирует обе ревизии и проверяет, что override только уменьшает budgets."""
        self.root, self.manifest, self.snapshot_id = Path(root), manifest, code_snapshot_id
        self.limits = dict(LIMITS)
        for name, value in (limits or {}).items():
            require(name in LIMITS and type(value) is int and 1 <= value <= LIMITS[name],
                    'INVALID_LIMIT', 'Экспорт допускает только уменьшение известных budgets.')
            self.limits[name] = value
        self.node_paths = {}
        for node in graph.get('nodes', []):
            filename = node.get('file') or node.get('source_file')
            if isinstance(filename, str) and isinstance(node.get('id'), str):
                self.node_paths.setdefault(filename, set()).add(node['id'])
        self.documents, self.chunks, self.warnings = [], [], []
        self.read_bytes, self.document_bytes, self.truncated = 0, 0, False

    def warn(self, message):
        """Обозначает partial coverage без неограниченного списка diagnostics."""
        self.truncated = True
        if len(self.warnings) < 50 and message not in self.warnings:
            self.warnings.append(message[:400])

    def document(self, binding):
        """Читает immutable captured blob; ссылка на рабочую копию не используется."""
        if len(self.documents) >= self.limits['maxDocuments']:
            self.warn('Достигнут предел captured Markdown документов.')
            return
        blob_hash = _artifact_name(binding.get('blobHash'))
        path = self.root / 'blobs' / blob_hash
        remaining = self.limits['maxReadBytes'] - self.read_bytes
        require(not path.is_symlink(), 'INVALID_ARTIFACT', 'Captured blob не должен быть symlink.')
        require(path.is_file(), 'ARTIFACT_MISSING', 'Captured blob недоступен.', 404)
        if path.stat().st_size > remaining:
            self.warn('Достигнут предел чтения captured Markdown bytes.')
            return
        with path.open('rb') as stream:
            raw = stream.read(remaining + 1)
        require(len(raw) <= remaining, 'READ_LIMIT', 'Captured blob изменился при чтении.')
        require(sha256(raw).hexdigest() == blob_hash == binding['sourceHash'],
                'ARTIFACT_HASH_MISMATCH', 'Captured blob не соответствует manifest.')
        self.read_bytes += len(raw)
        original = raw.decode('utf-8')
        captured = original[:self.limits['maxDocumentChars']]
        captured = captured.encode('utf-8')[:self.limits['maxDocumentBytes']].decode('utf-8', errors='ignore')
        size = len(captured.encode('utf-8'))
        if self.document_bytes + size > self.limits['maxDocumentTotalBytes']:
            self.warn('Достигнут общий предел captured Markdown corpus.')
            return
        if not captured.strip():
            return
        clipped = captured != original or binding.get('coverage', {}).get('status') != 'complete'
        filename = binding['path']
        own_nodes = sorted(self.node_paths.get(filename, []))
        provenance = {'documentMapId': self.manifest['mapId'], 'documentSnapshotId': self.manifest['snapshotId'],
                      'capturedDocumentId': binding['documentId'], 'revisionId': binding['revisionId']}
        identifier = 'document:' + identity(self.snapshot_id, provenance, filename, binding['sourceHash'])[:32]
        document = {'id': identifier, 'title': PurePosixPath(filename).name, 'text': captured,
                    'kind': 'documentation', 'format': 'markdown', 'file': filename,
                    'line': 1, 'endLine': captured.count('\n') + 1, 'sourceHash': binding['sourceHash'],
                    'sourceKind': 'captured-document-tree', 'capturedAt': self.manifest['createdAt'],
                    'nodeIds': own_nodes[:MAX_NODE_IDS], 'fileNodeIds': own_nodes[:MAX_NODE_IDS],
                    'fileNodeIdsTruncated': len(own_nodes) > MAX_NODE_IDS,
                    'association': 'file', 'truncated': clipped, **provenance}
        document['sourceLinks'] = self.links(binding, len(captured))
        self.documents.append(document)
        self.document_bytes += size
        if clipped or len(own_nodes) > MAX_NODE_IDS:
            self.warn('Captured Markdown документ или file associations усечены.')
        self.chunk(document)

    def links(self, binding, captured_characters):
        """Извлекает уже разобранные explicit hrefs, сохраняя только file-scope raw IDs."""
        revision = _artifact_name(binding['revisionId'])
        result = []
        with database(self.root / 'revisions' / revision / 'index.sqlite') as connection:
            sql = "SELECT n.payload, p.payload FROM nodes n LEFT JOIN nodes p ON n.parent_id=p.id WHERE n.kind='reference' ORDER BY n.position,n.id"
            for record, parent_record in connection.execute(sql):
                node = json.loads(record)
                attributes = node.get('attributes', {})
                if attributes.get('referenceKind') != 'link':
                    continue
                parent = json.loads(parent_record) if parent_record else {}
                locator = parent.get('sourceLocator', {})
                # Inline spans are structural, so a clipped paragraph cannot prove that its href
                # occurs inside the retained prefix. Exclude that paragraph conservatively.
                if locator.get('characterEnd', captured_characters + 1) > captured_characters:
                    continue
                link = self.source_link(binding['path'], attributes.get('target'))
                if link is None:
                    continue
                if len(result) >= MAX_SOURCE_LINKS:
                    self.warn('Captured Markdown source links усечены.')
                    break
                link['documentLine'] = locator.get('lineStart')
                result.append(link)
        return result

    def source_link(self, document_file, href):
        """Нормализует literal href без открытия target; имя символа не доказывает связь."""
        if not isinstance(href, str):
            return None
        try:
            parsed = urlsplit(href)
        except ValueError:
            return None
        if parsed.scheme or parsed.netloc:
            return None
        path = unquote(parsed.path)
        if not path or '\\' in path or any(ord(char) < 32 for char in path):
            return None
        filename = posixpath.normpath(posixpath.join(posixpath.dirname(document_file), path))
        if path.startswith('/'):
            filename = posixpath.normpath(path.lstrip('/'))
        if filename.startswith('../') or filename in {'.', '..'}:
            return None
        line = re.fullmatch(r'L([1-9][0-9]*)(?:-L?[1-9][0-9]*)?', parsed.fragment)
        nodes = sorted(self.node_paths.get(filename, []))
        result = {'file': filename, 'line': int(line[1]) if line else None, 'scope': 'file',
                  'nodeIds': nodes[:MAX_NODE_IDS], 'association': 'source-link'}
        if len(nodes) > MAX_NODE_IDS:
            result['nodeIdsTruncated'] = True
            self.warn('Captured explicit link raw IDs усечены.')
        return result

    def chunk(self, document):
        """Делит captured body по прежним corpus bounds; tokenizer принадлежит semantic index."""
        text = document['text'][:MAX_RECORD_CHARS]
        clipped = document['truncated'] or len(text) != len(document['text'])
        document['embeddingTruncated'] = clipped
        self.truncated |= clipped
        start = 0
        while start < len(text):
            end = min(start + MAX_CHUNK_CHARS, len(text))
            fragment = text[start:end]
            if fragment.strip():
                if len(self.chunks) >= self.limits['maxChunks']:
                    self.warn('Достигнут предел search chunks.')
                    return
                line = 1 + text[:start].count('\n')
                chunk = {name: document[name] for name in ('file', 'kind', 'sourceHash', 'sourceKind',
                    'nodeIds', 'association', 'documentMapId', 'documentSnapshotId', 'capturedDocumentId', 'revisionId')}
                chunk.update(id='doc:' + identity(document['id'], start, fragment)[:24],
                    documentId=document['id'], documentOffsetStart=start, documentOffsetEnd=end,
                    text=fragment, line=line, endLine=line + fragment.count('\n'), rangeKind='text-slice', truncated=clipped)
                self.chunks.append(chunk)
            if end == len(text):
                break
            start = end - CHUNK_OVERLAP_CHARS


def capture_document_corpus(artifact_root, manifest, graph, code_snapshot_id, limits=None):
    """Экспортирует pinned Markdown artifacts в schema 2 без parser/source reads.

    Caller подтверждает принадлежность artifact root/manifest проекту и code snapshot.
    В corpus сохраняются оба snapshot ID; результаты не создают inferred code edges.
    """
    require(graph.get('metadata', {}).get('snapshotId', code_snapshot_id) == code_snapshot_id,
            'SNAPSHOT_MISMATCH', 'Code graph не соответствует выбранному snapshot.')
    graph_project = graph.get('metadata', {}).get('projectId')
    require(graph_project is None or graph_project == manifest.get('projectId'),
            'PROJECT_MISMATCH', 'Document manifest и code graph принадлежат разным проектам.')
    require(isinstance(manifest.get('snapshotId'), str) and isinstance(manifest.get('documents'), list),
            'INVALID_MANIFEST', 'Требуется published document manifest.')
    corpus = _Corpus(artifact_root, manifest, graph, code_snapshot_id, limits)
    if manifest.get('coverage', {}).get('status') != 'complete':
        corpus.warn('Document map содержит partial coverage; экспорт сохраняет доступные Markdown artifacts.')
    for binding in sorted(manifest['documents'], key=lambda item: (item['path'], item['documentId'])):
        if binding.get('provider') == 'document-markdown':
            corpus.document(binding)
    documents_by_file = {document['file']: document['id'] for document in corpus.documents}
    for document in corpus.documents:
        for link in document['sourceLinks']:
            if link['file'] in documents_by_file:
                link['documentId'] = documents_by_file[link['file']]
    return {'schemaVersion': SCHEMA_VERSION, 'extractorVersion': 'document-tree-corpus-v1',
            'sourceKind': 'captured-document-tree', 'snapshotId': code_snapshot_id,
            'documentMapId': manifest['mapId'], 'documentSnapshotId': manifest['snapshotId'],
            'capturedAt': manifest['createdAt'], 'documents': corpus.documents, 'chunks': corpus.chunks,
            'warnings': corpus.warnings, 'truncated': corpus.truncated,
            'stats': {'documents': len(corpus.documents), 'chunks': len(corpus.chunks),
                      'readBytes': corpus.read_bytes, 'documentBytes': corpus.document_bytes},
            'limits': {**corpus.limits, 'tokenLimitGuaranteed': False}}
