"""Position anchoring, directed graph invariants and source safety independent of HTTP."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from atlas.position_context import PositionContextError, PositionContextInput, build_position_context


def fixture(tmp_path, text, declarations, filename='code.py', edges=(), hashed=True):
    """Capture exact source hashes with declared raw identities and line positions."""
    path = tmp_path / filename; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
    digest = sha256(path.read_bytes()).hexdigest()
    nodes = []
    for raw, name, line in declarations:
        node = {'id': raw, 'label': name+'()', 'kind': 'function', 'file': filename, 'line': line}
        if hashed:
            node['complexity'] = {'sourceKind': 'snapshot-captured-source', 'sourceHash': digest}
        nodes.append(node)
    return {'snapshotId': 's', 'metadata': {'snapshotId': 's', 'analysisStatus': 'partial'},
            'nodes': nodes, 'edges': [{'source': a, 'target': b, 'relation': relation, 'line': line}
                                     for a,b,relation,line in edges]}, digest


def query(root, graph, line, **options):
    """Exercise the actual pure public entry, preserving strict request validation."""
    return build_position_context(root, graph, PositionContextInput(snapshotId='s', file=options.pop('file','code.py'),line=line,**options))


def test_python_position_blank_lines_and_current_null_arguments(tmp_path):
    text='def top(x):\n    middle(value=None)\n\ndef middle(value):\n    if value is None:\n        leaf(value)\n\ndef leaf(value):\n    pass\n'
    g,h=fixture(tmp_path,text,[('top','top',1),('middle','middle',4),('leaf','leaf',8)],
                edges=[('top','middle','calls',2),('middle','leaf','calls',6)])
    before=deepcopy(g);r=query(tmp_path,g,5,expectedSourceHash=h)
    assert r['status']=='resolved' and r['anchorId']=='middle'
    assert r['incoming']['nodes']==['top'] and r['outgoing']['nodes']==['leaf']
    caller=r['sourceEvidence']['callers'][0]
    assert caller['nodeId']=='top' and caller['sourceProvenance']['status']=='match'
    assert caller['literalNullArguments'][0]['literal']=='None'
    assert caller['literalNullArguments'][0]['keywordName']=='value'
    assert caller['literalNullArguments'][0]['targetNodeIds']==['middle']
    assert caller['literalNullArguments'][0]['bindingStatus']=='candidate'
    assert 'UTF-8' in r['sourceEvidence']['coordinateSystem']
    assert query(tmp_path,g,3)['status']=='not-found' and query(tmp_path,g,7)['anchorId'] is None
    assert query(tmp_path,g,999)['status']=='not-found' and g==before


def test_csharp_literal_null_from_caller_and_no_sibling_fallback(tmp_path):
    text='class Example {\n void Top() { Middle(null); }\n\n void Middle(object value) {\n  if(value == null) Leaf(value);\n }\n void Leaf(object value) {}\n}\n'
    g,h=fixture(tmp_path,text,[('top','Top',2),('middle','Middle',4),('leaf','Leaf',7)],filename='code.cs',
                edges=[('top','middle','calls',2),('middle','leaf','calls',5)])
    r=query(tmp_path,g,5,file='code.cs')
    assert r['status']=='resolved' and r['anchorId']=='middle'
    null=r['sourceEvidence']['callers'][0]['literalNullArguments'][0]
    assert null['literal']=='null' and null['targetNodeIds']==['middle'] and null['line']==2
    assert r['sourceEvidence']['guards']
    assert query(tmp_path,g,3,file='code.cs')['status']=='not-found'


def test_nested_unmapped_declaration_never_falls_back_to_outer(tmp_path):
    text='def outer():\n    def inner():\n        leaf(None)\n    return inner()\n'
    g,_=fixture(tmp_path,text,[('outer','outer',1)])
    r=query(tmp_path,g,3)
    assert r['status']=='unknown' and r['anchorId'] is None
    assert r['astCandidates'][0]['name']=='inner'
    g['nodes'].append({'id':'inner','label':'inner()','kind':'function','file':'code.py','line':2,
                       'complexity':deepcopy(g['nodes'][0]['complexity'])})
    assert query(tmp_path,g,3)['anchorId']=='inner'
    outer=query(tmp_path,g,4)
    assert outer['anchorId']=='outer' and outer['sourceEvidence']['literalNullArguments']==[]


def test_same_line_csharp_overloads_remain_ambiguous(tmp_path):
    g,_=fixture(tmp_path,'class C { void A() {} void A(int x) {} }', [('one','A',1),('two','A',1)],filename='code.cs')
    r=query(tmp_path,g,1,file='code.cs')
    assert r['status']=='ambiguous' and r['anchorId'] is None


@pytest.mark.parametrize('depth',[1,2,3,8])
def test_independent_bfs_no_zigzag_cycles_diamond_and_minimum_distance(tmp_path,depth):
    names=list('abcdefg');text=''.join(f'def {n}():\n    pass\n\n' for n in names)
    edges=[('a','b','calls',1),('c','b','calls',7),('b','d','dispatches_to',4),('d','e','calls',10),
           ('b','f','calls',4),('f','e','calls',16),('e','b','calls',13),('b','b','calls',4),('g','a','calls',19)]
    g,_=fixture(tmp_path,text,[(n,n,i*3+1) for i,n in enumerate(names)],edges=edges)
    r=query(tmp_path,g,4,depth=depth)
    assert 'c' not in r['outgoing']['nodes'] and 'g' not in r['outgoing']['nodes']
    assert r['incoming']['depths']['a']==1 and r['outgoing']['depths']['d']==1
    if depth>=2:
        assert r['incoming']['depths']['g']==2 and r['outgoing']['depths']['e']==2
    assert all(value<=depth for direction in ('incoming','outgoing') for value in r[direction]['depths'].values())
    assert len({e['edgeHash'] for e in r['edges']})==len(r['edges'])


@pytest.mark.parametrize('direction',['incoming','outgoing'])
def test_single_direction_is_not_reversed_by_shared_frontier(tmp_path,direction):
    g,_=fixture(tmp_path,'def a():\n pass\ndef b():\n pass\ndef c():\n pass\n', [('a','a',1),('b','b',3),('c','c',5)],
                edges=[('a','b','calls',1),('b','c','calls',3)])
    r=query(tmp_path,g,3,direction=direction)
    other='outgoing' if direction=='incoming' else 'incoming'
    assert r[other]['nodes']==[] and r[direction]['nodes']==(['a'] if direction=='incoming' else ['c'])


def test_missing_hash_and_changed_source_are_never_exact_mapping(tmp_path):
    g,h=fixture(tmp_path,'def a():\n pass\n',[('a','a',1)],hashed=False)
    r=query(tmp_path,g,1);assert r['status']=='unknown' and r['anchorId']=='a' and r['sourceProvenance']['status']=='unknown'
    g['nodes'][0]['complexity']={'sourceKind':'snapshot-captured-source','sourceHash':h}
    (tmp_path/'code.py').write_text('def a():\n return None\n')
    r=query(tmp_path,g,2);assert r['status']=='unknown' and r['sourceProvenance']['status']=='changed'
    with pytest.raises(PositionContextError) as error:query(tmp_path,g,1,expectedSourceHash=h)
    assert error.value.status==409


def test_metadata_snapshot_conflict_and_unrepresented_file(tmp_path):
    g,_=fixture(tmp_path,'def a(): pass',[('a','a',1)])
    g['metadata']['snapshotId']='other'
    with pytest.raises(PositionContextError) as e:query(tmp_path,g,1)
    assert e.value.status==409
    g['metadata']['snapshotId']='s';(tmp_path/'other.py').write_text('def x(): pass')
    with pytest.raises(PositionContextError) as e:query(tmp_path,g,1,file='other.py')
    assert e.value.status==404


def test_path_boundaries_absolute_inside_and_symlink_outside(tmp_path):
    root=tmp_path/'repo';root.mkdir();g,_=fixture(root,'def a(): pass',[('a','a',1)])
    assert query(root,g,1,file=str(root/'code.py'))['anchorId']=='a'
    outside=tmp_path/'outside.py';outside.write_text('def a(): pass')
    for value in (str(outside),'../outside.py'):
        with pytest.raises(PositionContextError) as e:query(root,g,1,file=value)
        assert e.value.status==403
    (root/'link.py').symlink_to(outside)
    with pytest.raises(PositionContextError) as e:query(root,g,1,file='link.py')
    assert e.value.status==403


@pytest.mark.parametrize('field,value',[('line',True),('line',0),('depth',True),('depth',9),('maxNodes',121),('maxEdges',241),('maxEdges',False),('direction','sideways'),('graph',{}),('expectedSourceHash','a')])
def test_strict_inputs(field,value):
    values={'snapshotId':'s','file':'code.py','line':1};values[field]=value
    with pytest.raises(ValidationError):PositionContextInput(**values)


def test_shared_node_edge_and_utf8_byte_budgets_whole_identity(tmp_path):
    g,_=fixture(tmp_path,'def a(): pass',[('a','a',1)])
    long_id='caller:'+'я'*1700
    for i in range(130):
        raw=long_id+str(i);g['nodes'].append({'id':raw,'label':'я'*1000,'kind':'function','file':'folder/'+'я'*2000+'.py','line':1})
        g['edges'].append({'source':'a','target':raw,'relation':'calls','evidence':{'text':'я'*4000}})
    r=query(tmp_path,g,1,direction='outgoing',maxNodes=120,maxEdges=240)
    assert len(json.dumps(r,ensure_ascii=False).encode())<=64000 and r['truncated']
    ids={n['id'] for n in r['nodes']}
    assert 'a' in ids and all(raw=='a' or raw.startswith(long_id) for raw in ids)
    for n in r['nodes']:
        if n['id']!='a':assert n['file']=='folder/'+'я'*2000+'.py'
    assert all(e['source'] in ids and e['target'] in ids for e in r['edges'])
    small=query(tmp_path,g,1,maxNodes=1,maxEdges=1)
    assert len(small['nodes'])==1 and small['truncated']


def test_unreadable_caller_unknown_and_no_literal_text_regex(tmp_path):
    text='def middle(value):\n    log("None")\n\ndef top():\n    middle("None")\n'
    g,_=fixture(tmp_path,text,[('middle','middle',1),('top','top',4)],edges=[('top','middle','calls',5)])
    r=query(tmp_path,g,1)
    assert r['sourceEvidence']['callers'][0]['literalNullArguments']==[]
    g['nodes'][1]['file']='missing.py'
    r=query(tmp_path,g,1)
    assert r['sourceEvidence']['callers'][0]['status']=='unknown'
    assert r['sourceEvidence']['callers'][0]['literalNullStatus']=='unavailable'


@pytest.mark.parametrize('language',['python','csharp'])
@pytest.mark.parametrize('expression,expected',[('target(1); other(NULL)',0),('target(other(NULL))',0),('target(1); target(NULL)',1)])
def test_caller_nulls_are_bound_to_complete_ast_call_occurrences(tmp_path,language,expression,expected):
    if language=='python':
        text='def top():\n    '+expression.replace('NULL','None')+'\ndef target(x):\n    pass\ndef other(x):\n    pass\n'
        filename='code.py';decl=[('top','top',1),('target','target',3),('other','other',5)];line=3
    else:
        text='class C {\n void top() { '+expression.replace('NULL','null')+'; }\n void target(object x) {}\n void other(object x) {}\n}\n'
        filename='code.cs';decl=[('top','top',2),('target','target',3),('other','other',4)];line=3
    g,_=fixture(tmp_path,text,decl,filename=filename,edges=[('top','target','calls',2),('top','other','calls',2)])
    r=query(tmp_path,g,line,file=filename)
    facts=r['sourceEvidence']['callers'][0]['literalNullArguments']
    assert len(facts)==expected
    for fact in facts:
        assert fact['targetNodeIds']==['target']
        assert set(fact['callSite'])=={'line','column','endLine','endColumn'}


def test_caller_count_and_shared_file_budget_are_explicit(tmp_path):
    g,_=fixture(tmp_path,'def middle(x):\n pass\n',[('middle','middle',1)])
    for i in range(6):
        name=f'c{i}';text='def '+name+'():\n    middle(None)\n';p=tmp_path/(name+'.py');p.write_text(text)
        g['nodes'].append({'id':name,'label':name+'()','kind':'function','file':p.name,'line':1,
                           'complexity':{'sourceKind':'snapshot-captured-source','sourceHash':sha256(p.read_bytes()).hexdigest()}})
        g['edges'].append({'source':name,'target':'middle','relation':'calls','line':2})
    r=query(tmp_path,g,1)
    assert len(r['sourceEvidence']['callers'])==4
    assert r['sourceEvidence']['callersTruncated'] and r['truncated']
    assert all(c['literalNullArguments'][0]['requestedTargetId']=='middle' for c in r['sourceEvidence']['callers'])


def test_large_null_list_clips_with_explicit_observation_flag(tmp_path):
    g,_=fixture(tmp_path,'def a():\n    other('+','.join(['None']*30)+')\n',[('a','a',1)])
    r=query(tmp_path,g,1)
    assert len(r['sourceEvidence']['literalNullArguments'])==16
    assert r['sourceEvidence']['literalNullArgumentsTruncated']
    assert r['sourceEvidence']['truncated'] and r['truncated']


def test_decorated_python_decl_and_malformed_code_do_not_guess(tmp_path):
    g,_=fixture(tmp_path,'@decorate\ndef a():\n    pass\n',[('a','a',1)])
    assert query(tmp_path,g,1)['anchorId']=='a'
    (tmp_path/'code.py').write_text('def a(\n')
    r=query(tmp_path,g,1)
    assert r['status']=='unknown' and r['anchorId'] is None


def test_captured_range_unknown_language_requires_current_captured_hash(tmp_path):
    g,h=fixture(tmp_path,'header\nbody\nend\n',[('a','a',1)],filename='code.xyz')
    g['nodes'][0]['callableFacts']={'sourceKind':'snapshot-captured-source','sourceHash':h,'line':1,'endLine':3}
    r=query(tmp_path,g,2,file='code.xyz')
    assert r['status']=='resolved' and r['candidates'][0]['rangeKind']=='captured-range'
    assert r['sourceEvidence']['literalNullStatus']=='unavailable'


def test_duplicate_raw_ids_and_large_files_rejected(tmp_path):
    g,_=fixture(tmp_path,'def a(): pass',[('a','a',1)])
    g['nodes'].append(deepcopy(g['nodes'][0]))
    with pytest.raises(PositionContextError):query(tmp_path,g,1)
    g['nodes'].pop();(tmp_path/'code.py').write_text('x'*(1024*1024+1))
    with pytest.raises(PositionContextError) as e:query(tmp_path,g,1)
    assert e.value.status==413


@pytest.mark.parametrize('optional_first',[True,False])
def test_edge_contract_survives_optional_metadata_order_and_full_long_identity(tmp_path,optional_first):
    g,_=fixture(tmp_path,'def a(): pass',[('a','a',1)])
    target='target:'+'я'*800
    g['nodes'].append({'id':target,'kind':'external','label':'target'})
    mandatory={'source':'a','target':target,'relation':'calls','id':'edge:'+'й'*800}
    optional={f'annotation{i}':'x' for i in range(35)}
    edge={**optional,**mandatory} if optional_first else {**mandatory,**optional}
    g['edges']=[edge]
    r=query(tmp_path,g,1,direction='outgoing')
    actual=r['edges'][0]
    for key,value in mandatory.items():assert actual[key]==value
    assert actual['metadataTruncated']
    assert actual['edgeHash']==sha256(json.dumps(edge,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


@pytest.mark.parametrize('call',['target(value=None, *args)', 'target(\n        value=None,\n        *args\n    )'])
def test_python_keyword_before_starred_arguments_retains_lexical_source_ordinal(tmp_path,call):
    text='def top(args):\n    '+call+'\n'
    g,_=fixture(tmp_path,text,[('top','top',1)])
    fact=query(tmp_path,g,1)['sourceEvidence']['literalNullArguments'][0]
    assert fact['argumentIndex']==0 and fact['keywordName']=='value'
    assert fact['literal']=='None'


def test_csharp_argument_comments_do_not_count_as_arguments(tmp_path):
    g,_=fixture(tmp_path,'class C {\n void top() { target(/* comment */ value: null); }\n}\n',
                [('top','top',2)],filename='code.cs')
    fact=query(tmp_path,g,2,file='code.cs')['sourceEvidence']['literalNullArguments'][0]
    assert fact['argumentIndex']==0 and fact['keywordName']=='value'
