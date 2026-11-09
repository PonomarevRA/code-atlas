"""Проверки новых источников, отбора framework и безопасной подготовки архивов."""
import io
import tarfile
import zipfile

import pytest

from atlas.reference_documents import documents, eligible, LANGUAGES, ReferenceError
from atlas.reference_store import import_archive, ReferenceStore, validate_manifest
from atlas.reference_service import ReferenceService
from atlas.reference_framework import framework_members
from scripts.reference_downloads import tar_to_zip
from scripts.reference_sources import ADDITIONAL_SOURCES

META = {"language": "dotnet", "version": "10", "edition": "fixture", "locale": "en",
        "title": ".NET 10 fixture", "sourceUrl": "https://example.org/docs", "license": "fixture", "framework": "net-10.0"}
XML = b'''<Type FullName="T"><TypeSignature Language="DocId" Value="T:T"/>
<Docs><summary>Type documented for several runtimes.</summary></Docs><Members>
<Member MemberName="Select"><MemberSignature Language="DocId" Value="M:T.Select"/>
<MemberSignature Language="C#" Value="old signature" FrameworkAlternate="net-9.0"/>
<MemberSignature Language="C#" Value="selected signature" FrameworkAlternate="net-10.0;net-11.0"/>
<Docs><summary>Selected API.</summary></Docs></Member>
<Member MemberName="Future"><MemberSignature Language="DocId" Value="M:T.Future"/>
<Docs><summary>API available only in a later runtime.</summary></Docs></Member>
</Members></Type>'''


def test_dotnet_framework_uses_docids_and_selected_signature(tmp_path):
    archive = tmp_path / "api.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("api/xml/T.xml", XML)
        package.writestr("api/xml/FrameworksIndex/net-10.0.xml", '<Framework Name="net-10.0"><Type Id="T:T"><Member Id="M:T.Select"/></Type></Framework>')
    pack = import_archive(archive, META, tmp_path / "language-references")
    assert pack["framework"] == "net-10.0" and pack["documentCount"] == 2
    store = ReferenceStore(tmp_path)
    imported = store.pack(pack["id"])
    hit = store.search_pack(imported, "T.Select", "exact", 10)[0]
    page = store.document(pack["id"], hit["documentId"])
    assert page["signature"] == "selected signature"
    assert not store.search_pack(imported, "T.Future", "exact", 10)
    with pytest.raises(ReferenceError, match="framework"):
        validate_manifest({**META, "framework": "../../evil"})
    with pytest.raises(ReferenceError, match="declared"):
        validate_manifest({**META, "framework": "net-11.0"})
    with pytest.raises(ReferenceError, match="FrameworksIndex"):
        import_archive(archive, {**META, "framework": "net-11.0", "version": "11"}, tmp_path / "language-references")
    assert len(store.catalog()) == 1


def test_new_formats_and_exact_command_aliases():
    assert eligible("mdn/files/en-us/web/html/reference/elements/input/index.md", "html")
    assert not eligible("mdn/files/en-us/web/javascript/reference/index.md", "html")
    assert eligible("git/Documentation/git-rebase.adoc", "git")
    assert eligible("db/docs/web/search.mdx", "lancedb")
    git = next(documents("git/Documentation/git-rebase.adoc", b'git-rebase(1)\n=============\nReapply commits', "git"))
    assert "git rebase" in git["aliases"]
    pg = next(documents("postgres/html/sql-select.html", b'<title>SELECT</title><p>Rows</p>', "postgresql"))
    assert "SELECT" in pg["aliases"]
    sh = next(documents("posix/sh.html", b'<title>sh</title><a name="tag_case"></a><p>Shell command language</p>', "sh"))
    assert "tag_case" in sh["aliases"]


def test_sources_cover_requested_versions_without_shared_cache_collisions():
    assert {"sql", "postgresql", "lancedb", "html", "css", "git", "sh", "bat", "dotnet"} <= set(LANGUAGES)
    assert {"12", "13"} <= {s["version"] for s in ADDITIONAL_SOURCES if s["language"] == "csharp"}
    assert any(s.get("framework") == "net-10.0" for s in ADDITIONAL_SOURCES)
    cached = {}
    for source in ADDITIONAL_SOURCES:
        identity = (source.get("url"), source.get("format", "zip"), source.get("pages"))
        key = source["cacheKey"]
        assert key not in cached or cached[key] == identity
        cached[key] = identity


@pytest.mark.parametrize("content", [
    b'<!DOCTYPE Framework [<!ENTITY e "expanded">]><Framework Name="net-10.0"><Type Id="T:T"/></Framework>',
    '<Framework Name="net-10.0"><Type Id="T:T"/></Framework>'.encode("utf-16"),
])
def test_framework_index_rejects_entities_and_non_utf8(tmp_path, content):
    archive = tmp_path / "invalid.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("api/xml/FrameworksIndex/net-10.0.xml", content)
    with zipfile.ZipFile(archive) as package, pytest.raises(ReferenceError):
        framework_members(package, "net-10.0")


@pytest.mark.parametrize("unsafe", ["../evil", "/absolute", "symlink"])
def test_tar_preparation_rejects_unsafe_entries_preserving_existing_zip(tmp_path, unsafe):
    archive, result = tmp_path / "source.tar.gz", tmp_path / "old.zip"
    result.write_bytes(b"published")
    with tarfile.open(archive, "w:gz") as package:
        item = tarfile.TarInfo(unsafe)
        if unsafe == "symlink":
            item.type, item.linkname = tarfile.SYMTYPE, "outside"
            package.addfile(item)
        else:
            item.size = 3
            package.addfile(item, io.BytesIO(b"doc"))
    with pytest.raises(ReferenceError, match="Unsafe TAR"):
        tar_to_zip(archive, result)
    assert result.read_bytes() == b"published" and not list(tmp_path.glob("*.part"))


def test_tar_preparation_preserves_pages_and_license(tmp_path):
    archive, result = tmp_path / "source.tar.gz", tmp_path / "new.zip"
    with tarfile.open(archive, "w:gz") as package:
        for name, body in [("pg/html/index.html", b"<p>original</p>"), ("pg/COPYING", b"license")]:
            item = tarfile.TarInfo(name); item.size = len(body)
            package.addfile(item, io.BytesIO(body))
    tar_to_zip(archive, result)
    with zipfile.ZipFile(result) as package:
        assert package.read("pg/html/index.html") == b"<p>original</p>"
        assert package.read("pg/COPYING") == b"license"


def test_unfiltered_search_retains_csharp_with_more_than_sixteen_packs(tmp_path):
    class Offline:
        def available(self): return False
    service = ReferenceService(tmp_path, Offline())
    try:
        packs = [{"id": str(i), "language": "sql", "version": str(i), "edition": "v1", "title": str(i), "locale": "en"} for i in range(20)]
        packs.append({**packs[0], "id": "csharp", "language": "csharp", "title": "C#"})
        service.store.catalog = lambda: packs
        service.store.search_pack = lambda pack, query, mode, limit: []
        result = service.search("ToArray", mode="exact")
        assert len(result["searchedPacks"]) == 21 and not result["warnings"] and not result["truncated"]
    finally:
        service.close()
