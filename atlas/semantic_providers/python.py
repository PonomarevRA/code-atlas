"""Optional existing Jedi/Parso adapter; project Python is parsed in a staged source-only workspace."""
from hashlib import sha256
from itertools import islice
import json
import os
from pathlib import Path
import sys

from atlas.semantic_models import (SemanticAvailability, SemanticCoverage, SemanticFact,
                                   SemanticResult, SemanticSymbol, SourceSpan)
from atlas.semantic_process import SemanticProcessError, discover_executable, run_bounded
from atlas.semantic_runtime import ProbeOutcome

WORKER = Path(__file__).with_name('python_worker.py')


def _fingerprint(assets):
    """Bound the exact selected Jedi/Parso Python closure, never scanning unrelated environments."""
    paths = list(assets)
    for package in assets[2:4]:
        paths.extend(islice(package.parent.rglob('*.py'), 257))
    paths = sorted(set(paths))
    if len(paths) > 256:
        raise ValueError('Jedi implementation inventory exceeds budget')
    digest, size = sha256(), 0
    for path in paths:
        size += path.stat().st_size
        if size > 128 * 1024 * 1024:
            raise ValueError('Jedi implementation bytes exceed budget')
        digest.update(str(path).encode())
        with path.open('rb') as incoming:
            while chunk := incoming.read(65536):
                digest.update(chunk)
    return digest.hexdigest()


def _request(context, request):
    """Validate captured text and UTF-16 declaration/query identity before staging any file."""
    files, queries = request.get('files'), request.get('queries')
    if not isinstance(files, list) or not 1 <= len(files) <= 16:
        raise ValueError('Invalid Python semantic file count')
    if not isinstance(queries, list) or len(queries) > 64:
        raise ValueError('Invalid Python semantic query count')
    known, total = set(), 0
    for item in files:
        path, text, digest = item['path'], item['text'], item['sourceHash']
        SourceSpan(path, 0, 0, 0, 0)
        if path in known or not isinstance(text, str) or not path.endswith(('.py', '.pyi')):
            raise ValueError('Invalid Python semantic source')
        encoded = text.encode('utf-8')
        total += len(encoded)
        if len(encoded) > 1024 * 1024 or total > 3 * 1024 * 1024:
            raise ValueError('Python semantic source exceeds budget')
        hashes = {sha256(encoded).hexdigest(), sha256(b'\xef\xbb\xbf' + encoded).hexdigest()}
        if digest not in hashes or context.source_hashes.get(path) != digest:
            raise ValueError('Python semantic source is stale')
        known.add(path)
    for query in queries:
        source, site = SourceSpan(**query['sourceSpan']), SourceSpan(**query['callsite'])
        if query.get('kind') not in {'call', 'reference'} or query.get('path') not in known:
            raise ValueError('Invalid Python semantic query')
        if source.path != query['path'] or site.path != source.path:
            raise ValueError('Mismatched Python semantic source')
        if source.coordinate_kind != 'utf-16' or site.coordinate_kind != 'utf-16':
            raise ValueError('Expected Python query UTF-16 coordinates')
        if (site.start_line, site.start_column) < (source.start_line, source.start_column):
            raise ValueError('Python query starts outside its declaration')
        if (site.end_line, site.end_column) > (source.end_line, source.end_column):
            raise ValueError('Python query ends outside its declaration')
        for name in ('line', 'column'):
            if type(query.get(name)) is not int or query[name] < 0:
                raise ValueError('Invalid Python query coordinate')
        if not isinstance(query.get('sourceNodeId'), str) or not query['sourceNodeId']:
            raise ValueError('Invalid Python semantic source identity')
    scope = request.get('scope') or {}
    reasons = scope.get('reasons', []) if isinstance(scope, dict) else []
    if not isinstance(reasons, list) or len(reasons) > 32:
        raise ValueError('Invalid Python semantic scope')
    return {'files': files, 'queries': queries, 'reasons': [str(item)[:512] for item in reasons]}


