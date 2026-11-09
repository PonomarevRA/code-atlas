"""Compact coverage MCP pages preserve proof boundaries and actual duplicate-envelope budgets."""
import copy
import json
from pathlib import Path
import pytest
from atlas.test_coverage_mcp import validate_coverage_query, project_test_coverage, _rendered_size

BASE = 'http://127.0.0.1:8766'


def condition(index, text='yes', **kwargs):
    """Representative condition contains report aggregates and unknown AST objectives."""
    return {'id':f'c{index}','ownerNodeId':'raw','methodId':'m','methodLabel':'Calculate','namespace':'Calc','file':'Code.cs','line':index+1,'column':1,'endLine':index+2,'kind':'if','condition':text,'sourceStatus':'parsed','objectives':[{'id':f'c{index}:true','kind':'true','label':'true','implicit':False,'runtimeStatus':'unknown'},{'id':f'c{index}:false','kind':'false','label':'false','implicit':True,'runtimeStatus':'unknown'}], 'runtime':{'status':'partial','lineHits':2,'branchesCovered':1,'branchesValid':2,'reason':'Reported compiler transitions','reportPaths':['coverage.xml'],'freshness':'mtime-compatible-not-source-hash-proof'},'testKinds':{'unit':2,'gherkin':3},'testLinksTotal':5,'testLinksTruncated':True,'testLinks':[{'testId':'unit','testKind':'unit','targetNodeId':'raw','sourceKind':'graph-snapshot','confidence':'source','path':['unit','raw'],'relations':['calls']}],**kwargs}


def inventory(conditions=3):
    """Keeps current-source hash, namespaces, skipped units and Gherkin separate."""
    return {'schemaVersion':1,'analyzerVersion':'test-coverage-v1','snapshotId':'snapshot','projectId':'repo','sourceKind':'working-tree-and-snapshot','scope':{'nodeId':'raw','mode':'node-chain'},'status':'partial','summary':{'tests':2,'methods':1,'conditions':conditions,'namespaces':1,'reports':1,'testsByKind':{'unit':1,'gherkin':1},'testsByDeclaredStatus':{'enabled':1,'disabled':1,'unknown':0},'conditionsByStatus':{'covered':0,'partial':conditions,'uncovered':0,'unknown':0}},'truncation':{'files':True,'testLinks':True},'limits':{'maxSourceFiles':500},'warnings':['Static links do not prove execution.'],'conditions':[condition(index) for index in range(conditions)], 'tests':[{'id':'unit','nodeId':'unit-raw','kind':'unit','subtype':'fact','framework':'xunit','name':'SkippedTest','namespace':'Specs','file':'Unit.cs','line':3,'declaredStatus':'disabled','skipReasons':['legacy'],'runtime':{'status':'unknown','reason':'No individual test execution proof.'}}, {'id':'gh','nodeId':None,'kind':'gherkin','subtype':'scenario-outline','framework':'gherkin','name':'Сценарий','namespace':'Feature','file':'Cases.feature','line':1,'declaredStatus':'enabled','exampleRows':2,'exampleRowsTruncated':False,'tags':['slow'],'runtime':{'status':'unknown'},'steps':[{'text':'начальные данные','file':'Cases.feature','line':2,'bindingStatus':'ambiguous','bindings':[{'nodeId':'binding-raw','file':'Steps.cs','line':8,'pattern':'начальные данные','ambiguous':True,'sourceKind':'working-tree'}]}],'examples':[{'name':'Примеры','line':3,'rows':2,'declaredStatus':'disabled','tags':['ignore']}]}], 'reports':[{'id':'report','file':'coverage.xml','status':'partial','format':'cobertura','sourceHash':'r'*64,'mtimeNs':20,'warnings':['No source hash match.']}], 'sourceFacts':[{'file':'Code.cs','sourceHash':'c'*64,'mtimeNs':10,'stableRead':True},{'file':'Cases.feature','sourceHash':'f'*64,'mtimeNs':10,'stableRead':True}]}


