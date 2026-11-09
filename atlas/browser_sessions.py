"""RAM-only browser sessions, with hard expiry and explicit ownership of their lifetime."""
from copy import deepcopy
import json
from threading import Event, RLock, Thread, Semaphore
import time
from uuid import uuid4

from .browser_graph import build_browser_graph
from .mcp_response_budget import json_bytes, bound_list_response

MAX_SESSIONS = 8
MAX_RETAINED_BYTES = 64 * 1024 * 1024


from .browser_errors import BrowserSessionError


class BrowserSessions:
    """No filesystem or persistent indexes; encoded limits do not claim a Python RSS bound."""
    def __init__(self, *, clock=time.monotonic, wall_clock=time.time):
        """Construction allocates idle handles only; lifespan owns the optional sweeper."""
        self.clock, self.wall_clock = clock, wall_clock
        self.lock, self.entries = RLock(), {}
        self.stop = Event()
        self.admission = Semaphore(1)
        self.thread = None
        self.closed = False
        self.privacy = None

    def start(self):
        """Start one sweeper without retaining browser contents outside this service."""
        with self.lock:
            if self.thread is None and not self.closed:
                self.thread = Thread(target=self._sweep, name='atlas-browser-expiry', daemon=True)
                self.thread.start()

    def _sweep(self):
        """Release abandoned observations; access checks enforce expiry between sweeps."""
        while not self.stop.wait(5):
            with self.lock:
                self._expire()

    def _expire(self):
        """Use elapsed monotonic time, so wall-clock corrections cannot extend retention."""
        now = self.clock()
        for identifier in list(self.entries):
            if self.entries[identifier]['deadline'] <= now:
                del self.entries[identifier]

    def _entry(self, identifier):
        """Select only a live opaque ID; callers hold the service lock until projection ends."""
        self._expire()
        if self.closed or identifier not in self.entries:
            raise BrowserSessionError('Browser session is unavailable or expired')
        return self.entries[identifier]

    def create(self, body):
        """Build one bounded map at a time; failed admission never evicts a live session."""
        if not self.admission.acquire(blocking=False):
            raise BrowserSessionError('Browser capture is busy', 503)
        began, wall_began = self.clock(), self.wall_clock()
        try:
            privacy = None
            capture = body.capture
            if self.privacy is not None:
                from .browser_privacy_files import PrivacyFailure
                try:
                    with self.privacy.capture(capture) as (capture, privacy):
                        graph = build_browser_graph(capture)
                except PrivacyFailure:
                    raise BrowserSessionError('Browser privacy is unavailable', 424) from None
            else:
                # Direct stores are internal fixtures; production HTTP registration always attaches privacy.
                graph = build_browser_graph(capture)
            metadata = {'sessionId': uuid4().hex, 'page': capture.page.model_dump(),
                        'retention': body.retention, 'createdAt': wall_began,
                        'expiresAt': wall_began + body.ttlSeconds,
                        'nodeCount': len(graph['nodes']), 'edgeCount': len(graph['edges']),
                        'warnings': graph['warnings'], 'storage': 'memory-only',
                        'runtimeSelectionKnown': False, 'privacy': privacy}
            size = len(json.dumps([metadata, graph], ensure_ascii=False).encode('utf-8'))
            with self.lock:
                self._expire()
                if self.closed:
                    raise BrowserSessionError('Browser sessions are shutting down', 503)
                if self.clock() >= began + body.ttlSeconds:
                    raise BrowserSessionError('Browser capture expired during analysis', 410)
                if len(self.entries) >= MAX_SESSIONS or size + sum(item['bytes'] for item in self.entries.values()) > MAX_RETAINED_BYTES:
                    raise BrowserSessionError('Browser session capacity exceeded; finish an existing session', 429)
                self.entries[metadata['sessionId']] = {'metadata': metadata, 'graph': graph,
                    'deadline': began + body.ttlSeconds, 'bytes': size}
            return self._metadata(metadata)
        finally:
            self.admission.release()

    def list(self):
        """Return metadata only; source text is revealed solely by explicit bounded reads."""
        with self.lock:
            self._expire()
            return {'sessions': [self._metadata(item['metadata'], warnings=False) for item in self.entries.values()]}

    @staticmethod
    def _metadata(metadata, *, warnings=True):
        """Keep discovery and native-host replies bounded even for pathological page labels."""
        result = deepcopy(metadata)
        for key, maximum in (('url', 512), ('title', 128)):
            value = result['page'][key]
            result['page'][key] = value[:maximum]
            result['page'][key + 'Truncated'] = len(value) > maximum
        result['warningsCount'] = len(result['warnings'])
        result['warnings'] = [item[:128] for item in result['warnings'][:8]] if warnings else []
        result['warningsTruncated'] = result['warningsCount'] > len(result['warnings'])
        return result

    def query(self, identifier, body):
        """Search a captured graph without parsing or loading a page again."""
        with self.lock:
            entry = self._entry(identifier)
            page, total = [], 0
            query = body.query.casefold()
            for node in entry['graph']['nodes'].values():
                if body.kind is not None and node['kind'] != body.kind:
                    continue
                searchable = ' '.join(str(node.get(key, '')) for key in ('label', 'text', 'attributes', 'lexicalFacts'))
                if query not in searchable.casefold():
                    continue
                if body.offset <= total < body.offset + body.limit:
                    page.append({key: deepcopy(node[key]) for key in ('id', 'kind', 'label', 'scriptId', 'line', 'evidence') if key in node})
                total += 1
            self._entry(identifier)
            result = {'sessionId': identifier, 'nodes': page, 'total': total,
                    'nextOffset': body.offset + len(page) if body.offset + len(page) < total else None,
                    'warnings': [item[:128] for item in entry['graph']['warnings'][:8]],
                    'warningsTruncated': len(entry['graph']['warnings']) > 8, 'runtimeSelectionKnown': False}
            bounded = bound_list_response(result, ('nodes',))
            if bounded is None:
                raise BrowserSessionError('Browser query metadata exceeds budget', 413)
            delivered = len(bounded['nodes'])
            bounded['nextOffset'] = body.offset + delivered if body.offset + delivered < total else None
            return bounded

    def node(self, identifier, body):
        """Read an exact node and paged incident edges, preserving candidate evidence."""
        with self.lock:
            graph = self._entry(identifier)['graph']
            if body.nodeId not in graph['nodes']:
                raise BrowserSessionError('Browser node is unavailable')
            edges = [edge for edge in graph['edges'] if body.nodeId in (edge['source'], edge['target'])]
            result = {'sessionId': identifier, 'node': self._compact_node(graph['nodes'][body.nodeId]),
                      'edges': deepcopy(edges[body.offset:body.offset + body.limit]), 'totalEdges': len(edges),
                      'nextOffset': body.offset + body.limit if body.offset + body.limit < len(edges) else None,
                      'runtimeSelectionKnown': False}
            result = bound_list_response(result, ('edges',))
            if result is None:
                raise BrowserSessionError('Browser node metadata exceeds budget', 413)
            delivered = len(result['edges'])
            result['nextOffset'] = body.offset + delivered if body.offset + delivered < len(edges) else None
            self._entry(identifier)
            return result

    @staticmethod
    def _compact_node(node):
        """Keep valid large DOM nodes readable; omitted fields have an indexed detail reader."""
        result = deepcopy(node)
        omitted = []
        for field in ('attributes', 'styles', 'text'):
            if field in result and json_bytes(result[field]) > 8000:
                omitted.append({'field': field, 'propertyCount': len(result[field]) if isinstance(result[field], dict) else 1})
                del result[field]
        result['omittedFields'] = omitted
        return result

    def overview(self, identifier):
        """Inspect one exact capture and its coverage before requesting page content."""
        with self.lock:
            entry = self._entry(identifier)
            counts = {}
            for node in entry['graph']['nodes'].values():
                counts[node['kind']] = counts.get(node['kind'], 0) + 1
            result = self._metadata(entry['metadata'])
            result.update(counts=counts, scope=entry['graph']['scope'],
                          nextActions=['query_browser_session', 'get_browser_node', 'read_browser_node_source', 'finish_browser_session'])
            return result

    def property(self, identifier, body):
        """Read one original property, preserving Unicode keys and explicitly bounded values."""
        with self.lock:
            graph = self._entry(identifier)['graph']
            node = graph['nodes'].get(body.nodeId)
            if node is None or body.field not in node:
                raise BrowserSessionError('Browser node property is unavailable')
            value = node[body.field]
            items = list(value.items()) if isinstance(value, dict) else [('text', value)]
            if body.propertyIndex >= len(items):
                raise BrowserSessionError('Browser node property is unavailable')
            key, value = items[body.propertyIndex]
            text = value[body.offset:body.offset + body.limit]
            end = body.offset + len(text)
            result = {'sessionId': identifier, 'nodeId': body.nodeId, 'field': body.field,
                      'propertyIndex': body.propertyIndex, 'propertyCount': len(items), 'key': key,
                      'text': text, 'offset': body.offset, 'totalCharacters': len(value),
                      'nextOffset': end if end < len(value) else None, 'coordinateKind': 'unicode-codepoints'}
            self._entry(identifier)
            return result

    def node_source(self, identifier, body):
        """Slice the exact lexical byte range, then paginate its Unicode characters locally."""
        with self.lock:
            graph = self._entry(identifier)['graph']
            node = graph['nodes'].get(body.nodeId, {})
            script_id = node.get('scriptId')
            if script_id not in graph['sources']:
                raise BrowserSessionError('Browser node source is unavailable')
            source = graph['sources'][script_id]
            if node['kind'] == 'function':
                encoded = source.encode('utf-8')
                source = encoded[node['startByte']:node['endByte']].decode('utf-8')
            text = source[body.offset:body.offset + body.limit]
            end = body.offset + len(text)
            result = {'sessionId': identifier, 'nodeId': body.nodeId, 'scriptId': script_id,
                      'text': text, 'offset': body.offset, 'nextOffset': end if end < len(source) else None,
                      'totalCharacters': len(source), 'coordinateKind': 'node-relative-unicode-codepoints',
                      'sourceHash': graph['nodes']['script:' + script_id]['sourceHash'],
                      'evidence': node['evidence'], 'runtimeSelectionKnown': False}
            self._entry(identifier)
            return result

    def source(self, identifier, body):
        """Return original script characters and its exact hash; no beautification or execution."""
        with self.lock:
            graph = self._entry(identifier)['graph']
            if body.scriptId not in graph['sources']:
                raise BrowserSessionError('Browser script source is unavailable')
            source = graph['sources'][body.scriptId]
            text = source[body.offset:body.offset + body.limit]
            end = body.offset + len(text)
            self._entry(identifier)
            return {'sessionId': identifier, 'scriptId': body.scriptId, 'text': text,
                    'sourceHash': graph['nodes']['script:' + body.scriptId]['sourceHash'],
                    'offset': body.offset, 'nextOffset': end if end < len(source) else None,
                    'totalCharacters': len(source), 'coordinateKind': 'unicode-codepoints'}

    def finish(self, identifier, reason):
        """Idempotently release all session-owned source/graph references, including on errors."""
        with self.lock:
            self.entries.pop(identifier, None)
        return {'sessionId': identifier, 'deleted': True, 'reason': reason}

    def close(self):
        """Stop expiry work and release every observation before server shutdown completes."""
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=6)
        with self.lock:
            self.closed = True
            self.entries.clear()
        if self.privacy is not None:
            self.privacy.close()
