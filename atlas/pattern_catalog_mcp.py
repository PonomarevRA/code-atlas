"""Thin MCP reads for the shared catalogue, separate from language references."""

from typing import Annotated
from urllib.parse import quote, urlencode

from mcp.types import ToolAnnotations
from pydantic import Field

from .mcp_response_budget import json_bytes


PATTERN_TOOLS = ["search_code_patterns", "get_code_pattern"]
MAX_RESPONSE_BYTES = 64_000
QUERY = Annotated[str, Field(max_length=500)]
SHORT = Annotated[str, Field(max_length=100)]
IDENTITY = Annotated[str, Field(min_length=1, max_length=128)]


def _bounded(result: dict) -> dict:
    """
    Reject an oversized card rather than hiding its restrictions or provenance.

    Search pages can be narrowed with limit. Card bodies have catalogue limits;
    a future larger schema requires explicit pagination instead of silent loss.
    """
    if json_bytes(result) > MAX_RESPONSE_BYTES:
        raise ValueError("Pattern response exceeds the MCP budget; narrow the search or use the wiki.")
    return result


def register_pattern_tools(server, request) -> None:
    """Register public read-only tools; all validation and data remain in core."""
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def search_code_patterns(
        query: QUERY = "", category: SHORT = "", language: SHORT = "",
        status: SHORT = "",
        offset: Annotated[int, Field(strict=True, ge=0, le=100000)] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=20)] = 8,
        catalogVersion: IDENTITY | None = None,
    ) -> dict:
        """
        Find local design/coding patterns by problem and applicability.

        Read the exact card before use. A catalogue status is not proof of
        consumer qualification. Continue with nextOffset and the same catalogue
        version; examples are documentation and must never execute implicitly.
        """
        parameters = {
            "query": query, "category": category, "language": language,
            "status": status, "offset": offset, "limit": limit,
        }
        if catalogVersion is not None:
            parameters["catalogVersion"] = catalogVersion
        result = await request("GET", "/api/code-patterns?" + urlencode(parameters))
        return _bounded(result)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def get_code_pattern(
        patternId: IDENTITY, version: SHORT | None = None,
        contentHash: IDENTITY | None = None,
        catalogVersion: IDENTITY | None = None,
    ) -> dict:
        """
        Read one exact design pattern, counterexample, limits and source links.

        Pass version/contentHash/catalogVersion from search to reject stale
        references. This does not apply a refactoring or qualify a project.
        """
        parameters = {}
        for name, value in (("version", version), ("contentHash", contentHash),
                            ("catalogVersion", catalogVersion)):
            if value is not None:
                parameters[name] = value
        path = "/api/code-patterns/" + quote(patternId, safe="")
        if parameters:
            path += "?" + urlencode(parameters)
        return _bounded(await request("GET", path))