@pytest.mark.parametrize('args', [{'section':[]},{'section':'bad'},{'kind':{}},{'runtimeStatus':'green'},{'testKind':True},{'offset':True},{'offset':-1},{'limit':False},{'limit':41},{'limit':0},{'query':[]},{'query':'q'*501},{'conditionId':{}},{'conditionId':'a','testId':'b'},{'section':'tests','conditionId':'a'},{'reportPaths':['x']*9},{'reportPaths':'a'},{'reportPaths':[True]},{'reportPaths':['../coverage.xml']},{'reportPaths':['/tmp/report.xml']},{'reportPaths':['C:\\repo\\report.xml']},{'reportPaths':['https://example.com/report.xml']},{'analysisId':[]}])
def test_validation_is_strict_before_io(args):
    with pytest.raises(ValueError):
        validate_coverage_query(**args)


def test_valid_defaults_relative_paths_and_exact_detail_sections():
    result=validate_coverage_query()
    assert result['section']=='summary' and result['limit']==20 and result['offset']==0
    assert validate_coverage_query(conditionId='c1')['section']=='conditions'
    assert validate_coverage_query(testId='t1')['section']=='tests'
    assert validate_coverage_query(reportPaths=['reports\\coverage.xml'])['reportPaths']==['reports/coverage.xml']


def test_summary_analysis_hash_tracks_whole_source_result_and_does_not_mutate():
    raw=inventory();before=copy.deepcopy(raw)
    result=project_test_coverage(raw,{},BASE)
    assert raw==before and len(result['analysisId'])==64
    assert result['records']==[] and result['summary']==raw['summary']
    assert result['coverageUrl']==BASE+'/coverage.html?repo=repo&snapshot=snapshot&node=raw'
    assert result['truncation']['files'] and result['sourceKind']=='working-tree-and-snapshot'
    assert result['summary']['testsByKind']=={'unit':1,'gherkin':1}
    reordered={key:raw[key] for key in reversed(raw)}
    assert project_test_coverage(reordered,{},BASE)['analysisId']==result['analysisId']
    raw['tests'][1]['steps'][0]['text']='changed Gherkin'
    changed=project_test_coverage(raw,{},BASE)['analysisId']
    assert changed!=result['analysisId']
    with pytest.raises(ValueError,match=changed):
        project_test_coverage(raw,{'analysisId':result['analysisId']},BASE)


def test_conditions_filters_total_kind_counts_and_preserve_proof_limits():
    raw=inventory()
    raw['conditions'][1]['kind']='foreach'
    raw['conditions'][2]['kind']='switch-expression'
    raw['conditions'][0]['runtime']['status']='unknown'
    result=project_test_coverage(raw,{'section':'conditions','kind':'loop','testKind':'gherkin','query':'CALCULATE','runtimeStatus':'partial'},BASE)
    assert [row['id'] for row in result['records']]==['c1']
    row=result['records'][0]
    assert row['testKinds']=={'unit':2,'gherkin':3}
    assert row['runtime']['status']=='partial' and row['runtime']['branchesValid']==2
    assert all(goal['runtimeStatus']=='unknown' for goal in row['objectives'])
    assert row['testLinks'][0]['test']['declaredStatus']=='disabled'
    assert 'path' not in row['testLinks'][0] and row['proofPathsIncluded'] is False
    assert project_test_coverage(raw,{'section':'conditions','kind':'switch'},BASE)['records'][0]['id']=='c2'
    raw['conditions'][0]['kind'] = 'conditional'
    assert project_test_coverage(raw,{'section':'conditions','kind':'if'},BASE)['records'][0]['kind']=='conditional'


def test_tests_filter_and_detail_gives_bounded_gherkin_source_not_execution():
    raw=inventory()
    page=project_test_coverage(raw,{'section':'tests','testKind':'gherkin','query':'начальные','runtimeStatus':'unknown'},BASE)
    assert [row['id'] for row in page['records']]==['gh']
    detail=project_test_coverage(raw,{'testId':'gh'},BASE)
    assert detail['detail']['runtime']['status']=='unknown'
    assert detail['detail']['exampleRows']==2 and detail['detail']['examples'][0]['declaredStatus']=='disabled'
    assert detail['detail']['steps'][0]['bindings'][0]['nodeId']=='binding-raw'
    assert detail['sourceFacts']==[raw['sourceFacts'][1]]
    unit=project_test_coverage(raw,{'testId':'unit'},BASE)['detail']
    assert unit['declaredStatus']=='disabled' and unit['runtime']['status']=='unknown' and unit['skipReasons']==['legacy']


