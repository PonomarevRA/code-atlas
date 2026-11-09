/** Исходящая область вычисляется по полному графу, независимо от рендеринга, фильтров и ограничений. */
import assert from 'node:assert/strict';
import { isolateCallChain, isolateExecutionPath } from '../web/call-isolation.js';

const graph = {
  nodes: ['upstream', 'unrelated', 'root', 'interface', 'left', 'right', 'shared', 'leaf', 'reference'].map(id => ({ id, label: id })),
  edges: [
    { source: 'upstream', target: 'root', relation: 'calls' },
    { source: 'unrelated', target: 'shared', relation: 'calls' },
    { source: 'root', target: 'reference', relation: 'references' },
    { source: 'root', target: 'interface', relation: 'calls', confidence: 'EXTRACTED', sourceLine: 14 },
    { source: 'interface', target: 'left', relation: 'dispatches_to', confidence: 'candidate', sourceLine: 18, branchIds: ['if-1'] },
    { source: 'interface', target: 'right', relation: 'dispatches_to', confidence: 'candidate' },
    { source: 'left', target: 'shared', relation: 'calls' },
    { source: 'right', target: 'shared', relation: 'calls' },
    { source: 'shared', target: 'leaf', relation: 'calls' },
    { source: 'leaf', target: 'root', relation: 'calls' },
    { source: 'leaf', target: 'leaf', relation: 'calls' },
    { source: 'root', target: 'missing', relation: 'calls' },
    { source: 'missing', target: 'reference', relation: 'calls' },
  ],
};
const before = JSON.stringify(graph);
const full = isolateCallChain(graph, 'root');
assert.deepEqual(full.nodes.map(node => node.id), ['root', 'interface', 'left', 'right', 'shared', 'leaf']);
assert.deepEqual([...full.nodeIds], full.nodes.map(node => node.id));
assert.equal(full.allNodeIds.size, 6);
assert.equal(full.totalNodes, 6);
assert.equal(full.totalEdges, 8);
assert.equal(full.truncated, false);
assert.equal(full.missingRoot, false);
assert.ok(!full.nodeIds.has('upstream'));
assert.ok(!full.nodeIds.has('unrelated'));
assert.ok(!full.nodeIds.has('reference'));
assert.strictEqual(full.nodes[0], graph.nodes.find(node => node.id === 'root'));
assert.strictEqual(full.edges[1], graph.edges[4]);
assert.equal(full.edges[1].confidence, 'candidate');
assert.deepEqual(full.edges[1].branchIds, ['if-1']);
assert.equal(full.edges[1].sourceLine, 18);
assert.deepEqual(full.edges.map(edge => graph.edges.indexOf(edge)), [3, 4, 5, 6, 7, 8, 9, 10]);

const limited = isolateCallChain(graph, 'root', { maxNodes: 3, maxEdges: 1 });
assert.deepEqual([...limited.nodeIds], ['root', 'interface', 'left']);
assert.ok(limited.allNodeIds.has('leaf'), 'hidden intermediaries do not stop transitive traversal');
assert.equal(limited.totalNodes, 6);
assert.equal(limited.totalEdges, 8);
assert.equal(limited.edges.length, 1);
assert.equal(limited.truncated, true);
for (const edge of limited.edges) assert.ok(limited.nodeIds.has(edge.source) && limited.nodeIds.has(edge.target));
const rootOnly = isolateCallChain(graph, 'root', { maxNodes: 0, maxEdges: 0 });
assert.deepEqual(rootOnly.nodes.map(node => node.id), ['root']);
assert.equal(rootOnly.edges.length, 0);
assert.equal(rootOnly.totalNodes, 6);
assert.equal(rootOnly.truncated, true);
assert.deepEqual(isolateCallChain(graph, 'root', { relations: [] }).nodes.map(node => node.id), ['root']);
const callsOnly = isolateCallChain(graph, 'root', { relations: ['calls'] });
assert.deepEqual(callsOnly.nodes.map(node => node.id), ['root', 'interface']);
const system = isolateCallChain(graph, 'root', { relations: null });
assert.ok(system.nodeIds.has('reference'));
assert.ok(!system.nodeIds.has('upstream'), 'system dependencies stay directed');
assert.equal(system.totalEdges, 9);
assert.deepEqual(isolateCallChain(graph, 'root', { relations: new Set(['calls']) }).nodeIds, callsOnly.nodeIds);

const missing = isolateCallChain(graph, 'absent');
assert.equal(missing.missingRoot, true);
assert.equal(missing.truncated, false);
assert.equal(missing.totalNodes, 0);
assert.equal(missing.totalEdges, 0);
assert.equal(missing.nodeIds.size, 0);
assert.equal(missing.allNodeIds.size, 0);
assert.equal(isolateCallChain(null, 'root').missingRoot, true);
assert.equal(isolateCallChain({ nodes: [null, undefined], edges: [null] }, 'root').missingRoot, true);

// Лимиты SVG не уменьшают полные счётчики и не скрывают корень, даже если он последний в nodes.
const large = {
  nodes: Array.from({ length: 310 }, (_, i) => ({ id: `n${i}` })).reverse(),
  edges: Array.from({ length: 309 }, (_, i) => ({ source: `n${i}`, target: `n${i + 1}`, relation: 'calls' })),
};
const bounded = isolateCallChain(large, 'n0', { maxNodes: Infinity, maxEdges: NaN });
assert.equal(bounded.nodes[0].id, 'n0');
assert.equal(bounded.nodes.length, 240);
assert.equal(bounded.totalNodes, 310);
assert.equal(bounded.totalEdges, 309);
assert.equal(bounded.edges.length, 239);
assert.equal(bounded.allNodeIds.has('n309'), true);
assert.equal(bounded.nodeIds.has('n309'), false);
const implementation = { rootNodeId: 'n0', steps: large.nodes.map(node => ({ nodeId: node.id, controlFlow: { branches: [] } })), edges: large.edges };
const isolatedImplementation = isolateExecutionPath(implementation, bounded);
assert.equal(isolatedImplementation.steps.length, 310, 'the 240-card map cap must not clip a larger implementation analysis');
assert.strictEqual(isolatedImplementation.steps.at(-1).controlFlow, implementation.steps.at(-1).controlFlow);
assert.equal(isolatedImplementation.edges.length, 309);
assert.equal(isolateCallChain(large, 'n0', { maxNodes: 10000 }).nodes.length, 240);
assert.equal(isolateCallChain(large, 'n0', { maxNodes: '2.9' }).nodes.length, 2);
assert.equal(isolateCallChain(large, 'n0', { maxNodes: {}, maxEdges: Symbol('bad') }).nodes.length, 240);
const duplicates = { nodes: [{ id: 'r' }, { id: 'r' }], edges: Array.from({ length: 1300 }, (_, i) => ({ source: 'r', target: 'r', relation: 'calls', sourceLine: i })) };
const cappedEdges = isolateCallChain(duplicates, 'r', { maxEdges: 10000 });
assert.equal(cappedEdges.nodes.length, 1);
assert.equal(cappedEdges.totalEdges, 1300);
assert.equal(cappedEdges.edges.length, 1200);
assert.equal(cappedEdges.truncated, true);
assert.strictEqual(cappedEdges.edges.at(-1), duplicates.edges[1199]);
assert.equal(JSON.stringify(graph), before);
console.log('Call isolation outgoing closure, cycles/shared targets, candidates, exact counts and bounded display PASS');
