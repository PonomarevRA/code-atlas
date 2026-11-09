/** Чистая сцена и prototype facade сохраняют источники, раскрытие и поздние callbacks. */
import assert from 'node:assert/strict';
import { buildPathScope } from '../web/path-scope.js';
import { buildPathScene } from '../web/path-scene.js';
import { filterPathControls } from '../web/path-control-filter.js';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';

/** Замораживает вложенные исходные свидетельства, чтобы renderer не мог переписать модель. */
function freeze(value) {
  if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) freeze(child);
  return Object.freeze(value);
}
const nodes = freeze([
  { id: 'root:method', label: '.Calculate()', file: 'Root.cs', codeProjectId: 'p', controlFlow: {
    branches: [
      { kind: 'foreach', line: 2, header: 'item in items', arms: [{ id: 'loop:body', kind: 'body' }] },
      { kind: 'if', line: 3, condition: 'item.Valid', parentArmIds: ['loop:body'], arms: [{ id: 'if:true', kind: 'true' }, { id: 'if:false', kind: 'false' }] },
      { kind: 'switch-expression', line: 7, selector: 'item.Kind', arms: [{ id: 'switch:case', kind: 'case', label: 'Total' }] },
    ], terminals: [{ kind: 'return', line: 9, text: 'return total;', branchIds: ['loop:body', 'if:false'] }],
  }, events: [{ kind: 'call', line: 8, branchIds: ['switch:case'], targetNodeIds: ['shared:method', 'absent'], confidence: 'source' }] },
  { id: 'interface:method', label: '.Calculate()', file: 'ICalculator.cs' },
  { id: 'implementation:method', label: '.Calculate()', file: 'Calculator.cs' },
  { id: 'shared:method', label: '.Calculate()', file: 'Shared.cs' },
]);
const edges = freeze([
  { source: 'root:method', target: 'interface:method', relation: 'calls', sourceLine: 4, branchIds: ['loop:body', 'if:true'], evidence: { kind: 'typed-interface-call', receiverType: 'ICalculator' } },
  { source: 'root:method', target: 'shared:method', relation: 'calls', sourceLine: 6 },
  { source: 'interface:method', target: 'implementation:method', relation: 'dispatches_to', confidence: 'candidate', evidence: { kind: 'interface-implementation', implementationType: 'Calculator' } },
  { source: 'implementation:method', target: 'shared:method', relation: 'calls', sourceLine: 20 },
  { source: 'implementation:method', target: 'shared:method', relation: 'calls', sourceLine: 21 },
  { source: 'shared:method', target: 'root:method', relation: 'calls' },
]);
const original = JSON.stringify({ nodes, edges });
const scope = buildPathScope(nodes, edges, { rootId: 'root:method', expandedIds: nodes.map(node => node.id) });
const scene = buildPathScene(scope, { flowPath: true, selected: 'root:method' });
assert.deepEqual(scene.visuals.filter(node => node.decision).map(node => node.kind), ['foreach', 'if', 'switch']);
assert.equal(scene.byId.get('root:method::branch:3:1').codeProjectId, 'p');
assert.ok(scene.visualEdges.some(edge => edge.source === 'root:method::branch:3:1' && edge.target === 'interface:method' && edge.evidence.receiverType === 'ICalculator'));
assert.equal(scene.visualEdges.filter(edge => edge.source === 'implementation:method' && edge.target === 'shared:method').length, 2);
assert.ok(scene.visualEdges.some(edge => edge.source === 'root:method::branch:7:2' && edge.target === 'shared:method' && edge.sourceLine === 8));
assert.ok(scene.visuals.some(node => node.exit && node.line === 9));
assert.ok(scene.layout.placed.some(item => item.reference && item.focusId === 'root:method'));
assert.deepEqual(scene.layout, buildPathScene(scope, { flowPath: true, selected: 'root:method' }).layout);
assert.equal(JSON.stringify({ nodes, edges }), original);

// ?: uses the shared condition tree in ordinary and Natural views, including literal alternatives.
const conditionalNode = freeze({ id: 'ternary', controlFlow: { branches: [
  { kind: 'conditional', line: 1, condition: 'Ready()', arms: [
    { id: 'outer:true', kind: 'true', expression: 'Accept()' },
    { id: 'outer:false', kind: 'false', expression: 'x != null ? x : null' },
  ] },
  { kind: 'conditional', line: 1, condition: 'x != null', parentArmIds: ['outer:false'], arms: [
    { id: 'inner:true', kind: 'true', expression: 'x' },
    { id: 'inner:false', kind: 'false', expression: 'null' },
  ] },
] }, events: [{ kind: 'call', line: 1, branchIds: ['outer:true'], targetNodeIds: ['accept'] }] });
const ternaryScope = buildPathScope([conditionalNode, {id:'accept',label:'Accept()'}],
  [{source:'ternary',target:'accept',relation:'calls',branchIds:['outer:true']}], {rootId:'ternary',expandedIds:['ternary']});
