/** Сравнение реализаций вертикально, с деревьями разной высоты и исходными связями. */
import assert from 'node:assert/strict';
import { implementationComparisonGroups, implementationComparisonRows } from '../web/implementation-comparison-rows.js';
import { pathControlTreeLayout } from '../web/path-control-tree-layout.js';
import { pathReadingLayout } from '../web/path-reading-layout.js';
import { naturalLayout } from '../web/natural-layout.js';
import { buildPathScope } from '../web/path-scope.js';
import { buildPathScene } from '../web/path-scene.js';

const implementations = Array.from({ length: 10 }, (_, index) => ({ id: `impl-${index}`, label: `Implementation ${index}`, w: 260, h: 154 }));
const nodes = [{ id: 'root', label: 'Interface', w: 260, h: 154 }, ...implementations,
  { id: 'ordinary', label: 'Ordinary call', w: 260, h: 154 }];
const edges = implementations.map(node => ({ source: 'root', target: node.id, relation: 'dispatches_to', confidence: 'candidate' }));
edges.push({ source: 'root', target: 'ordinary', relation: 'calls' },
  { source: 'impl-3', target: 'root', relation: 'calls' }, { ...edges[0] });
const facts = JSON.stringify({ nodes, edges });

/** Карточки общего интерфейса стоят в одной колонке и не перекрываются. */
function assertComparison(positions) {
  let lastBottom = -Infinity;
  const x = positions.get(implementations[0].id).x;
  for (const node of implementations) {
    const point = positions.get(node.id);
    assert.equal(point.x, x, 'all ten candidates remain in one column');
    assert.ok(point.y - node.h / 2 > lastBottom, 'comparison reads top to bottom with space between cards');
    lastBottom = point.y + node.h / 2;
  }
  const ordinary = nodes.find(node => node.id === 'ordinary');
  assert.ok(positions.get('ordinary').y - ordinary.h / 2 > lastBottom, 'ordinary calls remain outside the comparison group');
  assert.equal(positions.size, nodes.length, 'cycles and duplicate evidence do not duplicate methods');
}
for (const layout of [
  (ns, es) => pathReadingLayout(ns, es, 'root'),
  (ns, es) => naturalLayout(ns, es, { rootId: 'root' }).positions,
  (ns, es) => pathControlTreeLayout(ns, es, [], 'root').positions,
  (ns, es) => pathControlTreeLayout(ns, es, [], 'root', { natural: true }).positions,
]) {
  const result = layout(nodes, edges);
  assertComparison(result);
  assert.deepEqual(layout([...nodes].reverse(), [...edges].reverse()), result, 'snapshot input permutation keeps comparison geometry');
}
assert.equal(JSON.stringify({ nodes, edges }), facts, 'raw candidate evidence stays intact');

// Раскрытые тела занимают независимые полосы целиком, даже при разной высоте ветвей.
const detailed = nodes.map((node, index) => ({ ...node, controlFlow: index === 1 ? { branches: [
  { kind: 'if', line: 1, arms: [{ id: 'true', kind: 'true' }, { id: 'false', kind: 'false' }] },
  { kind: 'foreach', line: 2, parentArmIds: ['true'], arms: [{ id: 'body', kind: 'body' }] },
], terminals: [{ kind: 'return', line: 3, text: 'return value;', branchIds: ['true', 'body'] }] }
  : index === 2 ? { branches: [{ kind: 'switch', line: 7, arms: [
    { id: 'case1', kind: 'case', label: 'case 1' }, { id: 'case2', kind: 'case', label: 'case 2' },
    { id: 'default', kind: 'default' }] }] } : undefined }));
const scope = buildPathScope(detailed, edges, { rootId: 'root', expandedIds: detailed.map(node => node.id) });
for (const natural of [false, true]) {
  const scene = buildPathScene(scope, { flowPath: true, flatFlow: true, reading: true, natural });
  const x = scene.positions.get('impl-0').x;
  let previousY = -Infinity;
  for (const node of implementations) {
    const point = scene.positions.get(node.id);
    assert.equal(point.x, x, 'different body heights keep method cards in one column');
    assert.ok(point.y > previousY, 'expanded comparison reads top to bottom');
    previousY = point.y;
  }
  const cards = [...scene.positions.values()];
  for (let i = 0; i < cards.length; i++) for (let j = i + 1; j < cards.length; j++) {
    const a = cards[i], b = cards[j];
    assert.ok(Math.abs(a.x - b.x) >= (a.w + b.w) / 2 || Math.abs(a.y - b.y) >= (a.h + b.h) / 2,
      'complete sibling bodies and ordinary calls never overlap');
  }
  const branch = scene.visuals.find(node => node.decision && node.line === 1);
  const loop = scene.visuals.find(node => node.decision && node.line === 2);
  assert.ok(scene.positions.get(branch.id).x < scene.positions.get(loop.id).x, 'lexical nesting still advances right');
  const candidates = scene.visualEdges.filter(edge => edge.relation === 'dispatches_to');
  assert.ok(candidates.every(edge => edge.caption === 'Возможная реализация'));
  assert.deepEqual(candidates.map(({ caption, ...evidence }) => evidence),
    edges.filter(edge => edge.relation === 'dispatches_to'), 'comparison preserves original candidate evidence including duplicates');
  const filtered = buildPathScene(scope, { flowPath: true, flatFlow: true, reading: true, natural,
    flowPositions: scene.positions, controlFilters: { conditions: false, loops: false } });
  for (const node of implementations) assert.deepEqual(filtered.positions.get(node.id), scene.positions.get(node.id),
    'hiding controls keeps comparison column coordinates');
}
// Общая реализация не объединяет два интерфейса и не появляется повторно.
const sharedLayer = ['a', 'b', 'c', 'd', 'call', 'single'].map(id => ({ id }));
const sharedEdges = [
  { source: 'i1', target: 'a', relation: 'dispatches_to' }, { source: 'i1', target: 'b', relation: 'dispatches_to' },
  { source: 'i2', target: 'b', relation: 'dispatches_to' }, { source: 'i2', target: 'c', relation: 'dispatches_to' },
  { source: 'i2', target: 'd', relation: 'dispatches_to' }, { source: 'i3', target: 'single', relation: 'dispatches_to' },
  { source: 'i1', target: 'call', relation: 'calls', confidence: 'candidate' },
  { source: 'i1', target: 'hidden', relation: 'dispatches_to' },
];
const rows = implementationComparisonRows(sharedLayer, implementationComparisonGroups(sharedEdges), 3);
assert.deepEqual(rows.map(row => row.map(node => node.id)), [['a'], ['b'], ['c'], ['d'], ['call', 'single']]);
assert.equal(new Set(rows.flat().map(node => node.id)).size, sharedLayer.length);
console.log('Vertical implementation comparison, nested bodies, filters and evidence PASS');
