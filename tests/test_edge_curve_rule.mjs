/** Свободные связи разных представлений используют одну кривую без волны в середине. */
import assert from 'node:assert/strict';
import { connectionCurve } from '../web/edge-curves.js';
import { flowEdgePath } from '../web/flow-layout.js';
import { routeEdge } from '../web/edge-routing.js';
import { savedEdgePath } from '../web/saved-view-renderer.js';
import { smoothImplementationEdgeRouter } from '../web/inline-flow-edges.js';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';
import { samplePath, assertClear } from './curve_geometry.mjs';

/** Проверяет одну кривую, монотонность и отсутствие замедления вертикального изгиба в центре. */
function assertGentle(d) {
  assert.equal((d.match(/C/g) || []).length, 1, 'a free connection has two controls and one cubic span');
  const points = samplePath(d);
  const direction = Math.sign(points.at(-1).y - points[0].y);
  for (let index = 1; index < points.length; index++) {
    assert.ok(points[index].x >= points[index - 1].x - 1e-6);
    assert.ok((points[index].y - points[index - 1].y) * direction >= -1e-6);
  }
  if (direction) {
    const centerSlope = Math.abs(points[31].y - points[29].y);
    const quarterSlope = Math.abs(points[16].y - points[14].y);
    assert.ok(centerSlope > quarterSlope, 'the center must not flatten into a second wave');
  }
}

const source = { x: 130, y: 180, w: 260, h: 124, width: 260, height: 124 };
for (const targetY of [30, 180, 480]) {
  const target = { ...source, x: 800, y: targetY };
  const d = flowEdgePath(source, target);
  assertGentle(d);
  assertClear(d, [source, target]);
  const values = d.match(/-?\d+(?:\.\d+)?/g).map(Number);
  const left = source.x + source.w / 2, right = target.x - target.w / 2;
  assert.equal(values[2], left + (right - left) / 3);
  assert.equal(values[4], left + 2 * (right - left) / 3);
  const route = smoothImplementationEdgeRouter(new Map([['source', source], ['target', target]]), [], [], { horizontal: true });
  assert.equal(route(source, target).d, d, 'detailed methods use exactly the same free-span rule');
  const savedSource = { x: source.x - source.w / 2, y: source.y - source.h / 2, width: source.w, height: source.h };
  const savedTarget = { x: target.x - target.w / 2, y: target.y - target.h / 2, width: target.w, height: target.h };
  if (targetY !== source.y) assert.equal(savedEdgePath(savedSource, savedTarget), d);
}
const vertical = connectionCurve({ x: 0, y: 0 }, { x: 60, y: 300 }, { axis: 'vertical' });
assert.equal(vertical, 'M 0 0 C 0 100 60 200 60 300');
const verticalRoute = smoothImplementationEdgeRouter(new Map([['a', { x: 0, y: 0, w: 260, h: 124 }], ['b', { x: 60, y: 450, w: 260, h: 124 }]]));
assert.equal((verticalRoute({ x: 0, y: 0, w: 260, h: 124 }, { x: 60, y: 450, w: 260, h: 124 }).d.match(/C/g) || []).length, 1);
assert.equal((routeEdge({ x: 0, y: 0 }, { x: 300, y: 150 }).match(/C/g) || []).length, 1, 'free topology has no central segment seam');

// API-вход правее обработчика требует внешнего канала, иначе кривая войдёт в карточку справа.
globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g');
viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const view = new GraphView(svg, () => {}, () => {});
view.setDisplayProvider('flow');
view.render([{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }], [{ source: 'a', target: 'b' }]);
const before = JSON.stringify([...view.positions]);
view.renderIngress([{ id: 'entry', kind: 'http', label: 'POST /calculate', targets: ['a', 'b'] }], () => {});
const cards = [...view.positions.values()].map(point => ({ ...point, w: point.width, h: point.height }));
const ingress = viewport.querySelectorAll('.ingress-edge');
assert.equal(ingress.length, 2);
for (const path of ingress) assertClear(path.attributes.d, cards);
assert.equal(JSON.stringify([...view.positions]), before);
console.log('Shared thirds rule, no central wave, renderer parity and ingress ports PASS');
