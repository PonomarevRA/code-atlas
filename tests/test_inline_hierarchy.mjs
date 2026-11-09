import assert from 'node:assert/strict';
import { buildInlineHierarchy, inlineFileId } from '../web/inline-hierarchy.js';
const raw = (id, kind = 'method', file = 'Code.cs', project = 'p') => ({ id, label: id, kind, file, codeProjectId: project });
const edge = (source, target, relation = 'contains', rest = {}) => ({ source, target, relation, ...rest });
const root = node => ({ id: node.id, rawId: node.id, label: node.label, kind: 'raw', node, memberIds: [node.id] });
const graph = { nodes: [raw('type', 'class'), raw('method'), raw('attr', 'attribute'), raw('target'), raw('neighbor')],
  edges: [edge('type', 'method', 'method'), edge('method', 'attr', 'has_attribute'), edge('method', 'target', 'calls'), edge('neighbor', 'target', 'dispatches_to', { confidence: 'INFERRED', evidence: { kind: 'interface-implementation' } })] };
const saved = JSON.stringify(graph);
const roots = [root(graph.nodes[0]), root(graph.nodes[1]), root(graph.nodes[4])];
const expanded = new Set(['type', 'method']);
const scene = buildInlineHierarchy(graph, { roots, rawNodes: graph.nodes, expandedIds: expanded });
assert.deepEqual(scene.roots, ['neighbor', 'type']);
assert.deepEqual(scene.items.map(item => item.id), ['neighbor', 'type', 'method', 'attr']);
assert.equal(scene.items.find(item => item.id === 'method').parentId, 'type');
assert.equal(scene.items.find(item => item.id === 'attr').parentId, 'method');
assert.ok(!scene.items.some(item => item.id === 'target'), 'calls never become children');
assert.equal(scene.items.find(item => item.id === 'type').node, graph.nodes[0]);
assert.equal(JSON.stringify(graph), saved);
assert.deepEqual([...expanded], ['type', 'method']);
assert.deepEqual(buildInlineHierarchy(graph, { roots }).roots, ['method', 'neighbor', 'type'], 'collapse restores all original roots');
assert.ok(scene.items.every(item => item.id === item.rawId));
assert.equal(scene.items.find(item => item.id === 'method').enter.rawId, 'method');

const project = { id: 'project-card', label: 'Project', kind: 'project', projectId: 'p', memberIds: graph.nodes.map(node => node.id), node: { id: 'project-card' } };
const fileId = inlineFileId(project.id, 'p', 'Code.cs');
const projectScene = buildInlineHierarchy(graph, { roots: [project], expandedIds: new Set([project.id, fileId, 'type', 'method']) });
assert.equal(projectScene.items.find(item => item.id === fileId).kind, 'file');
assert.equal(projectScene.items.find(item => item.id === fileId).parentId, project.id);
assert.equal(projectScene.items.find(item => item.id === 'method').parentId, 'type');
assert.equal(projectScene.items.find(item => item.id === 'attr').parentId, 'method');
assert.ok(projectScene.items.find(item => item.id === 'target').parentId === fileId, 'standalone declarations remain file roots');
assert.ok(projectScene.edges.some(item => item.source === 'method' && item.target === 'target' && item.relation === 'calls'));
assert.ok(projectScene.edges.some(item => item.relation === 'dispatches_to' && item.candidate && item.evidence.kind === 'interface-implementation'));
assert.notEqual(inlineFileId('parent2', 'p', 'Code.cs'), fileId);
assert.notEqual(inlineFileId(project.id, 'other', 'Code.cs'), fileId);

const filtered = buildInlineHierarchy(graph, { roots: [project], rawNodes: graph.nodes.filter(node => node.id !== 'attr'), expandedIds: new Set([project.id, fileId, 'type', 'method']) });
assert.ok(!filtered.items.some(item => item.id === 'attr'));
assert.ok(filtered.items.every(item => !item.memberIds.includes('attr')));
assert.equal(filtered.items.find(item => item.id === 'method').totalChildren, 0);
const callsOnly = buildInlineHierarchy(graph, { roots: [project], expandedIds: new Set([project.id, fileId, 'type']), relation: 'calls' });
assert.ok(callsOnly.edges.every(item => item.relation === 'calls'));
assert.ok(callsOnly.items.some(item => item.id === 'method'), 'relation filter applies displayed edges, not invented hierarchy');

