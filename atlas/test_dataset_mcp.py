"""Agent tools proxy snapshot-bound declared inputs; HTTP and MCP share one contract."""
from typing import Annotated
from urllib.parse import quote, urlencode
from mcp.types import ToolAnnotations
from pydantic import Field

TEST_DATA_TOOLS = ['get_test_data']


def register_test_data_tools(server, request, selected_project):
    """Expose exact source/row identities and honest capture limits without source rescans."""
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_test_data(snapshotId: str, nodeId: str, projectId: str | None = None,
                            sourceId: str | None = None, cursor: str | None = None,
                            limit: Annotated[int, Field(ge=1, le=30)] = 20) -> dict:
        """Read declared inputs with named parameter bindings, never executed test cases.

        First omit sourceId to list datasets. Then select a returned source ID;
        follow nextCursor only with the same snapshotId, nodeId and sourceId.
        captureTruncated means additional source rows were not captured; a null
        cursor does not guarantee provider completeness. Context/aggregators and
        unknown values are separate from indexed inputs. Missing, null, empty and
        string values have different kinds. Source IDs and anchors are preserved.
        """
        query = {'snapshotId': snapshotId, 'nodeId': nodeId, 'limit': limit}
        if sourceId is not None:
            query['sourceId'] = sourceId
        if cursor is not None:
            query['cursor'] = cursor
        path = '/api/projects/' + quote(selected_project(projectId), safe='') + '/test-datasets'
        return await request('GET', path + '?' + urlencode(query))
