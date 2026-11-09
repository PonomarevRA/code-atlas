/** Проверяет полный captured execution-path настоящим renderer без браузерных внутренних API. */
import fs from 'node:fs';
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { GraphView } from '../web/graph.js';
import { Svg } from '../tests/svg_fixture.mjs';
import { branchLabels } from '../web/path-annotations.js';

const input = process.argv[2];
if (!input) throw new Error('Usage: node scripts/measure-expanded-flow.mjs captured-path.json');
const path = JSON.parse(fs.readFileSync(input, 'utf8'));
const steps = new Map(path.steps.map(step => [step.nodeId, step]));
const nodes = path.steps.map(step => ({ ...step, id: step.nodeId }));
const edges = path.edges.map(edge => ({ ...edge, source_file: edge.sourceFile,
  caption: branchLabels(steps.get(edge.source)?.controlFlow, edge.branchIds || []).join(' · ') }));
const untouched = JSON.stringify({ nodes, edges });
globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g');
viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const view = new GraphView(svg, () => {}, () => {});
view.setDisplayProvider('flow'); view.pathRootId = path.rootNodeId; view.pathSnapshotId = path.snapshotId;
view.renderPathCards(nodes, edges, path.rootNodeId);
view.camera = { x: 15, y: 25, scale: .9 };
const started = performance.now();
view.expandAllPathNodes();
const renderMs = performance.now() - started;
assert.deepEqual(view.camera, { x: 15, y: 25, scale: .9 });
assert.equal(view.pathScope.visible, path.returnedNodes);
const cards = [...view.positions.values()];
for (let left = 0; left < cards.length; left++) {
  for (let right = left + 1; right < cards.length; right++) {
    const a = cards[left], b = cards[right];
    assert.ok(Math.abs(a.x - b.x) >= (a.w + b.w) / 2 - .001 || Math.abs(a.y - b.y) >= (a.h + b.h) / 2 - .001,
      `Cards overlap: ${a.id} / ${b.id}`);
  }
}
const positions = JSON.stringify([...view.positions]);
// Независимая проверка фактически нарисованных сегментов, а не внутреннего алгоритма маршрутизации.
const routes = viewport.querySelectorAll('.implementation-flow-edge');
for (const route of routes) {
  assert.ok(route.attributes.d, 'Semantic edge has no visible route');
  const points = route.attributes.d.match(/-?\d+(?:\.\d+)?/g).map(Number);
  for (let index = 2; index < points.length; index += 2) {
    const x1 = points[index - 2], y1 = points[index - 1], x2 = points[index], y2 = points[index + 1];
    assert.ok(x1 === x2 || y1 === y2, 'Flow route must be orthogonal');
    for (const card of cards) {
      const left = card.x - card.w / 2, right = card.x + card.w / 2, top = card.y - card.h / 2, bottom = card.y + card.h / 2;
      const hit = x1 === x2 ? x1 > left && x1 < right && Math.max(y1, y2) > top && Math.min(y1, y2) < bottom
        : y1 > top && y1 < bottom && Math.max(x1, x2) > left && Math.min(x1, x2) < right;
      assert.ok(!hit, `Route crosses ${card.id}`);
    }
  }
}
const captions = viewport.querySelectorAll('.path-edge-label').map(label => ({ x: Number(label.attributes.x), y: Number(label.attributes.y), width: label.textContent.length * 7 }));
for (let index = 0; index < captions.length; index++) {
  const a = captions[index];
  for (const card of cards) assert.ok(a.x + a.width / 2 <= card.x - card.w / 2 || a.x - a.width / 2 >= card.x + card.w / 2
    || a.y + 2 <= card.y - card.h / 2 || a.y - 11 >= card.y + card.h / 2, `Caption overlaps ${card.id}`);
  for (let other = index + 1; other < captions.length; other++) {
    const b = captions[other];
    assert.ok(Math.abs(a.x - b.x) >= (a.width + b.width) / 2 || Math.abs(a.y - b.y) >= 13, 'Captions overlap');
  }
}
view.expandAllPathNodes();
assert.equal(JSON.stringify([...view.positions]), positions);
assert.equal(JSON.stringify({ nodes, edges }), untouched);
console.log(JSON.stringify({ methods: view.pathScope.visible, decisions: viewport.querySelectorAll('.implementation-flow-decision').length,
  references: viewport.querySelectorAll('.path-reference').length, cards: cards.length, bounds: view.flowBounds,
  renderMs: Math.round(renderMs), cameraPreserved: true, deterministic: true, overlaps: 0,
  routes: routes.length, routeCrossings: 0, captions: captions.length, captionOverlaps: 0 }, null, 2));