const cycles = { nodes: [raw('a', 'class'), raw('b', 'class'), raw('shared')], edges: [edge('a', 'b'), edge('b', 'a'), edge('a', 'shared'), edge('b', 'shared')] };
const cycle = buildInlineHierarchy(cycles, { roots: cycles.nodes.slice(0, 2).map(root), expandedIds: new Set(['a', 'b']) });
assert.equal(new Set(cycle.items.map(item => item.id)).size, cycle.items.length);
assert.ok(cycle.warnings.some(warning => warning.kind === 'cycle'));
assert.ok(cycle.warnings.some(warning => warning.kind === 'shared-parent'));
assert.equal(cycle.items.filter(item => item.id === 'shared').length, 1);
const groupedCycle = buildInlineHierarchy(cycles, { roots: [{ id: 'group', label: 'Group', kind: 'community', memberIds: cycles.nodes.map(node => node.id) }], expandedIds: new Set(['group', 'a', 'b']) });
assert.ok(groupedCycle.warnings.some(warning => warning.kind === 'cyclic-root'));
assert.equal(new Set(groupedCycle.items.map(item => item.id)).size, groupedCycle.items.length);

const many = { nodes: [raw('owner', 'class'), ...Array.from({ length: 8 }, (_, index) => raw(`child${index}`)), raw('neighbor')], edges: Array.from({ length: 8 }, (_, index) => edge('owner', `child${index}`, 'method')) };
const limited = buildInlineHierarchy(many, { roots: [root(many.nodes[0]), root(many.nodes.at(-1))], expandedIds: new Set(['owner']), maxItems: 4, maxChildren: 6 });
const owner = limited.items.find(item => item.id === 'owner');
assert.equal(owner.totalChildren, 8); assert.equal(owner.visibleChildren, 2); assert.ok(owner.childrenTruncated && limited.truncated);
assert.ok(limited.items.some(item => item.id === 'neighbor'), 'children cannot evict a base neighbor');
assert.equal(limited.items.length, 4);
const depth = buildInlineHierarchy(graph, { roots: [root(graph.nodes[0])], expandedIds: expanded, maxDepth: 1 });
assert.equal(depth.items.find(item => item.id === 'method').visibleChildren, 0);
assert.equal(depth.items.find(item => item.id === 'method').totalChildren, 1);
assert.ok(depth.truncated);

const edgesGraph = { nodes: [raw('one'), raw('two')], edges: [edge('one','two','calls',{confidence:'HIGH'}), edge('one','two','calls',{confidence:'HIGH'}), edge('one','two','calls',{confidence:'INFERRED',evidence:{kind:'typed-interface-call'}})] };
const edgeScene = buildInlineHierarchy(edgesGraph, { roots: edgesGraph.nodes.map(root) });
assert.equal(edgeScene.edges.length, 2, 'source calls and inferred candidates stay distinct');
assert.equal(edgeScene.edges.find(item => !item.candidate).count, 2);
assert.equal(edgeScene.edges.find(item => item.candidate).count, 1);
const reversed = buildInlineHierarchy({ nodes: [...graph.nodes].reverse(), edges: [...graph.edges].reverse() }, { roots: [...roots].reverse(), rawNodes: [...graph.nodes].reverse(), expandedIds: expanded });
assert.deepEqual(reversed.items.map(({ id, parentId, depth }) => ({ id, parentId, depth })), scene.items.map(({ id, parentId, depth }) => ({ id, parentId, depth })));
console.log('Inline hierarchy model: PASS');

// A filtered/group scope cannot bridge through an excluded declaration and swallow a later visible member.
const bridge={nodes:[raw('insideA','class'),raw('outside','class'),raw('insideB')],edges:[edge('insideA','outside'),edge('outside','insideB')]};
const boundedGroup=buildInlineHierarchy(bridge,{roots:[{id:'small-group',label:'Small',kind:'community',memberIds:['insideA','insideB']}],expandedIds:new Set(['small-group','insideA'])});
assert.ok(boundedGroup.items.some(item=>item.id==='insideB'));
assert.ok(!boundedGroup.items.some(item=>item.id==='outside'));
assert.equal(boundedGroup.items.find(item=>item.id==='insideA').totalChildren,0);

// Lexically earlier method owners do not expand at root depth before being consumed into their ancestor.
const deep={nodes:[raw('zType','class'),raw('aNested','class'),raw('bMethod')],edges:[edge('zType','aNested'),edge('aNested','bMethod')]};
const boundedDepth=buildInlineHierarchy(deep,{roots:deep.nodes.slice(0,2).map(root),expandedIds:new Set(['zType','aNested']),maxDepth:1});
assert.ok(boundedDepth.items.every(item=>item.depth<=1));
assert.equal(boundedDepth.items.find(item=>item.id==='aNested').totalChildren,1);
assert.equal(boundedDepth.items.find(item=>item.id==='aNested').visibleChildren,0);

