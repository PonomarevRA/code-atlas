"""Active-use coordination of optional local engines and source-bound map sidecars."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock
import time

from .health_snapshot_overlay import file_stamp
from .mod_analysis import active_semantic_rules, assert_semantic_rules_current
from .semantic_models import SemanticContext
from .semantic_overlay import make_overlay, write_overlay
from .semantic_provider_registry import create_semantic_provider
from .semantic_requests import EXTENSIONS, build_semantic_request, project_identity
from .semantic_runtime import SemanticRuntime
from .semantic_sources import captured_source_hashes
from .semantic_engine_settings import SemanticEngineSettings
from .semantic_environment import discovery_environment_fingerprint
from .semantic_outcomes import analysis_outcome, add_reasons, record_result, apply_publication
from .source_viewer import _read_regular_file, SourceError
from .store import StoreError, write_json

CONFIG_FILES = ('tsconfig.json', 'jsconfig.json', 'package.json', 'pyproject.toml',
                'global.json', 'Directory.Build.props', 'Directory.Build.targets',
                'compile_commands.json', 'go.mod', 'Cargo.toml', 'pom.xml', 'composer.json',
                'rust-project.json', '.classpath')
MAX_STATES = 8
MAX_OPERATION_SECONDS = 45


def baseline_status(snapshot_id):
    """An unvalidated persisted observation never implies a successful current analysis."""
    return {'version': 'atlas-semantic-status-v1', 'snapshotId': snapshot_id,
            'providers': [], 'enrichment': 'baseline', 'runtimeSelectionKnown': False}


def _digest(value):
    """Hash private configuration inputs without exposing their contents through status APIs."""
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _configuration_stamps(root, config_names):
    """
    Identify every known context input even when its bytes exceed the hash budget.

    root is the workspace; config_names is the bounded sequence of relative
    context paths. Returns path/stat tuples using modification time, size, change
    time, inode and mode. Missing/unreadable inputs use a zero tuple. This performs
    no content reads and exposes only its caller's private fingerprint inputs.
    """
    identities = []
    for name in config_names:
        try:
            info = (root / name).stat()
            stamp = (info.st_mtime_ns, info.st_size, info.st_ctime_ns, info.st_ino, info.st_mode)
        except OSError:
            stamp = (0, 0, 0, 0, 0)
        identities.append((name, stamp))
    return identities


class SemanticEnrichment:
    """Return maps immediately; one bounded worker prepares explicit selected-node requests."""
    def __init__(self, store, *, provider_factory=create_semantic_provider, clock=time.time):
        """Create idle handles only; construction never discovers an engine or scans a project."""
        self.store, self.factory, self.clock = store, provider_factory, clock
        self.settings = SemanticEngineSettings(store.data_dir)
        self.runtime = SemanticRuntime(store.data_dir / 'semantic-engines', {}, clock=clock, usage=getattr(store, 'usage', None))
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='atlas-semantic-map')
        self.lock, self.states, self.pending, self.contexts = RLock(), OrderedDict(), {}, {}
        self.closed = False
        self.epoch = 0

    def _scope(self, project_id, snapshot_id):
        """Pin existing registry paths without accepting a caller-supplied filename."""
        with self.store.lock:
            path = self.store._snapshot_path(project_id, '_graph')
            if path.parent.name != snapshot_id:
                raise StoreError('Карта обновилась. Загрузите её перед уточнением.', 409)
            root = Path(self.store._project(project_id)['path'])
            return root, path

    def _signature(self, root, path, config_names=CONFIG_FILES):
        """
        Identify bounded settings/config stamps and discovery environment without source reads.

        root is the workspace; path is its canonical snapshot. config_names is the
        bounded relative context-file list, defaulting to known provider inputs.
        Returns a private SHA256 signature; missing context files contribute zero
        stamps so creation/removal invalidates negative discovery on normal use.
        """
        mods = self.store.mods
        manifests = []
        for directory in (mods.bundled, mods.directory):
            for candidate in sorted(directory.glob('*/mod.json'))[:64]:
                manifests.append((str(candidate), file_stamp(candidate)))
        configs = _configuration_stamps(root, config_names)
        return _digest([str(path), file_stamp(path), file_stamp(mods.state_path),
                        file_stamp(self.settings.path), manifests, configs,
                        discovery_environment_fingerprint()])

    def status(self, project_id, snapshot_id):
        """Read one bounded persisted map status without probing tools or reading source files."""
        _, path = self._scope(project_id, snapshot_id)
        with self.lock:
            state = self.states.get(project_id)
            if state and state['snapshotId'] == snapshot_id:
                return json.loads(json.dumps(state['public']))
        try:
            status_path = path.with_name('semantic-status.json')
            if status_path.stat().st_size > 64_000:
                raise ValueError('Oversized semantic status')
            result = json.loads(status_path.read_text(encoding='utf-8'))
            if (isinstance(result, dict) and result.get('snapshotId') == snapshot_id
                    and result.get('version') == 'atlas-semantic-status-v1'
                    and isinstance(result.get('providers'), list) and len(result['providers']) <= 64):
                return {**result, 'enrichment': 'unchecked', 'persistedObservation': True,
                        'runtimeSelectionKnown': False}
        except (OSError, ValueError, AttributeError, RecursionError):
            pass
        return baseline_status(snapshot_id)

    def use(self, project_id, snapshot_id, node_ids=(), *, force=False):
        """Schedule due discovery or selected-node analysis; no parsing or child process runs here."""
        if len(node_ids) > 16 or len(set(node_ids)) != len(node_ids):
            raise StoreError('Выберите до 16 различных узлов.', 400)
        root, path = self._scope(project_id, snapshot_id)
        if node_ids:
            with self.store.lock:
                graph = self.store._base_snapshot(project_id, path)
                selected = set(node_ids)
                if selected - {node['id'] for node in graph['nodes']}:
                    raise StoreError('Узел отсутствует в выбранной карте.', 404)
        with self.lock:
            config_names = self.states.get(project_id, {}).get('configNames', CONFIG_FILES)
        signature, now = self._signature(root, path, config_names), self.clock()
        previous_public = self.status(project_id, snapshot_id)
        with self.lock:
            if self.closed:
                return previous_public
            state = self.states.get(project_id)
            if state is None or state['signature'] != signature:
                for context, provider_id in self.contexts.get(project_id, []):
                    self.runtime.cancel(context, [provider_id])
                # Epochs are unique for this coordinator's lifetime, including
                # after LRU eviction. A retired job must never match a new state.
                self.epoch += 1
                generation = self.epoch
                state = {'signature': signature, 'snapshotId': snapshot_id, 'generation': generation,
                         'nextCheck': 0, 'requested': (), 'completed': OrderedDict(),
                         'nodeFiles': OrderedDict(), 'configNames': config_names,
                         'public': baseline_status(snapshot_id)}
                self.states[project_id] = state
            self.states.move_to_end(project_id)
            while len(self.states) > MAX_STATES:
                retired, _ = self.states.popitem(last=False)
                for context, provider_id in self.contexts.pop(retired, []):
                    self.runtime.cancel(context, [provider_id])
            requested = tuple(node_ids) or state['requested']
            if node_ids:
                if state['requested'] and state['requested'] != requested:
                    # A quick symbol change supersedes older selected work. The
                    # immutable snapshot remains usable while callbacks stop.
                    self.epoch += 1
                    state['generation'] = self.epoch
                    for context, provider_id in self.contexts.get(project_id, []):
                        self.runtime.cancel(context, [provider_id])
                    for old_key, future in list(self.pending.items()):
                        if old_key[0] == project_id and future.cancel():
                            self.pending.pop(old_key, None)
                state['requested'] = requested
            selected_stamp = tuple((identifier, file_stamp(root / state['nodeFiles'][identifier]))
                                   for identifier in requested if identifier in state['nodeFiles'])
            completed_key = requested, selected_stamp
            due = force or now >= state['nextCheck'] or bool(requested and completed_key not in state['completed'])
            key = project_id, snapshot_id, state['generation'], requested
            if due and key not in self.pending and len(self.pending) < 16:
                state['public'] = {**state['public'], 'enrichment': 'queued' if requested else 'baseline'}
                future = self.executor.submit(self._run, project_id, snapshot_id, state['generation'],
                                              signature, requested, force)
                self.pending[key] = future
            return json.loads(json.dumps(state['public']))

    def _configuration(self, root, config_names):
        """
        Hash bounded declarative inputs and the private discovery environment identity.

        root is the workspace; config_names lists relative known context paths.
        Every input contributes a bounded stat identity, including missing or
        over-budget files. Content reads are bounded to512KiB per file and1MiB
        in total; omitted content never removes that input from discovery identity.
        Returns a SHA256 digest only; no file text or environment paths are exposed.
        """
        identities = _configuration_stamps(root, config_names)
        hashes, remaining = {}, 1024 * 1024
        for name in config_names:
            if remaining <= 0:
                break
            try:
                content = _read_regular_file(root.resolve(), Path(name), min(512 * 1024, remaining))
            except SourceError:
                continue
            remaining -= len(content)
            hashes[name] = sha256(content).hexdigest()
        return _digest([identities, hashes, discovery_environment_fingerprint()])

    def _current(self, project_id, snapshot_id, generation, signature):
        """Fence source/config/mod changes and cancellation immediately before publication."""
        root, path = self._scope(project_id, snapshot_id)
        with self.lock:
            state = self.states.get(project_id)
            if self.closed or state is None or state['generation'] != generation:
                return False
            names = tuple(state['configNames'])
        return self._signature(root, path, names) == signature

    def _config_names(self, graph):
        """Include captured project manifests without walking the working tree or evaluating build files."""
        names = list(CONFIG_FILES)
        candidates = list(graph.get('codeProjects', []))
        candidates.extend(node.get('codeProjectFacts') or {} for node in graph['nodes'])
        for project in candidates:
            for key in ('manifest', 'buildManifest'):
                name = project.get(key)
                if not isinstance(name, str) or name in names:
                    continue
                candidate = Path(name)
                if not candidate.is_absolute() and '..' not in candidate.parts and ':' not in name:
                    names.append(name)
                    if len(names) >= 32:
                        return tuple(names)
        return tuple(names)

    def _wait(self, future, timeout=65):
        """Wait in short intervals so coordinator shutdown never waits on an unbounded callback."""
        deadline = time.monotonic() + timeout
        while not self.closed and time.monotonic() < deadline:
            try:
                return future.result(timeout=.1)
            except TimeoutError:
                pass
        return None

    def _sources_current(self, root, contents, hashes):
        """Check exact raw/decoded bytes at publication, without rebuilding ASTs or walking directories."""
        for filename, expected in contents.items():
            try:
                raw = _read_regular_file(root.resolve(), Path(filename), 1024 * 1024)
            except SourceError:
                return False
            if sha256(raw).hexdigest() != hashes.get(filename):
                return False
            if raw.removeprefix(b'\xef\xbb\xbf') != expected:
                return False
        return True

    def _target_groups(self, graph, node_index, requested, language):
        """Separate captured compilation owners before any provider is probed."""
        cohorts, targets = {}, {}
        for node in graph['nodes']:
            filename = node.get('file') or ''
            if Path(filename).suffix.lower() in EXTENSIONS[language]:
                cohorts.setdefault(project_identity(node), set()).add(filename)
        for identifier in requested:
            node = node_index[identifier]
            suffix = Path(node.get('file') or '').suffix.lower()
            if suffix in EXTENSIONS[language]:
                targets.setdefault(project_identity(node), []).append(identifier)
        if not targets:
            # Probe separate captured compilation cohorts. A .NET monorepo
            # cannot share one Roslyn reference context across its projects.
            for target in cohorts:
                targets.setdefault(target, [])
            if not targets:
                targets = {'': []}
        return cohorts, targets

    def _analyze_target(self, context, provider_id, graph, identifiers, language, target, all_contents, force, deadline):
        """
        Prepare one bounded selected-scope result without claiming publication.

        context pins snapshot/source/config; provider_id names the registered
        host adapter. graph owns identifiers, the selected raw node IDs. language
        and target identify their captured cohort. all_contents tracks earlier
        scopes against the shared16file/4MiB budget. force requests fresh discovery;
        deadline is the monotonic operation deadline. Returns availability, phase,
        optional projectable result, source bytes, original runtime proof and a
        bounded public outcome. Typed unsupported results stay partial. Request
        validation errors may raise ValueError for the coordinator to classify.
        """
        root = Path(context.workspace_root)
        result, proof, contents = None, None, {}
        outcome = analysis_outcome()
        self.runtime.on_use(context, [provider_id], force=force)
        # This wait belongs to the already requested background
        # operation. It neither blocks HTTP nor schedules idle scans.
        self.runtime.wait_for_idle(timeout=max(0, min(3.5, deadline - time.monotonic())))
        availability = self.runtime.status(context, [provider_id])[provider_id]
        phase = 'baseline'
        if identifiers and availability.status == 'available':
            phase = 'running'
            request, contents, _ = build_semantic_request(
                root, graph, identifiers, language, target, context)
            outcome['queryCount'] = len(request['queries'])
            add_reasons(outcome, request['scope']['reasons'])
            if (len(set(all_contents) | set(contents)) > 16 or
                    sum(len(value) for value in {**all_contents, **contents}.values()) > 4 * 1024 * 1024):
                phase = 'partial'
                add_reasons(outcome, ['source-scope-budget'], priority=True)
            elif not contents:
                phase = 'partial'
                add_reasons(outcome, ['no-source-bindings'], priority=True)
            elif not request['queries']:
                phase = 'partial'
                add_reasons(outcome, ['no-queries'], priority=True)
            else:
                future = self.runtime.submit_analysis(context, provider_id, request)
                result = self._wait(future, timeout=max(0, deadline - time.monotonic())) if future is not None else None
                if result is None:
                    self.runtime.cancel(context, [provider_id])
                    availability = self.runtime.status(context, [provider_id])[provider_id]
                    add_reasons(outcome, ['engine-analysis-no-result', availability.reason], priority=True)
                else:
                    record_result(outcome, result)
                if result is not None and result.status in {'partial', 'complete'}:
                    proof = result
                    # Unclassified compilations can report candidates,
                    # but cannot invent confirmed project ownership.
                    if not request['scope']['qualified']:
                        result = replace(result, status='partial', facts=tuple(
                            replace(fact, confidence='candidate') if fact.confidence == 'confirmed-static' else fact
                            for fact in result.facts))
                    # Binding may still reject every raw fact. Only the successful
                    # publication boundary below can promote this to applied.
                    phase = 'partial'
                elif result is not None and result.status == 'unsupported':
                    phase = 'partial'
                    result = None
                else:
                    phase = 'failed'
                    result = None
        elif identifiers and availability.status == 'checking':
            phase = 'queued'
            add_reasons(outcome, ['engine-check-pending'])
        elif identifiers:
            add_reasons(outcome, ['engine-' + availability.status, availability.reason])
        else:
            add_reasons(outcome, ['no-selected-nodes'])
        return availability, phase, result, contents, proof, outcome

    def _run(self, project_id, snapshot_id, generation, signature, requested, force):
        """
        Discover providers and publish verified selected-node facts before reporting applied.

        project_id and snapshot_id identify the stored map; generation and signature
        fence superseded work. requested is the bounded selected-node tuple; force
        requests new discovery. Returns no value. Failures become a failed public
        status with bounded reasons; cancellation/source/config/proof fences keep
        earlier immutable graph bytes untouched and never promote an unwritten delta.
        """
        key = project_id, snapshot_id, generation, requested
        deadline = time.monotonic() + MAX_OPERATION_SECONDS
        providers = []
        try:
            root, path = self._scope(project_id, snapshot_id)
            with self.store.lock:
                graph = self.store._base_snapshot(project_id, path)
            if not self._current(project_id, snapshot_id, generation, signature):
                return
            config_names = self._config_names(graph)
            signature = self._signature(root, path, config_names)
            node_index = {node['id']: node for node in graph['nodes']}
            if set(requested) - node_index.keys():
                raise StoreError('Узел отсутствует в выбранной карте.', 404)
            with self.lock:
                state = self.states.get(project_id)
                if state is None or state['generation'] != generation or self.closed:
                    return
                state['configNames'], state['signature'] = config_names, signature
                for identifier in requested:
                    node = node_index[identifier]
                    filename = node.get('file')
                    if filename and not Path(filename).is_absolute() and '..' not in Path(filename).parts:
                        state['nodeFiles'][identifier] = filename
                while len(state['nodeFiles']) > 256:
                    state['nodeFiles'].popitem(last=False)
            inventory = {'files': sorted({node.get('file') for node in graph['nodes'] if node.get('file')}),
                         'truncated': False}
            rules = active_semantic_rules(self.store.mods, inventory=inventory)
            hashes, config = captured_source_hashes(graph), self._configuration(root, config_names)
            contexts, results, proofs, all_contents = [], [], [], {}
            settings = self.settings.read()
            selected_languages = {language for language, extensions in EXTENSIONS.items()
                                  if any(Path(node_index[identifier].get('file') or '').suffix.lower() in extensions
                                         for identifier in requested)}
            declarations = sorted(rules['semanticProviders'], key=lambda item: item['language'] not in selected_languages)
            for declaration in declarations:
                if len(providers) >= 32 or time.monotonic() >= deadline:
                    break
                provider_id = declaration['modId'] + '/' + declaration['id']
                with self.runtime.lock:
                    self.runtime.registry[provider_id] = self.factory(declaration, settings.get(declaration['modId'], {}))
                cohorts, targets = self._target_groups(graph, node_index, requested, declaration['language'])
                # Automatic environment checks never fan out over an unbounded
                # monorepo. Explicit selection prioritizes its own target cohort.
                for target, identifiers in list(targets.items())[:min(16, 32 - len(providers))]:
                    if time.monotonic() >= deadline:
                        break
                    cohort_hashes = {name: hashes[name] for name in sorted(cohorts.get(target, ())) if name in hashes}
                    context = SemanticContext(snapshot_id, project_id, str(root),
                        declaration['language'] + ':' + (target or 'workspace'),
                        _digest([config, self.settings.fingerprint(declaration['modId'])]),
                        cohort_hashes, declaration['modFingerprint'])
                    contexts.append((context, provider_id))
                    with self.lock:
                        state = self.states.get(project_id)
                        if state is None or state['generation'] != generation or self.closed:
                            return
                        self.contexts[project_id] = list(contexts)
                    availability, phase, result, contents, proof, outcome = self._analyze_target(
                        context, provider_id, graph, identifiers, declaration['language'], target, all_contents, force, deadline)
                    if result is not None:
                        results.append(result)
                        proofs.append(proof)
                        all_contents.update(contents)
                    providers.append({'id': provider_id, 'language': declaration['language'],
                                      'targetId': context.target_id, 'availability': asdict(availability),
                                      'enrichment': phase, 'outcome': outcome})
            if not self._current(project_id, snapshot_id, generation, signature):
                return
            if self._configuration(root, config_names) != config:
                raise ValueError('Semantic configuration changed during analysis')
            assert_semantic_rules_current(self.store.mods, rules, inventory=inventory)
            if results:
                # Re-read selected file bytes before publishing; the engine may
                # have taken long enough for the working tree to change.
                from .semantic_sources import declaration_bindings
                verified, bindings = declaration_bindings(root, graph, all_contents, snapshot_id, hashes)
                if verified != all_contents:
                    raise ValueError('Semantic source changed during analysis')
                projection_counts = {}
                overlay = make_overlay(path, graph, root, results, bindings, verified, rules,
                                       projection_counts=projection_counts)
                with self.store.lock:
                    if not self._current(project_id, snapshot_id, generation, signature):
                        return
                    if (not self._sources_current(root, verified, hashes)
                            or self._configuration(root, config_names) != config):
                        raise ValueError('Semantic source or configuration changed before publication')
                    assert_semantic_rules_current(self.store.mods, rules, inventory=inventory)
                    if not all(self.runtime.result_is_current(proof) for proof in proofs):
                        raise ValueError('Semantic engine provenance changed before publication')
                    write_overlay(path, overlay)
                    apply_publication(providers, projection_counts)
            if not self._current(project_id, snapshot_id, generation, signature):
                return
            with self.lock:
                state = self.states.get(project_id)
                if state is None or state['generation'] != generation or self.closed:
                    return
                self.contexts[project_id] = contexts
                checking = any(item['availability']['status'] == 'checking' for item in providers)
                deadlines = [item['availability']['next_check_at'] for item in providers
                             if item['availability']['next_check_at'] is not None]
                state['nextCheck'] = self.clock() + .3 if checking else min(deadlines, default=self.clock() + 86400)
                if requested and not checking:
                    selected_stamp = tuple((identifier, file_stamp(root / state['nodeFiles'][identifier]))
                                           for identifier in requested if identifier in state['nodeFiles'])
                    state['completed'][(requested, selected_stamp)] = True
                    while len(state['completed']) > 64:
                        state['completed'].popitem(last=False)
                    state['requested'] = ()
                phases = {item['enrichment'] for item in providers}
                phase = next((value for value in ('applied', 'running', 'queued', 'failed', 'partial') if value in phases), 'baseline')
                public = {'version': 'atlas-semantic-status-v1', 'snapshotId': snapshot_id,
                          'providers': providers, 'enrichment': phase,
                          'scopeNodeIds': list(requested), 'partialScope': True,
                          'operationBudgetSeconds': MAX_OPERATION_SECONDS,
                          'updatedAt': self.clock(), 'runtimeSelectionKnown': False}
                state['public'] = public
                write_json(path.with_name('semantic-status.json'), public)
        except Exception as error:
            with self.lock:
                state = self.states.get(project_id)
                if state and state['generation'] == generation and not self.closed:
                    state['nextCheck'] = self.clock() + 86400
                    for provider in providers:
                        provider['enrichment'] = 'failed'
                        add_reasons(provider['outcome'], ['analysis-publication-failed'], priority=True)
                    state['public'] = {**state['public'], 'enrichment': 'failed',
                                       'providers': providers,
                                       'reason': 'optional-analysis-failed:' + type(error).__name__}
                    state['requested'] = ()
                    write_json(path.with_name('semantic-status.json'), state['public'])
        finally:
            with self.lock:
                self.pending.pop(key, None)

    def close(self):
        """Cancel owned callbacks and queues with a bounded runtime shutdown."""
        with self.lock:
            self.closed = True
            for contexts in self.contexts.values():
                for context, provider_id in contexts:
                    self.runtime.cancel(context, [provider_id])
        self.runtime.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
