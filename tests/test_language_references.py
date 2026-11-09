"""Контракты offline справочников: полные оригиналы, версии, atomic import и lookup."""
import asyncio
import json
from pathlib import Path
import time
import zipfile

from fastapi.testclient import TestClient
import httpx
import pytest

from atlas.reference_documents import ReferenceError, documents
from atlas.reference_store import import_archive, ReferenceStore
from atlas.reference_service import ReferenceService
from atlas.server import create_app

META = {"language": "csharp", "version": "8", "edition": "v1", "title": ".NET API",
        "locale": "en", "sourceUrl": "https://example.org/api", "license": "CC-BY-4.0"}
XML = '''<Type FullName="System.Linq.Enumerable"><Docs><summary>Sequence methods.</summary></Docs><Members>
<Member MemberName="Select"><MemberSignature Language="C#" Value="Select(IEnumerable source, Func selector)"/><Docs><summary>Projects each element of a sequence.</summary><remarks>Uses deferred execution.</remarks></Docs></Member>
<Member MemberName="Select"><MemberSignature Language="C#" Value="Select(IEnumerable source, Func indexedSelector)"/><Docs><summary>Projects with an index.</summary></Docs></Member>
<Member MemberName="ToArray"><MemberSignature Language="C#" Value="ToArray(IEnumerable source)"/><Docs><summary>Creates an array.</summary><remarks>Enumerates immediately.</remarks></Docs></Member>
</Members></Type>'''


def archive(tmp_path, filename="docs.zip", entries=None):
    """ZIP содержит XML перегрузок и original license, без фиктивного сетевого MCP."""
    path = tmp_path / filename
    with zipfile.ZipFile(path, "w") as output:
        for key, value in (entries or {"api/xml/System.Linq/Enumerable.xml": XML, "api/LICENSE": "Original license"}).items():
            output.writestr(key, value)
    return path


class FakeEmbedding:
    """Малая детерминированная модель позволяет выполнить настоящий LanceDB build/search."""
    def available(self): return True
    def identity(self): return {"dimensions": 2, "fingerprint": "test-local"}
    def split_text(self, text): return [{"text": text[:500]}]
    def embed(self, texts): return [[1., 0.] if "array" in t.lower() else [0., 1.] for t in texts]
    def query(self, text): return self.embed([text])[0]


def test_versions_overloads_full_text_and_no_mutation(tmp_path):
    source = archive(tmp_path)
    original = source.read_bytes()
    store = ReferenceStore(tmp_path)
    first = import_archive(source, META, store.directory)
    assert first["documentCount"] == 4
    assert import_archive(source, META, store.directory)["id"] == first["id"]
    second = import_archive(source, {**META, "version": "10", "edition": "v2"}, store.directory)
    assert first["id"] != second["id"]
    assert source.read_bytes() == original
    assert len(store.catalog()) == 2
    pack = store.pack(first["id"])
    hits = store.search_pack(pack, "System.Linq.Enumerable.Select", "exact", 10)
    assert len(hits) == 2 and len({hit["documentId"] for hit in hits}) == 2
    assert all(hit["excerptScope"] == "author-summary" for hit in hits)
    assert not any("Assembly versions" in hit["excerpt"] for hit in hits)
    body = store.document(first["id"], hits[0]["documentId"], limit=10)
    assert body["nextOffset"] == 10 and body["totalChars"] > 10
    remainder = store.document(first["id"], hits[0]["documentId"], offset=10)
    assert len(body["text"] + remainder["text"]) == body["totalChars"]
    assert store.search_pack(pack, 'deferred " OR *)', "fulltext", 10)
    assert not store.search_pack(pack, "NotARealMethod", "exact", 10)
    assert store.search_pack(pack, "  System.Linq.Enumerable.ToArray<int>()  ", "exact", 10)
    assert "Original license" in (Path(pack["_directory"]) / "licenses.json").read_text()


@pytest.mark.parametrize("entry,text", [("../outside.md", "bad"), ("/absolute.md", "bad"),
    ("api/xml/T.xml", '<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><Type/>'),
    ("api/xml/T.xml", "<broken>")])
