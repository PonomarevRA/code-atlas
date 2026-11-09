"""Optional local SDK Roslyn bridge; metadata binding never builds or executes the analyzed project."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from threading import Event

from ..runtime_paths import resource_root
from ..source_viewer import SourceError
from ..semantic_models import (SemanticAvailability, SemanticCoverage, SemanticFact,
                               SemanticResult, SemanticSymbol, SourceSpan)
from ..semantic_process import SemanticProcessError, run_bounded
from ..semantic_runtime import ProbeOutcome
from .roslyn_context import ContextUnavailable, configuration_fingerprint, resolve_configuration

PROTOCOL = 'atlas-roslyn-v1'
MAX_FILES = 16
MAX_QUERIES = 64
MAX_SOURCE_BYTES = 4 * 1024 * 1024


def _span(value):
    """Accept the shared DTO spelling and the worker's equivalent UTF-16 query spelling."""
    if 'start_line' in value:
        span = SourceSpan(**value)
    else:
        span = SourceSpan(value['path'], value['line'], value['column'], value['endLine'], value['endColumn'])
    if span.coordinate_kind != 'utf-16':
        raise ValueError('Roslyn requests require explicit UTF-16 coordinates')
    return span


def _worker_span(span):
    """Keep the worker protocol small and independent of Python dataclass naming conventions."""
    return {'path': span.path, 'line': span.start_line, 'column': span.start_column,
            'endLine': span.end_line, 'endColumn': span.end_column}


