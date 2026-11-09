"""Contract checks for truthful isolated load evidence; synthetic data never selects a user store."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import httpx
import pytest

SPEC = importlib.util.spec_from_file_location('ag_service_performance_runner', Path(__file__).parents[1] / 'tools/performance/service_runner.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def response(value, status=200):
    """Give semantic validation a real httpx response with a bound synthetic request."""
    return httpx.Response(status, json=value, request=httpx.Request('GET', 'http://127.0.0.1/data'))


def test_environment_does_not_inherit_any_store_selector():
    """Keep production store selectors and arbitrary inherited secrets out of private test processes."""
    with patch.dict(os.environ, {'PATH':'/bin','HOME':'/actual-home','MemoryOptions__StoragePath':'production', 'UnrecognizedSecret':'hidden', 'AG_DESIGN_LIBRARY_DIR':'production','ASPNETCORE_URLS':'production','COMPlus_gcServer':'0'},clear=True):
        env=runner.isolated_environment({'env':{'AG_DESIGN_LIBRARY_DIR':'{data}/library'}},{'data':'/test','port':'19200'})
    assert env=={'PATH':'/bin','HOME':'/actual-home','COMPlus_gcServer':'0','AG_DESIGN_LIBRARY_DIR':'/test/library'}


@pytest.mark.parametrize('value', [{'status':'unavailable','items':[]},{'status':'available','items':[]},{'status':'available','items':[1,2]}])
def test_successful_http_is_not_successful_seeded_work(value):
    """HTTP 200 cannot make a wrong state or wrong fixture count valid."""
    with pytest.raises(ValueError):
        runner.assert_response(response(value),{'expectedValues':{'status':'available'},'expectedLengths':{'items':1}})


def test_semantic_success_and_wrong_status_are_distinct():
    """Require seeded content and preserve the separate HTTP failure path."""
    runner.assert_response(response({'items':[{'id':'seed'}]}),{'expectedValues':{'items.0.id':'seed'},'expectedTypes':{'items':'array'}})
    with pytest.raises(httpx.HTTPStatusError):runner.assert_response(response({},503),{})


def test_malformed_spec_cannot_start_a_workload():
    """Reject workloads without semantic checks and paths escaping the local origin."""
    with pytest.raises(ValueError):runner.validate_spec({'port':19200,'requests':[{'name':'empty','path':'/'}]})
    with pytest.raises(ValueError):runner.validate_spec({'port':19200,'requests':[{'name':'external','path':'//external','expectedValues':{'ok':True}}]})


def test_failures_never_improve_success_percentiles_or_rates():
    """Mix success and failure to prove throughput and latency use successful requests only."""
    calls=0
    def answer(request):
        """Alternate seeded semantic success and failure under the mock HTTP transport."""
        nonlocal calls
        calls+=1
        return httpx.Response(200,json={'value':calls%2},request=request)
    async def run():
        """Exercise the asynchronous worker pool with deterministic in-process responses."""
        async with httpx.AsyncClient(base_url='http://127.0.0.1',transport=httpx.MockTransport(answer)) as client:
            return await runner.workload(client,[{'name':'value','path':'/','expectedValues':{'value':1}}],4,2)
    results=asyncio.run(run());row=results['value'];assert row['succeeded']==2 and row['failed']==2
    assert len(row['latenciesMs'])==2 and len(row['attemptLatenciesMs'])==4
    summary=runner.load_summary(results,{'wallSeconds':2,'metricsComplete':True},4)
    assert summary['successfulRequestsPerSecond']==1 and summary['attemptsPerSecond']==2 and not summary['valid']


def test_disk_inventory_does_not_follow_links_or_double_count_hardlinks(tmp_path):
    """Measure owned inode content without following external symlinks or duplicating hardlinks."""
    data=tmp_path/'data';data.mkdir();(data/'a').write_bytes(b'x'*1024)
    os.link(data/'a',data/'hard');outside=tmp_path/'outside';outside.write_bytes(b'y'*4096);(data/'link').symlink_to(outside)
    result=runner.disk_usage(data);assert result['logicalBytes']==1024 and result['files']==1;assert result['allocatedBytes']>=1024
    with pytest.raises(ValueError):runner.tree_entries(data,hashes=True)


def test_frozen_critical_content_change_is_detected_even_with_restored_metadata(tmp_path):
    """A binary mutation must invalidate evidence even when timestamps and size match."""
    root=tmp_path/'root';root.mkdir();file=root/'binary';file.write_bytes(b'AAAA');manifest=runner.freeze_tree(root,tmp_path/'manifests','test')
    stat=file.stat();file.write_bytes(b'BBBB');os.utime(file,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    with pytest.raises(ValueError):runner.verify_tree(manifest,root,['binary'])


def test_pairing_rejects_changed_fixture_and_runtime_environment():
    """Only valid evidence from the same fixture can be paired for comparison."""
    base={'valid':True,'schemaVersion':runner.SCHEMA,'system':'same','logicalCpuCount':8,'profile':{},'provenance':{'workloadSha256':'w','runnerSha256':'r','inheritedRuntimeEnvironment':{},'seed':{'contentSha256':'seed'}}}
    assert runner.pair_compatible(base,json.loads(json.dumps(base)))
    other=json.loads(json.dumps(base));other['provenance']['seed']['contentSha256']='other';assert not runner.pair_compatible(base,other)
    other=json.loads(json.dumps(base));other['valid']=False;assert not runner.pair_compatible(base,other)


def test_owned_receipt_never_admits_a_reused_pid():
    """A reused process identifier cannot authorize a signal to a foreign process."""
    owned=runner.OwnedTree.__new__(runner.OwnedTree);owned.receipts={123:100}
    class Reused:
        def __init__(self,pid):self.pid=pid
        def create_time(self):return 101
        def status(self):return 'running'
    with patch.object(runner.psutil,'Process',Reused):assert owned.live()==[]


def test_cleanup_proves_child_and_listener_termination():
    """Verify real child teardown and a free loopback port rather than trusting signal delivery."""
    process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        owned=runner.OwnedTree(process);proof=runner.stop_owned(process,owned,19399,deadlines=(1,1,1))
        assert proof['valid'] and proof['remainingOwned']==[] and proof['portFree'] and process.poll() is not None
    finally:
        if process.poll() is None:process.kill();process.wait()


@pytest.mark.parametrize('duration', [float('nan'), float('inf'), -1, 3601, True, '60', None])
def test_unbounded_or_ambiguous_soak_is_rejected_before_launch(duration):
    """Malformed duration cannot bypass the finite private-process lifetime contract."""
    spec = {'port': 19200, 'soakSeconds': duration,
            'requests': [{'name': 'seed', 'path': '/', 'expectedValues': {'ok': True}}]}
    with pytest.raises(ValueError, match='soakSeconds'):
        runner.validate_spec(spec)