def test_import_failure_never_replaces_published_corpus(tmp_path, entry, text):
    first = import_archive(archive(tmp_path), META, tmp_path / "packs")
    malformed = archive(tmp_path, "bad.zip", {entry: text})
    with pytest.raises(ReferenceError):
        import_archive(malformed, {**META, "edition": "v2"}, tmp_path / "packs")
    assert (tmp_path / "packs" / first["id"] / "manifest.json").is_file()
    assert not list((tmp_path / "packs").glob(".import-*"))


def test_zip_limits_and_symlink(tmp_path, monkeypatch):
    from atlas import reference_store as module
    source = archive(tmp_path)
    monkeypatch.setattr(module, "MAX_EXPANDED_BYTES", 20)
    with pytest.raises(ReferenceError, match="expanded"):
        import_archive(source, META, tmp_path / "packs")
    monkeypatch.setattr(module, "MAX_EXPANDED_BYTES", 100000)
    path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(path, "w") as output:
        entry = zipfile.ZipInfo("api/xml/evil.xml"); entry.external_attr = (0o120777 << 16)
        output.writestr(entry, "/etc/passwd")
    with pytest.raises(ReferenceError, match="Unsafe"):
        import_archive(path, META, tmp_path / "packs")


def test_parser_preserves_code_as_text_and_language_api_aliases():
    html = list(documents("index.html", b'<title>API</title><script>alert(1)</script><dt id="list.append">Append</dt><p>Full original</p>', "python"))[0]
    assert "alert" not in html["text"] and "list.append" in html["aliases"]
    tutorial = list(documents("tutorial.html", b'<dt class="sig sig-object py"><span>list.</span>\n<span>append</span>(value)</dt>', "python"))[0]
    assert "list.append" in tutorial["aliases"]
    js = list(documents("content/files/en-us/web/javascript/reference/global_objects/array/map/index.md", b"# Array.map\nOriginal docs", "javascript"))[0]
    assert "array.map" in js["aliases"]
    go = list(documents("go/src/strings/strings.go", b"package strings\n// Trim trims.\nfunc Trim(s string) string {}", "go"))[0]
    assert "strings.Trim" in go["aliases"]
    receiver = list(documents("go/src/strings/reader.go", b"package strings\nfunc (r *Reader) Read(b []byte) {}", "go"))[0]
    assert "strings.Reader.Read" in receiver["aliases"] and "strings.Read" not in receiver["aliases"]
    assert list(documents("api/xml/T.xml", XML.encode(), "csharp"))[-1]["summary"] == "Creates an array."
    java = list(documents("jdk/src/java.base/share/classes/java/util/Arrays.java", b"package java.util;\npublic class Arrays { public static int[] copyOf(int[] a, int n) {} }", "java"))[0]
    assert "java.util.Arrays.copyOf" in java["aliases"]
    generic = list(documents("api/xml/T.xml", XML.replace('MemberName="ToArray"', 'MemberName="ToArray&lt;TSource&gt;"').encode(), "csharp"))
    assert "System.Linq.Enumerable.ToArray" in generic[-1]["aliases"]
    cdata = list(documents("api/xml/T.xml", b'<Type FullName="T"><Docs><summary><![CDATA[Example: <!DOCTYPE html>]]></summary></Docs></Type>', "csharp"))
    assert "DOCTYPE" in cdata[0]["text"]


def test_vectors_locale_fallback_and_version_scoping(tmp_path):
    store = ReferenceStore(tmp_path)
    first = import_archive(archive(tmp_path), META, store.directory)
    second = import_archive(archive(tmp_path), {**META, "version": "10"}, store.directory)
    service = ReferenceService(tmp_path, FakeEmbedding())
    try:
        service.vectors.build(service.store.pack(first["id"]), lambda **kw: None)
        result = service.search("array", version="8", locale="ru")
        assert all(hit["version"] == "8" for hit in result["results"])
        assert result["requestedLocale"] == "ru" and result["results"][0]["locale"] == "en"
        assert service.vectors.status(first["id"])["documentCount"] == 4
        assert service.vectors.status(first["id"])["coverage"] == "document-summaries"
        assert service.vectors.status(second["id"]) is None
        exact = service.search("ToArray", mode="exact", pack_id=first["id"])
        assert exact["results"][0]["match"] == "exact"
        overloads = service.search("Select", mode="exact", pack_id=first["id"], limit=1)
        assert len(overloads["results"]) == 1 and overloads["truncated"]
        with pytest.raises(ReferenceError, match="does not match"):
            service.search("ToArray", pack_id=first["id"], version="10")
    finally:
        service.close()


