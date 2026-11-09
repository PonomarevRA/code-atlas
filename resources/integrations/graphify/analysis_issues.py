"""Safe per-file failure records shared by both standalone extraction runtimes."""

from hashlib import sha256
import json
from pathlib import PurePosixPath

POLICY_VERSION = 'atlas-file-isolation-v1'
MAX_ISSUES = 256
DATA_VALIDATION_BYTES = 8 * 1024 * 1024
# Conservative filename exclusions follow the pinned Graphify 0.9.85 config policy.
CONFIG_JSON_NAMES = {'package.json', 'tsconfig.json', 'jsconfig.json', 'composer.json',
    'deno.json', 'bower.json', 'manifest.json', 'app.json', 'now.json', 'vercel.json',
    'angular.json', 'nest-cli.json', 'biome.json', 'renovate.json', '.babelrc.json',
    '.eslintrc.json', '.prettierrc.json', 'babel.config.json', 'appsettings.json'}


def file_issue(path, root, result):
    """
    Convert one extractor error/empty result to a safe diagnostic, never raw exception text.

    Args:
        path: Regular source already admitted by the corpus planner.
        root: Fixed project root used by Graphify.
        result: File-local extraction result; successful/skipped results return None.
    Returns:
        Bounded public issue or None. Large generated JSON gets an explicit data-map alternative.
    """
    error = result.get('error') or ('parse-errors' if result.get('parse_errors') else None)
    if not error and (result.get('nodes') or result.get('skipped')):
        return None
    relative = path.resolve().relative_to(root).as_posix()
    code = 'FILE_EXTRACTION_FAILED' if error else 'EMPTY_EXTRACTION'
    message = 'Файл не удалось разобрать; его программные факты не включены в карту.'
    if error and ('tree_sitter' in str(error) or 'tree-sitter' in str(error)):
        code, message = 'GRAMMAR_UNAVAILABLE', 'Для файла недоступна совместимая грамматика.'
    elif error == 'atlas-no-extractor':
        code, message = 'UNSUPPORTED_SOURCE', 'Для типа этого файла не зарегистрирован структурный индексатор.'
    elif result.get('parse_errors'):
        code, message = 'PARSE_ERROR', 'Парсер сообщил об ошибках синтаксиса; факты файла не включены в карту.'
    issue = {'code': code, 'path': relative, 'message': message, 'severity': 'error',
             'bytes': path.stat().st_size,
             'nodeId': 'atlas-analysis-error:' + sha256(relative.encode()).hexdigest()}
    if path.suffix.lower() == '.json' and error == 'json file too large to index':
        issue.update(code='JSON_CODE_INDEX_LIMIT', message='JSON превышает лимит кодового индексатора; доступен отдельный анализ данных.')
        issue['alternative'] = {'kind': 'data-map', 'file': relative, 'status': 'not-built'}
        # This narrow classification mirrors Graphify's intentional data-array skip.
        # A byte cap bounds input, not json.loads CPU or expanded Python memory.
        parts = PurePosixPath(relative).parts
        generated = ('docs' in parts and 'reports' in parts) or bool(set(parts) & {'benchmark-results', 'test-results', 'artifacts'})
        config = path.name.lower() in CONFIG_JSON_NAMES or path.name.lower().endswith(
            ('.config.json', '.schema.json', '.eslintrc.json', '.prettierrc.json',
             '.babelrc.json', 'tsconfig.json', 'jsconfig.json')) or path.name.lower().startswith('appsettings.')
        if generated and not config and path.stat().st_size <= DATA_VALIDATION_BYTES:
            try:
                with path.open('rb') as stream:
                    content = stream.read(DATA_VALIDATION_BYTES + 1)
                if len(content) <= DATA_VALIDATION_BYTES and isinstance(json.loads(content), list):
                    issue['excludedData'] = True
                    issue['sourceHash'] = sha256(content).hexdigest()
            except (OSError, ValueError, UnicodeError, RecursionError):
                pass
    return issue
