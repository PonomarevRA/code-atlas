"""Captured mod evidence survives search, description, neighbors and MCP budget fallbacks."""
from copy import deepcopy
import json
from atlas.agent_views import search_nodes
from atlas.analysis_facts import analysis_preview, bounded_fact
from atlas.graph_context import read_graph_node, read_graph_neighbors
from atlas.node_description import build_node_description
from atlas.mcp_response_budget import compact_description
from atlas.mcp_graph import build_mcp_graph
from test_control_flow import branch_project


def captured_facts():
    """Use the common captured contract; these tests do not execute a framework or a provider."""
    return {'codeProjectFacts':{'id':'area','label':'tests','kind':'logical-area','language':'java',
                'buildManifest':'build.gradle','modId':'language-java','sourceKind':'snapshot-captured-source'},
        'testFacts':{'id':'test:sample','label':'Проверка невалидного ClientId','framework':'junit5','language':'java',
            'modId':'framework-junit5','tags':['SmokeRegression'],'declaredStatus':'disabled','runtimeStatus':'unknown',
            'sourceKind':'snapshot-captured-source','dataSources':[{'kind':'method-source','label':'CommonData#invalidClientIdParams',
                'nodeIds':['provider:exact'],'status':'declared','declaredRowCount':3,'rows':[{'values':['missing']},{'values':['bad']},{'values':['empty']}]}]},
        'attributeFacts':{'sourceKind':'snapshot-captured-source','sourceHash':'captured','language':'java','status':'parsed',
            'groups':[{'scope':'declaration','attributes':[{'name':'DisplayName','qualifiedName':'org.junit.DisplayName'}]}]}}


def test_search_and_neighbors_use_mod_evidence_without_changing_graph():
    """Find intent, tags, annotation FQNs and provider links even when the raw name contains none of them."""
    node={'id':'method','label':'Run()','file':'Test.java',**captured_facts()}
    graph={'nodes':[node,{'id':'provider:exact','label':'Data'}],'edges':[{'source':'method','target':'provider:exact','relation':'uses_test_data'}],
        'metadata':{'snapshotId':'captured','analysisMods':{'version':'analysis-rules-v1','fingerprint':'pinned','mods':[{'id':'language-java','fingerprint':'exact'}]}}}
    before=deepcopy(graph)
    for query in ['невалидного ClientId','SmokeRegression','invalidClientIdParams','org.junit.DisplayName','build.gradle']:
        found=search_nodes(graph,query)
        assert found['total']==1 and found['nodes'][0]['id']=='method'
        assert found['analysisContext']['fingerprint']=='pinned'
        preview=found['nodes'][0]['analysisFacts']['test']
        assert preview['runtimeStatus']=='unknown' and preview['declaredStatus']=='disabled'
        assert preview['dataSources'][0]['nodeIds']==['provider:exact']
        assert 'rows' not in preview['dataSources'][0]
    assert search_nodes(graph,'SmokeRegression',exclude_kinds=['symbol'])['total']==1
    raw=read_graph_node(graph,'method');assert len(raw['node']['testFacts']['dataSources'][0]['rows'])==3
    neighbors=read_graph_neighbors(graph,'method');assert neighbors['nodes'][0]['analysisFacts']['project']['buildManifest']=='build.gradle'
    compatible=build_mcp_graph(graph)
    attributes=next(n for n in compatible['nodes'] if n['id']=='method')['attributes']
    assert attributes['atlas.0.analysis']['framework']=='junit5'
    assert attributes['atlas.analysisFacts']['test']['runtimeStatus']=='unknown'
    raw['node']['testFacts']['tags'].append('changed')
    assert graph==before


def test_description_preserves_captured_framework_facts_and_working_tree_flow(branch_project):
    """Current control flow and immutable mod metadata retain separate provenance."""
    root,graph=branch_project
    graph['nodes'][0].update(captured_facts())
    node_id=graph['nodes'][0]['id']
    result=build_node_description(root,graph,node_id,'s')
    assert result['testFacts']['sourceKind']=='snapshot-captured-source'
    assert result['sourceProvenance']['snapshotId']=='s'
    assert result['testFacts']['dataSources'][0]['declaredRowCount']==3
    assert result['events'] and result['controlFlow']['branches']
    fallback=compact_description({**result,'comment':'x'*100_000},'s',node_id,('comment',))
    assert len(json.dumps(fallback,ensure_ascii=False).encode())<=64_000
    assert fallback['analysisFacts']['test']['runtimeStatus']=='unknown'
    assert fallback['testFacts']['dataSources'][0]['nodeIds']==['provider:exact']


def test_large_fact_sections_are_bounded_and_old_maps_remain_unknown():
    """Truncation stays explicit; no invented provider or runtime success appears on old maps."""
    facts=captured_facts();facts['testFacts']['tags']=['tag-'+str(i) for i in range(500)]
    facts['testFacts']['dataSources'][0]['rows']=[{'values':['Ж'*2000]} for _ in range(500)]
    bounded=bounded_fact(facts['testFacts'])
    assert len(json.dumps(bounded,ensure_ascii=False).encode())<=10_000
    assert bounded['truncated']
    preview=analysis_preview(facts)
    assert preview['test']['truncated'] and preview['test']['runtimeStatus']=='unknown'
    old=read_graph_node({'nodes':[{'id':'old'}],'edges':[]},'old')
    assert old['analysisContext']['status']=='unavailable' and old['analysisFacts']=={}


def test_large_annotations_do_not_evict_runtime_boundary_or_provider_ids():
    """Evidence essentials survive even when earlier source-ordered metadata exhausts the detail budget."""
    facts=captured_facts()['testFacts']
    facts['annotations']=[{'arguments':['Ж'*1000 for _ in range(12)]} for _ in range(50)]
    # The real captured schema places runtimeStatus after annotations and fixtures.
    facts={**{k:v for k,v in facts.items() if k!='runtimeStatus'}, 'runtimeStatus':'unknown'}
    result=bounded_fact(facts,5000)
    assert len(json.dumps(result,ensure_ascii=False).encode())<=5000
    assert result['runtimeStatus']=='unknown' and result['sourceKind']=='snapshot-captured-source'
    assert result['dataSources'][0]['nodeIds']==['provider:exact']
