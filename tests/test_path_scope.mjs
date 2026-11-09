/** Независимая проверка раскрытия: контекст, общие цели, циклы и достоверность данных. */
import assert from 'node:assert/strict';
import { buildPathScope } from '../web/path-scope.js';

const nodes = [
  { id: 'root', label: 'Controller.Calculate', controlFlow: { branches: [{ kind: 'if', line: 4 }] }, events: [{ kind: 'call', targetNodeIds: ['left'] }] },
  { id: 'left', label: 'Left.Build', controlFlow: { branches: [{ kind: 'foreach', line: 8 }] }, events: [{ kind: 'call', targetNodeIds: ['shared'] }] },
  { id: 'right', label: 'Right.Build' },
  { id: 'shared', label: 'Shared.Calculate' },
  { id: 'deep', label: 'Deep.Calculate' },
  { id: 'orphan', label: 'Unreachable.Calculate' },
  { id: 'path-entry:http', label: 'POST /calculate', kind: 'entry' },
];
const edges = [
  { source: 'path-entry:http', target: 'root', relation: 'вход' },
  { source: 'root', target: 'left', relation: 'calls', confidence: 'source', sourceLine: 5, branchIds: ['root-arm'] },
  { source: 'root', target: 'right', relation: 'calls', confidence: 'graph', sourceLine: 6 },
  { source: 'left', target: 'shared', relation: 'calls', confidence: 'source', sourceLine: 9 },
  { source: 'right', target: 'shared', relation: 'calls', confidence: 'graph', sourceLine: 11 },
  { source: 'shared', target: 'deep', relation: 'calls' },
  { source: 'shared', target: 'root', relation: 'calls' },
  { source: 'deep', target: 'deep', relation: 'calls' },
  { source: 'root', target: 'missing', relation: 'calls' },
];
const original = JSON.stringify({ nodes, edges });

/** Упорядоченные ID позволяют проверять состав независимо от порядка расположения. */
function visible(scope) { return [...scope.visibleIds].sort(); }

const initial = buildPathScope(nodes, edges, { rootId: 'root' });
assert.deepEqual(visible(initial), ['left', 'path-entry:http', 'right', 'root']);
assert.ok(initial.expandedIds.has('root'));
assert.equal(initial.nodes.find(node => node.id === 'left').controlFlow, undefined);
assert.deepEqual(initial.nodes.find(node => node.id === 'left').events, []);
assert.equal(initial.stats.get('left').hiddenBranches, 1);
assert.equal(initial.stats.get('left').hiddenDescendants, 2);
assert.deepEqual(initial.edges.find(edge => edge.target === 'left'), edges[1]);

const requested = new Set(['left', 'shared']);
const expanded = buildPathScope(nodes, edges, { rootId: 'root', expandedIds: requested });
assert.deepEqual(visible(expanded), ['deep', 'left', 'path-entry:http', 'right', 'root', 'shared']);
assert.ok(expanded.nodes.find(node => node.id === 'left').controlFlow);
assert.equal(expanded.stats.get('left').hiddenBranches, 0);
assert.ok(expanded.edges.some(edge => edge.source === 'shared' && edge.target === 'root'));
assert.equal(expanded.nodes.filter(node => node.id === 'root').length, 1);
assert.deepEqual([...requested], ['left', 'shared']);

// Удаление раскрытия одной ветви не скрывает общую цель второй раскрытой ветви.
const collapsedLeft = buildPathScope(nodes, edges, { rootId: 'root', expandedIds: ['right', 'shared'] });
assert.ok(collapsedLeft.visibleIds.has('shared'));
assert.ok(collapsedLeft.visibleIds.has('deep'));
assert.ok(collapsedLeft.visibleIds.has('left'));
assert.ok(!collapsedLeft.edges.some(edge => edge.source === 'left'));
const collapsedBoth = buildPathScope(nodes, edges, { rootId: 'root', expandedIds: ['shared', 'deep'] });
assert.deepEqual(visible(collapsedBoth), visible(initial));
assert.ok(!collapsedBoth.expandedIds.has('shared'), 'hidden expanded descendants must not appear as orphan roots');