def test_cancelled_rebuild_preserves_published_vectors_and_fulltext(tmp_path):
    """Неудачная ручная пересборка не заменяет поколение, доступное читателям."""
    store = ReferenceStore(tmp_path)
    imported = import_archive(archive(tmp_path), META, store.directory)
    service = ReferenceService(tmp_path, FakeEmbedding())
    try:
        pack = service.store.pack(imported["id"])
        previous = service.vectors.build(pack, lambda **kw: None)
        with pytest.raises(ReferenceError, match="cancelled"):
            service.vectors.build(pack, lambda **kw: None, cancelled=lambda: True)
        assert service.vectors.status(pack["id"]) == previous
        assert service.vectors.search(pack, [1., 0.], 2)
        assert service.store.search_pack(pack, "ToArray", "exact", 1)
        assert not list((service.vectors.directory / pack["id"]).glob(".vectors-*"))
    finally:
        service.close()


def test_model_mismatch_falls_back_and_locale_editions_remain_separate(tmp_path):
    """Новая модель и локализация не запускают неявное обновление или смешение версий."""
    store = ReferenceStore(tmp_path)
    english = import_archive(archive(tmp_path), META, store.directory)
    russian = import_archive(archive(tmp_path, "ru.zip", {"api/xml/T.xml": XML.replace("Creates an array.", "Создаёт массив.")}),
                              {**META, "locale": "ru"}, store.directory)
    service = ReferenceService(tmp_path, FakeEmbedding())
    try:
        service.vectors.build(store.pack(english["id"]), lambda **kw: None)
        service.embedder.identity = lambda: {"dimensions": 2, "fingerprint": "another-model"}
        result = service.search("array", pack_id=english["id"])
        assert result["results"] and all(hit["match"] == "fulltext" for hit in result["results"])
        assert any("different model" in warning for warning in result["warnings"])
        assert service.vectors.status(english["id"])["model"]["fingerprint"] == "test-local"
        localized = service.search("ToArray", language="csharp", version="8", locale="ru", mode="exact")
        assert {hit["packId"] for hit in localized["results"]} == {russian["id"]}
        assert "Создаёт массив" in localized["results"][0]["excerpt"]
        assert service.vectors.status(russian["id"]) is None
        selected = service.search("ToArray", pack_id=russian["id"], locale="en", mode="exact")
        assert selected["results"][0]["locale"] == "ru"
    finally:
        service.close()


def test_catalog_and_exact_read_do_not_contact_network_or_load_model(tmp_path, monkeypatch):
    """Чтение справочника обходится без модели, download и неявного writer job."""
    import socket
    store = ReferenceStore(tmp_path)
    imported = import_archive(archive(tmp_path), META, store.directory)
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected network/model operation")
    model = FakeEmbedding()
    model.available = lambda: False
    model.identity = model.embed = model.query = forbidden
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    service = ReferenceService(tmp_path, model)
    try:
        assert not service.catalog()["modelAvailable"]
        result = service.search("ToArray", mode="exact")
        assert result["results"] and not service.jobs
        assert service.store.document(imported["id"], result["results"][0]["documentId"])["text"]
        assert not service.vectors.directory.exists()
    finally:
        service.close()


