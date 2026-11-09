"""Entity Framework NuGet doc-comments retain overloads, versions and execution caveats."""
import zipfile
import pytest
from atlas.reference_documents import documents, eligible, file_limit, MAX_FILE_BYTES, ReferenceError
from atlas.reference_store import import_archive, ReferenceStore
from scripts.reference_sources import ADDITIONAL_SOURCES

XML = b'''<doc><assembly><name>Microsoft.EntityFrameworkCore</name></assembly><members>
<member name="M:Microsoft.EntityFrameworkCore.EntityFrameworkQueryableExtensions.ToListAsync``1(System.Linq.IQueryable{``0},System.Threading.CancellationToken)"><summary>Asynchronously creates a list from an IQueryable.</summary><param name="source">The source query.</param><remarks>Do not execute multiple operations on the same context.</remarks><exception cref="T:System.OperationCanceledException">Cancellation.</exception></member>
<member name="M:Microsoft.EntityFrameworkCore.EntityFrameworkQueryableExtensions.ToListAsync``1(System.Linq.IQueryable{``0})"><summary>Another overload.</summary></member>
</members></doc>'''
META = {"language": "efcore", "version": "9.0.0", "edition": "fixture", "title": "EF Core fixture",
        "sourceUrl": "https://example.org/package", "license": "MIT", "includes": ["lib/net8.0/*.xml"]}


def test_nuget_xml_preserves_overloads_and_author_summary(tmp_path):
    """Оба overload сохраняются; executable DLL не импортируется, generic lookup читает remarks."""
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("lib/net8.0/Microsoft.EntityFrameworkCore.xml", XML)
        package.writestr("lib/net8.0/Microsoft.EntityFrameworkCore.dll", b"not loaded")
    result = import_archive(archive, META, tmp_path / "language-references")
    assert result["documentCount"] == 2
    store = ReferenceStore(tmp_path)
    hits = store.search_pack(store.pack(result["id"]), "Microsoft.EntityFrameworkCore.EntityFrameworkQueryableExtensions.ToListAsync<int>()", "exact", 10)
    assert len(hits) == 2
    pages = [store.document(result["id"], h["documentId"]) for h in hits]
    assert all(p["signature"].startswith("M:") for p in pages)
    assert any("multiple operations" in p["text"] and "Cancellation" in p["text"] for p in pages)
    assert any(p["summary"] == "Asynchronously creates a list from an IQueryable." for p in pages)
    assert all(p["pack"]["version"] == "9.0.0" for p in pages)


def test_ef6_remains_a_distinct_api_namespace_and_guide_scope():
    """EF6 имена не превращаются в EF Core; разделы guides отбираются раздельно."""
    original = XML.replace(b'Microsoft.EntityFrameworkCore.EntityFrameworkQueryableExtensions', b'System.Data.Entity.QueryableExtensions')
    refs = list(documents("lib/net45/EntityFramework.xml", original, "ef6"))
    assert refs[0]["symbol"] == "System.Data.Entity.QueryableExtensions.ToListAsync"
    assert eligible("docs/entity-framework/core/querying/index.md", "efcore")
    assert not eligible("docs/entity-framework/ef6/querying/index.md", "efcore")
    assert eligible("docs/entity-framework/ef6/querying/index.md", "ef6")


def test_ef_xml_limit_does_not_relax_other_file_limits():
    """Только монолитный EF XML имеет увеличенный лимит; DTD по-прежнему запрещён."""
    assert file_limit("lib/net10.0/API.xml", "efcore") == 32*1024*1024
    assert file_limit("docs/page.md", "efcore") == MAX_FILE_BYTES
    assert file_limit("lib/API.xml", "csharp") == MAX_FILE_BYTES
    with pytest.raises(ReferenceError, match="entities"):
        list(documents("lib/API.xml", b'<!DOCTYPE doc [<!ENTITY x "bad">]><doc/>', "efcore"))


def test_ef_package_versions_and_target_framework_are_independent():
    """EF Core 9 использует net8.0 XML; core и relational пакеты всех трёх версий зарегистрированы."""
    sources=[s for s in ADDITIONAL_SOURCES if s["language"] == "efcore" and s["version"] != "multi-version"]
    assert {s["version"] for s in sources} == {"8.0.0","9.0.0","10.0.0"}
    assert len(sources)==6
    assert all(s["includes"] == ["lib/net8.0/*.xml"] for s in sources if s["version"]=="9.0.0")