// Последовательное раскрытие достигает каждого узла реального call-графа, включая петли.
let all = initial;
const allExpanded = new Set();
for (let pass = 0; pass < nodes.length; pass += 1) {
  for (const id of all.visibleIds) allExpanded.add(id);
  all = buildPathScope(nodes, edges, { rootId: 'root', expandedIds: allExpanded });
}
assert.deepEqual(visible(all), ['deep', 'left', 'path-entry:http', 'right', 'root', 'shared']);
assert.ok(all.edges.some(edge => edge.source === 'deep' && edge.target === 'deep'));
assert.equal(all.nodes.filter(node => node.id === 'deep').length, 1);

// Единственный dispatch прозрачен, несколько реализаций остаются явными кандидатами.
const dispatchNodes = ['root', 'interface', 'implementation', 'work', 'alternative'].map(id => ({ id, label: id }));
const dispatchEdges = [
  { source: 'root', target: 'interface', relation: 'calls' },
  { source: 'interface', target: 'implementation', relation: 'dispatches_to', confidence: 'candidate', sourceLine: null },
  { source: 'implementation', target: 'work', relation: 'calls' },
];
const dispatch = buildPathScope(dispatchNodes, dispatchEdges, { rootId: 'root' });
assert.ok(dispatch.visibleIds.has('implementation'));
assert.ok(dispatch.visibleIds.has('work'));
assert.deepEqual(dispatch.edges.find(edge => edge.relation === 'dispatches_to'), dispatchEdges[1]);
const collapsedImpl = buildPathScope(dispatchNodes, dispatchEdges, { rootId: 'root', collapsedIds: ['implementation'] });
assert.ok(collapsedImpl.visibleIds.has('implementation'));
assert.ok(!collapsedImpl.visibleIds.has('work'));
const ambiguous = buildPathScope(dispatchNodes, [...dispatchEdges, { source: 'interface', target: 'alternative', relation: 'dispatches_to', confidence: 'candidate' }], { rootId: 'root' });
assert.deepEqual(visible(ambiguous), ['interface', 'root']);
const alternatives = buildPathScope(dispatchNodes, [...dispatchEdges, { source: 'interface', target: 'alternative', relation: 'dispatches_to', confidence: 'candidate' }], { rootId: 'root', expandedIds: ['interface'] });
assert.ok(alternatives.visibleIds.has('implementation'));
assert.ok(alternatives.visibleIds.has('alternative'));
assert.ok(alternatives.stats.get('interface').canExpand, 'expanded alternatives must retain the collapse action');
assert.equal(alternatives.edges.filter(edge => edge.relation === 'dispatches_to' && edge.confidence === 'candidate').length, 2);

const chainNodes = ['root', 'i1', 'i2', 'i3', 'i4'].map(id => ({ id, label: id }));
const chainEdges = [
  { source: 'root', target: 'i1', relation: 'calls' },
  { source: 'i1', target: 'i2', relation: 'dispatches_to', confidence: 'candidate' },
  { source: 'i2', target: 'i3', relation: 'dispatches_to', confidence: 'candidate' },
  { source: 'i3', target: 'i4', relation: 'dispatches_to', confidence: 'candidate' },
];
const chain = buildPathScope(chainNodes, chainEdges, { rootId: 'root' });
assert.deepEqual(visible(chain), ['i1', 'i2', 'i3', 'root']);
assert.equal(chain.stats.get('i2').canExpand, false, 'transparent wrapper must not expose an action with no effect');
assert.equal(chain.stats.get('i3').canExpand, true, 'dispatch hop boundary remains explorable');
const expandedBoundary = buildPathScope(chainNodes, chainEdges, { rootId: 'root', expandedIds: ['i3'] });
assert.ok(expandedBoundary.visibleIds.has('i4'));
assert.ok(expandedBoundary.stats.get('i3').canExpand, 'explicitly expanded dispatch boundary can be collapsed');
assert.equal(JSON.stringify({ nodes, edges }), original);
assert.deepEqual(visible(buildPathScope([], [], { rootId: 'missing' })), []);
console.log('Path scope inline expansion, shared targets, cycles, candidates and immutability PASS');
