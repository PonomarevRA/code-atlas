/** Горизонтальное чтение и независимые фильтры сохраняют вызовы, геометрию и раскрытие. */
import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';
import { pathReadingLayout } from '../web/path-reading-layout.js';
import { buildPathScene } from '../web/path-scene.js';
import { buildPathScope } from '../web/path-scope.js';
import { Svg } from './svg_fixture.mjs';
import { assertClear } from './curve_geometry.mjs';

const nodes = [
  { id: 'root', label: 'GetCostChangeTotalAsync', controlFlow: { branches: [
    { kind: 'if', line: 5, arms: [{ id: 'yes', kind: 'true' }] },
    { kind: 'foreach', line: 8, parentArmIds: ['yes'], arms: [{ id: 'body', kind: 'body' }] },
    { kind: 'switch-expression', line: 12, parentArmIds: ['body'], arms: [{ id: 'case', kind: 'case' }] },
    { kind: 'while', line: 18, arms: [{ id: 'while-body', kind: 'body' }] },
    { kind: 'for', line: 20, arms: [{ id: 'for-body', kind: 'body' }] },
    { kind: 'do', line: 22, arms: [{ id: 'do-body', kind: 'body' }] },
  ], terminals: [{ kind: 'return', text: 'return total;', line: 16, branchIds: ['yes', 'body', 'case'] }] } },
  { id: 'first', label: 'Calculate', file: 'Service.cs' },
  { id: 'second', label: 'Read', controlFlow: { branches: [{ kind: 'if', line: 50, arms: [] }] } },
  { id: 'third', label: 'Map' },
  { id: 'deep', label: 'Finish' },
];
const edges = [
  { source: 'root', target: 'third', relation: 'calls', sourceLine: 30 },
  { source: 'root', target: 'second', relation: 'calls', sourceLine: 26 },
  { source: 'root', target: 'first', relation: 'calls', sourceLine: 14, branchIds: ['yes', 'body', 'case'], confidence: 'candidate' },
  { source: 'root', target: 'first', relation: 'calls', sourceLine: 15, branchIds: ['yes', 'body', 'case'], confidence: 'graph' },
  { source: 'second', target: 'deep', relation: 'calls', sourceLine: 51 },
  { source: 'deep', target: 'root', relation: 'calls', sourceLine: 70 },
];
const original = JSON.stringify({ nodes, edges });
const simple = pathReadingLayout(nodes.map(node => ({ ...node, w: 260, h: 154 })), edges, 'root');
assert.ok(simple.get('first').x < simple.get('second').x && simple.get('second').x < simple.get('third').x,
  'siblings follow call source lines rather than input order');
assert.ok(simple.get('first').x > simple.get('root').x && simple.get('deep').x > simple.get('third').x);

const wideNodes = [{ id: 'start', w: 260, h: 154 }];
const wideEdges = [];
for (let index = 0; index < 240; index++) {
  wideNodes.push({ id: `target-${index}`, w: 260, h: 154 });
  wideEdges.push({ source: 'start', target: `target-${index}`, sourceLine: index });
}
const wide = pathReadingLayout(wideNodes, wideEdges, 'start');
assert.equal(new Set([...wide.values()].map(point => point.x)).size, 7, 'wide ranks use six columns');
assert.ok(Math.max(...[...wide.values()].map(point => point.y)) < 9000, 'wide ranks avoid a 240-card column');
assert.deepEqual(pathReadingLayout(wideNodes, wideEdges, 'start'), wide);

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g');
viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const view = new GraphView(svg, () => {}, () => {});
view.pathReading = true; view.pathRootId = 'root'; view.pathSnapshotId = 'one';
view.setDisplayProvider('flow'); view.renderPathCards(nodes, edges, 'root');
assert.equal(view.pathFlatFlow, true, 'a method path without an entry uses horizontal reading');
assert.equal(viewport.querySelectorAll('.path-method-frame').length, 0);
view.fitPathInitial();
const rootPoint = view.positions.get('root');
assert.equal((rootPoint.x - rootPoint.w / 2) * view.camera.scale + view.camera.x, 24);
assert.equal((rootPoint.y - rootPoint.h / 2) * view.camera.scale + view.camera.y, 48);
assert.equal(view.camera.scale, .9);
const positions = new Map(view.positions);
view.camera = { x: 17, y: 29, scale: .85 };
const camera = { ...view.camera };
const expanded = [...view.pathScope.expandedIds];
const filters = [
  { conditions: false, loops: true }, { conditions: true, loops: false },
  { conditions: false, loops: false }, { conditions: true, loops: true },
];
for (const filter of filters) {
  view.setPathControlFilters(filter);
  const controls = viewport.querySelectorAll('.path-decision').map(group => group.attributes['data-control-kind']);
  assert.equal(controls.includes('if'), filter.conditions);
  assert.equal(controls.includes('switch'), filter.conditions);
  for (const kind of ['for', 'foreach', 'while', 'do']) assert.equal(controls.includes(kind), filter.loops);
  for (const [id, point] of positions) if (view.positions.has(id)) assert.deepEqual(view.positions.get(id), point);
  assert.deepEqual(view.camera, camera); assert.equal(view.selectedId, 'root');
  assert.deepEqual([...view.pathScope.expandedIds], expanded);
  assert.ok([...view.positions.keys()].some(id => id.includes('::exit:16:')), 'hidden ancestors retain the original return ID');
  const calls = view.pathEdgeElements.filter(edge => edge.target === 'first');
  assert.equal(calls.length, 2, 'repeated calls survive independently');
  assert.ok(calls.some(edge => edge.path.querySelector('title').textContent.includes('14')));
  for (const path of viewport.querySelectorAll('.path-edge')) assertClear(path.attributes.d, [...view.positions.values()]);
}
view.togglePathNode('second');
for (const [id, point] of positions) assert.deepEqual(view.positions.get(id), point, 'expansion keeps the horizontal reservation');
assert.ok(view.positions.has('deep')); assert.deepEqual(view.camera, camera);

const scope = buildPathScope(nodes, edges, { rootId: 'root', expandedIds: nodes.map(node => node.id) });
for (const flowPath of [true, false]) {
  const scene = buildPathScene(scope, { flowPath, controlFilters: { conditions: false, loops: false } });
  assert.equal(scene.visuals.filter(node => node.decision).length, 0);
  assert.equal(scene.visualEdges.filter(edge => edge.target === 'first').length, 2);
  assert.ok(scene.visualEdges.filter(edge => edge.target === 'first').every(edge => edge.source === 'root' && edge.branchIds.length === 3));
}
assert.equal(JSON.stringify({ nodes, edges }), original);
console.log('Horizontal reading, source order, independent control filters and stable geometry PASS');
