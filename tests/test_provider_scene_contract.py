"""Eleven real parsers feed one production scene builder and preserve existing raw targets."""
from pathlib import Path
import json
import shutil
import subprocess
import pytest
from atlas.member_graph import enrich_member_graph
from atlas.node_description import build_node_description


def test_all_language_providers_use_one_scene_contract(tmp_path):
    """Equivalent source choices use shared cards; language-specific uncertainty remains explicit."""
    node_executable = shutil.which('node')
    if not node_executable:
        pytest.skip('Node.js unavailable for production scene verification')
    samples = json.loads((Path(__file__).parent / 'fixtures/flow/provider-grid.json').read_text())
    items = []
    for language, (suffix, source, line) in samples.items():
        filename = language + suffix
        (tmp_path / filename).write_text(source)
        raw = {'id':language+'-root','label':'run()','kind':'method','file':filename,'line':line}
        graph = {'nodes':[{'id':'file-'+language,'label':filename,'kind':'file','file':filename,'line':1},raw,
                          {'id':language+'-work','label':'work()','kind':'external'}],
                 'edges':[{'source':raw['id'],'target':language+'-work','relation':'calls','confidence':'EXTRACTED'}]}
        captured = enrich_member_graph(graph, tmp_path) if language != 'csharp' else graph
        description = build_node_description(tmp_path, captured, raw['id'], 's')
        assert description['sourceStatus']=='parsed' and description['methodFlow']['status']=='parsed', language
        assert description['methodFlow']['contractVersion']=='atlas-method-flow-v1', language
        assert any(event['callee']=='work' and event['targetNodeIds']==[language+'-work'] for event in description['events']), language
        items.append({'language':language,'root':raw['id'],'graph':captured,'description':description})
    data_path = tmp_path / 'scenes.json'
    data_path.write_text(json.dumps(items))
    result = subprocess.run([node_executable,str(Path(__file__).with_name('provider_scene_contract.mjs')),str(data_path)],
                            capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stderr
    assert '11 provider trees PASS' in result.stdout
