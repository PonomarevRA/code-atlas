/** Проверяет отметки Git и их пересечение с существующими фильтрами без изменения графа. */
import assert from 'node:assert/strict';
import { markChangedNodes, markChangedCommunities } from '../web/changes.js';
import { filterNodes, aggregateCommunities } from '../web/filters.js';

const graph = {
  nodes: [
    { id: 'a', label: 'Invoice', community: 'billing', codeProjectId: 'main' },
    { id: 'b', label: 'InvoiceTests', community: 'tests', codeProjectId: 'test' },
    { id: 'c', label: 'InvoiceCache', community: 'billing', codeProjectId: 'main' },
  ],
  edges: [{ source: 'a', target: 'c', relation: 'calls' }, { source: 'a', target: 'b', relation: 'calls' }],
  communities: [{ id: 'billing', nodeCount: 2 }, { id: 'tests', nodeCount: 1 }],
};
const before = JSON.stringify(graph);
const changedIds = ['a', 'b', 'unknown'];
const marked = markChangedNodes(graph.nodes, changedIds);
assert.deepEqual(marked.map(node => node.changedFile), [true, true, false]);
const filtered = filterNodes(graph, { keywords: 'invoice', codeProjectIds: ['main'] });
const aggregate = aggregateCommunities(graph, filtered);
const groups = markChangedCommunities(aggregate.communities, filtered, changedIds);
assert.equal(groups.length, 1);
assert.equal(groups[0].nodeCount, 2);
assert.equal(groups[0].changedNodeCount, 1);
// Режим «только изменённые» сначала ограничивает узлы, затем пересчитывает индуцированные связи.
const onlyChanged = { ...graph, nodes: graph.nodes.filter(node => changedIds.includes(node.id)) };
const intersection = filterNodes(onlyChanged, { codeProjectIds: ['main'] });
const changes = aggregateCommunities(onlyChanged, intersection);
assert.deepEqual(intersection.map(node => node.id), ['a']);
assert.equal(changes.edges.length, 0);
assert.deepEqual(markChangedCommunities(graph.communities, [], changedIds).map(group => group.changedNodeCount), [0, 0]);
assert.equal(JSON.stringify(graph), before);
console.log('Git highlights PASS');
