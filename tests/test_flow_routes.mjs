/** Геометрическая регрессия тесных циклов: проверяем сами кривые, а не формулу маршрутизатора. */
import assert from 'node:assert/strict';
import { flowEdgePath } from '../web/flow-layout.js';
import { inlineFlowEdgeRouter } from '../web/inline-flow-edges.js';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';
import { assertClear } from './curve_geometry.mjs';

const a = { id: 'service', x: 130, y: 180, w: 260, h: 124, cycleId: 'cycle' };
const b = { ...a, id: 'interface', x: 430 };
const narrow = { ...b, x: 418 }; // 28px between two cards of the same SCC.
const route = inlineFlowEdgeRouter(new Map([[a.id, a], [narrow.id, narrow]]));
const forward = route(a, narrow, 1).d;
const samples = assertClear(forward, [a, narrow]);
for (let index = 1; index < samples.length; index++) {
  assert.ok(samples[index].x >= samples[index - 1].x - 1e-6, 'Nearby cyclic edge must never turn back');
}
assert.ok(samples.every(point => Math.abs(point.y - a.y) < 1e-6), 'Nearby SCC cards use their shared free gap');
assert.ok(samples.some(point => Math.abs(point.x - (a.x + a.w / 2 + narrow.x - narrow.w / 2) / 2) < 1e-6),
  'Forward curve passes through the midpoint between ports');
for (let index = 0; index < 6; index++) {
  const backward = flowEdgePath({ ...b, width: b.w, height: b.h }, { ...a, width: a.w, height: a.h }, index);
  assertClear(backward, [a, b]);
  assertClear(route(a, a, index).d, [a, narrow]);
}

// A neighbor blocks the old upper loop; a skip-edge is blocked by the middle card.
const blocker = { ...a, id: 'blocker', x: 130, y: 28 };
const middle = { ...a, id: 'middle', x: 486 };
const far = { ...a, id: 'far', x: 842, y: 280 };
const obstacles = new Map([a, blocker, middle, far].map(card => [card.id, card]));
const detour = inlineFlowEdgeRouter(obstacles);
assertClear(detour(a, a, 0).d, [...obstacles.values()]);
assertClear(detour(a, far, 0).d, [...obstacles.values()]);

// The real Flow renderer must use the same obstacle routing and keep all facts and ports.
globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g');
viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const view = new GraphView(svg, () => {}, () => {});
view.setDisplayProvider('flow');
const nodes = ['a', 'b', 'c', 'd'].map(id => ({ id, label: id }));
const edges = [
  { source: 'a', target: 'b' }, { source: 'b', target: 'c' }, { source: 'c', target: 'd' },
  { source: 'd', target: 'a' }, { source: 'a', target: 'a' }, { source: 'b', target: 'b' },
  { source: 'a', target: 'd' }, { source: 'a', target: 'd', relation: 'references' },
];
const original = JSON.stringify({ nodes, edges });
view.render(nodes, edges);
const paths = viewport.querySelectorAll('.flow-edge');
assert.equal(paths.length, edges.length);
const cards = [...view.positions.values()].map(card => ({ ...card, w: card.width, h: card.height }));
for (const path of paths) {
  const points = assertClear(path.attributes.d, cards);
  const source = view.positions.get(path.attributes['data-source']), target = view.positions.get(path.attributes['data-target']);
  assert.deepEqual(points[0], { x: source.x + source.width / 2, y: source.y });
  assert.deepEqual(points.at(-1), { x: target.x - target.width / 2, y: target.y });
  const { x, y, width, height } = view.flowBounds;
  assert.ok(points.every(point => point.x >= x && point.x <= x + width && point.y >= y && point.y <= y + height));
}
assert.equal(JSON.stringify({ nodes, edges }), original);
console.log('Flow routes narrow cycles, self-intersections, obstacles and actual renderer PASS');
