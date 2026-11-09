"""Bounded source capture and lexical research reuse Atlas services without executing project code."""
from hashlib import sha256
import json
from pathlib import Path
import re
from .models import ResearchError
from ..node_description import build_node_description
from ..source_viewer import read_node_source, _read_regular_file, SourceError

RELATIONS = {'calls', 'dispatches_to', 'method'}


class EvidenceCollector:
    """Pin one immutable graph; working-tree excerpts are separately hashed and verified."""
    def __init__(self, projects, records, references, libraries, run):
        self.projects, self.records = projects, records
        self.references, self.libraries, self.run = references, libraries, run
        self.root, self.graph = projects.comparison_snapshot(run['projectId'])
        projects.assert_snapshot(run['projectId'], run['snapshotId'])
        self.nodes = {node['id']: node for node in self.graph.get('nodes', [])}
        self.outgoing = {}
        for edge in self.graph.get('edges', []):
            if edge.get('relation') in RELATIONS and edge.get('target') in self.nodes:
                self.outgoing.setdefault(edge['source'], []).append(edge)

    def seeds(self) -> list[dict]:
        """Select a named symbol or lexical matches; a question is never silently treated as a semantic answer."""
        if self.run['nodeId']:
            if self.run['nodeId'] not in self.nodes:
                raise ResearchError('Выбранный символ отсутствует в снимке.', 404)
            return [{'id': self.run['nodeId'], 'depth': 0}]
        tokens = [word.casefold() for word in re.findall(r'[\w]+', self.run['question'])
                  if len(word) >= 3 and word.casefold() not in {'как', 'что', 'это', 'для', 'или', 'работает', 'проекта', 'the', 'how', 'does'}][:12]
        ranked = []
        for node in self.nodes.values():
            text = ' '.join(str(node.get(key) or '') for key in ('label', 'id', 'file', 'comment', 'implementationComment')).casefold()
            score = sum(token in text for token in tokens)
            if score:
                ranked.append((-score, node['id']))
        return [{'id': identifier, 'depth': 0} for _, identifier in sorted(ranked)[:min(3, self.run['maxNodes'])]]

    def capture_node(self, identifier: str) -> tuple[str, list[dict]]:
        """Capture this declaration and explicit outgoing relations, with no inferred runtime ordering."""
        node = self.nodes[identifier]
        edges = self.outgoing.get(identifier, [])
        evidence = {'kind': 'code', 'nodeId': identifier, 'label': str(node.get('label', identifier))[:300],
                    'file': node.get('file') or '', 'line': node.get('line'),
                    'language': node.get('language') or (node.get('memberFacts') or {}).get('language') or '',
                    'commentScope': node.get('commentScope', 'unknown'),
                    'capturedComment': str(node.get('comment') or node.get('implementationComment') or '')[:2000],
                    'relations': [{'target': edge['target'], 'label': str(self.nodes[edge['target']].get('label', edge['target']))[:300],
                                   'relation': edge['relation'], 'confidence': edge.get('confidence', 'unknown')} for edge in edges[:24]],
                    'relationsTruncated': len(edges) > 24, 'sourceStatus': 'unavailable'}
        try:
            source = read_node_source(self.root, node, self.run['snapshotId'], include_syntax=False)
            description = build_node_description(self.root, self.graph, identifier, self.run['snapshotId'])
            start = max(0, (description.get('line') or source['line'] or 1) - 4)
            lines = source['text'].splitlines()
            end = min(start + 80, description.get('endLine') or len(lines))
            excerpt = '\n'.join(lines[start:end])
            evidence.update(file=source['file'], sourceHash=source['sourceByteHash'], sourceStatus='captured',
                            startLine=start + 1, source=excerpt[:6000], sourceTruncated=len(excerpt) > 6000 or (description.get('endLine') or len(lines)) > end)
            evidence['description'] = {key: description.get(key) for key in
                                       ('signature', 'documentation', 'sourceStatus', 'methodFlow', 'unresolved', 'warning')}
            if len(json.dumps(evidence['description'], ensure_ascii=False).encode()) > 24_000:
                evidence['description'] = {'signature': description.get('signature'), 'sourceStatus': description.get('sourceStatus')}
                evidence['descriptionTruncated'] = True
            # Only the bytes are needed for the race check; avoid highlighting the entire file again.
            current_hash = sha256(_read_regular_file(self.root, Path(source['file']))).hexdigest()
            if current_hash != source['sourceByteHash']:
                raise ResearchError('Исходник изменился во время чтения. Создайте новое исследование.', 409)
        except SourceError as error:
            evidence['sourceWarning'] = str(error)
        eid = self.records.capture(self.run['id'], evidence)
        return eid, edges

    def capture_references(self) -> list[str]:
        """Read only already imported fulltext documentation; no embedding, download or automatic index build."""
        items = self.records.evidence(self.run['id'])
        terms = []
        for item in items:
            if item.get('kind') != 'code':
                continue
            for term in ['async', 'await', 'foreach', 'if', 'switch', 'null', 'return']:
                if re.search(r'\b' + term + r'\b', item.get('source', '')) and term not in terms:
                    terms.append(term)
        from ..reference_documents import LANGUAGES
        from ..flow_languages.registry import MODULES
        extensions = {'.cs':'csharp', **MODULES, '.ts':'javascript', '.tsx':'javascript'}
        languages = set()
        for item in items:
            language = extensions.get(Path(item.get('file') or '').suffix.lower()) or str(item.get('language') or '').casefold()
            if language in LANGUAGES:
                languages.add(language)
        captured = []
        for language in sorted(languages - {''})[:2]:
            for term in terms[:3]:
                result = self.references.search(term, language=language, mode='fulltext', locale='ru', limit=1)
                for hit in result.get('results', []):
                    doc = self.references.store.document(hit['packId'], hit['documentId'], limit=2400)
                    captured.append(self._capture_document('language', hit, doc))
        query = self.run['question'][:200] or (items[0]['label'] if items else '')
        if query:
            result = self.libraries.search(self.run['projectId'], query, mode='fulltext', locale='ru', limit=2)
            for hit in result.get('results', []):
                doc = self.libraries.document(self.run['projectId'], hit['packId'], hit['documentId'], offset=0, limit=2400)
                captured.append(self._capture_document('library', hit, doc))
        return list(dict.fromkeys(captured))

    def _capture_document(self, kind, hit, doc):
        """Retain document edition and literal excerpt; a search candidate does not bind an API to a call."""
        text = str(doc.get('text') or hit.get('excerpt') or '')[:2400]
        evidence = {'kind': kind, 'label': str(hit.get('title', 'Справочник'))[:300], 'text': text,
                    'documentId': hit['documentId'], 'packId': hit['packId'], 'sourceHash': sha256(text.encode()).hexdigest(),
                    'version': hit.get('version'), 'locale': hit.get('locale'), 'edition': hit.get('edition'),
                    'sourceUrl': hit.get('sourceUrl'), 'excerptTruncated': doc.get('nextOffset') is not None,
                    'warning': 'Выдержка из локального справочника; применимость к вызову не установлена автоматически.'}
        return self.records.capture(self.run['id'], evidence)

    def verify(self) -> dict:
        """Recheck captured hashes; unavailable code remains partial rather than verified."""
        self.projects.assert_snapshot(self.run['projectId'], self.run['snapshotId'])
        changed = []
        unavailable = []
        files = {}
        for item in self.records.evidence(self.run['id']):
            if item['kind'] != 'code':
                continue
            if not item.get('sourceHash'):
                unavailable.append(item.get('file') or item.get('nodeId') or 'unknown')
                continue
            previous = files.setdefault(item['file'], item['sourceHash'])
            if previous != item['sourceHash']:
                changed.append(item['file'])
        for file, expected in files.items():
            try:
                actual = sha256(_read_regular_file(self.root, Path(file))).hexdigest()
                if actual != expected:
                    changed.append(file)
            except SourceError:
                changed.append(file)
        status = 'verified-at-capture'
        if unavailable:
            status = 'partial'
        # A mismatch in any known hash blocks publication, even if another file was unavailable.
        if changed:
            status = 'stale'
        return {'status': status, 'changedFiles': sorted(set(changed)), 'unavailableFiles': sorted(set(unavailable)),
                'scope': 'working files and project snapshot; reference excerpts retain their captured editions'}
