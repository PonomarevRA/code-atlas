"""Host-owned allowlist: discovery не импортирует parsers и не устанавливает пакеты."""
from hashlib import sha256
from functools import lru_cache
from importlib import import_module, metadata, util
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
import json
import marshal

from .models import DocumentError

PROVIDERS = {
    'document-markdown': ('provider_markdown', 'MarkdownProvider', ['.md', '.markdown'], ['markdown-it-py']),
    'document-yaml': ('provider_yaml', 'YamlProvider', ['.yaml', '.yml'], ['PyYAML']),
    'document-helm': ('provider_helm', 'HelmProvider', ['.yaml', '.yml', '.tpl'], ['PyYAML']),
    'document-docx': ('provider_docx', 'DocxProvider', ['.docx'], ['python-docx']),
    'document-xlsx': ('provider_xlsx', 'XlsxProvider', ['.xlsx'], ['openpyxl']),
    'document-pptx': ('provider_pptx', 'PptxProvider', ['.pptx'], ['python-pptx']),
    'document-pdf-basic': ('provider_pdf', 'PdfProvider', ['.pdf'], ['pypdf', 'pdfplumber']),
    'logs-structured': ('provider_logs', 'StructuredLogsProvider', ['.log', '.jsonl', '.ndjson'], []),
    'logs-drain3': ('provider_logs', 'Drain3LogsProvider', ['.log', '.jsonl', '.ndjson'], ['drain3']),
    'logs-lnav': ('provider_logs', 'LnavLogsProvider', ['.log'], []),
}


def runtime_fingerprint(distribution, source_file, *extra_files):
    """Хеширует installed metadata и исходники adapters; optional parser не импортируется."""
    digest = sha256()
    for name in ([distribution] if isinstance(distribution, str) else distribution):
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = 'unavailable'
        digest.update(json.dumps([name, version]).encode())
    files = [Path(source_file), Path(__file__), Path(__file__).with_name('text_source.py'),
             Path(__file__).with_name('models.py')]
    files.extend(Path(source_file).with_name(name) for name in extra_files)
    for path in sorted(set(files)):
        digest.update(path.name.encode())
        module_name = __package__ + '.' + path.name.split('.', 1)[0]
        digest.update(_adapter_bytes(module_name))
    return digest.hexdigest()


def _adapter_bytes(module_name):
    """Читает source или bundled code object, включая PyInstaller PYZ без .py файла."""
    spec = util.find_spec(module_name)
    if spec is None or spec.loader is None:
        raise DocumentError('CAPABILITY_UNAVAILABLE', 'Host adapter не найден в runtime pack.')
    get_code = getattr(spec.loader, 'get_code', None)
    if get_code is not None:
        code = get_code(module_name)
        if code is not None:
            return marshal.dumps(code)
    # A loader may expose source only; reading it does not execute the adapter or its imports.
    get_source = getattr(spec.loader, 'get_source', None)
    source = get_source(module_name) if get_source is not None else None
    if source is not None:
        return source.encode('utf-8')
    origin = Path(spec.origin) if spec.origin else None
    if origin and any(str(origin).endswith(suffix) for suffix in EXTENSION_SUFFIXES):
        stamp = origin.stat()
        return _compiled_digest(str(origin), stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns)
    raise DocumentError('CAPABILITY_UNAVAILABLE', 'Runtime pack не предоставляет fingerprint adapter.')


@lru_cache(maxsize=128)
def _compiled_digest(filename, size, mtime_ns, ctime_ns):
    """Хеширует готовый native module; неизменный бинарник не читается на каждый документ."""
    if size > 64 * 1024 * 1024:
        raise DocumentError('CAPABILITY_UNAVAILABLE', 'Native adapter превышает бюджет runtime fingerprint.')
    digest = sha256()
    with Path(filename).open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    return b'native-sha256:' + digest.hexdigest().encode('ascii')


def available_providers():
    """Возвращает asset state без optional imports, чтения документов или запуска worker."""
    result = []
    for identifier, (module, class_name, formats, distributions) in PROVIDERS.items():
        missing = []
        for distribution in ['psutil', *distributions]:
            try:
                metadata.version(distribution)
            except metadata.PackageNotFoundError:
                missing.append(distribution)
        if util.find_spec(__package__ + '.' + module) is None:
            missing.append('host-adapter')
        if identifier == 'logs-lnav':
            from ..lnav_runtime import capability
            state = capability()
            if not state['available']:
                missing.append(state['missingAsset'])
        table_provider = identifier in {'document-markdown', 'document-docx', 'document-xlsx', 'document-pptx', 'document-pdf-basic'}
        result.append({'id': identifier, 'strategy': identifier + '-v1', 'version': '1', 'formats': formats,
                       'status': 'capability_unavailable' if missing else 'available', 'missingAssets': missing,
                       'capabilities': {'tree': True, 'tables': table_provider, 'references': True,
                                        'ocr': False, 'offlineCertified': False}})
    return result


def resolve_provider(path, *, strategy=None, options=None, context=None):
    """Выбирает один явный профиль; Helm context имеет приоритет над generic YAML."""
    path, context = Path(path), context or {}
    if strategy:
        identifier = strategy.removesuffix('-v1')
        if identifier not in PROVIDERS or strategy not in {identifier, identifier + '-v1'}:
            raise DocumentError('STRATEGY_UNSUPPORTED', 'Неизвестная host-owned стратегия документа.')
    elif path.suffix.lower() in {'.yaml', '.yml'}:
        identifier = 'document-helm' if context.get('chartContext') or path.name == 'Chart.yaml' else 'document-yaml'
    else:
        candidates = [name for name, (_, _, extensions, _) in PROVIDERS.items()
                      if path.suffix.lower() in extensions and name not in {'logs-drain3', 'logs-lnav'}]
        if len(candidates) != 1:
            raise DocumentError('FORMAT_UNSUPPORTED', 'Формат требует явной поддержанной стратегии.')
        identifier = candidates[0]
    module, class_name, extensions, distributions = PROVIDERS[identifier]
    if path.suffix.lower() not in extensions:
        raise DocumentError('FORMAT_MISMATCH', 'Стратегия не поддерживает расширение файла.')
    for distribution in distributions:
        try:
            metadata.version(distribution)
        except metadata.PackageNotFoundError as error:
            raise DocumentError('CAPABILITY_UNAVAILABLE', 'Отсутствует runtime компонент: ' + distribution) from error
    try:
        provider_class = getattr(import_module('.' + module, __package__), class_name)
    except (ImportError, AttributeError) as error:
        raise DocumentError('CAPABILITY_UNAVAILABLE', 'Host adapter недоступен.') from error
    return provider_class()
