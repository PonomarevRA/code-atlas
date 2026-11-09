/** Вложенные альтернативы образуют дерево в обоих провайдерах, включая пустые ветви. */
import assert from 'node:assert/strict';
import { buildPathScene } from '../web/path-scene.js';
import { buildPathScope } from '../web/path-scope.js';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';
import { assertClear } from './curve_geometry.mjs';
import { pathStubKey } from '../web/path-control-tree-layout.js';
import { pathControlTreeLayout } from '../web/path-control-tree-layout.js';

const nodes = [{ id: 'root', label: 'Root', controlFlow: { branches: [
  { kind: 'if', line: 10, arms: [{ id: 'yes', kind: 'true' }, { id: 'no', kind: 'false' }] },
  { kind: 'foreach', line: 12, parentArmIds: ['yes'], arms: [{ id: 'body', kind: 'body' }] },
  { kind: 'switch', line: 15, parentArmIds: ['yes', 'body'], arms: [
    { id: 'one', kind: 'case', label: 'case 1' }, { id: 'two', kind: 'case', label: 'case 2' }, { id: 'default', kind: 'default' }] },
  { kind: 'if', line: 18, parentArmIds: ['yes', 'body', 'one'], arms: [{ id: 'inner-yes', kind: 'true' }, { id: 'inner-no', kind: 'false' }] },
  { kind: 'while', line: 30, parentArmIds: ['no'], arms: [{ id: 'else-body', kind: 'body' }] },
], terminals: [
  { kind: 'return', line: 19, text: 'return 1;', branchIds: ['yes', 'body', 'one', 'inner-yes'] },
  { kind: 'throw', line: 31, text: 'throw error;', branchIds: ['no', 'else-body'] },
] } }, { id: 'true-call', label: 'TrueCall' }, { id: 'false-call', label: 'FalseCall' },
{ id: 'deep-call', label: 'DeepCall' }];
const edges = [
  { source: 'root', target: 'true-call', branchIds: ['yes', 'body', 'one', 'inner-yes'], sourceLine: 18, relation: 'calls', confidence: 'candidate' },
  { source: 'root', target: 'false-call', branchIds: ['no', 'else-body'], sourceLine: 30, relation: 'calls' },
  { source: 'true-call', target: 'deep-call', sourceLine: 3, relation: 'calls' },
  { source: 'deep-call', target: 'root', sourceLine: 7, relation: 'calls' },
];
const facts = JSON.stringify({ nodes, edges });
const scope = buildPathScope(nodes, edges, { rootId: 'root', expandedIds: nodes.map(node => node.id) });

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
for (const natural of [false, true]) {
  const scene = buildPathScene(scope, { flowPath: true, flatFlow: true, reading: true, natural });
  const atLine = line => scene.positions.get(scene.visuals.find(node => node.decision && node.line === line).id);
  const outer = atLine(10), loop = atLine(12), choice = atLine(15), inner = atLine(18), alternative = atLine(30);
  assert.ok(outer.x < loop.x && loop.x < choice.x && choice.x < inner.x, 'lexical nesting advances right');
  assert.equal(loop.x, alternative.x, 'if/else children have equal depth');
  assert.ok(inner.y < alternative.y, 'the complete true subtree precedes the false subtree');
  assert.ok(scene.positions.get('true-call').y < scene.positions.get('false-call').y, 'branch calls continue their distinct lanes');
  assert.equal(scene.positions.get('true-call').x, scene.positions.get('false-call').x);
  const empty = scene.stubs.filter(stub => stub.source === scene.visuals.find(node => node.line === 15 && node.decision).id);
  assert.equal(empty.length, 2);
  const emptyPoints = empty.map(stub => scene.layout.stubPositions.get(pathStubKey(stub)));
  assert.ok(inner.y < emptyPoints[0].y && emptyPoints[0].y < emptyPoints[1].y && emptyPoints[1].y < alternative.y,
    'empty switch arms have separate lanes outside their sibling subtrees');
  assert.equal(scene.positions.size, scene.visuals.length, 'reserved call slots never add graph IDs');
  const cards = [...scene.positions.values()];
  for (let i = 0; i < cards.length; i++) for (let j = i + 1; j < cards.length; j++) {
    const a = cards[i], b = cards[j];
    assert.ok(Math.abs(a.x - b.x) >= (a.w + b.w) / 2 || Math.abs(a.y - b.y) >= (a.h + b.h) / 2, 'tree card bounds do not overlap');
  }
  const svg = new Svg('svg'), viewport = new Svg('g');
  viewport.setAttribute('id', 'viewport'); svg.append(viewport);
  const view = new GraphView(svg, () => {}, () => {});
  view.groupingProvider = natural ? 'natural' : 'graphistry';
  view.pathReading = true; view.pathRootId = 'root'; view.setDisplayProvider('flow');
  view.renderPathCards(nodes, edges, 'root');
  assert.equal(viewport.querySelectorAll('.path-branch-stub').length, scene.stubs.length, 'each empty arm renders its own endpoint');
  view.camera = { x: 17, y: 23, scale: .8 };
  const camera = { ...view.camera };
  view.togglePathNode('true-call');
  assert.ok(view.positions.has('deep-call'));
  const reserved = view.entryPathLayout, before = new Map(view.positions);
  for (const filters of [{ conditions: false, loops: true }, { conditions: true, loops: false }, { conditions: true, loops: true }]) {
    view.setPathControlFilters(filters);
    assert.equal(view.entryPathLayout, reserved);
    for (const [id, point] of before) if (view.positions.has(id)) assert.deepEqual(view.positions.get(id), point);
    assert.deepEqual(view.camera, camera);
    assert.ok(view.pathEdgeElements.some(edge => edge.path.querySelector('title').textContent.includes('candidate')));
    for (const path of viewport.querySelectorAll('.path-edge')) assertClear(path.attributes.d, [...view.positions.values()]);
  }
}
assert.equal(JSON.stringify({ nodes, edges }), facts);
// Глубина исходника не должна превращаться в глубину JavaScript-стека.
const deepNodes = [{ id: 'owner', w: 260, h: 154 }], deepEdges = [];
for (let index = 0; index < 2000; index++) {
  const id = `condition-${index}`;
  deepNodes.push({ id, ownerId: 'owner', decision: true, w: 260, h: 124, line: index });
  deepEdges.push({ source: index ? `condition-${index - 1}` : 'owner', target: id });
}
const deepTree = pathControlTreeLayout(deepNodes, deepEdges, [], 'owner');
assert.equal(deepTree.positions.size, deepNodes.length);
assert.ok(Number.isFinite(deepTree.positions.get('condition-1999').x));
assert.deepEqual(pathControlTreeLayout([...deepNodes].reverse(), [...deepEdges].reverse(), [], 'owner'), deepTree,
  'input order cannot change the lexical tree');
console.log('Control trees, distinct alternatives, empty arms and stable provider geometry PASS');
