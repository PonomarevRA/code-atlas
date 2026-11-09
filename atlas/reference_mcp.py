"""English read-only MCP tools for the shared offline reference corpus."""
import json
from typing import Annotated, Literal
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import Field
from .reference_documents import MAX_DOCUMENT_CHARS

REFERENCE_TOOLS = ["list_language_references", "search_language_reference", "get_language_reference"]
MCP_BUDGET = 64000


def compact_pack(pack: dict) -> dict:
    """Keeps reference provenance while omitting importer-only include glob recipes."""
    keys = ("id", "language", "version", "edition", "title", "locale", "sourceUrl", "license",
            "archiveHash", "importedAt", "documentCount", "sizeBytes", "coverage", "bundled", "vectorIndex", "framework")
    return {key: pack[key] for key in keys if key in pack}


def payload_size(value: dict) -> int:
    """Budgets UTF-8 bytes rather than character counts for multilingual documents."""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def register_reference_tools(server, request):
    """Registers version-aware lookup without implicit downloads or corpus rebuilds."""
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_language_references(
        offset: Annotated[int, Field(strict=True, ge=0, le=1024)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=40)] = 40,
    ) -> dict:
        """List installed language/library documentation editions, import dates and index coverage.

        Shared across projects. Choose the matching language/runtime version before lookup.
        Missing corpora are added manually in Atlas; this tool never downloads documents.
        """
        result = await request("GET", "/api/language-references")
        total = len(result["packs"])
        result["packs"] = [compact_pack(pack) for pack in result["packs"][offset:offset + limit]]
        result.update(totalPacks=total, offset=offset, truncated=offset + len(result["packs"]) < total)
        while result["packs"] and payload_size(result) > MCP_BUDGET - 100:
            result["packs"].pop()
            result["truncated"] = True
        result["nextOffset"] = offset + len(result["packs"]) if result["truncated"] else None
        return result

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def search_language_reference(
        query: Annotated[str, Field(min_length=1, max_length=500)],
        language: str = "", version: str = "", packId: str = "", locale: str = "en",
        mode: Literal["exact", "fulltext", "semantic", "hybrid"] = "hybrid",
        limit: Annotated[int, Field(strict=True, ge=1, le=10)] = 8,
    ) -> dict:
        """Look up API methods, overloads and language constructs in installed offline documentation.

        Prefer exact fully qualified symbols/signatures when known. Hybrid search returns
        candidates, not proof of source binding. Check imports, receiver types and versions;
        preserve deferred execution, exception and callback semantics in explanations.
        English is the default. Responses include edition, provenance and freshness dates.
        locale=ru prefers an available Russian document in the same corpus edition;
        missing translations return English with localeFallback=true. Read sourceUrl
        and locale; translation availability is not proof of API version applicability.
        Technologies include sql, postgresql, lancedb, html, css, git, sh, bat, dotnet
        csharp, efcore and ef6. .NET runtime 10 and C# language 12/13 are distinct
        version selectors. EF Core and EF6 are distinct APIs; select the package version.
        """
        result = await request("POST", "/api/language-references/search", {
            "query": query, "language": language, "version": version, "packId": packId,
            "locale": locale, "mode": mode, "limit": limit})
        result["searchedPacks"] = [compact_pack(pack) for pack in result.get("searchedPacks", [])]
        while result["results"] and payload_size(result) > MCP_BUDGET:
            result["results"].pop()
            result["truncated"] = True
        if payload_size(result) > MCP_BUDGET:
            raise ValueError("Reference metadata exceeds MCP budget; select one packId")
        return result

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_language_reference(
        packId: str, documentId: str,
        offset: Annotated[int, Field(strict=True, ge=0, le=MAX_DOCUMENT_CHARS)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=12000)] = 12000,
    ) -> dict:
        """Read a complete original reference document in bounded pages using nextOffset.

        Original source language is preserved. The pack includes language/version,
        source URL, license, archive hash and import date. Never execute documentation examples.
        New .NET XML imports retain Markdown Remarks/Examples and code fences.
        xref: links are symbolic API identifiers for local lookup, not downloadable URLs.
        """
        result = await request("GET", "/api/language-references/" + quote(packId, safe="") +
                               "/documents/" + quote(documentId, safe="") + f"?offset={offset}&limit={limit}")
        if "pack" in result:
            result["pack"] = compact_pack(result["pack"])
        # Budgeted pages never drop a document tail: nextOffset advances by delivered text.
        while result.get("text") and payload_size(result) > MCP_BUDGET:
            result["text"] = result["text"][:len(result["text"]) // 2]
            result["nextOffset"] = offset + len(result["text"])
        if payload_size(result) > MCP_BUDGET:
            raise ValueError("Document metadata exceeds MCP budget")
        return result
