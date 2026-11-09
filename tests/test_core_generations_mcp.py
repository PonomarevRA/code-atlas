"""One actual MCP session survives read-only compute replacement in a stable source host."""

import asyncio
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import socket
import sys
from threading import Thread
import time

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
import uvicorn

from atlas.core_generations import BundleArtifact, CoreGenerations, GenerationError, build_bundle
from atlas.core_generations.artifacts import FILES, canonical
from atlas.server import create_app
from test_agent_mcp import unpack
from test_grouping_cache import legacy_store

ROOT = Path(__file__).resolve().parents[1]


def compute_source(destination, *, delayed=False):
    """Copy the approved real implementation; a test-only compatible wrapper changes source bytes."""
    destination.mkdir()
    for name in FILES:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    with (destination / 'atlas/module_compute.py').open('a') as stream:
        stream.write('\n_original = compute_module_projection\n')
        stream.write("def compute_module_projection(projection, *, algorithms=('current',), seed=42, resolution=1.0):\n")
        if delayed:
            stream.write("    __import__('time').sleep(0.6)\n")
        stream.write('    value = _original(projection, algorithms=algorithms, seed=seed, resolution=resolution)\n')
        stream.write("    return {'assessment': dict(value['assessment']), 'comparison': dict(value['comparison'])}\n")
    return destination


def stable(value):
    """Compare all factual fields, excluding only explicitly nondeterministic provider elapsed time."""
    value = deepcopy(value)
    for proposal in value['comparison']['proposals']:
        proposal.pop('elapsedSeconds', None)
    return value


def descriptor_hash(tools):
    """Fingerprint actual SDK descriptors, including schemas/annotations, independent of listing order."""
    values = [tool.model_dump(mode='json', exclude_none=True) for tool in tools]
    return sha256(canonical(sorted(values, key=lambda item: item['name']))).hexdigest()


def wait_busy(owner, generation):
    """Wait on observed dispatch ownership rather than guessing an old call's timing."""
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        if any(item['generationId'] == generation and item['busy'] for item in owner.state()['generations']):
            return
        time.sleep(.01)
    raise AssertionError('Expected a pinned actual worker call')


def test_existing_mcp_connection_pins_old_call_and_uses_new_compute(tmp_path):
    """Use real stdio, HTTP, ProjectStore and child processes without an installed-app update."""
    state, project, graph_path, _ = legacy_store(tmp_path)
    registry = state.registry
    state.close()
    protected = {str(path): path.read_bytes() for path in (registry, graph_path)}
    owner = CoreGenerations()
    first = build_bundle(compute_source(tmp_path/'source-N', delayed=True), tmp_path/'bundle-N', generation_id='N')
    second = build_bundle(compute_source(tmp_path/'source-N1'), tmp_path/'bundle-N1', generation_id='N1')
    old_identity = owner.prepare(first)
    owner.activate('N')
    app = create_app(tmp_path/'state', module_generations=owner)
    # Language-reference embedding is outside this compute/session fixture.
    app.state.references.start_initial_indexing = lambda: None
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0)); listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))
    thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    evidence = {}
    try:
        deadline = time.monotonic()+10
        with httpx.Client(trust_env=False, timeout=.3) as client:
            while time.monotonic() < deadline:
                try:
                    if client.get(f'http://127.0.0.1:{port}/api/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.03)
            else:
                pytest.fail('Stable source host did not become ready')

        async def scenario():
            """Initialize once, change actual compute processes, then keep using the same SDK connection."""
            parameters = StdioServerParameters(command=sys.executable,
                args=['-m', 'atlas.agent_mcp', '--port', str(port)],
                env={**os.environ, 'HTTP_PROXY': 'http://127.0.0.1:1', 'ALL_PROXY': 'http://127.0.0.1:1'})
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    before_tools = (await session.list_tools()).tools
                    arguments = {'projectId': project, 'snapshotId': 'first', 'algorithms': ['current']}
                    before = unpack(await session.call_tool('analyze_code_modules', arguments))
                    new_identity = await asyncio.to_thread(owner.prepare, second)
                    in_flight = asyncio.create_task(session.call_tool('analyze_code_modules', arguments))
                    await asyncio.to_thread(wait_busy, owner, 'N')
                    switch = owner.activate('N1')
                    after = unpack(await session.call_tool('analyze_code_modules', arguments))
                    retired = await asyncio.to_thread(owner.retire, 'N', deadline=time.monotonic()+5)
                    pinned = unpack(await in_flight)
                    assert stable(before) == stable(after) == stable(pinned)
                    assert retired['forced'] is False
                    assert new_identity['pid'] != old_identity['pid']
                    assert new_identity['computeHash'] != old_identity['computeHash']
                    assert descriptor_hash(before_tools) == descriptor_hash((await session.list_tools()).tools)
                    # An incompatible candidate is rejected before dispatch and cannot switch the client.
                    bad = build_bundle(ROOT, tmp_path/'bad-bundle', generation_id='bad')
                    manifest = json.loads((bad.directory/'manifest.json').read_text())
                    manifest['semantics'] = 'incompatible/v99'
                    encoded = canonical(manifest)
                    (bad.directory/'manifest.json').write_bytes(encoded)
                    with pytest.raises(GenerationError):
                        owner.prepare(BundleArtifact(bad.directory, sha256(encoded).hexdigest()))
                    after_rejection = unpack(await session.call_tool('analyze_code_modules', arguments))
                    assert stable(after_rejection) == stable(after)
                    assert owner.state()['active'] == 'N1'
                    evidence.update(old=old_identity, new=new_identity, switched=switch, retired=retired,
                        toolCount=len(before_tools), descriptorHash=descriptor_hash(before_tools),
                        initializeCount=1, sameNonTimingFacts=True, rejectedCandidatePreservedDispatch=True)
        asyncio.run(scenario())
        for path, content in protected.items():
            assert Path(path).read_bytes() == content
        evidence.update(scope='real source SDK/HTTP/store/process fixture; projection workers only',
            canonicalFixtureBytesPreserved=True, installedRuntimeQualified=False,
            limitations=['Stable state/HTTP/MCP owner was not replaced.',
                         'Compute exports no jobs/cursors/subscriptions; their host owners are unchanged.',
                         'Native packaging, real user connections and full application-core handoff remain unqualified.'])
        output = ROOT/'outputs/core-generations/mcp-source-proof.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    finally:
        server.should_exit = True
        thread.join(10)
        owner.close()
        listener.close()
        assert not thread.is_alive()
        assert not owner.state()['generations']
