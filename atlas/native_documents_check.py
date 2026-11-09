"""Самопроверка установленного runtime на fixtures без внешнего Python и исходников."""
from importlib import util
from importlib.machinery import EXTENSION_SUFFIXES
import json
from pathlib import Path
import sys
import tempfile

from .documents.providers import available_providers
from .documents.store import DocumentStore
from .runtime_paths import resource_root
from .lnav_runtime import capability as lnav_capability, pack_directory

NATIVE_MODULES = {
    'capture', 'capture_windows', 'corpus_adapter', 'graph_projection', 'leases', 'models',
    'office_package', 'provider_docx', 'provider_helm', 'provider_logs', 'provider_markdown',
    'provider_pdf', 'provider_pptx', 'provider_xlsx', 'provider_yaml', 'providers', 'queries',
    'retention', 'storage', 'store', 'text_source', 'worker',
}


def _native_modules(manifest, root):
    """Не допускает неполный manifest, Python fallback или native module за пределами бандла."""
    expected = {'atlas.documents.' + name for name in NATIVE_MODULES}
    entries = manifest.get('extensions')
    if manifest.get('schemaVersion') != 1 or not isinstance(entries, list):
        raise RuntimeError('Invalid native document manifest.')
    names = [entry.get('module') for entry in entries if isinstance(entry, dict)]
    if len(entries) != len(expected) or len(names) != len(set(names)) or set(names) != expected:
        raise RuntimeError('Incomplete native document manifest module inventory.')
    result = []
    for entry in entries:
        name = entry['module']
        relative = Path(entry.get('file', ''))
        if relative.is_absolute() or '..' in relative.parts or relative.parent != Path('atlas/documents'):
            raise RuntimeError('Invalid compiled module path: ' + name)
        spec = util.find_spec(name)
        target = root / relative
        if (spec is None or not spec.origin or not any(spec.origin.endswith(suffix) for suffix in EXTENSION_SUFFIXES)
                or not target.is_file() or Path(spec.origin).resolve() != target.resolve()):
            raise RuntimeError('Expected bundled compiled document module: ' + name)
        result.append({'module': name, 'origin': spec.origin})
    return result


def _check_captured_content(store, snapshot):
    """Проверяет реальное содержимое семи fixtures, а не только наличие корней."""
    markers = {'guide.md': 'Guide', 'values.yaml': 'replicas', 'events.jsonl': 'native check',
               'guide.docx': 'Guide', 'data.xlsx': '42', 'slides.pptx': 'Guide', 'guide.pdf': 'Native document check'}
    checked = []
    for binding in snapshot['documents']:
        name, document_id = binding['path'], binding['documentId']
        coverage = binding['coverage']
        if coverage.get('status') not in {'complete', 'partial'}:
            raise RuntimeError('Unsupported or failed fixture extraction: ' + name)
        if name == 'guide.pdf' and 'pdf_reading_order_heuristic' not in coverage.get('reasonCodes', []):
            raise RuntimeError('PDF fixture lost its layout uncertainty metadata.')
        result = store.search('native-check', query=markers[name], snapshot_id=snapshot['snapshotId'], document_id=document_id)
        if not result['items']:
            raise RuntimeError('Expected captured fixture content missing: ' + name)
        graph = store.graph('native-check', snapshot_id=snapshot['snapshotId'], document_id=document_id)
        if name == 'guide.pdf':
            page = next((n for n in graph['nodes'] if n['kind'] == 'page'), {})
            if page.get('attributes', {}).get('status') != 'native_text':
                raise RuntimeError('PDF fixture has no native text.')
        if name in {'guide.md', 'guide.docx', 'data.xlsx'}:
            kind = 'sheet' if name == 'data.xlsx' else 'table'
            table = next((n for n in graph['nodes'] if n['kind'] == kind), None)
            if table is None:
                raise RuntimeError('Expected table missing: ' + name)
            window = store.table('native-check', table['id'], snapshot_id=snapshot['snapshotId'], document_id=document_id,
                                 row_count=3, column_count=3)
            if not any(cell.get('text') == '42' for cell in window['cells']):
                raise RuntimeError('Expected fixture table value missing: ' + name)
        checked.append(name)
    return checked


