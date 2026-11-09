"""Common captured-source LSP lifecycle; each language owns its static context policy."""
import os
from dataclasses import replace
from pathlib import Path
import tempfile

from ..semantic_lsp import LspSession
from ..semantic_lsp_providers import _definition_facts, _fingerprint, _request_sources
from ..semantic_models import SemanticAvailability, SemanticCoverage, SemanticResult
from ..semantic_process import SemanticProcessError, discover_executable, run_bounded
from ..semantic_runtime import ProbeOutcome


def callable_targets(contents):
    """Index bounded original declaration names; containing functions are not variable targets."""
    from ..method_flow_chunks import _parse, DECLARATIONS
    from ..semantic_sources import SourceCoordinates, _name_node
    names, coordinates, count = {}, {}, 0
    for path, text in contents.items():
        content = text.encode('utf-8')
        tree, _ = _parse(content, path)
        coordinates[path] = SourceCoordinates(content)
        names[path] = []
        pending = [tree.root_node] if tree is not None else []
        while pending and count < 20000:
            node = pending.pop()
            count += 1
            if node.type in DECLARATIONS:
                name = _name_node(node)
                if name is not None:
                    names[path].append((name.start_byte, name.end_byte))
            pending.extend(reversed(node.named_children))

    def selected(target):
        """Require a compiler-selected range inside the declaration's own name."""
        span = target.source_span
        if span is None or span.path not in coordinates:
            return False
        start, end = coordinates[span.path].span_bytes(span)
        return any(left <= start and end <= right for left, right in names[span.path])

    return selected


def read_context(path, maximum=256 * 1024):
    """Read bounded static metadata; no importers, shell fragments or build tasks are evaluated."""
    with path.open('rb') as handle:
        payload = handle.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError('Language context exceeds budget')
    return payload