// Real Graphify file symbols are transparent within synthetic file/community cards, preserving declaration IDs.
const fileWrapper={id:'web_graph',kind:'symbol',label:'graph.js',file:'web/graph.js',source_file:'web/graph.js',source_location:'L1',file_type:'code',_origin:'ast'};
const graphify={nodes:[fileWrapper,raw('GraphView','class','web/graph.js'),raw('render','method','web/graph.js'),raw('helper','function','web/graph.js')],edges:[edge('web_graph','GraphView'),edge('web_graph','helper'),edge('GraphView','render','method')]};
const fileScene=buildInlineHierarchy(graphify,{roots:[{id:'file-card',label:'graph.js',kind:'file',file:'web/graph.js',memberIds:graphify.nodes.map(item=>item.id)}],expandedIds:new Set(['file-card','GraphView'])});
assert.deepEqual(fileScene.items.find(item=>item.id==='file-card').children,['GraphView','helper']);
assert.ok(!fileScene.items.some(item=>item.id==='web_graph'));
assert.equal(fileScene.items.find(item=>item.id==='render').parentId,'GraphView');
assert.equal(fileScene.membership.get('web_graph'),'file-card');
assert.equal(fileScene.items.find(item=>item.id==='GraphView').rawId,'GraphView');
const deliberate=buildInlineHierarchy({...graphify,nodes:[{...fileWrapper,file_type:'custom'},...graphify.nodes.slice(1)]},{roots:[{id:'file-card',label:'graph.js',kind:'file',memberIds:graphify.nodes.map(item=>item.id)}],expandedIds:new Set(['file-card'])});
assert.ok(deliberate.items.some(item=>item.id==='web_graph'),'label alone cannot classify arbitrary symbols as file wrappers');

// Native screenshot: base raw file/class/method/attribute cards become one zone; collapsed methods represent their attrs.
const nativeNodes=[raw('nativeFile','symbol','Native.cs'),raw('nativeClass','class','Native.cs'),...Array.from({length:6},(_,index)=>raw('m'+index,'method','Native.cs')),...Array.from({length:6},(_,index)=>raw('a'+index,'attribute','Native.cs')),raw('externalNative','class','Elsewhere.cs')];
const nativeGraph={nodes:nativeNodes,edges:[edge('nativeFile','nativeClass'),...Array.from({length:6},(_,index)=>edge('nativeClass','m'+index,'method')),...Array.from({length:6},(_,index)=>edge('m'+index,'a'+index,'has_attribute')),edge('a3','externalNative','references')]};
const originalNative=JSON.stringify(nativeGraph);
const nativeRoots=nativeNodes.map(root);
const nativeScene=buildInlineHierarchy(nativeGraph,{roots:nativeRoots,expandedIds:new Set(['nativeFile','nativeClass'])});
assert.deepEqual(nativeScene.roots,['externalNative','nativeFile']);
assert.equal(nativeScene.items.length,9);
assert.equal(nativeScene.hiddenRoots.length,6);
assert.equal(nativeScene.counts.hiddenBaseRoots,6);
assert.equal(nativeScene.membership.get('a3'),'m3');
assert.equal(nativeScene.items.find(item=>item.id==='m3').totalChildren,1);
assert.equal(nativeScene.items.find(item=>item.id==='m3').visibleChildren,0);
assert.ok(nativeScene.edges.some(item=>item.source==='m3'&&item.target==='externalNative'&&item.relation==='references'));
assert.ok(nativeScene.items.every(item=>!/^a\d$/u.test(item.id)));
const oneMethod=buildInlineHierarchy(nativeGraph,{roots:nativeRoots,expandedIds:new Set(['nativeFile','nativeClass','m0'])});
assert.equal(oneMethod.items.length,10);
assert.equal(oneMethod.items.find(item=>item.id==='a0').parentId,'m0');
assert.equal(oneMethod.hiddenRoots.length,5);
assert.deepEqual(buildInlineHierarchy(nativeGraph,{roots:nativeRoots}).roots,[...nativeRoots].map(item=>item.id).sort());
assert.equal(JSON.stringify(nativeGraph),originalNative);
const absentMethod=buildInlineHierarchy(nativeGraph,{roots:nativeRoots.filter(item=>item.id!=='m3'),rawNodes:nativeNodes.filter(item=>item.id!=='m3'),expandedIds:new Set(['nativeFile','nativeClass'])});
assert.ok(absentMethod.items.some(item=>item.id==='a3'),'filtered absent owner cannot swallow its attribute');
assert.equal(absentMethod.membership.get('a3'),'a3');
const externalMethodGraph={nodes:[...nativeNodes,raw('outsideMethod','method','Elsewhere.cs'),raw('outsideAttr','attribute','Elsewhere.cs')],edges:[...nativeGraph.edges,edge('outsideMethod','outsideAttr','has_attribute'),edge('m0','outsideMethod','calls')]};
const externalMethod=buildInlineHierarchy(externalMethodGraph,{roots:externalMethodGraph.nodes.map(root),expandedIds:new Set(['nativeFile','nativeClass'])});
assert.ok(externalMethod.items.some(item=>item.id==='outsideAttr'));
assert.ok(externalMethod.roots.includes('outsideMethod')&&externalMethod.roots.includes('outsideAttr'),'calls cannot consume another method or its attributes');
