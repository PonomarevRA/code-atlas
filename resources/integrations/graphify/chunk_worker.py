"""
Run bounded Graphify extraction batches before its authoritative global pass.

This script intentionally has no Atlas imports: the independent provider Python
executes the same reviewed code as the embedded runtime. Graphify's final JSON
omits resolver intermediates, so final chunk JSON files must never be concatenated.
"""

import argparse
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import runpy
import sys
import time


VERSION = 'atlas-graphify-chunks-v1'
SUPPORTED_PROVIDER = '0.9.85'
DEFAULT_MAX_FILES = 32
DEFAULT_TARGET_BYTES = 2 * 1024 * 1024
PERSISTENT_LOCAL_SUFFIXES = {'.py', '.cs'}


def _file_hash(path):
    """
    Hash a complete source with bounded buffers; unreadable inputs fail the job.
    """
    digest = sha256()
    with path.open('rb') as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def plan_chunks(paths, root, *, max_files=DEFAULT_MAX_FILES, target_bytes=DEFAULT_TARGET_BYTES):
    """
    Partition whole files in provider order without changing their project root.

    A single large file remains atomic and is reported as oversized. The byte
    target bounds ordinary batches, not native parser RSS or the global index.
    Returns batches and a source identity for revalidation after resolution.
    """
    if type(max_files) is not int or not 1 <= max_files <= 1024:
        raise ValueError('max_files must be an integer between 1 and 1024')
    if type(target_bytes) is not int or not 1 <= target_bytes <= 64 * 1024 * 1024:
        raise ValueError('target_bytes must be between 1 and 67108864')
    root = Path(root).resolve()
    batches, batch, size, identities = [], [], 0, []
    for value in paths:
        path = Path(value)
        resolved = path.resolve()
        relative = resolved.relative_to(root).as_posix()
        if path.is_symlink() or not path.is_file():
            raise ValueError('Chunk source must be a regular file: ' + relative)
        source_size = path.stat().st_size
        if batch and (len(batch) >= max_files or size + source_size > target_bytes):
            batches.append(batch)
            batch, size = [], 0
        identity = {'file': relative, 'bytes': source_size, 'sha256': _file_hash(path)}
        batch.append(path)
        identities.append(identity)
        size += source_size
    if batch:
        batches.append(batch)
    return batches, identities


def _chunk_record(paths, root, identities):
    """
    Describe one batch using source identities; no source text enters metadata.
    """
    files, records = [], []
    for path in paths:
        relative = path.resolve().relative_to(root).as_posix()
        files.append(relative)
        records.append(identities[relative])
    encoded = json.dumps(records, sort_keys=True, separators=(',', ':')).encode()
    return {'id': sha256(encoded).hexdigest(), 'files': files,
            'bytes': sum(record['bytes'] for record in records)}


def _support(filename):
    """
    Load a fixed reviewed sibling script in either provider interpreter.
    """
    return runpy.run_path(str(Path(__file__).with_name(filename)))


def _persistent_tier(project, output, records, configuration, cache_module):
    """
    Open a bounded compressed namespace without making cache availability mandatory.

    Args:
        project: Canonical source root.
        output: Private extraction directory outside source.
        records: Planned relative file identities.
        configuration: Captured resolver fingerprint.
        cache_module: Pinned vendor cache key implementation.
    Returns:
        Compressed cache instance or an explicit disabled-cache reason.
    """
    try:
        support = _support('provider_cache.py')
        namespace = {'root': str(project), 'configuration': configuration['fingerprint'],
                     'classificationPolicy': 'atlas-file-isolation-v1',
                     'vendorNamespace': cache_module.cache_dir(output, 'ast').name,
                     'files': sorted(record['file'] for record in records)}
        return support['ProviderCache'](output.parent / '_provider-cache',
                                        json.dumps(namespace, sort_keys=True))
    except (OSError, ValueError) as error:
        return {'enabled': False, 'reason': type(error).__name__}


def _private_entries(paths, root, output, cache_module, bypass_suffixes):
    """
    Select only live-file vendor keys; no untrusted cache directory is copied wholesale.
    """
    folder = cache_module.cache_dir(output, 'ast')
    entries = []
    for path in paths:
        if Path(path).suffix in bypass_suffixes or Path(path).suffix.lower() not in PERSISTENT_LOCAL_SUFFIXES:
            continue
        key = cache_module.file_hash(path, root, cache_root=output)
        entries.append((key, folder / (key + '.json')))
    return entries