def _fixtures(root):
    """Создаёт небольшие настоящие файлы каждого базового формата, без пользовательских данных."""
    from docx import Document
    from openpyxl import Workbook
    from pptx import Presentation
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    (root / 'guide.md').write_text('# Guide\n\n| Key | Value |\n| --- | --- |\n| A | 42 |\n')
    (root / 'values.yaml').write_text('service:\n  replicas: 2\n')
    (root / 'events.jsonl').write_text('{"message":"native check","level":"INFO"}\n')
    document = Document()
    document.add_heading('Guide', level=1)
    document.add_table(rows=1, cols=1).cell(0, 0).text = '42'
    document.save(root / 'guide.docx')
    book = Workbook()
    book.active['B2'] = '42'
    book.save(root / 'data.xlsx')
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = 'Guide'
    presentation.save(root / 'slides.pptx')
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 250 Td (Native document check) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(root / 'guide.pdf')
    return sorted(path.name for path in root.iterdir())


def check_native_documents(require_compiled=True):
    """Проверяет native origins и настоящий spawn import/query; результаты печатаются как JSON."""
    manifest_path = resource_root() / 'resources' / 'native-documents-manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    native = _native_modules(manifest, resource_root()) if manifest else []
    if require_compiled and not native:
        raise RuntimeError('Native document manifest отсутствует или пуст; release не проверен.')
    unavailable = [p['id'] for p in available_providers() if p['id'] != 'logs-lnav' and p['status'] != 'available']
    if unavailable:
        raise RuntimeError('Missing bundled providers: ' + ', '.join(unavailable))
    with tempfile.TemporaryDirectory(prefix='atlas-native-documents-') as directory:
        root = Path(directory) / 'input'
        root.mkdir()
        files = _fixtures(root)
        store = DocumentStore(Path(directory) / 'state', root)
        try:
            created = store.ensure('native-check', files)
            job = store.wait(created['id'], timeout=120)
            if job['status'] != 'completed':
                raise RuntimeError('Native extraction failed: ' + str(job))
            snapshot = store.snapshot('native-check', job['snapshotId'])
            if len(snapshot['documents']) != len(files):
                raise RuntimeError('Missing format results in native extraction.')
            roots = store.tree('native-check', job['snapshotId'])
            if len(roots['items']) != len(files):
                raise RuntimeError('Indexed native roots differ from captured documents.')
            checked = _check_captured_content(store, snapshot)
            drain = store.ensure('native-drain', ['events.jsonl'], strategy='logs-drain3')
            drain_job = store.wait(drain['id'], timeout=60)
            if drain_job['status'] != 'completed':
                raise RuntimeError('Native Drain3 extraction failed: ' + str(drain_job))
            lnav = lnav_capability()
            if (pack_directory() / 'manifest.json').exists():
                if not lnav['available']:
                    raise RuntimeError('Bundled lnav pack failed its integrity/capability check.')
                (root / 'access.log').write_text('127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /native-check HTTP/1.1" 200 2326\n')
                extraction = store.ensure('native-lnav', ['access.log'], strategy='logs-lnav')
                lnav_job = store.wait(extraction['id'], timeout=60)
                if lnav_job['status'] != 'completed':
                    raise RuntimeError('Native lnav extraction failed: ' + str(lnav_job))
                found = store.search('native-lnav', query='native-check', snapshot_id=lnav_job['snapshotId'])
                if not found['items']:
                    raise RuntimeError('Bundled lnav did not preserve the Apache fixture event.')
            return {'status': 'PASS', 'frozen': bool(getattr(sys, 'frozen', False)), 'pythonExecutable': sys.executable,
                    'externalPythonRequired': False, 'compiledModules': native,
                    'formats': files, 'checkedContent': checked, 'documentCount': len(snapshot['documents']),
                    'drain3': drain_job['status'], 'lnav': lnav, 'jobStatus': job['status']}
        finally:
            store.close()
