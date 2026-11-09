"""Optional TS5 Compiler API adapter shared by independent JavaScript/TypeScript mods."""
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Event

from atlas.semantic_models import (SemanticAvailability, SemanticContext, SemanticCoverage,
                                   SemanticFact, SemanticResult, SemanticSymbol, SourceSpan)
from atlas.semantic_process import SemanticProcessError, discover_node_executable, run_bounded
from atlas.semantic_runtime import ProbeOutcome

WORKER = Path(__file__).with_name('typescript_worker.cjs')
CAPABILITIES = ('definition', 'calls')
MAX_FILE_BYTES = 1024 * 1024


def _digest_assets(assets):
    """Fingerprint the actual trusted compiler/worker files once per discovery attempt."""
    digest = sha256()
    for path in assets:
        digest.update(str(path).encode())
        with path.open('rb') as incoming:
            while chunk := incoming.read(65536):
                digest.update(chunk)
    return digest.hexdigest()


def _regular_asset(path):
    """Only exact bounded engine files participate; discovery never recursively scans installs."""
    path = Path(path).expanduser()
    if not path.is_absolute():
        return None
    try:
        return path.resolve() if path.is_file() and path.stat().st_size <= 128 * 1024 * 1024 else None
    except OSError:
        return None


def _configuration(root):
    """Select one local JSON configuration, never evaluating project scripts or nested configs."""
    for name in ('tsconfig.json', 'jsconfig.json'):
        path = root / name
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            if resolved.is_file() and resolved.stat().st_size <= 256 * 1024:
                return resolved
        except (OSError, ValueError):
            continue
    return None


def _checked_request(context, request):
    """Require bounded captured UTF-8 text and validated UTF-16 graph/query anchors."""
    files, queries = request.get('files'), request.get('queries')
    if not isinstance(files, list) or not 1 <= len(files) <= 16:
        raise ValueError('Invalid semantic source files')
    if not isinstance(queries, list) or len(queries) > 64:
        raise ValueError('Invalid semantic query count')
    known, total = {}, 0
    for item in files:
        path, text, source_hash = item.get('path'), item.get('text'), item.get('sourceHash')
        SourceSpan(path, 0, 0, 0, 0)
        if path in known or not isinstance(text, str):
            raise ValueError('Invalid semantic source text')
        encoded = text.encode('utf-8')
        total += len(encoded)
        if len(encoded) > MAX_FILE_BYTES or total > 3 * MAX_FILE_BYTES:
            raise ValueError('Semantic source text exceeds budget')
        digest = sha256(encoded).hexdigest()
        # The common safe reader removes UTF-8 BOM while retaining the original-byte hash.
        if digest != source_hash:
            digest = sha256(b'\xef\xbb\xbf' + encoded).hexdigest()
        if digest != source_hash or context.source_hashes.get(path) != source_hash:
            raise ValueError('Semantic source text is stale')
        known[path] = item
    checked = []
    for query in queries:
        if query.get('path') not in known or query.get('kind') not in {'call', 'reference'}:
            raise ValueError('Invalid semantic query source')
        source = SourceSpan(**query['sourceSpan'])
        callsite = SourceSpan(**query['callsite'])
        if source.path != query['path'] or callsite.path != source.path:
            raise ValueError('Mismatched semantic query source')
        if source.coordinate_kind != 'utf-16' or callsite.coordinate_kind != 'utf-16':
            raise ValueError('Semantic worker expects UTF-16 coordinates')
        if (callsite.start_line, callsite.start_column) < (source.start_line, source.start_column):
            raise ValueError('Semantic query starts outside its declaration')
        if (callsite.end_line, callsite.end_column) > (source.end_line, source.end_column):
            raise ValueError('Semantic query ends outside its declaration')
        identifier = query.get('sourceNodeId')
        label = query.get('label', identifier)
        if not isinstance(identifier, str) or not identifier or not isinstance(label, str):
            raise ValueError('Invalid semantic source identity')
        checked.append({**query, 'sourceSpan': source.__dict__, 'callsite': callsite.__dict__})
    scope = request.get('scope') or {}
    reasons = scope.get('reasons', []) if isinstance(scope, dict) else []
    if not isinstance(reasons, list) or len(reasons) > 32:
        raise ValueError('Invalid semantic scope reasons')
    if any(not isinstance(reason, str) or len(reason) > 512 for reason in reasons):
        raise ValueError('Invalid semantic scope reason')
    return {'files': files, 'queries': checked, 'scope': {'reasons': reasons}}


