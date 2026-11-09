"""Local MCP entry points for capturing and sharing an agent change review."""

from typing import Annotated
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import Field

from .checkout_maps_mcp import Identity, Session

CHANGE_REVIEW_TOOLS = ['begin_change_review', 'finish_change_review', 'get_change_review']


def register_change_review_tools(server, request, base, project_id=None, data_map_id=None):
    """Use the same connection scope as the private checkout tools."""
    read = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    def path(review_id=None):
        """Construct a review route only on an unbound local connection."""
        if project_id is not None or data_map_id is not None:
            raise ValueError('Use an unbound Atlas MCP connection for change reviews.')
        return '/api/change-reviews' + ('/' + quote(review_id, safe='') if review_id else '')

    def browser_url(result):
        """Return a clickable loopback URL, as other Atlas view tools do."""
        return {**result, 'url': base + result['url']}

    @server.tool(annotations=write)
    async def begin_change_review(
        sessionId: Session, snapshotId: Identity,
        nodeId: Annotated[str, Field(min_length=1, max_length=4096)],
        title: Annotated[str, Field(max_length=160)] | None = None,
    ) -> dict:
        """Before edits, save the fresh private map's function facts and one selected static entry path."""
        body = {'sessionId': sessionId, 'snapshotId': snapshotId, 'nodeId': nodeId, 'title': title}
        return browser_url(await request('POST', path(), body, timeout=120))

    @server.tool(annotations=write)
    async def finish_change_review(reviewId: Session, sessionId: Session, snapshotId: Identity) -> dict:
        """After edits and a checkout map rebuild, publish the comparison and its local browser URL."""
        body = {'sessionId': sessionId, 'snapshotId': snapshotId}
        return browser_url(await request('POST', path(reviewId) + '/finish', body, timeout=120))

    @server.tool(annotations=read)
    async def get_change_review(
        reviewId: Session, offset: Annotated[int, Field(ge=0, le=100_000, strict=True)] = 0,
    ) -> dict:
        """Read a page of ten changed functions. The returned URL opens both paths in Atlas."""
        return browser_url(await request('GET', path(reviewId) + f'?offset={offset}&limit=10'))