def test_exact_unknown_id_errors_and_selected_file_hash_only():
    raw=inventory()
    with pytest.raises(ValueError,match='missing'):
        project_test_coverage(raw,{'conditionId':'missing'},BASE)
    detail=project_test_coverage(raw,{'conditionId':'c0','query':'not matching'},BASE)
    assert detail['detail']['id']=='c0' and detail['sourceFacts']==[raw['sourceFacts'][0]]


def test_sized_pages_never_skip_tail_and_duplicate_ascii_envelope_fits():
    raw=inventory(80)
    for row in raw['conditions']:
        row['condition']='Русское условие'*100
        row['methodLabel']='метод'*200
    offset=0;seen=[];pages=0
    while True:
        page=project_test_coverage(raw,{'section':'conditions','offset':offset,'limit':40},BASE)
        rendered,envelope=_rendered_size(page)
        assert rendered<=30000 and envelope<=64000
        ids=[row['id'] for row in page['records']]
        assert ids and page['pagination']['emitted']==len(ids)
        seen.extend(ids);pages+=1
        next_offset=page['pagination']['nextOffset']
        if next_offset is None:break
        assert next_offset==offset+len(ids)
        offset=next_offset
        assert pages<81
    assert seen==[row['id'] for row in raw['conditions']]
    assert pages>2


def test_oversized_single_row_emits_placeholder_and_cursor_progresses():
    raw=inventory(2)
    raw['conditions'][0]['objectives']=[{'id':'goal','kind':'case','label':'я'*160,'runtimeStatus':'unknown'} for _ in range(100)]
    raw['conditions'][0]['testLinks']=[{'testId':'unit','targetNodeId':'r'*1024,'relations':['я'*64]*30,'bindingEvidence':{'nodeId':'r'*1024,'file':'я'*512,'pattern':'я'*512}}]*2
    raw['conditions'][0]['methodLabel']='я'*1024
    page=project_test_coverage(raw,{'section':'conditions','limit':1},BASE)
    assert page['records'][0]['id']=='c0'
    assert 'recordPayload' in page['records'][0]['fieldsOmitted']
    assert page['pagination']['nextOffset']==1
    assert page['projection']['recordOmissions']==['c0']
    assert _rendered_size(page)[0]<=30000 and _rendered_size(page)[1]<=64000
    detail=project_test_coverage(raw,{'conditionId':'c0'},BASE)
    assert detail['detail']['id']=='c0' and 'recordPayload' in detail['detail']['fieldsOmitted']
    assert _rendered_size(detail)[0]<=30000


def test_long_detail_steps_and_examples_have_explicit_tail_markers():
    raw=inventory();test=raw['tests'][1]
    test['steps']=test['steps']*120
    test['examples']=test['examples']*10
    detail=project_test_coverage(raw,{'testId':'gh'},BASE)
    record=detail['detail']
    assert record['stepsTotal']==120 and len(record['steps'])==8
    assert record['examplesTotal']==10 and len(record['examples'])==4
    assert {'steps','examples'}<=set(record['fieldsOmitted'])
    assert detail['projection']['truncated']


@pytest.mark.parametrize('url',['https://example.com','file:///tmp','http://user:pass@localhost:8766','http://localhost:8766/?token=a'])
def test_local_url_only(url):
    with pytest.raises(ValueError):project_test_coverage(inventory(),{},url)


def test_source_hash_and_report_change_also_invalidates_analysis():
    raw=inventory();first=project_test_coverage(raw,{},BASE)['analysisId']
    raw['sourceFacts'][0]['sourceHash']='d'*64
    assert project_test_coverage(raw,{},BASE)['analysisId']!=first
    first=project_test_coverage(raw,{},BASE)['analysisId'];raw['reports'][0]['mtimeNs']+=1
    assert project_test_coverage(raw,{},BASE)['analysisId']!=first


def test_url_restores_explicit_reports_filters_and_exact_condition():
    from urllib.parse import parse_qs,urlsplit
    query={'conditionId':'c1','query':'доход','kind':'if','runtimeStatus':'partial','testKind':'gherkin','reportPaths':['reports/a.xml','reports/b.xml']}
    result=project_test_coverage(inventory(),query,BASE)
    params=parse_qs(urlsplit(result['coverageUrl']).query)
    assert params['repo']==['repo'] and params['snapshot']==['snapshot']
    assert params['report']==query['reportPaths']
    assert params['condition']==['c1'] and params['q']==['доход']
    assert params['kind']==['if'] and params['runtime']==['partial'] and params['tests']==['gherkin']


