"""Ограниченная лексическая подсветка исходников без выполнения кода проекта."""

from pathlib import PurePath

try:
    from pygments.lexers.c_cpp import CppLexer
    from pygments.lexers.dotnet import CSharpLexer
    from pygments.lexers.go import GoLexer
    from pygments.lexers.javascript import JavascriptLexer, TypeScriptLexer
    from pygments.lexers.jvm import JavaLexer, KotlinLexer
    from pygments.lexers.objective import SwiftLexer
    from pygments.lexers.php import PhpLexer
    from pygments.lexers.python import PythonLexer
    from pygments.lexers.rust import RustLexer
    from pygments.token import Token
except ImportError:
    _LEXERS = {}
else:
    # Только встроенные классы: файл проекта не выбирает plugin или Python-модуль.
    _LEXERS = {
        "csharp": CSharpLexer, "python": PythonLexer, "java": JavaLexer,
        "javascript": JavascriptLexer, "typescript": TypeScriptLexer,
        "go": GoLexer, "cpp": CppLexer, "kotlin": KotlinLexer,
        "php": PhpLexer, "rust": RustLexer, "swift": SwiftLexer,
    }


SYNTAX_VERSION = "atlas-lexical-v1"
MAX_SYNTAX_CHARS = 256 * 1024
MAX_SYNTAX_TOKENS = 20_000
_LANGUAGES = {
    ".cs": "csharp", ".py": "python", ".java": "java",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".h": "cpp", ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
    ".kt": "kotlin", ".kts": "kotlin", ".php": "php",
    ".rs": "rust", ".swift": "swift",
}


def _token_kind(token) -> str:
    """Сводит лексические категории к общей палитре, не определяя типы символов."""
    categories = (
        (Token.Comment, "comment"), (Token.Keyword, "keyword"),
        (Token.Literal.String, "string"), (Token.Literal.Number, "number"),
        (Token.Name.Function, "function"), (Token.Name.Class, "type"),
        (Token.Operator, "operator"),
    )
    for category, kind in categories:
        if token in category:
            return kind
    return "code"


def _token_lines(lexer, source: str, max_tokens: int) -> list | None:
    """Лексирует целый файл и проверяет сохранность текста до разбиения на строки.

    Бюджет учитывает и входные токены, и части после разделения: один огромный
    многострочный комментарий не должен создавать неограниченное число строк.
    None означает превышение бюджета; нарушение сохранности текста даёт ошибку.
    """
    lines = [[]]
    offset = 0
    source_tokens = 0
    output_units = 1  # Начальная строка тоже занимает контейнер в JSON-ответе.
    for index, token, value in lexer.get_tokens_unprocessed(source):
        source_tokens += 1
        if source_tokens > max_tokens:
            return None
        if index != offset or source[index:index + len(value)] != value:
            raise ValueError("Lexer changed source text")
        offset += len(value)
        kind = _token_kind(token)
        # Считаем до split: большой блок пустых строк не выделяет тысячи списков.
        line_breaks = value.count("\n")
        output_units += 1 + 2 * line_breaks
        if output_units > max_tokens:
            return None
        parts = value.split("\n")
        for part_index, part in enumerate(parts):
            if part_index:
                lines.append([])
            if part:
                lines[-1].append({"kind": kind, "text": part})
    if offset != len(source):
        raise ValueError("Lexer omitted source text")
    return lines


def highlight_source(filename: str, text: str, *, max_chars: int = MAX_SYNTAX_CHARS,
                     max_tokens: int = MAX_SYNTAX_TOKENS) -> dict:
    """Возвращает безопасные токены 11 языков либо компактный plain fallback.

    text остаётся ответственностью вызывающего source-reader. В ready-ответе
    строки восстанавливают исходный текст с LF, включая последнюю пустую строку.
    Лимит символов применяется до нормализации. Подсветка не доказывает binding
    методов, не запускает анализируемый код и никогда не возвращает HTML.
    """
    language = _LANGUAGES.get(PurePath(filename).suffix.lower(), "text")
    result = {"version": SYNTAX_VERSION, "language": language, "status": "plain"}
    if language == "text":
        return {**result, "reason": "unsupported-language"}
    if len(text) > max_chars:
        return {**result, "reason": "character-budget"}
    lexer_class = _LEXERS.get(language)
    if lexer_class is None:
        return {**result, "reason": "lexer-unavailable"}
    source = text.replace("\r\n", "\n").replace("\r", "\n")
    if source.count("\n") + 1 > max_tokens:
        return {**result, "reason": "token-budget"}
    try:
        # Unprocessed обходит stripnl/ensurenl и не меняет пробелы или конец файла.
        lexer = lexer_class(stripnl=False, ensurenl=False)
        lines = _token_lines(lexer, source, max_tokens)
    except Exception:
        # Необязательная подсветка не должна скрывать доступный безопасный исходник.
        return {**result, "reason": "lexer-error"}
    if lines is None:
        return {**result, "reason": "token-budget"}
    return {**result, "status": "ready", "lines": lines}
