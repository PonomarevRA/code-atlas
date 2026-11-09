"""Агентский CSS-поиск использует тот же captured HTML5-контракт, что и UI."""
from typing import Annotated
from urllib.parse import quote
from mcp.types import ToolAnnotations
from pydantic import Field
from .mcp_response_budget import bound_list_response

HTML_TOOLS = ['query_html']


def register_html_tools(server, request, selected_project):
    """Добавляет один read-only инструмент с закреплённым проектом и лимитами."""
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def query_html(snapshotId: str, nodeId: str, selector: Annotated[str, Field(min_length=1, max_length=8192)] = '*',
                         projectId: str | None = None, offset: Annotated[int, Field(ge=0, le=5000)] = 0,
                         limit: Annotated[int, Field(ge=1, le=50)] = 30) -> dict:
        """Find HTML elements with CSS selectors in the selected node's captured file.

        Start with search_nodes to find an HTML page/block and snapshotId. Supports
        structural selectors, attributes, :is/:not/:nth-child and one :has per query.
        Runtime pseudo-classes and pseudo-elements return an explicit error.
        Generated HTML5 elements have no source range. Follow nextOffset with the
        same snapshot/node/selector; captureTruncated is independent of pagination.
        No filesystem rescan, script execution, browser state or network page load.
        """
        path = '/api/projects/' + quote(selected_project(projectId), safe='') + '/html-query'
        result = await request('POST', path, {'snapshotId': snapshotId, 'nodeId': nodeId,
            'selector': selector, 'offset': offset, 'limit': limit})
        bounded = bound_list_response(result, ('nodes',))
        if bounded is None:
            return {'snapshotId': snapshotId, 'nodes': [], 'truncated': True,
                    'warning': 'Narrow the CSS selector; response metadata exceeds the MCP budget.'}
        # A budget-trimmed page resumes after the delivered items, not the HTTP page.
        delivered = len(bounded['nodes'])
        bounded['nextOffset'] = offset + delivered if offset + delivered < result['total'] else None
        return bounded
