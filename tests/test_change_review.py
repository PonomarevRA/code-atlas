"""End-to-end review using the real extraction pipeline and local MCP transport."""

import asyncio
import os
import sys

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_agent_mcp import atlas_server, unpack

BEFORE = """def route(value):
    if value < 0:
        return old_handler(value)
    return 1

def old_handler(value):
    return -value

def unchanged():
    return 42
"""
AFTER = """def route(value):
    if value <= 0:
        return new_handler(value)
    return 2

def new_handler(value):
    return value * 2


def unchanged():
    return 42
"""


async def wait_for_map(session, session_id):
    """Wait for a real successful published extraction, including a queued rebuild."""
    for _ in range(240):
        result = unpack(await session.call_tool('get_checkout_map', {'sessionId': session_id}))
        project = result['project']
        assert project['status'] != 'failed', result
        if project['snapshotId'] and not result['activeJob']:
            return project['snapshotId']
        await asyncio.sleep(.1)
    raise AssertionError(f'Checkout map did not finish: {result}')


def test_review_keeps_exact_before_and_after_after_checkout_is_removed(atlas_server, tmp_path):
    """An uncommitted edit compares functions and captured paths through real MCP and HTTP."""
    port, origin = atlas_server
    checkout = tmp_path / 'review-checkout'
    checkout.mkdir()
    program = checkout / 'handlers.py'
    program.write_text(BEFORE)
    helper = checkout / 'helper.py'
    helper.write_text('def helper():\n    return 0\n')
    bindings = checkout / 'binding.js'
    bindings.write_text('const handler = (value) => value + 1;\n')
    parameters = StdioServerParameters(command=sys.executable,
                                       args=['-m', 'atlas.agent_mcp', '--port', str(port)],
                                       env=dict(os.environ))

    async def exercise():
        """Capture both sides and prove stale and unrelated captures are rejected."""
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                private = unpack(await session.call_tool('ensure_checkout_map', {
                    'checkout': str(checkout), 'ownerKey': 'review-fixture'}))
                sid = private['sessionId']
                before_id = await wait_for_map(session, sid)
                matches = unpack(await session.call_tool('search_checkout_nodes', {
                    'query': 'route', 'sessionId': sid, 'snapshotId': before_id}))
                node = next(item for item in matches['nodes'] if item['label'] == 'route()')
                capture = {'sessionId': sid, 'snapshotId': before_id, 'nodeId': node['id']}
                async with httpx.AsyncClient(base_url=origin, trust_env=False) as client:
                    program.write_text(AFTER)
                    stale_source = await client.post('/api/change-reviews', json=capture)
                    assert stale_source.status_code == 409, stale_source.text
                    program.write_text(BEFORE)
                    helper.write_text('def helper():\n    return 3\n')
                    stale_helper = await client.post('/api/change-reviews', json=capture)
                    assert stale_helper.status_code == 409
                    assert 'helper.py' in stale_helper.json()['error']
                    helper.write_text('def helper():\n    return 0\n')
                    draft = unpack(await session.call_tool('begin_change_review', capture))
                    review_id = draft['reviewId']
                    assert draft['status'] == 'baseline'
                    assert (await client.get(draft['url'])).status_code == 200
                    same = await client.post(f'/api/change-reviews/{review_id}/finish',
                                             json={'sessionId': sid, 'snapshotId': before_id})
                    assert same.status_code == 409
                    program.write_text(AFTER)
                    bindings.write_text('const handler = (value) => value + 2;\n')
                    unpack(await session.call_tool('rebuild_checkout_map', {'sessionId': sid}))
                    after_id = await wait_for_map(session, sid)
                    assert after_id != before_id
                    finish = {'reviewId': review_id, 'sessionId': sid, 'snapshotId': after_id}
                    complete = unpack(await session.call_tool('finish_change_review', finish))
                    assert complete['status'] == 'complete'
                    assert complete['counts'] == {'added': 1, 'removed': 1, 'modified': 2}
                    assert unpack(await session.call_tool('finish_change_review', finish)) == complete
                    unpack(await session.call_tool('finish_checkout_map', {'sessionId': sid}))
                    program.unlink()
                    result = unpack(await session.call_tool('get_change_review', {'reviewId': review_id}))
                    assert {row['kind'] for row in result['changes']} == {'added', 'removed', 'modified'}
                    assert any(row['kind'] == 'modified' and row['after']['file'] == 'binding.js' for row in result['changes'])
                    assert all('unchanged' not in (row['after'] or row['before'])['label'] for row in result['changes'])
                    response = await client.get(f'/api/change-reviews/{review_id}?includePaths=true&limit=1')
                    assert response.status_code == 200, response.text
                    payload = response.json()
                    assert payload['nextOffset'] == 1
                    paths = payload['paths']
                    assert paths['before']['steps'][0]['sourceVerified']
                    assert paths['after']['steps'][0]['sourceVerified']
                    assert any('old_handler' in step['label'] for step in paths['before']['steps'])
                    assert any('new_handler' in step['label'] for step in paths['after']['steps'])
                    assert any('value < 0' in row['text'] for row in paths['before']['steps'][0]['events'])
                    assert any('value <= 0' in row['text'] for row in paths['after']['steps'][0]['events'])
    asyncio.run(exercise())


