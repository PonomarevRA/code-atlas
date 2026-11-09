"""Bounded cached infrastructure evidence shared by execution paths and future map clients."""
from collections import defaultdict, OrderedDict
from concurrent.futures import Future
from hashlib import sha256
from pathlib import Path
from threading import Lock, RLock
import re
import time
from ..source_viewer import read_node_source, SourceError
from .syntax import GRAMMARS, parse, literal
from .providers import detect_calls, VERSION
from .configuration import configuration, safe_name
from .csharp import declarations, orm_facts
from .handlers import consumer_handlers, targets

MAX_FILES, MAX_BYTES, MAX_FACTS = 1200, 16 * 1024 * 1024, 12000
_CACHE, _LOCK = OrderedDict(), RLock()
_IN_FLIGHT: dict[tuple[str, str, str], Future["InfrastructureIndex"]] = {}


def _candidates(root, graph):
    """Discover only graph-owned source files and adjacent JSON configuration variants, within the root."""
    files = {n.get('file') for n in graph.get('nodes', []) if n.get('file')}
    files = {f for f in files if Path(f).suffix.lower() in GRAMMARS and not any(p.lower() in {'tests', 'migrations', 'bin', 'obj', 'node_modules'} for p in Path(f).parts)}
    for directory in sorted({str(Path(f).parent) for f in files}):
        candidate = (root / directory).resolve()
        if not candidate.is_relative_to(root):
            continue
        try:
            for path in candidate.glob('appsettings*.json'):
                if path.is_file() and path.resolve().is_relative_to(root):
                    files.add(path.relative_to(root).as_posix())
        except OSError:
            continue
    return sorted(files)