class JediProvider:
    """Use the selected installed Python/Jedi; importing this factory does not import Jedi."""

    def __init__(self, provider_id, *, python_path=None):
        """Keep user/tool selection explicit, with no pip or automatic environment modification."""
        self.provider_id, self.python_path = provider_id, python_path

    def probe(self, context, cancel):
        """Isolated probe imports only trusted installed analyzer packages, outside source sys.path."""
        root = Path(context.workspace_root).resolve()
        explicit = self.python_path or os.environ.get('ATLAS_JEDI_PYTHON')
        candidates = [root / '.venv/bin/python', root / 'venv/bin/python',
                      root / '.venv/Scripts/python.exe', root / 'venv/Scripts/python.exe']
        if not getattr(sys, 'frozen', False):
            candidates.append(Path(sys.executable))
        system_python = discover_executable('python3') or discover_executable('python')
        if system_python:
            candidates.append(system_python)
        choices = [explicit] if explicit else candidates
        selected = []
        for candidate in choices:
            python = discover_executable('python3', explicit=candidate)
            if python is not None and python not in selected:
                selected.append(python)
        if not selected:
            return ProbeOutcome(SemanticAvailability('missing', 'python-not-found'))
        for python in selected:
            outcome = self._probe_python(python, root, cancel)
            if outcome.availability.status != 'missing' or explicit:
                return outcome
        return ProbeOutcome(SemanticAvailability('missing', 'jedi-not-installed'))

    def _probe_python(self, python, root, cancel):
        """Probe a single known interpreter; auto discovery may continue past absent Jedi."""
        try:
            process = run_bounded([str(python), '-I', str(WORKER), '--probe'], cwd=root,
                                  cancel=cancel, timeout=5, max_output_bytes=16384)
            info = json.loads(process.stdout)
            if info.get('status') == 'missing':
                return ProbeOutcome(SemanticAvailability('missing', 'jedi-not-installed'))
            if process.returncode or info.get('status') != 'supported':
                return ProbeOutcome(SemanticAvailability('incompatible', 'unsupported-jedi-api'))
            assets = (python, WORKER.resolve(), Path(info['jedi_path']), Path(info['parso_path']))
            fingerprint = _fingerprint(assets)
            return ProbeOutcome(SemanticAvailability('available', 'staged-pinned-source-only',
                engine_version=info['version'], engine_fingerprint=fingerprint,
                capabilities=('definition',)), assets)
        except (OSError, ValueError, KeyError, SemanticProcessError) as error:
            reason = error.reason if isinstance(error, SemanticProcessError) else 'invalid-jedi-probe'
            return ProbeOutcome(SemanticAvailability('failed', reason))

    def analyze(self, context, probe, request, cancel):
        """Resolve declared definitions with source-only imports; never evaluate application modules."""
        payload = _request(context, request)
        if probe.availability.status != 'available' or len(probe.assets) != 4:
            raise ValueError('Jedi engine unavailable')
        if _fingerprint(probe.assets) != probe.availability.engine_fingerprint:
            raise ValueError('Jedi engine changed')
        process = run_bounded([str(probe.assets[0]), '-I', str(probe.assets[1])], cwd=context.workspace_root,
            cancel=cancel, timeout=20, max_output_bytes=1024 * 1024,
            input_bytes=json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        if process.returncode or _fingerprint(probe.assets) != probe.availability.engine_fingerprint:
            raise SemanticProcessError('jedi-analysis-failed-or-changed')
        response = json.loads(process.stdout)
        if len(response.get('results', [])) != len(payload['queries']):
            raise ValueError('Invalid Jedi worker response')
        facts = []
        for query, row in zip(payload['queries'], response['results']):
            source = SemanticSymbol(query['sourceNodeId'], 'method', query.get('label', query['sourceNodeId']),
                                    SourceSpan(**query['sourceSpan']), graph_node_id=query['sourceNodeId'])
            targets = row.get('targets', [])
            if not isinstance(targets, list) or len(targets) > 8:
                raise ValueError('Invalid Jedi definition count')
            if not targets:
                facts.append(SemanticFact(query['kind'], source, None, 'unknown',
                                          SourceSpan(**query['callsite']), row.get('reason', 'unresolved')))
            for value in targets:
                target = SemanticSymbol(value['symbol_id'], value['kind'], value['label'],
                                        SourceSpan(**value['source_span']))
                # A second external/filtered definition still prevents a unique static binding.
                confidence = 'candidate' if len(targets) > 1 or row.get('ambiguous') else 'confirmed-static'
                facts.append(SemanticFact('reference', source, target, confidence,
                    SourceSpan(**query['callsite']), 'jedi-definition-only; runtime-call-target-unknown'))
        reasons = tuple(response.get('reasons', [])[:32])
        coverage = {'definition': SemanticCoverage('partial', len(payload['queries']), None, reasons)}
        return SemanticResult(context, self.provider_id, context.provider_fingerprint or sha256(b'jedi-local-v1').hexdigest(),
                              probe.availability.engine_fingerprint, facts=tuple(facts), coverage=coverage,
                              diagnostics=reasons)