for (const natural of [false, true]) {
  const scene = buildPathScene(ternaryScope, { flowPath: true, reading: true, natural });
  assert.deepEqual(scene.visuals.filter(node => node.decision).map(node => node.label), ['?:', '?:']);
  assert.ok(scene.visualEdges.some(edge => edge.source === 'ternary::branch:1:0' && edge.target === 'ternary::branch:1:1' && edge.armId === 'outer:false'));
  assert.ok(scene.visualEdges.some(edge => edge.source === 'ternary::branch:1:0' && edge.target === 'accept' && edge.armId === 'outer:true'));
  assert.ok(scene.stubs.some(stub => stub.armId === 'inner:false' && stub.caption === 'иначе: null'));
  const hidden = buildPathScene(ternaryScope, { flowPath: true, reading: true, natural, controlFilters: {conditions:false} });
  assert.equal(hidden.visuals.filter(node => node.decision).length, 0);
  assert.ok(hidden.visualEdges.some(edge => edge.source === 'ternary' && edge.target === 'accept'));
}
const controls = freeze([{ id: 'owner' }, { id: 'if', decision: true, kind: 'if', ownerId: 'owner' },
  { id: 'nested', decision: true, kind: 'if', ownerId: 'owner' }, { id: 'target' }]);
const controlEdges = freeze([{ source: 'owner', target: 'if' }, { source: 'if', target: 'nested' },
  { source: 'nested', target: 'target', sourceLine: 9, caption: 'истина' }, { source: '', target: 'target' }]);
const controlStubs = freeze([{ source: 'if' }]);
const allControls = filterPathControls(controls, controlEdges, controlStubs);
assert.deepEqual(allControls, { visuals: controls, visualEdges: controlEdges.slice(0, 3), stubs: controlStubs });
assert.notEqual(allControls.visuals, controls);
assert.notEqual(allControls.stubs, controlStubs);
assert.equal(allControls.visualEdges[2], controlEdges[2]);
assert.deepEqual(filterPathControls(controls, controlEdges, controlStubs, { conditions: false }), {
  visuals: [controls[0], controls[3]],
  visualEdges: [{ source: 'owner', target: 'target', sourceLine: 9, caption: '' }],
  stubs: [],
});
const limitedScope = buildPathScope([{ id: 'many', controlFlow: { branches: Array.from({ length: 14 }, (_, line) => ({ kind: 'if', line: line + 1, arms: [] })) } }], [], { rootId: 'many' });
assert.equal(buildPathScene(limitedScope).visuals.filter(node => node.decision).length, 12);
assert.ok(buildPathScene(limitedScope).visuals.some(node => node.omission));
assert.equal(buildPathScene(limitedScope, { flowPath: true }).visuals.filter(node => node.decision).length, 14);

// Контекстные дочерние view создаются через prototype и не проходят constructor.
globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
const svg = new Svg('svg'), viewport = new Svg('g');
svg.append(viewport);
const view = Object.create(GraphView.prototype);
Object.assign(view, { svg, viewport, onSelect: () => {}, nodeElements: new Map(), communityElements: new Map(), camera: { x: 12, y: 34, scale: .8 }, displayProvider: 'flow', pathRootId: 'root:method', pathSnapshotId: 'one' });
view.renderPathCards(nodes, edges, 'root:method');
let source;
const sourceButton = view.nodeElements.get('root:method').group.querySelector('.path-card-source');
view.onPathSource = id => { source = id; };
sourceButton.listeners.click({ stopPropagation() {} });
assert.equal(source, 'root:method', 'callbacks resolve at activation, after rendering');
const camera = { ...view.camera };
view.expandAllPathNodes();
assert.deepEqual(view.camera, camera);
for (const item of view.positions.values()) {
  if (!item.reference) continue;
  assert.ok(viewport.querySelectorAll('.path-reference-link').some(edge => edge.attributes['data-reference-id'] === item.id), 'colon-containing caller and target retain reference continuation');
}
// Подсветка активного пути не может уйти в renderer предыдущего обычного Flow.
view.flowRenderer = { setSearchMatches() { throw new Error('inactive FlowRenderer used'); } };
const positionsBeforeSearch = JSON.stringify([...view.positions]);
const cameraBeforeSearch = { ...view.camera };
const branchId = 'root:method::branch:3:1';
assert.equal(view.setSearchMatches(['root:method', branchId, 'offscreen']), 2);
const rootRing = view.nodeElements.get('root:method').group.querySelector('.regex-ring');
assert.equal(rootRing.tag, 'rect');
assert.equal(rootRing.attributes.x, -4);
assert.equal(rootRing.attributes.width, view.positions.get('root:method').w + 8);
const branchRing = view.nodeElements.get(branchId).group.querySelector('.regex-ring');
assert.equal(branchRing.attributes.x, -view.positions.get(branchId).w / 2 - 4);
assert.equal(view.setSearchMatches([]), 0);
assert.ok(!view.nodeElements.get('root:method').group.querySelector('.regex-ring'));
assert.equal(JSON.stringify([...view.positions]), positionsBeforeSearch);
assert.deepEqual(view.camera, cameraBeforeSearch);
view.pathSnapshotId = 'two';
view.renderPathCards(nodes, edges, 'root:method');
assert.equal(view.pathExpandedIds.size, 0, 'new snapshot resets local explicit expansion');
assert.equal(JSON.stringify({ nodes, edges }), original);
console.log('Pure path scene and prototype-safe facade PASS');