def test_api_import_and_persistence_without_implicit_build(tmp_path):
    source = archive(tmp_path)
    with TestClient(create_app(tmp_path / "state"), base_url="http://127.0.0.1:8766") as client:
        route = "/api/language-references"
        # Built-in corpora are independent of this test's manually added pack.
        imported = client.post(route + "/import", json={"archivePath": str(source), "manifest": META})
        assert imported.status_code == 202, imported.text
        job_id = imported.json()["id"]
        for _ in range(100):
            job = client.get(route + "/jobs/" + job_id).json()
            if job["status"] != "running": break
            time.sleep(.01)
        assert job["status"] == "completed", job
        pack_id = job["result"]["id"]
        result = client.post(route + "/search", json={"query": "ToArray", "packId": pack_id, "mode": "exact"})
        assert result.status_code == 200, result.text
        hit = result.json()["results"][0]
        body = client.get(route + f'/{pack_id}/documents/{hit["documentId"]}').json()
        assert "Creates an array" in body["text"] and body["pack"]["importedAt"]
        assert client.post(route + "/search", json={"query": "x", "limit": 30}).status_code == 422
        assert client.post(route + "/search", json={"query": "x", "sourceCode": "RAW"}).status_code == 422
    with TestClient(create_app(tmp_path / "state"), base_url="http://127.0.0.1:8766") as client:
        packs = client.get(route).json()["packs"]
        selected = next(p for p in packs if p["id"] == pack_id)
        assert selected["vectorIndex"] is None
        assert client.get("/language-docs.html").status_code == 200
        assert client.get("/locales/ru/references.json").status_code == 200


def test_actual_mcp_reference_tools_are_english_and_read_only(monkeypatch):
    from atlas import agent_mcp
    calls = []
    original = httpx.AsyncClient
    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert json.loads(request.content)["locale"] == "en"
        return httpx.Response(200, json={"packs": [], "results": [], "offline": True})
    monkeypatch.setattr(agent_mcp.httpx, "AsyncClient", lambda **options: original(**options, transport=httpx.MockTransport(handler)))
    async def scenario():
        mcp = agent_mcp.create_mcp(8766, project_id="unrelated-project")
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        assert tools["search_language_reference"].annotations.readOnlyHint
        assert "offline documentation" in tools["search_language_reference"].description
        await mcp.call_tool("list_language_references", {})
        await mcp.call_tool("search_language_reference", {"query": "ToArray"})
        await mcp.call_tool("get_language_reference", {"packId": "one", "documentId": "two"})
    asyncio.run(scenario())
    assert len(calls) == 3


def test_mcp_utf8_budget_preserves_catalog_cursor_and_document_tail():
    """Много редакций и Unicode читаются продолжением, без потери хвоста документа."""
    from mcp.server.fastmcp import FastMCP
    from atlas.reference_mcp import register_reference_tools, payload_size
    from urllib.parse import parse_qs, urlparse
    packs = [{**META, "id": str(i), "sourceUrl": "https://example.org/" + "Ж" * 1900,
              "includes": ["x" * 500] * 20} for i in range(40)]
    original = "🙂" * 24000
    async def request(method, path, body=None):
        if path == "/api/language-references":
            return {"packs": packs[:], "offline": True}
        query = parse_qs(urlparse(path).query)
        offset = int(query["offset"][0]); limit = int(query["limit"][0])
        return {"text": original[offset:offset + limit], "totalChars": len(original), "offset": offset,
                "nextOffset": offset + limit if offset + limit < len(original) else None,
                "pack": packs[0], "path": "Ж" * 6000, "signature": "Ж" * 2000}
    def unpack(result):
        return result[1] if isinstance(result, tuple) else json.loads(result[0].text)
    async def scenario():
        mcp = FastMCP("reference-budget")
        register_reference_tools(mcp, request)
        first = unpack(await mcp.call_tool("list_language_references", {}))
        assert payload_size(first) <= 64000 and first["nextOffset"]
        second = unpack(await mcp.call_tool("list_language_references", {"offset": first["nextOffset"]}))
        assert first["packs"][-1]["id"] != second["packs"][0]["id"]
        assert all("includes" not in pack for pack in first["packs"])
        offset, delivered = 0, ""
        while True:
            page = unpack(await mcp.call_tool("get_language_reference", {"packId": "0", "documentId": "one", "offset": offset}))
            assert payload_size(page) <= 64000
            delivered += page["text"]
            if page["nextOffset"] is None:
                break
            assert page["nextOffset"] == offset + len(page["text"])
            offset = page["nextOffset"]
        assert delivered == original
    asyncio.run(scenario())
