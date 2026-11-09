"""Неполный перевод не скрывает английские документы и не смешивает версии/overloads."""
import json
import zipfile
import pytest

from atlas.reference_documents import documents, ReferenceError
from atlas.reference_service import ReferenceService
from atlas.reference_store import import_archive
from scripts.reference_learn import LearnPage
from tests.test_language_references import META, XML, archive, FakeEmbedding


def test_partial_translation_keeps_missing_documents_and_overloads(tmp_path):
    """RU содержит только одну перегрузку Select; другая и ToArray возвращаются по-английски."""
    service = ReferenceService(tmp_path, FakeEmbedding())
    try:
        en = import_archive(archive(tmp_path), META, service.store.directory)
        path = "api/xml/System.Linq/Enumerable.xml"
        original = list(documents(path, XML.encode(), "csharp"))[1]
        row = {**original, "text": "Преобразует каждый элемент последовательности.", "sourceUrl": "https://example.org/ru/select"}
        translated = tmp_path / "ru.zip"
        with zipfile.ZipFile(translated, "w") as output:
            output.writestr("page.reference.json", json.dumps([row], ensure_ascii=False))
        ru = import_archive(translated, {**META, "locale": "ru", "includes": ["*.reference.json"]}, service.store.directory)
        result = service.search("Select", pack_id=en["id"], locale="ru", mode="exact")
        assert len(result["results"]) == 2
        assert {h["locale"] for h in result["results"]} == {"ru", "en"}
        russian = next(h for h in result["results"] if h["locale"] == "ru")
        assert russian["sourceUrl"] == row["sourceUrl"]
        assert not russian["localeFallback"]
        page = service.store.document(ru["id"], russian["documentId"])
        assert page["sourceUrl"] == row["sourceUrl"] and "Преобразует" in page["text"]
        fallback = service.search("ToArray", pack_id=ru["id"], locale="ru", mode="exact")
        assert fallback["results"][0]["packId"] == en["id"]
        assert fallback["results"][0]["localeFallback"]
        english = service.search("Select", locale="en", mode="exact")
        assert len(english["results"]) == 2 and all(h["locale"] == "en" for h in english["results"])
        lexical = service.search("deferred", pack_id=en["id"], locale="ru", mode="fulltext")
        assert any(h["locale"] == "ru" for h in lexical["results"])
    finally:
        service.close()


def test_translations_never_cross_versions(tmp_path):
    """Русский документ версии 9 не заменяет английский версии 8."""
    service = ReferenceService(tmp_path, FakeEmbedding())
    try:
        import_archive(archive(tmp_path), META, service.store.directory)
        import_archive(archive(tmp_path), {**META, "version": "9", "locale": "ru"}, service.store.directory)
        result = service.search("ToArray", version="8", locale="ru", mode="exact")
        assert len(result["results"]) == 1 and result["results"][0]["version"] == "8"
        assert result["results"][0]["locale"] == "en"
    finally:
        service.close()


def test_learn_checks_prose_uid_moniker_and_ignores_other_versions():
    """Русский UI с английским описанием не выдаётся за перевод; чужой moniker исключён."""
    html = '''<meta name="locale" content="ru-ru"><meta name="uid" content="Example.Select*">
    <meta name="monikers" content="net-10.0"><meta name="description" content="Преобразует элементы последовательности &amp;lt;T&amp;gt;.">
    <div class="content"><h1>Select</h1><p data-moniker="net-9.0">Wrong version</p>
    <p data-moniker="net-10.0">Преобразует элементы.</p><script>throw Error()</script></div>'''
    parser = LearnPage("net-10.0"); parser.feed(html)
    result = parser.russian("Example.Select")
    assert "Wrong version" not in result["text"] and "throw Error" not in result["text"]
    assert "<T>" in result["text"] and "&lt;" not in result["text"]
    for altered, match in [(html.replace("Преобразует элементы последовательности &amp;lt;T&amp;gt;.", "Projects elements."), "Russian description"),
                           (html.replace('content="net-10.0"', 'content="net-9.0"'), "version"),
                           (html.replace("Example.Select*", "Example.Other*"), "UID")]:
        parser = LearnPage("net-10.0"); parser.feed(altered)
        with pytest.raises(ReferenceError, match=match):
            parser.russian("Example.Select")


def test_captured_json_rejects_unsafe_source_paths():
    """Офлайн JSON остаётся данными и проходит те же ограничения provenance/path."""
    row = {"path": "../unsafe", "title": "API", "symbol": "A", "signature": "", "text": "Original", "sourceUrl": "https://example.org"}
    with pytest.raises(ReferenceError, match="Unsafe"):
        list(documents("page.reference.json", json.dumps([row]).encode(), "efcore"))


def test_offline_translation_preparation_never_downloads(tmp_path, monkeypatch):
    """Cache-only не вызывает URL ни для страницы, ни для архива; 429 не повторяется."""
    from types import SimpleNamespace
    import urllib.request
    from urllib.error import HTTPError
    from scripts.reference_learn import fetch_page, LearnRateLimit
    from scripts.prepare_reference_translations import prepared_archive
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected network")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    with pytest.raises(ReferenceError, match="not captured"):
        fetch_page("https://learn.microsoft.com/ru-ru/example", tmp_path, offline=True)
    args = SimpleNamespace(offline_cache_only=True, cache=tmp_path, edition="fixture")
    with pytest.raises(ReferenceError, match="never downloads"):
        prepared_archive({"language": "python", "title": "Docs"}, args)
    calls = []
    def limited(url, **kwargs):
        calls.append(url)
        raise HTTPError(url, 429, "Too Many Requests", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", limited)
    with pytest.raises(LearnRateLimit):
        fetch_page("https://learn.microsoft.com/ru-ru/example", tmp_path)
    assert len(calls) == 1


def test_same_capture_produces_the_same_archive(tmp_path):
    """Повтор подготовки без изменения документа не создаёт новую immutable редакцию."""
    from scripts.prepare_reference_translations import write_captured_archive
    row = {"path": "source.md", "title": "Описание", "symbol": "source", "signature": "",
           "text": "Русский оригинал", "sourceUrl": "https://example.org/ru/", "aliases": ["z", "a"]}
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    write_captured_archive(first, [row], "CC-BY-4.0")
    write_captured_archive(second, [{**row, "aliases": ["a", "z"]}], "CC-BY-4.0")
    assert first.read_bytes() == second.read_bytes()