class RoslynProvider:
    """Reuse an installed compiler and references with a tiny host-built framework-dependent bridge."""

    def __init__(self, provider_id, *, dotnet=None, worker_path=None, sdk_root=None,
                 reference_paths=(), target_framework=None, defines=None, language_version=None):
        """Configure trusted host options; repository manifests cannot select executable commands."""
        self.provider_id = provider_id
        self.worker_path = Path(worker_path).resolve() if worker_path else None
        self.options = {'dotnet': dotnet, 'sdk_root': sdk_root, 'target_framework': target_framework,
                        'reference_paths': tuple(reference_paths), 'defines': defines,
                        'language_version': language_version}

    def _worker(self):
        """Look only in known Atlas build/package paths; never compile on ordinary map access."""
        if self.worker_path:
            return self.worker_path
        base = resource_root()
        candidates = (base / 'resources/semantic-csharp/Atlas.Semantic.Roslyn.dll',
                      base / 'build/semantic-csharp/Atlas.Semantic.Roslyn.dll')
        return next((path for path in candidates if path.is_file()), candidates[0])

    def probe(self, context, cancel):
        """Require a real compatible bridge/compiler/reference context; dotnet presence alone is insufficient."""
        worker = self._worker()
        if not worker.is_file() or not worker.with_suffix('.runtimeconfig.json').is_file():
            return ProbeOutcome(SemanticAvailability('incompatible', 'atlas-roslyn-bridge-missing'))
        try:
            configuration = resolve_configuration(context, **self.options)
            output = run_bounded([str(configuration.dotnet), str(worker), '--roslyn',
                                  str(configuration.sdk / 'Roslyn/bincore'), '--probe'],
                                 cwd=worker.parent, timeout=5, max_output_bytes=8192, cancel=cancel,
                                 input_bytes=json.dumps({'languageVersion': configuration.language_version,
                                     'references': [str(path) for path in configuration.references]}).encode())
            if output.returncode != 0:
                raise ContextUnavailable('compiler-runtime-incompatible')
            payload = json.loads(output.stdout)
            if payload.get('protocol') != PROTOCOL or not isinstance(payload.get('version'), str):
                raise ContextUnavailable('bridge-protocol-incompatible')
            fingerprint = configuration_fingerprint(configuration, worker)
            paths = (configuration.dotnet, worker, worker.with_suffix('.runtimeconfig.json'),
                     configuration.sdk / 'Roslyn/bincore/Microsoft.CodeAnalysis.dll',
                     configuration.sdk / 'Roslyn/bincore/Microsoft.CodeAnalysis.CSharp.dll',
                     *configuration.context_files)
            return ProbeOutcome(SemanticAvailability('available', 'pinned-source-cohort-only',
                engine_version=payload['version'], engine_fingerprint=fingerprint,
                capabilities=('definition', 'calls')), tuple(paths))
        except FileNotFoundError:
            return ProbeOutcome(SemanticAvailability('missing', 'dotnet-or-local-compiler-missing'))
        except (ContextUnavailable, OSError, ValueError, SemanticProcessError, SourceError) as error:
            reason = str(error) if isinstance(error, (ContextUnavailable, SemanticProcessError)) else 'invalid-local-context'
            return ProbeOutcome(SemanticAvailability('incompatible', reason[:200]))

    def _request(self, context, request):
        """Validate pinned exact source text and bounded query identities before starting the compiler."""
        files, queries = request.get('files'), request.get('queries')
        if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES or not isinstance(queries, list) or len(queries) > MAX_QUERIES:
            raise ValueError('Roslyn request inventory budget exceeded')
        texts, total = {}, 0
        for file in files:
            path, text, digest = file['path'], file['text'], file['sourceHash']
            if not isinstance(text, str) or path in texts or Path(path).suffix.lower() != '.cs':
                raise ValueError('Invalid Roslyn source cohort')
            SourceSpan(path, 0, 0, 0, 0)
            if context.source_hashes.get(path) != digest:
                raise ValueError('Roslyn source hash is not pinned by context')
            encoded = text.encode('utf-8')
            # The safe reader strips a UTF-8 BOM; its original byte hash remains the context anchor.
            if digest not in {sha256(encoded).hexdigest(), sha256(b'\xef\xbb\xbf' + encoded).hexdigest()}:
                raise ValueError('Roslyn source payload changed after capture')
            total += len(encoded)
            if total > MAX_SOURCE_BYTES:
                raise ValueError('Roslyn source byte budget exceeded')
            texts[path] = text
        normalized = []
        for query in queries:
            path = query['path']
            if path not in texts:
                raise ValueError('Roslyn query source is not pinned')
            site = _span(query.get('callsite') or query)
            owner = _span(query['sourceSpan'])
            if owner.path != path or site.path != path:
                raise ValueError('Roslyn query has inconsistent source paths')
            normalized.append({**_worker_span(site), 'sourceSpan': _worker_span(owner)})
        if set(texts) != {path for path in context.source_hashes if Path(path).suffix.lower() == '.cs'}:
            raise ValueError('Roslyn request must include its entire pinned source cohort')
        return files, queries, normalized

    def analyze(self, context, probe, request, cancel):
        """Resolve exact captured queries; missing symbols and partial compilation remain explicitly unknown."""
        files, queries, normalized = self._request(context, request)
        configuration = resolve_configuration(context, **self.options)
        worker = self._worker()
        if configuration_fingerprint(configuration, worker) != probe.availability.engine_fingerprint:
            raise ValueError('Roslyn context or compiler changed after discovery')
        if self.options['defines'] is None and any('#if' in item['text'] or '#elif' in item['text'] for item in files):
            return self._result(context, probe, (), len(queries), 0,
                                ('conditional-compilation-symbols-not-explicit',))
        payload = {'protocol': PROTOCOL, 'files': files, 'queries': normalized,
                   'references': [str(path) for path in configuration.references],
                   'languageVersion': configuration.language_version, 'defines': configuration.defines}
        output = run_bounded([str(configuration.dotnet), str(worker), '--roslyn',
                              str(configuration.sdk / 'Roslyn/bincore')], cwd=worker.parent,
                             timeout=45, max_output_bytes=1024 * 1024, cancel=cancel,
                             input_bytes=json.dumps(payload, ensure_ascii=False).encode(),
                             memory_bytes=768 * 1024 * 1024)
        if output.returncode != 0:
            raise SemanticProcessError('roslyn-analysis-failed')
        response = json.loads(output.stdout)
        if response.get('protocol') != PROTOCOL or len(response.get('results', [])) != len(queries):
            raise ValueError('Roslyn worker result does not match captured query count')
        after = resolve_configuration(context, **self.options)
        if (configuration_fingerprint(after, worker) != probe.availability.engine_fingerprint
                or after.binding_reasons != configuration.binding_reasons):
            raise ValueError('Roslyn context changed during analysis')
        reasons = list(configuration.binding_reasons)
        if response.get('compilationHasErrors') is not False:
            reasons.append('compilation-has-errors-or-incomplete-diagnostics')
        binding_unproven = bool(reasons)
        facts, resolved = [], 0
        for query, result in zip(queries, response['results']):
            if result['status'] == 'language-construct':
                resolved += 1
                reasons.append('nameof-excluded-from-calls')
                continue
            source = SemanticSymbol(query['sourceNodeId'], 'method', str(query.get('label') or 'selected declaration')[:400],
                                    _span(query['sourceSpan']))
            site = _span(query.get('callsite') or query)
            target = None
            confidence = 'unknown'
            evidence = result.get('reason', 'unresolved-project-or-reference')
            if result['status'] == 'confirmed-static':
                location = result.get('location')
                target_span = _span(location) if location is not None and location['path'] in context.source_hashes else None
                target = SemanticSymbol(result['symbolId'], result['kind'], result['label'], target_span,
                    uri='assembly:' + str(result.get('assembly') or 'unknown') if result['origin'] == 'metadata' else None)
                confidence = 'candidate' if binding_unproven else 'confirmed-static'
                if confidence == 'confirmed-static':
                    resolved += 1
                evidence = json.dumps({key: result.get(key) for key in ('origin', 'assembly', 'receiverType', 'extensionMethod')})
            else:
                reasons.append(evidence)
            facts.append(SemanticFact('call' if query.get('kind') == 'call' else 'reference', source, target,
                                      confidence, site, evidence[:1000]))
        return self._result(context, probe, facts, len(queries), resolved, tuple(dict.fromkeys(reasons))[:16])

    def _result(self, context, probe, facts, total, resolved, reasons):
        """Keep source-cohort coverage partial even when every requested query binds successfully."""
        scope = ('bounded captured queries; source closure and project-context proof govern confidence; no runtime dispatch claim',)
        return SemanticResult(context, self.provider_id, context.provider_fingerprint,
            probe.availability.engine_fingerprint, 'partial', tuple(facts),
            {'definition': SemanticCoverage('partial', resolved, total, reasons=scope + reasons)},
            diagnostics=scope)
