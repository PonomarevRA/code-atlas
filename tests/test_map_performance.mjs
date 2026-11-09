import assert from 'node:assert/strict';
import { RectangleIndex, cardRectangle } from '../web/rectangle-index.js';
import { cachedPathRouter } from '../web/path-route-cache.js';
import { inlineFlowEdgeRouter } from '../web/inline-flow-edges.js';
import { entryPathCaption } from '../web/entry-path-edges.js';

// Детерминированные разные размеры, отрицательные координаты и точные границы.
const rectangles = [];
for (let i = 0; i < 700; i++) rectangles.push({ left: (i * 71) % 2100 - 1000,
  right: (i * 71) % 2100 - 980 + i % 60, top: (i * 43) % 1400 - 700,
  bottom: (i * 43) % 1400 - 680 + i % 30, id: i });
const index = new RectangleIndex(rectangles);
for (let i = 0; i < 130; i++) {
  const rect = { left: i * 15, right: i * 15 + 19, top: -80, bottom: 800, id: 700 + i };
  rectangles.push(rect); index.add(rect);
}
for (let i = 0; i < 1200; i++) {
  const box = { left: i % 2100 - 1000, right: i % 2100 - 1000 + i % 7 * 160,
    top: (i * 47) % 1500 - 710, bottom: (i * 47) % 1500 - 710 + i % 11 * 90 };
  const expected = rectangles.filter(rect => box.left < rect.right && box.right > rect.left
    && box.top < rect.bottom && box.bottom > rect.top);
  assert.deepEqual(new Set(index.query(box)), new Set(expected));
}

const source = { x: 0, y: 0, w: 100, h: 80, panelIndex: 0 };
const target = { x: 300, y: 100, w: 100, h: 80, panelIndex: 0 };
const obstacle = { x: 150, y: 0, w: 80, h: 160, panelIndex: 0 };
const positions = new Map([['source', source], ['target', target], ['obstacle', obstacle]]);
const view = { pathSnapshotId: 'one', pathRootId: 'source', groupingProvider: 'natural' };
let created = 0, calls = 0;
/** Считает реальные обращения к маршрутизатору, включая воспроизведение внешних портов. */
function factory(cards) {
  created++;
  const route = inlineFlowEdgeRouter(cards);
  return (...args) => { calls++; return route(...args); };
}
const external = { ...target, x: 410, y: 240 };
let route = cachedPathRouter(view, positions, factory);
const stub = route(source, external), expected = route(source, target, 0);
assert.equal(calls, 2);
route = cachedPathRouter(view, new Map(positions), factory);
assert.deepEqual(route(source, external), stub);
const cachedResult = route(source, target, 0);
assert.deepEqual(cachedResult, expected);
assert.equal(calls, 2, 'Повтор той же геометрии не строит маршруты');
assert.equal(created, 1);
cachedResult.d = 'caller mutation';
route = cachedPathRouter(view, positions, factory);
assert.deepEqual(route(source, external), stub);
assert.deepEqual(route(source, target, 0), expected, 'Изменение возвращённого объекта не портит кэш');
// Изменившийся индекс ребра воспроизводит предыдущую заглушку перед новым маршрутом.
route = cachedPathRouter(view, positions, factory);
route(source, external);
const changed = route(source, target, 5);
const fresh = inlineFlowEdgeRouter(positions); fresh(source, external);
assert.deepEqual(changed, fresh(source, target, 5));
assert.equal(calls, 4);
for (const property of ['pathSnapshotId', 'pathRootId', 'groupingProvider']) {
  view[property] += '-changed';
  route = cachedPathRouter(view, positions, factory); route(source, target, 0);
}
assert.equal(created, 5);
target.x += 25;
route = cachedPathRouter(view, positions, factory);
assert.deepEqual(route(source, target, 0), inlineFlowEdgeRouter(positions)(source, target, 0));
assert.equal(created, 6);
const path = 'M 50 0 C 100 0 150 100 250 100';
const occupied = [{ left: 180, right: 230, top: 70, bottom: 95 }];
assert.deepEqual(entryPathCaption(path, 'если верно', positions, [...occupied]),
  entryPathCaption(path, 'если верно', positions, [...occupied], new RectangleIndex(positions.values(), cardRectangle)));
console.log('Map performance contracts PASS');