def test_boundary_metadata_including_cursor_omissions_is_measured_before_acceptance(monkeypatch):
    import atlas.test_coverage_mcp as module
    raw=inventory(40)
    for row in raw['conditions']:
        row['condition']='Русское условие'*100
        row['objectives']*=10
    # Exercise different exact limits: cursor and omission IDs cannot push a just-fitting row over budget.
    for limit in [8000,10000,12000,15000,21000,29000]:
        monkeypatch.setattr(module,'DATA_BUDGET_BYTES',limit)
        result=module.project_test_coverage(raw,{'section':'conditions','limit':40},BASE)
        assert module._rendered_size(result)[0]<=limit
        assert module._rendered_size(result)[1]<=64000
        assert result['pagination']['nextOffset']==len(result['records'])
        assert result['pagination']['emitted']==len(result['records'])>0


def test_valid_max_url_state_cannot_bypass_envelope_cap():
    query={'reportPaths':['я'*1024+'.xml' for _ in range(8)]}
    # Paths exceed1024 here; validator must reject before projection; then valid max-size paths get an explicit URL omission.
    with pytest.raises(ValueError):project_test_coverage(inventory(),query,BASE)
    query['reportPaths']=['я'*1020+'.xml' for _ in range(8)]
    result=project_test_coverage(inventory(),query,BASE)
    assert _rendered_size(result)[0]<=30000 and _rendered_size(result)[1]<=64000
    assert result['coverageUrl'] is None and 'coverageUrl' in result['projection']['fieldsOmitted']


def test_offset_cap_matches_public_tool():
    assert validate_coverage_query(offset=100000)['offset']==100000
    with pytest.raises(ValueError):validate_coverage_query(offset=100001)


@pytest.mark.parametrize('length',[900,1024])
def test_nested_raw_ids_remain_exact_despite_short_text_caps(length):
    raw=inventory();identifier='raw-'+('x'*(length-4))
    raw['conditions'][0]['testLinks'][0]['bindingEvidence']={'nodeId':identifier,'file':'Steps.cs','line':1,'pattern':'step'}
    raw['tests'][1]['steps'][0]['bindings'][0]['nodeId']=identifier
    condition=project_test_coverage(raw,{'conditionId':'c0'},BASE)
    assert condition['detail']['testLinks'][0]['bindingEvidence']['nodeId']==identifier
    test=project_test_coverage(raw,{'testId':'gh'},BASE)
    assert test['detail']['steps'][0]['bindings'][0]['nodeId']==identifier
    for result in [condition,test]:
        assert _rendered_size(result)[0]<=30000 and _rendered_size(result)[1]<=64000


def test_oversized_identity_fields_and_lists_are_unavailable_not_sliced():
    raw=inventory();identifier='raw-'+('x'*1500)
    row=raw['conditions'][0];row['ownerNodeId']=identifier
    row['testLinks'][0]['targetNodeId']=identifier
    row['testLinks'][0]['bindingEvidence']={'nodeId':identifier,'file':'Steps.cs'}
    test=raw['tests'][1];test['nodeId']=identifier
    test['steps'][0]['bindings'][0]['nodeId']=identifier
    test['staticTargetIds']=['usable',identifier]
    condition=project_test_coverage(raw,{'conditionId':'c0'},BASE)['detail']
    assert condition['ownerNodeId'] is None
    assert condition['testLinks'][0]['targetNodeId'] is None
    assert condition['testLinks'][0]['bindingEvidence']['nodeId'] is None
    assert {'ownerNodeId','testLinks[0].targetNodeId','testLinks[0].bindingEvidence.nodeId'}<=set(condition['fieldsOmitted'])
    detail=project_test_coverage(raw,{'testId':'gh'},BASE)['detail']
    assert detail['nodeId'] is None and detail['steps'][0]['bindings'][0]['nodeId'] is None
    assert detail['staticTargetIds']==['usable']
    assert {'nodeId','steps[0].bindings.nodeId','staticTargetIds[1]'}<=set(detail['fieldsOmitted'])
    # No result contains the syntactically valid-looking 1024-character prefix of the oversized identity.
    assert identifier[:1024] not in json.dumps(condition) and identifier[:1024] not in json.dumps(detail)