class TypeScriptProvider:
    """One-shot bounded processes; both profiles share this backend without requiring each other."""

    def __init__(self, provider_id, language='typescript', *, engine_path=None, node_path=None):
        """Explicit host paths override known project/global locations; nothing is installed."""
        if language not in {'typescript', 'javascript'}:
            raise ValueError('Unsupported TypeScript backend profile')
        self.provider_id, self.language = provider_id, language
        self.engine_path, self.node_path = engine_path, node_path

    def probe(self, context: SemanticContext, cancel: Event) -> ProbeOutcome:
        """Check TS5 API compatibility and exact engine assets without indexing project files."""
        root = Path(context.workspace_root).resolve()
        node = discover_node_executable(explicit=self.node_path)
        if node is None:
            return ProbeOutcome(SemanticAvailability('missing', 'node-not-found'))
        explicit = self.engine_path or os.environ.get('ATLAS_TYPESCRIPT_PATH')
        candidates = [explicit] if explicit else [root / 'node_modules/typescript/lib/typescript.js',
            node.parent.parent / 'lib/node_modules/typescript/lib/typescript.js']
        engine = None
        for candidate in candidates:
            engine = _regular_asset(candidate)
            if engine is not None:
                break
        if engine is None:
            return ProbeOutcome(SemanticAvailability('missing', 'typescript-not-found'))
        configuration = _configuration(root)
        assets = [node, engine, WORKER.resolve()]
        package = _regular_asset(engine.parent.parent / 'package.json')
        if package:
            assets.append(package)
        if configuration:
            assets.append(configuration)
        try:
            process = run_bounded([str(node), str(WORKER), str(engine), '--probe'], cwd=root,
                                  timeout=5, max_output_bytes=16384, cancel=cancel)
            info = json.loads(process.stdout)
            if process.returncode or info.get('supported') is not True:
                return ProbeOutcome(SemanticAvailability('incompatible', 'unsupported-typescript-api'))
            fingerprint = _digest_assets(assets)
            return ProbeOutcome(SemanticAvailability('available', 'pinned-source-context-only',
                engine_version=info['version'], engine_fingerprint=fingerprint,
                capabilities=CAPABILITIES), tuple(assets))
        except (OSError, ValueError, KeyError, SemanticProcessError) as error:
            reason = error.reason if isinstance(error, SemanticProcessError) else 'invalid-typescript-probe'
            return ProbeOutcome(SemanticAvailability('failed', reason))

    def analyze(self, context, probe, request, cancel):
        """Resolve only captured virtual source; ambient libraries/build references stay partial."""
        checked = _checked_request(context, request)
        if probe.availability.status != 'available' or len(probe.assets) < 3:
            raise ValueError('TypeScript engine unavailable')
        node, engine, worker = probe.assets[:3]
        configuration = next((path for path in probe.assets[3:] if path.name in {'tsconfig.json', 'jsconfig.json'}), None)
        payload = {**checked, 'root': context.workspace_root, 'language': self.language}
        if configuration:
            payload['configuration'] = str(configuration)
        process = run_bounded([str(node), str(worker), str(engine)], cwd=context.workspace_root,
            timeout=20, max_output_bytes=1024 * 1024, cancel=cancel,
            input_bytes=json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        if process.returncode:
            raise SemanticProcessError('typescript-analysis-failed')
        response = json.loads(process.stdout)
        rows = response.get('results')
        if not isinstance(rows, list) or len(rows) != len(checked['queries']):
            raise ValueError('Invalid TypeScript worker response')
        facts = []
        for query, row in zip(checked['queries'], rows):
            source = SemanticSymbol(query['sourceNodeId'], 'method', query.get('label', query['sourceNodeId']),
                                    SourceSpan(**query['sourceSpan']), graph_node_id=query['sourceNodeId'])
            target = None
            if row.get('target') is not None:
                value = row['target']
                target = SemanticSymbol(value['symbol_id'], value['kind'], value['label'],
                                        SourceSpan(**value['source_span']))
            confidence = row.get('confidence', 'unknown')
            reason = row.get('reason', '')
            # Defense in depth: no worker/request may assert project closure in this bounded adapter.
            if confidence == 'confirmed-static':
                confidence, reason = 'candidate', 'complete-project-reference-closure-unproven'
            facts.append(SemanticFact(query['kind'], source, target, confidence,
                                      SourceSpan(**query['callsite']), reason))
        count = len(facts)
        reasons = tuple(response.get('reasons', [])[:16])
        coverage = {name: SemanticCoverage('partial', count, None, reasons) for name in CAPABILITIES}
        return SemanticResult(context, self.provider_id, context.provider_fingerprint or sha256(b'typescript-local-v1').hexdigest(),
                              probe.availability.engine_fingerprint, facts=tuple(facts), coverage=coverage,
                              diagnostics=reasons)
