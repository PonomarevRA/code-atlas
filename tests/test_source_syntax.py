"""Контракт офлайн-подсветки: точный текст, многострочный контекст и бюджеты."""

import pytest

from atlas import source_syntax
from atlas.source_syntax import highlight_source


def restored(result):
    """Собирает видимый текст из строк без предположений о цветах грамматики."""
    return "\n".join("".join(token["text"] for token in line) for line in result["lines"])


@pytest.mark.parametrize("filename,language,text", [
    ("A.cs", "csharp", 'class A { string s = "hi"; /* comment */ }'),
    ("a.py", "python", 'def a():\n    return "hi" # comment\n'),
    ("A.java", "java", 'class A { String s = "hi"; /* comment */ }'),
    ("a.js", "javascript", 'function a() { return "hi"; /* comment */ }'),
    ("a.ts", "typescript", 'function a(): string { return "hi"; /* comment */ }'),
    ("a.go", "go", 'package main\nfunc a() string { return "hi" /* comment */ }'),
    ("a.cpp", "cpp", 'int a() { return 1; /* comment */ }'),
    ("a.kt", "kotlin", 'fun a(): String { return "hi" /* comment */ }'),
    ("a.php", "php", '<?php function a() { return "hi"; /* comment */ }'),
    ("a.rs", "rust", 'fn a() -> i32 { 1 /* comment */ }'),
    ("a.swift", "swift", 'func a() -> String { return "hi" /* comment */ }'),
])
def test_supported_languages_preserve_source(filename, language, text):
    """Каждый заявленный язык использует установленный lexer и настоящие категории."""
    result = highlight_source(filename, text)
    assert result["version"] == "atlas-lexical-v1"
    assert result["status"] == "ready"
    assert result["language"] == language
    assert restored(result) == text
    kinds = {token["kind"] for line in result["lines"] for token in line}
    assert "keyword" in kinds
    assert "comment" in kinds
    assert kinds <= {"code", "keyword", "string", "number", "comment", "function", "type", "operator"}


@pytest.mark.parametrize("filename,text,kind,needle", [
    ("a.cs", "/* begin\nreturn fake;\nend */\nreturn 1;", "comment", "return fake;"),
    ("a.py", 'x = """begin\nreturn fake\nend"""\nreturn 1', "string", "return fake"),
    ("a.js", 'const x = `begin\nreturn fake;\nend`;\nreturn 1;', "string", "return fake;"),
])
def test_multiline_context_survives_line_boundaries(filename, text, kind, needle):
    """Токены второй строки зависят от начала многострочного блока в первой."""
    result = highlight_source(filename, text)
    assert result["status"] == "ready"
    assert restored(result) == text
    assert any(needle in token["text"] and token["kind"] == kind for token in result["lines"][1])


@pytest.mark.parametrize("text", ["", "\n", "a\n", "\t  x\r\ny\rz\n", "😀 Привет\n", "\ud800"])
def test_newlines_whitespace_and_unicode_are_exact(text):
    """Нормализуются лишь переводы строк; даже некорректный Python Unicode не теряется."""
    result = highlight_source("a.py", text)
    assert result["status"] == "ready"
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    assert restored(result) == normalized
    assert len(result["lines"]) == normalized.count("\n") + 1


def test_source_markup_is_returned_as_text():
    """Текст атаки остаётся данными; HTML-разметка не является частью контракта."""
    text = 'x = "<script>alert(1)</script>"\n# <img src=x onerror=alert(1)>\n'
    result = highlight_source("a.py", text)
    assert restored(result) == text
    assert all(set(token) == {"kind", "text"} for line in result["lines"] for token in line)
    assert "html" not in result


@pytest.mark.parametrize("filename,language", [("x.txt", "text"), ("x", "text"), ("x.PY", "python"), ("x.tsx", "typescript"), ("x.hpp", "cpp")])
def test_filename_selection_is_explicit(filename, language):
    """Неизвестные суффиксы не запускают guessing или загрузку расширений."""
    result = highlight_source(filename, "return 1")
    assert result["language"] == language
    assert result["status"] == ("plain" if language == "text" else "ready")
    if language == "text":
        assert result["reason"] == "unsupported-language"
        assert "lines" not in result


@pytest.mark.parametrize("text,kwargs,reason", [
    ("return 123", {"max_chars": 3}, "character-budget"),
    ("return 1", {"max_tokens": 1}, "token-budget"),
    ("/*\n" + "\n" * 30 + "*/", {"max_tokens": 5}, "token-budget"),
])
def test_budgets_return_compact_plain_fallback(text, kwargs, reason):
    """Отказ от подсветки не дублирует исходник и не возвращает частичные токены."""
    result = highlight_source("a.cs", text, **kwargs)
    assert result == {"version": "atlas-lexical-v1", "language": "csharp", "status": "plain", "reason": reason}


def test_unavailable_lexer_keeps_plain_source_possible(monkeypatch):
    """Необязательный пакет или lexer может отсутствовать в исходном runtime."""
    monkeypatch.setattr(source_syntax, "_LEXERS", {})
    assert highlight_source("a.py", "pass")["reason"] == "lexer-unavailable"


def test_ready_output_counts_fragments_and_line_containers():
    """Готовый JSON ограничен суммой строк и текстовых фрагментов после split."""
    for text in ["\n" * 100, "/*\n" * 50 + "*/", "return 1;\n" * 30]:
        result = highlight_source("a.cs", text, max_tokens=500)
        assert result["status"] == "ready"
        units = len(result["lines"]) + sum(len(line) for line in result["lines"])
        assert units <= 500
    assert highlight_source("a.cs", "\n" * 260_000)["reason"] == "token-budget"


def test_lexer_failure_or_text_mutation_falls_back(monkeypatch):
    """Нарушение текста сторонним lexer не публикует неправильные цветные строки."""
    class BrokenLexer:
        """Тестовый lexer возвращает изменённый текст вместо входного."""
        def __init__(self, **kwargs):
            pass

        def get_tokens_unprocessed(self, text):
            """Моделирует несовместимость, не зависящую от версии Pygments."""
            yield 0, source_syntax.Token.Text, "different"

    monkeypatch.setitem(source_syntax._LEXERS, "python", BrokenLexer)
    result = highlight_source("a.py", "pass")
    assert result["status"] == "plain"
    assert result["reason"] == "lexer-error"
    assert "lines" not in result
