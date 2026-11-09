"""Typed loopback tools for presence diagnostics and reversible host-global path choices."""
from typing import Annotated, Literal
from urllib.parse import quote, urlencode

from mcp.types import ToolAnnotations
from pydantic import Field

ENGINE_TOOLS = ['inspect_semantic_engine_settings', 'set_semantic_engine_paths']


def register_semantic_engine_tools(server, request, selected_project, bound_project):
    """Share the HTTP contract; this MCP proxy never searches the client's own filesystem."""
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def inspect_semantic_engine_settings(
        modId: Annotated[str, Field(min_length=1, max_length=128)] | None = None,
        projectId: Annotated[str, Field(min_length=1, max_length=128)] | None = None,
    ) -> dict:
        """Inspect Atlas's engine configuration and find existing path candidates without executing tools.

        Without modId, list settings, allowed fields, installed profiles and HTTP operations.
        With modId, inspect fixed known locations on the Atlas host; optional projectId adds
        that registered project's known local paths. A project-bound MCP cannot inspect another
        project. Candidates prove file presence only; use get_semantic_engine_status for map
        compatibility. Reading this report grants no permission to change host-global settings.
        """
        project = selected_project(projectId) if projectId is not None or bound_project is not None else None
        if modId is None:
            return await request('GET', '/api/semantic-engines/settings')
        suffix = '?' + urlencode({'projectId': project}) if project is not None else ''
        return await request('GET', '/api/mods/' + quote(modId, safe='') + '/semantic-engine-diagnostics' + suffix)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                            idempotentHint=True, openWorldHint=False))
    async def set_semantic_engine_paths(
        modId: Annotated[str, Field(min_length=1, max_length=128)],
        paths: Annotated[dict[str, str], Field(max_length=4)],
        mode: Literal['patch', 'replace'] = 'patch',
    ) -> dict:
        """Set paths only when the user authorizes engine configuration; scope is host-global.

        Inspect allowed fields first. Values must be absolute paths, never shell commands.
        patch preserves other fields; an empty string restores automatic selection for that field.
        replace with paths={} restores automatic selection for the entire mod. This does not
        enable a disabled mod, install anything or prove compatibility; request a map recheck
        through the documented HTTP operation after configuration. A browser agent can use
        the same settings page or HTTP contract without an MCP connection.
        """
        if any(len(name) > 64 or len(path) > 4096 for name, path in paths.items()):
            raise ValueError('Engine path input exceeds its bounded settings contract')
        return await request('POST', '/api/mods/' + quote(modId, safe='') + '/semantic-engine-settings',
                             {'paths': paths, 'mode': mode})
