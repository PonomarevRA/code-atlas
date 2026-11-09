"""Read-only HTTP adapter for the local versioned engineering pattern catalogue."""

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from .code_patterns import PatternCatalogError


def register_pattern_routes(app: FastAPI, catalog) -> None:
    """
    Register bounded catalogue reads without changing projects or executing examples.

    The catalogue owns validation and exact content identity. This adapter only
    maps HTTP parameter names and preserves its structured error category.
    """
    @app.exception_handler(PatternCatalogError)
    async def pattern_error(request, error):
        """Preserve a catalogue failure without reflecting request data."""
        return JSONResponse({"error": str(error), "code": error.code}, status_code=error.status)

    @app.get("/api/code-patterns")
    def search_patterns(
        query: str = Query("", max_length=500),
        category: str = Query("", max_length=100),
        language: str = Query("", max_length=100),
        status: str = Query("", max_length=40),
        offset: int = Query(0, ge=0, le=100000),
        limit: int = Query(8, ge=1, le=20),
        catalogVersion: str | None = Query(None, max_length=128),
    ):
        """Search one catalogue revision; continuation never changes the source."""
        return catalog.search(
            query=query, category=category, language=language, status=status,
            offset=offset, limit=limit, catalog_version=catalogVersion,
        )

    @app.get("/api/code-patterns/{pattern_id}")
    def read_pattern(
        pattern_id: str,
        version: str | None = Query(None, max_length=100),
        contentHash: str | None = Query(None, max_length=128),
        catalogVersion: str | None = Query(None, max_length=128),
    ):
        """Read an exact card; stale version/hash references are not upgraded."""
        return catalog.get(
            pattern_id, version=version, content_hash=contentHash,
            catalog_version=catalogVersion,
        )
