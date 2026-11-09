/** Приёмка сочетаний фильтров и точных агрегатов индуцированного подграфа. */
import assert from 'node:assert/strict';
import { filterNodes, aggregateCommunities } from '../web/filters.js';

const graph = {
  nodes: [
    { id: 'a', label: 'Invoice Export', file: 'src/Billing/Export.cs', kind: 'method', community: 'one', codeProjectId: 'billing' },
    { id: 'b', label: 'Invoice Cache', file: 'src/Billing/Cache.cs', kind: 'class', community: 'one', codeProjectId: 'billing' },
    { id: 'c', label: 'Payment Export', file: 'src/Payments/Export.cs', kind: 'method', community: 'two', codeProjectId: 'payments' },
    { id: 'd', label: 'Export Tests', file: 'tests/Export.cs', kind: 'method', community: 'two', codeProjectId: 'tests' },
    { id: 'isolated', label: 'Detached', file: '', kind: 'external', community: 'three', codeProjectId: '__unassigned__' },
  ],
  edges: [
    { source: 'a', target: 'b', relation: 'calls' },
    { source: 'a', target: 'c', relation: 'imports' },
    { source: 'c', target: 'a', relation: 'calls' },
    { source: 'b', target: 'd', relation: 'calls' },
  ],
  communities: [
    { id: 'one', label: 'Billing', nodeCount: 2, internalEdges: 1, externalEdges: 3, x: 0, y: 0 },
    { id: 'two', label: 'Payments', nodeCount: 2, internalEdges: 0, externalEdges: 3, x: 1, y: 1 },
    { id: 'three', label: 'External', nodeCount: 1, internalEdges: 0, externalEdges: 0, x: -1, y: -1 },
  ],
};
const before = JSON.stringify(graph);
/** Сравнивает совпадения без зависимости от порядка отображения списка. */
const ids = nodes => nodes.map(node => node.id).sort();
assert.deepEqual(ids(filterNodes(graph, { keywords: ' EXPORT invoice ' })), ['a']);
assert.deepEqual(ids(filterNodes(graph, { keywords: 'src export', kind: 'method' })), ['a', 'c']);
assert.deepEqual(ids(filterNodes(graph, { codeProjectIds: ['billing'] })), ['a', 'b']);
assert.deepEqual(ids(filterNodes(graph, { codeProjectIds: [] })), []);
assert.deepEqual(ids(filterNodes(graph, { codeProjectIds: null })), ['a', 'b', 'c', 'd', 'isolated']);
assert.deepEqual(ids(filterNodes(graph, { keywords: 'export', codeProjectIds: ['billing'], kind: 'method' })), ['a']);
assert.deepEqual(ids(filterNodes(graph, { keywords: 'export', codeProjectIds: ['billing'], kind: 'class' })), []);
assert.deepEqual(ids(filterNodes(graph, { relation: 'imports' })), ['a', 'c']);
assert.deepEqual(ids(filterNodes(graph, { neighborOf: 'a' })), ['a', 'b', 'c']);
// Исключённый фильтром центр не создаёт видимую связь с соседями вне подграфа.
assert.deepEqual(ids(filterNodes(graph, { neighborOf: 'a', codeProjectIds: ['payments'] })), []);

const billing = aggregateCommunities(graph, filterNodes(graph, { codeProjectIds: ['billing'] }));
assert.equal(billing.communities.length, 1);
assert.equal(billing.communities[0].nodeCount, 2);
assert.equal(billing.communities[0].internalEdges, 1);
assert.equal(billing.communities[0].externalEdges, 0);
assert.equal(billing.edges.length, 1);
assert.equal(billing.communityEdges.length, 0);
const imports = aggregateCommunities(graph, filterNodes(graph, { relation: 'imports' }), { relation: 'imports' });
assert.equal(imports.communities.length, 2);
assert.equal(imports.edges.length, 1);
assert.equal(imports.communityEdges.length, 1);
assert.equal(imports.communityEdges[0].weight, 1);
assert.equal(imports.communities.reduce((total, group) => total + group.nodeCount, 0), 2);
assert.equal(imports.communities.reduce((total, group) => total + group.externalEdges, 0), 2);
const empty = aggregateCommunities(graph, filterNodes(graph, { keywords: 'missing' }));
assert.deepEqual(empty, { communities: [], communityEdges: [], edges: [] });
const all = aggregateCommunities(graph, graph.nodes);
assert.equal(all.edges.length, graph.edges.length);
assert.equal(all.communityEdges.length,2);
assert.equal(all.communityEdges.find(edge=>edge.source==='one'&&edge.target==='two').weight,2);
assert.equal(all.communityEdges.find(edge=>edge.source==='two'&&edge.target==='one').weight,1);
assert.equal(all.communities.reduce((total, group) => total + group.internalEdges, 0) + all.communityEdges.reduce((total, edge) => total + edge.weight, 0), graph.edges.length);
assert.equal(JSON.stringify(graph), before);
console.log('Filter semantics PASS');
const {communityGeometry}=await import('../web/layout.js');
const directedGeometry=communityGeometry(all.communities,all.communityEdges);
const undirectedGeometry=communityGeometry(all.communities,[{source:'one',target:'two',weight:3}]);
for(const group of all.communities){const a=directedGeometry.get(group.id),b=undirectedGeometry.get(group.id);assert.deepEqual([a.x,a.y,a.r],[b.x,b.y,b.r]);}
console.log('Directed aggregation preserves geometry PASS');
