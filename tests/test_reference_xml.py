"""Структура .NET XML, literal code и DocId должны переживать нормализацию."""
from atlas.reference_documents import documents
from atlas.reference_xml import normalize_xrefs
from atlas.reference_store import ReferenceStore, import_archive
import zipfile


def test_compiled_sections_keep_paragraphs_code_and_overload_identity():
    """Mixed content не склеивает слова; code сохраняет отступы и xref литералы."""
    source = b'''<doc><assembly><name>Acme</name></assembly><members>
      <member name="M:Acme.Run(System.Int32)"><summary>Uses <see cref="T:System.Array"/> safely.</summary>
      <param name="count">Number of items.</param><returns>The array.</returns>
      <remarks><para>First paragraph.</para><para>Second <paramref name="count"/> paragraph.</para>
      <list type="number"><item><description>Validate.</description></item><item><term>Then</term><description>Run.</description></item></list></remarks>
      <example><code language="csharp">
        if (ready)
        {
            Run(); // &lt;xref:Acme.Run&gt;
        }
      </code></example><exception cref="T:System.Exception">Failure.</exception>
      <inheritdoc cref="M:Other.Run"/><include file="/no-read" path="/*"/>
      </member></members></doc>'''
    record = next(documents('library-xml/Acme.xml', source, 'dotnet'))
    text = record['text']
    assert record['signature'] == 'M:Acme.Run(System.Int32)'
    assert 'Uses [System.Array](xref:T:System.Array) safely.' in text
    assert '## Parameter \u00b7 count' in text and '## Returns' in text
    assert '## Remarks\n\nFirst paragraph.\n\n' in text
    assert 'Second count paragraph.' in text
    assert '1. Validate.\n2. Then: Run.' in text
    assert '## Examples\n\n```csharp\nif (ready)\n{\n    Run(); // <xref:Acme.Run>\n}' in text
    assert 'Unresolved inheritdoc' in text and 'Unresolved include' in text
    assert '## Exception \u00b7 System.Exception' in text


def test_type_docs_preserve_docfx_cdata_and_avoid_duplicate_headings():
    """Реальный mdoc формат содержит Markdown и fence внутри format/CDATA."""
    source = b'''<Type FullName="Acme.Widget"><Docs><summary>A widget.</summary>
      <remarks><format type="text/markdown"><![CDATA[
## Remarks

See <xref:System.Linq.Enumerable.Select*?displayProperty=nameWithType>.

## Examples

```csharp
if (value != null)
{
    var literal = "<xref:Acme.Widget>";
}
```
      ]]></format></remarks></Docs><Members/></Type>'''
    record = next(documents('api/xml/Acme/Widget.xml', source, 'csharp'))
    assert record['text'].count('## Remarks') == 1
    assert record['text'].count('## Examples') == 1
    assert '[Enumerable.Select](xref:System.Linq.Enumerable.Select*?displayProperty=nameWithType)' in record['text']
    assert '    var literal = "<xref:Acme.Widget>";' in record['text']
    assert record['summary'] == 'A widget.'


def test_markdown_xrefs_do_not_change_inline_fenced_or_tilde_code():
    original = ('See <xref:Acme.Run>. `<xref:Acme.Literal>`\n'
                '~~~csharp\n<xref:Acme.Code>\n~~~\n'
                '````\n``` <xref:Acme.Nested>\n````\n'
                '<xref:Acme.Next>\n')
    normalized = normalize_xrefs(original)
    assert '[Acme.Run](xref:Acme.Run)' in normalized and '[Acme.Next](xref:Acme.Next)' in normalized
    assert '`<xref:Acme.Literal>`' in normalized
    assert '<xref:Acme.Code>' in normalized and '<xref:Acme.Nested>' in normalized
    guide = next(documents('docs/guide.md', original.encode(), 'dotnet'))
    assert guide['text'] == normalized


def test_code_with_backticks_is_kept_inside_longer_fence():
    source = b'<doc><members><member name="M:Acme.Run"><example><code>```\nx\n```</code></example></member></members></doc>'
    record = next(documents('library-xml/Acme.xml', source, 'dotnet'))
    assert '````\n```\nx\n```\n````' in record['text']


def test_xml_table_headers_are_not_lost():
    source = b'''<doc><members><member name="M:Acme.Run"><remarks><list type="table">
    <listheader><term>State</term><description>Action</description></listheader>
    <item><term>Ready</term><description>Run.</description></item>
    </list></remarks></member></members></doc>'''
    record = next(documents('library-xml/Acme.xml', source, 'dotnet'))
    assert '| State | Action |\n| --- | --- |\n| Ready | Run. |' in record['text']


def test_docfx_generic_uid_resolves_without_overriding_exact_overload(tmp_path):
    """DocFX arity работает со старым alias, но полный DocId имеет приоритет."""
    source = b'''<Type FullName="Acme.List&lt;T&gt;"><Docs><summary>A list.</summary></Docs><Members>
      <Member MemberName="Count"><MemberSignature Language="DocId" Value="P:Acme.List`1.Count"/>
      <Docs><summary>Number of items.</summary></Docs></Member>
      <Member MemberName="Find"><MemberSignature Language="DocId" Value="M:Acme.List`1.Find(System.Int32)"/>
      <Docs><summary>Integer match.</summary></Docs></Member>
      <Member MemberName="Find"><MemberSignature Language="DocId" Value="M:Acme.List`1.Find(System.String)"/>
      <Docs><summary>String match.</summary></Docs></Member></Members></Type>'''
    archive = tmp_path / 'refs.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr('api/xml/Acme/List.xml', source)
    meta = {'language':'dotnet', 'version':'10', 'edition':'fixture', 'title':'API',
            'sourceUrl':'https://example.org/api', 'license':'MIT'}
    pack = import_archive(archive, meta, tmp_path / 'language-references')
    store = ReferenceStore(tmp_path); selected = store.pack(pack['id'])
    assert len(store.search_pack(selected, 'Acme.List`1.Count', 'exact', 10)) == 1
    overload = store.search_pack(selected, 'Acme.List`1.Find(System.Int32)', 'exact', 10)
    assert len(overload) == 1 and 'Int32' in overload[0]['signature']
    assert len(store.search_pack(selected, 'Acme.List`1.Find', 'exact', 10)) == 2
