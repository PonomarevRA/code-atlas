"""
Validate the standalone provider's bounded chunk report before publication.
"""

import json
from pathlib import Path
import re
from hashlib import sha256

VERSION = 'atlas-graphify-chunks-v1'
MAX_REPORT_BYTES = 16 * 1024 * 1024


def _validate_diagnostics(value: dict, files: list[str]) -> None:
    """
    Validate issue identities and completeness arithmetic before snapshot publication.

    Args:
        value: New worker diagnostic section; legacy reports may omit it entirely.
        files: Validated complete relative source inventory from the report.
    Returns:
        None when the bounded report belongs to its declared corpus.
    Raises:
        ValueError: A malformed, excessive or contradictory diagnostic record.
    """
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Invalid analysis diagnostic version')
    if value.get('policyVersion') != 'atlas-file-isolation-v1':
        raise ValueError('Unsupported analysis isolation policy')
    names = ('sourceFileCount', 'processedFileCount', 'failedFileCount', 'excludedDataCount', 'errorCount')
    if any(type(value.get(name)) is not int or value[name] < 0 for name in names):
        raise ValueError('Invalid analysis diagnostic counters')
    if (value['sourceFileCount'] != len(files)
            or value['errorCount'] != value['failedFileCount'] + value['excludedDataCount']
            or value['processedFileCount'] + value['errorCount'] != len(files)
            or value.get('analysisStatus') != ('partial' if value['errorCount'] else 'complete')):
        raise ValueError('Contradictory analysis completeness')
    issues = value.get('issues')
    if not isinstance(issues, list) or len(issues) != min(value['errorCount'], 256):
        raise ValueError('Invalid analysis issue bound')
    if value.get('truncated') is not (value['errorCount'] > 256):
        raise ValueError('Invalid analysis issue coverage')
    known, seen = set(files), set()
    for issue in issues:
        path = issue.get('path') if isinstance(issue, dict) else None
        if (path not in known or path in seen or len(path) > 2000
                or issue.get('nodeId') != 'atlas-analysis-error:' + sha256(path.encode()).hexdigest()
                or issue.get('code') not in {'FILE_EXTRACTION_FAILED', 'EMPTY_EXTRACTION', 'GRAMMAR_UNAVAILABLE', 'JSON_CODE_INDEX_LIMIT', 'PARSE_ERROR', 'UNSUPPORTED_SOURCE'}
                or issue.get('severity') != 'error'
                or not isinstance(issue.get('message'), str) or len(issue['message']) > 400):
            raise ValueError('Invalid analysis issue identity')
        seen.add(path)
        alternative = issue.get('alternative')
        if alternative is not None and alternative != {'kind': 'data-map', 'file': path, 'status': 'not-built'}:
            raise ValueError('Invalid diagnostic alternative')


def read_chunk_report(output: Path) -> dict:
    """
    Read bounded provenance; absent/corrupt reports fail a requested chunk build.

    Args:
        output: Server-owned private directory containing graphify-out.
    Returns:
        Validated bounded report, including optional per-file completeness diagnostics.
    Raises:
        ValueError: Report structure, source coverage or issue identity is inconsistent.
        OSError: Report cannot be read.
    """
    path = output / 'graphify-out' / 'atlas-chunks.json'
    with path.open('rb') as source:
        encoded = source.read(MAX_REPORT_BYTES + 1)
    if len(encoded) > MAX_REPORT_BYTES:
        raise ValueError('Graphify chunk report exceeds its byte budget')
    report = json.loads(encoded)
    if not isinstance(report, dict) or report.get('version') != VERSION:
        raise ValueError('Unsupported Graphify chunk report')
    if (report.get('strategy') != 'bounded-extraction-global-resolution'
            or report.get('globalResolution') != 'full-corpus'):
        raise ValueError('Invalid Graphify chunk resolution contract')
    chunks = report.get('chunks')
    if (not isinstance(chunks, list) or type(report.get('chunkCount')) is not int
            or report['chunkCount'] != len(chunks)):
        raise ValueError('Invalid Graphify chunk count')
    if type(report.get('fileCount')) is not int or report['fileCount'] < 0:
        raise ValueError('Invalid Graphify source count')
    files = []
    for chunk in chunks:
        if (not isinstance(chunk, dict) or not isinstance(chunk.get('files'), list)
                or not chunk['files'] or type(chunk.get('bytes')) is not int
                or chunk['bytes'] < 0 or not isinstance(chunk.get('id'), str)
                or re.fullmatch(r'[a-f0-9]{64}', chunk['id']) is None):
            raise ValueError('Invalid Graphify chunk descriptor')
        for filename in chunk['files']:
            if (not isinstance(filename, str) or not filename or '\x00' in filename
                    or Path(filename).is_absolute() or '..' in Path(filename).parts):
                raise ValueError('Invalid Graphify chunk source identity')
            files.append(filename)
    if len(files) != report.get('fileCount') or len(files) != len(set(files)):
        raise ValueError('Graphify chunk files must cover the detected corpus exactly once')
    if 'analysisDiagnostics' in report:
        _validate_diagnostics(report['analysisDiagnostics'], files)
    return report
