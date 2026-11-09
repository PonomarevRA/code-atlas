/** Приёмка размещения по реальным связям, а не только устранения столкновений. */
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { topologyLayout } from '../web/topology-layout.js';

/** Создаёт два плотных модуля со слабым мостом и одинаковыми радиусами. */
function modularFixture() {
  const nodes = [], edges = [];
  for (const prefix of ['a', 'b']) {
    for (let index = 0; index < 6; index++) nodes.push({ id: `${prefix}${index}`, r: 18, custom: 'preserve' });
    for (let first = 0; first < 6; first++) for (let second = first + 1; second < 6; second++) {
      edges.push({ source: `${prefix}${first}`, target: `${prefix}${second}`, weight: 10 });
    }
  }
  edges.push({ source: 'a0', target: 'b0', weight: .05 });
  return { nodes, edges };
}
/** Сравнивает координаты по стабильным ID без зависимости от порядка Map. */
function normalized(positions) { return [...positions].sort(([left], [right]) => left.localeCompare(right)); }
/** Проверяет радиусы и конечность всех координат. */
function valid(positions, count) {
  assert.equal(positions.size, count);
  for (const point of positions.values()) {
    assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y), JSON.stringify(point));
    assert.ok(Number.isFinite(point.r) && point.r > 0);
  }
}
/** Измеряет среднюю длину заданных пар узлов. */
function averageDistance(positions, pairs) {
  return pairs.reduce((sum, [left, right]) => {
    const a = positions.get(left), b = positions.get(right);
    return sum + Math.hypot(a.x - b.x, a.y - b.y);
  }, 0) / pairs.length;
}
const fixture = modularFixture();
const before = JSON.stringify(fixture);
const grouped = topologyLayout(fixture.nodes, fixture.edges);
valid(grouped, 12);
assert.equal(JSON.stringify(fixture), before);
assert.deepEqual(normalized(grouped), normalized(topologyLayout(fixture.nodes, fixture.edges)));
assert.deepEqual(normalized(grouped), normalized(topologyLayout([...fixture.nodes].reverse(), [...fixture.edges].reverse())));
assert.ok([...grouped.values()].every(point => point.custom === 'preserve'));
const within = [], across = [];
for (let first = 0; first < 6; first++) {
  for (let second = first + 1; second < 6; second++) {
    within.push([`a${first}`, `a${second}`], [`b${first}`, `b${second}`]);
  }
  for (let second = 0; second < 6; second++) across.push([`a${first}`, `b${second}`]);
}
const innerDistance = averageDistance(grouped, within);
const outerDistance = averageDistance(grouped, across);
assert.ok(outerDistance > innerDistance * 1.5, `Dense modules should separate: within=${innerDistance}, across=${outerDistance}`);
const cycleEdges = fixture.nodes.map((node, index) => ({ source: node.id, target: fixture.nodes[(index + 1) % fixture.nodes.length].id, weight: 1 }));
assert.notDeepEqual(normalized(grouped), normalized(topologyLayout(fixture.nodes, cycleEdges)));

const disconnected = [...fixture.nodes, ...Array.from({ length: 8 }, (_, index) => ({ id: `isolated-${index}`, r: 18 }))];
const withoutBridge = fixture.edges.filter(edge => edge.source[0] === edge.target[0]);
const packed = topologyLayout(disconnected, withoutBridge);
valid(packed, 20);
const points = [...packed.values()];
for (let first = 0; first < points.length; first++) for (let second = first + 1; second < points.length; second++) {
  const a = points[first], b = points[second];
  assert.ok(Math.hypot(a.x - b.x, a.y - b.y) + .2 >= a.r + b.r, `Overlapping circles: ${a.id}/${b.id}`);
}
const width = Math.max(...points.map(point => point.x + point.r)) - Math.min(...points.map(point => point.x - point.r));
const height = Math.max(...points.map(point => point.y + point.r)) - Math.min(...points.map(point => point.y - point.r));
const circleArea = points.reduce((sum, point) => sum + Math.PI * point.r ** 2, 0);
assert.ok(width * height / circleArea < 30, `Disconnected packing has excessive empty extent: ${width}x${height}`);
// Слабые листья вокруг плотного ядра не должны уменьшать ядро до нечитаемых точек при fit.
const hubNodes = [], hubEdges = [];
for (let index = 0; index < 24; index++) hubNodes.push({ id: `core${index}`, r: 28 });
for (let first = 0; first < 24; first++) for (let second = first + 1; second < 24; second++) {
  hubEdges.push({ source: `core${first}`, target: `core${second}`, weight: 50 });
}
for (let index = 0; index < 12; index++) {
  hubNodes.push({ id: `leaf${index}`, r: 18 });
  hubEdges.push({ source: 'core0', target: `leaf${index}`, weight: .01 });
}
const hub = [...topologyLayout(hubNodes, hubEdges).values()];
const hubWidth = Math.max(...hub.map(point => point.x + point.r)) - Math.min(...hub.map(point => point.x - point.r));
const hubHeight = Math.max(...hub.map(point => point.y + point.r)) - Math.min(...hub.map(point => point.y - point.r));
// Повторяем рамку GraphView.fit при обычной ширине рабочей области 760 × 800.
const fittedCoreDiameter = 56 * Math.min(1.6, 690 / (hubWidth + 170), 670 / (hubHeight + 100));
assert.ok(fittedCoreDiameter >= 18, `Weak leaves dominate fit: core diameter ${fittedCoreDiameter}px`);

assert.equal(topologyLayout([], []).size, 0);
valid(topologyLayout([{ id: 'single', r: 22 }], []), 1);

const maximum = Array.from({ length: 320 }, (_, index) => ({ id: `n${index}`, r: 12 + index % 15 }));
const maximumEdges = maximum.map((node, index) => ({ source: node.id, target: maximum[(index + 1) % 320].id, weight: 1 }));
for (let index = 1; index < 320; index += 7) maximumEdges.push({ source: 'n0', target: `n${index}`, weight: 3 });
const started = performance.now();
const maximumResult = topologyLayout(maximum, maximumEdges);
const elapsed = performance.now() - started;
valid(maximumResult, 320);
console.log(JSON.stringify({ within: innerDistance, across: outerDistance, packingAreaRatio: width * height / circleArea, maximum320Milliseconds: elapsed }));
console.log('Topology layout PASS');
