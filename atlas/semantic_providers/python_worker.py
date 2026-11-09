"""Host-owned Jedi worker: parse a private staged source inventory without application imports."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def probe():
    """Report installed package/API identity without treating Python presence as Jedi availability."""
    try:
        import jedi
        import parso
    except ImportError:
        return {'status': 'missing'}
    supported = (jedi.__version__.startswith(('0.19.', '0.20.')) and
                 callable(getattr(jedi, 'Script', None)) and callable(getattr(jedi, 'Project', None)))
    return {'status': 'supported' if supported else 'incompatible', 'version': jedi.__version__,
            'jedi_path': str(Path(jedi.__file__).resolve()), 'parso_path': str(Path(parso.__file__).resolve())}


def column(text, row, utf16_column):
    """Convert a UTF-16 endpoint to Python codepoints without rounding split surrogate pairs."""
    lines = text.split('\n')
    if not 0 <= row < len(lines):
        raise ValueError('query-row-outside-source')
    encoded = lines[row].removesuffix('\r').encode('utf-16-le')
    count = utf16_column * 2
    if count > len(encoded):
        raise ValueError('query-column-outside-source')
    return len(encoded[:count].decode('utf-16-le'))


def resolve(script, query, texts, directory):
    """Follow declared source definitions only; external/builtin/ambiguous results stay bounded."""
    text = texts[query['path']]
    row, offset = query['line'], column(text, query['line'], query['column'])
    names = script.goto(row + 1, offset, follow_imports=True, follow_builtin_imports=False)
    targets = []
    for name in names[:8]:
        if name.module_path is None or name.line is None or name.column is None:
            continue
        try:
            relative = Path(name.module_path).resolve().relative_to(directory).as_posix()
        except ValueError:
            continue
        if relative not in texts or query['kind'] == 'call' and name.type not in {'function', 'class'}:
            continue
        source_lines = texts[relative].split('\n')
        line = source_lines[name.line - 1]
        start = len(line[:name.column].encode('utf-16-le')) // 2
        end = start + len(name.name.encode('utf-16-le')) // 2
        targets.append({'symbol_id': f'{relative}:{name.line}:{name.column}:{name.name}',
            'kind': name.type, 'label': name.name,
            'source_span': {'path': relative, 'start_line': name.line - 1, 'start_column': start,
                'end_line': name.line - 1, 'end_column': end, 'coordinate_kind': 'utf-16'}})
    return {'targets': targets, 'ambiguous': len(names) > 1,
            'reason': 'definition-outside-context-or-dynamic' if not targets else ''}


def main():
    """Run isolated analyzer imports, then parse only source files staged from checked input."""
    if '--probe' in sys.argv:
        print(json.dumps(probe()))
        return
    import jedi
    import parso
    incoming = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
    if len(incoming) > 4 * 1024 * 1024:
        raise ValueError('input-limit')
    request = json.loads(incoming)
    texts = {item['path']: item['text'] for item in request['files']}
    with TemporaryDirectory(prefix='atlas-jedi-') as temporary:
        directory = Path(temporary).resolve()
        for relative, text in texts.items():
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        project = jedi.Project(directory, sys_path=[str(directory)],
                               smart_sys_path=False, load_unsafe_extensions=False)
        results = []
        for query in request['queries']:
            script = jedi.Script(texts[query['path']], path=directory / query['path'], project=project)
            try:
                results.append(resolve(script, query, texts, directory))
            except (ValueError, IndexError):
                results.append({'targets': [], 'reason': 'invalid-or-unresolved-query'})
        reasons = ['definition-scope-only', 'dynamic-python-and-runtime-targets-unknown',
                   'external-environment-and-stubs-not-loaded', *request.get('reasons', [])]
        print(json.dumps({'results': results, 'reasons': reasons}))


if __name__ == '__main__':
    main()