def test_review_path_uses_only_captured_graph_edges(tmp_path, monkeypatch):
    """Live infrastructure detection must not supply edges to a historical review side."""
    from hashlib import sha256

    from atlas.change_review_facts import capture_side
    from atlas.infrastructure import index

    def live_infrastructure(*args):
        """Fail if a review tries to derive a dispatch from live infrastructure files."""
        raise AssertionError('review inspected live infrastructure')

    monkeypatch.setattr(index, 'infrastructure_index', live_infrastructure)
    program = b'def route():\n    return 7\n'
    (tmp_path / 'service.py').write_bytes(program)
    digest = sha256(program).hexdigest()
    graph = {'nodes': [{'id': 'entry', 'label': 'route()', 'kind': 'method', 'file': 'service.py',
                        'line': 1, 'healthIdentity': 'entry-id',
                        'complexity': {'sourceKind': 'snapshot-captured-source', 'sourceHash': digest},
                        'reviewDeclaration': {'identity': 'entry-id', 'file': 'service.py', 'sourceHash': digest,
                                              'declarationHash': digest, 'line': 1, 'endLine': 2}}],
             'edges': []}
    for index_number in range(35):
        field = f'field-{index_number}'
        graph['nodes'].append({'id': field, 'kind': 'field', 'label': field})
        graph['edges'].append({'source': 'entry', 'target': field, 'relation': 'reads'})
    graph['nodes'].append({'id': 'callee', 'kind': 'external', 'label': 'callee'})
    graph['edges'].append({'source': 'entry', 'target': 'callee', 'relation': 'calls'})
    saved = capture_side(tmp_path, graph, 'snapshot', node_id='entry')
    assert [edge['target'] for edge in saved['path']['edges']] == ['callee']
    assert saved['path']['steps'][0]['sourceVerified']
    assert saved['path']['steps'][0]['events'][0]['text'] == 'return 7'



def test_decorator_change_and_collapsed_declarations(tmp_path):
    """Decorators affect the declaration hash; collapsed symbols cannot claim a unique match."""
    from atlas.change_review_facts import compare_functions, function_inventory
    from atlas.member_graph import enrich_member_graph

    def inventory(content):
        """Use the real member pass for a small Python module."""
        (tmp_path / 'entry.py').write_text(content)
        graph = enrich_member_graph({'nodes': [{'id': 'file', 'label': 'entry.py',
                                               'file': 'entry.py', 'kind': 'file'}], 'edges': []}, tmp_path)
        functions, ambiguous, _ = function_inventory(tmp_path, graph)
        return {'functions': functions, 'ambiguous': ambiguous}

    before = inventory('@app.route("/before")\ndef entry():\n    return 1\n')
    after = inventory('@app.route("/after")\ndef entry():\n    return 1\n')
    [change] = compare_functions(before, after)
    assert change['kind'] == 'modified'
    assert change['before']['line'] == change['after']['line'] == 1
    duplicate = inventory('if flag:\n    def entry():\n        return 1\nelse:\n    def entry():\n        return 2\n')
    assert not duplicate['functions']
    assert len(duplicate['ambiguous']) == 1
    assert compare_functions(after, duplicate) == []



def test_failed_file_does_not_look_like_a_removed_function():
    """A missing declaration in a failed file is uncertain even when the graph was published."""
    from atlas.change_review_facts import compare_functions

    row = {'file': 'handler.py', 'line': 1}
    before = {'functions': {'entry': row}, 'ambiguous': []}
    after = {'functions': {}, 'ambiguous': [], 'failedFiles': ['handler.py']}
    assert compare_functions(before, after) == [{'kind': 'unconfirmed', 'before': row, 'after': None}]