class CapturedLspProvider:
    """A one-shot LSP backend with explicit presence, context and compatibility gates."""
    language = ''
    binary = ''
    version_flags = ('--version',)
    known_locations = ()
    settings = {}

    def __init__(self, provider_id, *, engine_path=None):
        """Keep user engine overrides host-local; a broken explicit path has no fallback."""
        self.provider_id, self.engine_path = provider_id, engine_path

    def candidates(self):
        """Use application-owned known locations and PATH, without recursive SDK discovery."""
        if self.engine_path:
            path = discover_executable(self.binary, explicit=self.engine_path)
            return [path] if path else []
        paths = [discover_executable(self.binary)]
        for candidate in self.known_locations:
            path = Path(candidate).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                paths.append(path.absolute())
        return list(dict.fromkeys(path for path in paths if path))

    def context_assets(self, root):
        """Subclasses require static local context and return a bounded file inventory."""
        raise NotImplementedError

    def version_supported(self, text):
        """Subclasses recognize their actual engine banner, not an arbitrary executable."""
        raise NotImplementedError

    def prepare_context(self, root, workspace, contents):
        """Create only static metadata for pinned files; never copy analyzed build scripts."""
        raise NotImplementedError

    def command(self, engine, workspace):
        """The default LSP transport is stdio; no user-controlled command string is accepted."""
        return [str(engine)]

    def environment(self, workspace):
        """Keep engine caches/configs private and disable Go toolchain/package-driver downloads."""
        env = dict(os.environ)
        env.update({'HOME': str(workspace), 'XDG_CONFIG_HOME': str(workspace / '.config'),
                    'XDG_CACHE_HOME': str(workspace / '.cache'), 'GOTOOLCHAIN': 'local',
                    'GOPROXY': 'off', 'GOSUMDB': 'off', 'GOWORK': 'off',
                    'GOPACKAGESDRIVER': 'off', 'CGO_ENABLED': '0'})
        return env

    def initialization_options(self):
        """Most servers accept their read-only settings directly in initializationOptions."""
        return self.settings

    def probe_version(self, engine, root, cancel):
        """Run only the fixed engine version operation; profiles may use a safe LSP handshake."""
        with tempfile.TemporaryDirectory(prefix='atlas-version-') as directory:
            workspace = Path(directory)
            result = run_bounded([str(engine), *self.version_flags], cwd=workspace, cancel=cancel,
                timeout=3, max_output_bytes=16384,
                environment={**self.environment(workspace), 'RUSTUP_AUTO_INSTALL': '0'})
        if result.returncode:
            raise ValueError('Engine version unavailable')
        return (result.stdout + result.stderr).decode('utf-8', errors='replace').strip()

    def probe(self, context, cancel):
        """Discover installed engines and static metadata without indexing source or installing tools."""
        root = Path(context.workspace_root)
        candidates = self.candidates()
        if not candidates:
            return ProbeOutcome(SemanticAvailability('missing', f'{self.binary}-not-installed'))
        try:
            context_assets = tuple(self.context_assets(root))
            if len(context_assets) > 8:
                raise ValueError('Too many context assets')
        except (OSError, ValueError, KeyError, TypeError, SemanticProcessError):
            return ProbeOutcome(SemanticAvailability('incompatible', 'context-missing-or-unsupported'))
        for engine in candidates[:3]:
            try:
                version = self.probe_version(engine, root, cancel)
                if not self.version_supported(version):
                    continue
                from importlib import import_module
                profile = Path(import_module(type(self).__module__).__file__).resolve()
                assets = (engine, *context_assets, Path(__file__).resolve(), profile,
                          Path(__file__).resolve().parents[1] / 'semantic_lsp.py',
                          Path(__file__).resolve().parents[1] / 'semantic_lsp_providers.py')
                return ProbeOutcome(SemanticAvailability('available', 'native-workspace-not-qualified-until-analysis',
                    engine_version=version[:256], engine_fingerprint=_fingerprint(assets, version),
                    capabilities=('definition', 'calls')), assets)
            except (SemanticProcessError, OSError, ValueError):
                continue
        return ProbeOutcome(SemanticAvailability('incompatible', 'engine-version-or-runtime-unavailable'))

    def analyze(self, context, probe, request, cancel):
        """Resolve selected positions in an isolated captured workspace, with explicit partial coverage."""
        contents, queries = _request_sources(context, request)
        with tempfile.TemporaryDirectory(prefix=f'atlas-{self.language}-') as directory:
            workspace = Path(directory)
            for path, text in contents.items():
                destination = workspace / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(text, encoding='utf-8')
            reasons = self.prepare_context(Path(context.workspace_root), workspace, contents)
            with LspSession(self.command(probe.assets[0], workspace), workspace, cancel,
                            settings=self.settings, environment=self.environment(workspace)) as session:
                session.initialize(workspace, self.initialization_options())
                session.notify('workspace/didChangeConfiguration', {'settings': self.settings})
                for path, text in contents.items():
                    session.notify('textDocument/didOpen', {'textDocument': {'uri': (workspace / path).as_uri(),
                        'languageId': self.language, 'version': 1, 'text': text}})
                facts, resolved, failures = _definition_facts(session, workspace, context,
                    self.provider_id, contents, queries, callable_target=callable_targets(contents))
            # Isolated source-only workspaces omit dependencies that can change overload/type
            # selection. A single LSP location is useful evidence, but is not closure proof.
            facts = tuple(replace(fact, confidence='candidate',
                                  evidence='project-and-reference-closure-not-proven')
                          if fact.confidence == 'confirmed-static' else fact for fact in facts)
            limitations = tuple(sorted(set((*reasons, *failures, 'selected-pinned-scope-only',
                                           'project-and-reference-closure-not-proven'))))
            coverage = {'definition': SemanticCoverage('partial', 0, len(queries), limitations),
                'calls': SemanticCoverage('partial',
                    sum(fact.kind == 'call' and fact.confidence == 'confirmed-static' for fact in facts),
                    sum(query.get('kind') == 'call' for query in queries), limitations)}
            return SemanticResult(context, self.provider_id, context.provider_fingerprint,
                probe.availability.engine_fingerprint, 'partial', facts, coverage, limitations[:16])
