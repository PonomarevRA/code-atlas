/** The actual application path renderer must honor captured test ownership even for generic root kinds. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
const app = await readFile(new URL('../web/app.js', import.meta.url), 'utf8');
const start = app.indexOf('function paintPathGraph(data) {');
const end = app.indexOf("$('execution-path-open').addEventListener", start);
assert.ok(start > 0 && end > start);
const inspected = [];
const context = vm.createContext({ showTestClassOverview: id => { inspected.push(id); return true; } });
vm.runInContext(app.slice(start, end), context);
for (const rootKind of ['symbol', 'type', 'class', undefined]) {
  context.paintPathGraph({ rootNodeId: 'raw-test-container', rootKind });
}
assert.deepEqual(inspected, Array(4).fill('raw-test-container'));
// The real helper clears the active path before draw, preventing draw -> paint recursion.
const helperStart = app.indexOf('function showTestClassOverview(nodeId) {');
const helperEnd = app.indexOf('/** Показывает связи выбранного узла', helperStart);
let draws = 0, closes = 0;
const node = { id: 'raw-test-container', testContainerFacts: { declaredTestCount: 36 } };
const state = { nodes: new Map([[node.id, node]]), ingress: {}, pathOnGraph: { rootNodeId: node.id } };
const helperContext = vm.createContext({ state, testsForContainer: () => [], pathViewer: { close: () => { closes++; } },
  draw: () => { assert.equal(state.pathOnGraph, null); draws++; }, renderSelectedTestClass: () => {}, controls: () => {},
  activitySidebar: { reveal: () => {} }, status: () => {} });
vm.runInContext(app.slice(helperStart, helperEnd), helperContext);
assert.equal(helperContext.showTestClassOverview(node.id), true);
assert.equal(state.selected, node.id); assert.equal(draws, 1); assert.equal(closes, 1);
assert.equal(helperContext.showTestClassOverview('another-class'), false);
console.log('Actual app renderer intercepts captured test class for generic rootKind and clears path before draw PASS');