class InfrastructureIndex:
    """Resource identities and candidates are local facts, never runtime environment selection."""
    def __init__(self, root, graph, snapshot_id, files):
        """Scan graph-owned sources once with shared byte/time/AST budgets and explicit coverage."""
        self.root, self.graph, self.snapshot_id = root, graph, snapshot_id
        self.facts, self.symbols, self.configurations = defaultdict(list), defaultdict(list), []
        self.sources, self.syntaxes = {}, {}
        self.coverage = {'candidateFiles': len(files), 'readFiles': 0, 'unavailableFiles': 0, 'unavailableSources': [], 'parseErrors': 0, 'limited': False,
                         'maxFiles': MAX_FILES, 'maxBytes': MAX_BYTES, 'maxFacts': MAX_FACTS, 'providerVersion': VERSION}
        self.coverage.update(analysisScope='graph-owned-supported-files', supportedExtensions=sorted(GRAMMARS))
        started = time.monotonic()
        tables, sets = self._read_sources(files, started)
        self._analyze_sources(tables, sets, started)

    def _read_sources(self, files, started):
        """Read a bounded corpus and retain only explicit declarations/configuration metadata."""
        tables, sets, size, ast_nodes = defaultdict(list), defaultdict(list), 0, 0
        for filename in files[:MAX_FILES]:
            if size >= MAX_BYTES or time.monotonic() - started > 25:
                self.coverage['limited'] = True
                break
            try:
                source = read_node_source(self.root, {'file': filename}, self.snapshot_id, include_syntax=False)
                size += len(source['text'].encode('utf-8'))
                if size > MAX_BYTES:
                    self.coverage['limited'] = True
                    break
                self.sources[filename] = source
                self.coverage['readFiles'] += 1
                if filename.endswith('.json'):
                    self.configurations.extend(configuration(source))
                    if len(self.configurations) > 2000:
                        self.configurations = self.configurations[:2000]
                        self.coverage['limited'] = True
                    continue
                syntax = parse(source)
                if syntax is None:
                    continue
                ast_nodes += len(syntax.nodes)
                if ast_nodes > 1500000:
                    self.coverage['limited'] = True
                    break
                self.syntaxes[filename] = syntax
                if syntax.language == 'csharp':
                    symbols, mapped_tables, mapped_sets = declarations(syntax)
                    for symbol in symbols:
                        self.symbols[symbol['name']].append(symbol)
                    for entity, names in mapped_tables.items():
                        tables[entity].extend(names)
                    for name, entity in mapped_sets.items():
                        sets[name].extend(entity)
            except SourceError:
                self.coverage['unavailableFiles'] += 1
                if len(self.coverage['unavailableSources']) < 12:
                    self.coverage['unavailableSources'].append(filename)
            except (ImportError, ValueError, RecursionError):
                self.coverage['parseErrors'] += 1
        self.coverage['limited'] |= len(files) > MAX_FILES
        return tables, sets

    def _analyze_sources(self, tables, sets, started):
        """Prepare bindings once, project common facts, then release temporary syntax trees."""
        from .csharp_bindings import ProducerBindings
        from .csharp_orm import expand_contexts, generic_bindings, query_facts
        from .database_bindings import database_candidates
        expand_contexts(self.syntaxes.values(), sets)
        self.databases = database_candidates(self.syntaxes.values(), self.configurations)
        context_types = {record['owner'] for records in sets.values() for record in records}
        generic_tables = generic_bindings(self.syntaxes.values(), tables, context_types)
        self.producers = ProducerBindings(list(self.syntaxes.values()), self.configurations)
        handlers = consumer_handlers(self.syntaxes.values(), self.graph.get('nodes', []))
        self.handlers = {}
        count = 0
        for filename, syntax in self.syntaxes.items():
            if time.monotonic() - started > 35:
                self.coverage['limited'] = True
                break
            facts = list(detect_calls(syntax))
            if syntax.language == 'csharp':
                facts.extend(orm_facts(syntax, sets, tables))
                facts.extend(query_facts(syntax, tables, generic_tables, context_types))
            for fact in facts:
                if count >= MAX_FACTS:
                    self.coverage['limited'] = True
                    break
                resolved = self._resolve(fact, syntax)
                self.facts[filename].append(resolved)
                if resolved.get('handlerType'):
                    self.handlers[resolved['id']] = targets(syntax, resolved['handlerType'], handlers)
                count += 1
        self.coverage['facts'] = count
        self.coverage['complete'] = not (self.coverage['limited'] or self.coverage['unavailableFiles'] or self.coverage['parseErrors'])
        self.syntaxes.clear()
        self._dispatches = None
        self._dispatch_lock = Lock()

    def dispatches(self):
        """Build framework edges lazily only when actual sources declare the MediatR namespace."""
        with self._dispatch_lock:
            if self._dispatches is None:
                edges = []
                if any(re.search(r'\busing\s+MediatR\s*;', s['text']) for s in self.sources.values()):
                    from .mediator import mediator_edges
                    edges, coverage = mediator_edges(self.root, self.graph)
                    self.coverage['mediator'] = coverage
                self._dispatches = edges
            return self._dispatches

    def _resolve(self, fact, syntax):
        """Resolve literals or unique visible static declarations; otherwise preserve configured candidates."""
        expression = fact.pop('expression', None) or ''
        bound = self.producers.candidates(syntax, fact) if fact['kind'] == 'kafka' and syntax.language == 'csharp' else []
        fact.pop('callee', None)  # Receiver chains can contain connection arguments; never publish them.
        value = safe_name(literal(expression))
        evidence = fact.get('evidence', [])
        if value is None and re.fullmatch(r'[\w.]+', expression):
            symbols = self.symbols.get(expression, [])
            imports = re.findall(r'\busing\s+([\w.]+)\s*;', syntax.source['text'])
            visible = [s for s in symbols if s['file'] == syntax.source['file'] or any(s['qualified'].startswith(i + '.') for i in imports)]
            if len({s['value'] for s in visible}) == 1:
                value = safe_name(visible[0]['value'])
                evidence = [{k: s[k] for k in ('file', 'line')} for s in visible[:4]]
        candidates = bound or (self.databases if fact['kind'] == 'database' else [c for c in self.configurations if c['kind'] == fact['kind']])
        configured_name = None
        if not value and bound and len({c['name'] for c in bound}) == 1:
            configured_name = bound[0]['name']
        # Configurations are alternatives, not a binding to every call in the repository.
        fact.update(name=value or configured_name, status='resolved' if value else 'configured' if configured_name else 'unknown', runtimeSelectionKnown=False,
                    file=syntax.source['file'], sourceHash=syntax.source['sourceByteHash'], evidence=evidence,
                    configurationCandidates=candidates[:12], configurationCandidatesTruncated=len(candidates) > 12,
                    configurationBindingKnown=bool(bound))
        if fact.get('bindingCandidate'):
            fact['status'] = 'candidate'
        identity = f"{fact['file']}:{fact['line']}:{fact.get('column')}:{fact['kind']}:{fact['operation']}:{value or configured_name}:{fact.get('handlerType')}"
        fact['id'] = 'infrastructure:' + sha256(identity.encode()).hexdigest()[:24]
        return fact

    def for_node(self, node, description, limit=30):
        """Return resource operations within a parsed declaration's lexical source interval (including closures)."""
        if description.get('sourceStatus') != 'parsed' or description.get('kind') == 'class':
            return []
        start, end = description.get('line'), description.get('endLine')
        if not start or not end:
            return []
        return [dict(fact) for fact in self.facts.get(node.get('file'), []) if start <= fact['line'] <= end][:limit]

    def node_metadata(self, node, description):
        """Expose per-declaration limits separately from corpus coverage, including closure lexical ownership."""
        facts = self.for_node(node, description, limit=None)
        with self._dispatch_lock:
            coverage = dict(self.coverage)
        return {'infrastructure': facts[:30], 'infrastructureTotal': len(facts), 'infrastructureTruncated': len(facts) > 30,
                'infrastructureCoverage': coverage, 'infrastructureScope': 'lexical-including-closures'}


def infrastructure_index(root, graph, snapshot_id):
    """Reuse evidence for the same snapshot and source stamps, including concurrent cache misses."""
    root = Path(root).resolve()
    files = _candidates(root, graph)
    stamps = []
    for filename in files:
        path = (root / filename).resolve()
        if not path.is_relative_to(root):
            continue
        try:
            stat = path.stat()
            stamps.append((filename, stat.st_size, stat.st_mtime_ns))
        except OSError:
            stamps.append((filename, None, None))
    key = (str(root), snapshot_id, sha256(repr(stamps).encode()).hexdigest())
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached
        pending = _IN_FLIGHT.get(key)
        owns_build = pending is None
        if pending is None:
            pending = _IN_FLIGHT[key] = Future()
    if not owns_build:
        return pending.result()

    try:
        built = InfrastructureIndex(root, graph, snapshot_id, files)
    except BaseException as error:
        with _LOCK:
            del _IN_FLIGHT[key]
            pending.set_exception(error)
        raise

    with _LOCK:
        _CACHE[key] = built
        while len(_CACHE) > 2:
            _CACHE.popitem(last=False)
        del _IN_FLIGHT[key]
        pending.set_result(built)
    return built