def run_chunked(project, output, *, max_files=DEFAULT_MAX_FILES, target_bytes=DEFAULT_TARGET_BYTES):
    """
    Warm per-file intermediates in batches, then use unmodified global CLI logic.

    Graphify's public extract function writes its original pre-resolution facts
    into the worker-private cache. Reusing that cache retains raw calls, aliases,
    language-specific resolution and duplicate evidence. JS cache-bypass inputs
    can be parsed again by the global pass and are counted explicitly.

    Args:
        project: Source root; it is never modified.
        output: Private job directory outside source.
        max_files: Whole-file batch count bound, not a global memory limit.
        target_bytes: Ordinary batch byte target; an atomic file may exceed it.
    Returns:
        Bounded diagnostic report after the authoritative global pass.
    Raises:
        RuntimeError: Provider/root/API integrity, mixed inputs or global resolution fails.
        ValueError: Unsafe source/output or invalid batch configuration.
    """
    installed = version('graphifyy')
    if installed != SUPPORTED_PROVIDER:
        raise RuntimeError('Chunk protocol requires graphifyy ' + SUPPORTED_PROVIDER
                           + '; selected provider is ' + installed)
    import graphify.extract as provider
    import graphify.cache as cache_module
    original = provider.extract
    original_file_extract = provider._safe_extract_with_xaml_root
    original_cache_load = provider.load_cached
    issue_policy = _support('analysis_issues.py')
    issues = {}
    project, output = Path(project).resolve(), Path(output).resolve()
    if output == project or project in output.parents:
        raise ValueError('Chunk output must be outside the source project')
    plan_chunks([], project, max_files=max_files, target_bytes=target_bytes)
    configuration_reader = _support('resolution_context.py')['configuration_state']
    configuration = configuration_reader(project)
    report = {'version': VERSION, 'providerVersion': installed,
              'strategy': 'bounded-extraction-global-resolution',
              'globalResolution': 'full-corpus', 'maxFiles': max_files,
              'targetBytes': target_bytes, 'chunkCount': 0, 'fileCount': 0,
              'oversizedFiles': 0, 'cacheBypassFiles': 0, 'chunks': [],
              'fileExtractorCalls': {'chunks': 0, 'global': 0},
              'seconds': {'chunks': 0, 'global': 0},
              'configurationFingerprint': configuration['fingerprint'],
              'configurationCoverage': configuration['coverage'],
              'persistentEligibleSuffixes': sorted(PERSISTENT_LOCAL_SUFFIXES),
              'persistentContextBypassFiles': 0,
              'sourceFingerprint': sha256(b'[]').hexdigest(),
              'warning': 'Batch limits are not a global resolver or native RSS bound; JS cache-bypass files may be parsed again.'}
    invocations = 0
    phase = 'chunks'
    persistent, private_entries, source_records = None, [], []

    def count_file_extract(*args, **kwargs):
        """
        Count actual provider file-extractor invocations, not estimated AST parses.

        Args:
            args: Pinned provider tuple (extractor, path, root); path belongs to the planned corpus.
            kwargs: Unchanged provider keyword options.
        Returns:
            Successful facts or a private skipped sentinel paired with a public safe diagnostic.
        """
        report['fileExtractorCalls'][phase] += 1
        result = original_file_extract(*args, **kwargs)
        path = Path(args[1])
        issue = issue_policy['file_issue'](path, project, result)
        if issue is not None:
            issues[issue['path']] = issue
            # Only this file is omitted. No partial declarations/edges from a
            # failed parser may enter cross-file resolution as valid facts.
            # The explicit (false) error key prevents Graphify's cache write,
            # while skipped keeps this diagnosed file out of program resolution.
            return {'nodes': [], 'edges': [], 'error': None, 'skipped': 'atlas-analysis-error'}
        return result

    def load_valid_cached(*args, **kwargs):
        """
        Ignore legacy cached failure sentinels so a warm job cannot claim full coverage.

        Args:
            args: Unchanged pinned provider cache lookup arguments.
            kwargs: Unchanged root/cache context and lookup options.
        Returns:
            Healthy cached facts, or None to retry a previously diagnosed failure.
        """
        cached = original_cache_load(*args, **kwargs)
        if isinstance(cached, dict) and (cached.get('skipped') == 'atlas-analysis-error'
                                        or cached.get('parse_errors') or cached.get('error')):
            return None
        return cached

    def extract_batches(paths, *args, **kwargs):
        """
        Intercept the CLI's AST stage once, preserving exact arguments and root.

        Args:
            paths: Complete ordered source corpus selected by the CLI.
            args: Must be empty under the pinned provider API.
            kwargs: Original extraction context, including the invariant project root.
        Returns:
            Global provider result over valid files with explicit per-file omissions.
        """
        nonlocal invocations, phase, persistent, private_entries, source_records
        invocations += 1
        if invocations != 1:
            raise RuntimeError('Unexpected repeated Graphify extraction stage')
        root = Path(kwargs.get('root') or project).resolve()
        if root != project or args:
            raise RuntimeError('Graphify extraction root or API contract changed')
        batches, records = plan_chunks(paths, root, max_files=max_files, target_bytes=target_bytes)
        source_records = records
        identities = {record['file']: record for record in records}
        for path in paths:
            if provider._get_extractor(Path(path)) is None:
                issue = issue_policy['file_issue'](Path(path), project, {'error': 'atlas-no-extractor'})
                issues[issue['path']] = issue
        private_entries = _private_entries(paths, root, output, cache_module, provider._JS_CACHE_BYPASS_SUFFIXES)
        persistent = _persistent_tier(project, output, records, configuration, cache_module)
        if not isinstance(persistent, dict):
            for key, destination in private_entries:
                persistent.restore(key, destination)
        report['fileCount'] = len(records)
        report['persistentContextBypassFiles'] = sum(Path(path).suffix.lower() not in PERSISTENT_LOCAL_SUFFIXES for path in paths)
        report['oversizedFiles'] = sum(record['bytes'] > target_bytes for record in records)
        report['cacheBypassFiles'] = sum(Path(path).suffix in provider._JS_CACHE_BYPASS_SUFFIXES for path in paths)
        report['sourceFingerprint'] = sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
        began = time.monotonic()
        for batch in batches:
            # Final graphs are discarded here. Only the provider-owned cached
            # extraction facts are inputs to the authoritative full-corpus pass.
            local = original(batch, **{**kwargs, 'parallel': False, 'max_workers': 1})
            failures = local.get('failed_sources') or local.get('errors')
            if failures:
                raise RuntimeError('A Graphify chunk could not be extracted completely')
            report['chunks'].append(_chunk_record(batch, root, identities))
            del local
        report['chunkCount'] = len(batches)
        report['seconds']['chunks'] = time.monotonic() - began
        phase = 'global'
        began = time.monotonic()
        # One sequential worker keeps extraction accounting and native lifetime
        # deterministic even when cache-bypass files exceed the pool threshold.
        result = original(paths, **{**kwargs, 'parallel': False, 'max_workers': 1})
        report['seconds']['global'] = time.monotonic() - began
        if result.get('failed_sources'):
            raise RuntimeError('Global Graphify resolution contains failed source extraction')
        # Zero program facts is a valid diagnostic-only partial result when all
        # failures are source-local. Runtime/root/resolver integrity failures
        # still raise independently; a count of local failures cannot prove one.
        for path in paths:
            relative = Path(path).resolve().relative_to(root).as_posix()
            if _file_hash(Path(path)) != identities[relative]['sha256']:
                raise RuntimeError('Source changed during chunk analysis: ' + relative)
        return result

    provider.extract = extract_batches
    provider._safe_extract_with_xaml_root = count_file_extract
    provider.load_cached = load_valid_cached
    previous = sys.argv
    try:
        # Atlas certifies the full current corpus even when callers reuse an
        # output directory. CLI incremental detection would hide unchanged
        # sources before our hook; --force retains AST cache reuse but scans all.
        sys.argv = ['graphify', 'extract', str(project), '--force', '--code-only', '--no-cluster',
                    '--out', str(output), '--max-workers', '1']
        try:
            runpy.run_module('graphify', run_name='__main__', alter_sys=True)
        except SystemExit as error:
            if error.code not in (None, 0):
                raise
    finally:
        provider.extract = original
        provider._safe_extract_with_xaml_root = original_file_extract
        provider.load_cached = original_cache_load
        sys.argv = previous
    # Configuration and source fences cover CLI post-processing as well as AST
    # work. Never export reusable facts or certify a report after mixed inputs.
    if configuration_reader(project)['fingerprint'] != configuration['fingerprint']:
        raise RuntimeError('Resolver configuration changed during chunk analysis')
    for record in source_records:
        if _file_hash(project / record['file']) != record['sha256']:
            raise RuntimeError('Source changed before chunk publication: ' + record['file'])
    if persistent is not None:
        if isinstance(persistent, dict):
            report['persistentCache'] = persistent
        else:
            for key, source in private_entries:
                # Failure sentinels belong only to this private job cache.
                # Never let a later rebuild restore a previous file failure.
                if not issues:
                    persistent.export(key, source)
            report['persistentCache'] = {'enabled': True, **persistent.summary()}
    ordered_issues = [issues[key] for key in sorted(issues)]
    source_identities = {record['file']: record for record in source_records}
    for issue in ordered_issues:
        issue['sourceHash'] = source_identities[issue['path']]['sha256']
        issue['sourceKind'] = 'analysis-diagnostic'
    excluded = [item for item in ordered_issues if item.get('excludedData')]
    failed_count = len(ordered_issues) - len(excluded)
    report['analysisDiagnostics'] = {'version': 1, 'policyVersion': issue_policy['POLICY_VERSION'],
        'analysisStatus': 'partial' if issues else 'complete', 'errorCount': len(issues),
        'sourceFileCount': report['fileCount'], 'processedFileCount': report['fileCount'] - len(issues),
        'failedFileCount': failed_count, 'excludedDataCount': len(excluded),
        'issues': ordered_issues[:issue_policy['MAX_ISSUES']],
        'truncated': len(issues) > issue_policy['MAX_ISSUES']}
    report_path = output / 'graphify-out' / 'atlas-chunks.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    return report


def main():
    """
    Read only explicit project/output paths and bounded provider batch settings.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    parser.add_argument('output')
    parser.add_argument('--max-files', type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument('--target-bytes', type=int, default=DEFAULT_TARGET_BYTES)
    arguments = parser.parse_args()
    run_chunked(arguments.project, arguments.output, max_files=arguments.max_files,
                target_bytes=arguments.target_bytes)


if __name__ == '__main__':
    main()
