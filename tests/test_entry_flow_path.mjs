/** Входной Flow-путь сохраняет всю видимую геометрию при локальном раскрытии. */
import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g');
viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const view = new GraphView(svg, () => {}, () => {});
view.setDisplayProvider('flow'); view.pathRootId = 'root'; view.pathSnapshotId = 'one';
const nodes = [
  { id: 'path-entry:http', kind: 'entry', label: 'POST /calculate' },
  { id: 'root', label: 'Calculate', file: 'Controller.cs', controlFlow: { branches: [
    { kind: 'if', line: 5, condition: 'valid', arms: [{ id: 'valid', kind: 'true' }, { id: 'invalid', kind: 'false' }] },
  ], terminals: [{ kind: 'return', line: 6, branchIds: ['invalid'], text: 'return error;' }] } },
  { id: 'left', label: 'Apply', file: 'Service.cs', controlFlow: { branches: [
    { kind: 'foreach', line: 10, header: 'item in items', arms: [{ id: 'body', kind: 'body' }] },
  ] } },
  { id: 'right', label: 'Apply', file: 'OtherService.cs' },
  { id: 'iface', label: 'Resolve', file: 'IResolver.cs' },
  { id: 'impl', label: 'Resolve', file: 'Resolver.cs' },
  { id: 'deep', label: 'Finish', file: 'Finish.cs' },
  { id: 'orphan', label: 'Not reachable' },
];
const edges = [
  { source: 'path-entry:http', target: 'root', relation: 'вход', caption: 'POST вход' },
  { source: 'root', target: 'left', relation: 'calls', branchIds: ['valid'], sourceLine: 7 },
  { source: 'root', target: 'right', relation: 'calls', sourceLine: 8 },
  { source: 'left', target: 'iface', relation: 'calls', branchIds: ['body'], sourceLine: 11 },
  { source: 'iface', target: 'impl', relation: 'dispatches_to', confidence: 'candidate', evidence: { kind: 'interface-implementation' } },
  { source: 'impl', target: 'deep', relation: 'calls', sourceLine: 20 },
  { source: 'impl', target: 'deep', relation: 'calls', sourceLine: 22 },
  { source: 'deep', target: 'left', relation: 'calls', sourceLine: 30 },
];
const original = JSON.stringify({ nodes, edges });

/** Проверяет настоящие кубические дуги и боковые порты независимо от расчёта маршрутов. */
function assertFlowGeometry() {
  assert.equal(viewport.querySelectorAll('.path-method-frame').length, 0);
  assert.equal(viewport.querySelectorAll('.path-reference').length, 0);
  assert.equal(viewport.querySelectorAll('.path-reference-link').length, 0);
  const cards = [...view.positions.values()];
  for (const card of cards) assert.equal(card.reference, undefined);
  for (const path of viewport.querySelectorAll('.path-edge')) {
    const d = path.attributes.d;
    assert.ok(d && d.includes('C') && !/[LQ]/.test(d), 'all visible transitions use smooth curves');
    if (path.attributes['data-source']) {
      const source = view.positions.get(path.attributes['data-source']);
      const target = view.positions.get(path.attributes['data-target']);
      const endpoints = d.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);
      assert.deepEqual(endpoints.slice(0, 2), [source.x + source.w / 2, source.y]);
      assert.deepEqual(endpoints.slice(-2), [target.x - target.w / 2, target.y]);
    }
    let from;
    for (const command of d.match(/[MC][^MC]*/g)) {
      const values = command.slice(1).match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);
      const end = { x: values.at(-2), y: values.at(-1) };
      if (command[0] === 'C') {
        for (let step = 1; step <= 80; step++) {
          const t = step / 80, u = 1 - t;
          const x = u ** 3 * from.x + 3 * u ** 2 * t * values[0] + 3 * u * t ** 2 * values[2] + t ** 3 * end.x;
          const y = u ** 3 * from.y + 3 * u ** 2 * t * values[1] + 3 * u * t ** 2 * values[3] + t ** 3 * end.y;
          for (const card of cards) assert.ok(x <= card.x - card.w / 2 + 1e-6 || x >= card.x + card.w / 2 - 1e-6
            || y <= card.y - card.h / 2 + 1e-6 || y >= card.y + card.h / 2 - 1e-6, `curve crosses ${card.id}`);
        }
      }
      from = end;
    }
  }
  for (let first = 0; first < cards.length; first++) {
    for (let second = first + 1; second < cards.length; second++) {
      const a = cards[first], b = cards[second];
      assert.ok(Math.abs(a.x - b.x) >= (a.w + b.w) / 2 || Math.abs(a.y - b.y) >= (a.h + b.h) / 2);
    }
  }
}

/** Сравнивает и позиции, и экранные координаты всех уже видимых карточек. */
function assertStable(before, camera) {
  assert.deepEqual(view.camera, camera);
  for (const [id, point] of before) {
    const current = view.positions.get(id);
    if (current) assert.deepEqual(current, point, `existing card ${id} must stay in place`);
  }
  assertFlowGeometry();
}

view.renderPathCards(nodes, edges, 'root');
assertFlowGeometry();
assert.ok(!view.positions.has('iface'));
const initial = new Map(view.positions);
view.camera = { x: 33, y: 44, scale: .8 };
const camera = { ...view.camera };
view.togglePathNode('left');
assertStable(initial, camera);
assert.ok(view.positions.has('iface') && view.positions.has('impl'));
assert.ok(viewport.querySelectorAll('.path-decision').some(group => group.attributes['data-control-kind'] === 'foreach'));
const expandedLeft = new Map(view.positions);
const dispatch = view.pathEdgeElements.find(edge => edge.source === 'iface' && edge.target === 'impl').path;
assert.equal(dispatch.attributes['stroke-dasharray'], '5 4');
assert.ok(dispatch.querySelector('title').textContent.includes('Возможный переход'));
view.setSelected('iface');
assert.ok(dispatch.classList.contains('path-related-outgoing'));
view.expandAllPathNodes();
assertStable(expandedLeft, camera);
assert.ok(!view.positions.has('orphan'));
const repeats = view.pathEdgeElements.filter(edge => edge.source === 'impl' && edge.target === 'deep');
assert.equal(repeats.length, 2);
assert.ok(repeats.some(edge => edge.path.querySelector('title').textContent.includes('20')));
assert.ok(repeats.some(edge => edge.path.querySelector('title').textContent.includes('22')));
const all = new Map(view.positions);
view.collapseAllPathNodes();
assertStable(all, camera);
view.expandAllPathNodes();
assertStable(all, camera);
view.renderPathCards(structuredClone(nodes), structuredClone(edges), 'right');
assertStable(all, camera);
view.setDisplayProvider('topology'); view.renderPathCards(nodes, edges, 'right');
assert.ok(viewport.querySelectorAll('.path-method-frame').length > 0, 'detailed topology remains available');
view.setDisplayProvider('flow'); view.renderPathCards(nodes, edges, 'right');
assertStable(all, camera);
view.pathSnapshotId = 'two'; view.renderPathCards(nodes, edges, 'root');
assert.equal(view.pathExpandedIds.size, 0);
assert.equal(JSON.stringify({ nodes, edges }), original);
console.log('Stable entry Flow path, curves, branches, evidence, camera and snapshots PASS');
