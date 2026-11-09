"""Настоящий stdio MCP fixture проверяет scope до вызова HTTP request adapter."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mcp.server.fastmcp import FastMCP
from atlas.document_search_mcp import register_document_search_tools

parser = argparse.ArgumentParser()
parser.add_argument('--project')
parser.add_argument('--data-map')
arguments = parser.parse_args()
server = FastMCP('Captured search scope fixture', log_level='ERROR')


def selected_project(requested):
    """Rejects a foreign project before the request callback can observe the operation."""
    if arguments.project and requested not in (None, arguments.project):
        raise ValueError('Connection project cannot be overridden.')
    return arguments.project or requested


async def request(method, path, body=None):
    """Acts as an observable gateway without any TCP listener or source access."""
    return {'gatewayReached': True, 'method': method, 'path': path, 'body': body}


register_document_search_tools(server, request, selected_project, arguments.project, arguments.data_map)
server.run(transport='stdio')
