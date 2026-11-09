/** Локальная ссылка и реальный метод должны образовывать непрерывную статическую связь. */
import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';
import { assertClear } from './curve_geometry.mjs';
globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g');
viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const view = new GraphView(svg, () => {}, () => {});
const nodes = [
  { id: 'root', label: '.Calculate()', controlFlow: { branches: [{ kind: 'if', line: 4, condition: 'valid', arms: [{ id: 'valid', kind: 'true' }] }] } },
  { id: 'iface', label: '.Calculate()', file: 'ICalculator.cs' },
  { id: 'impl', label: '.Calculate()', file: 'Calculator.cs' },
  { id: 'shared', label: '.Resolve()', file: 'Shared.cs' },
];
const edges = [
  { source: 'root', target: 'iface', relation: 'calls', confidence: 'source', branchIds: ['valid'], sourceLine: 5 },
  { source: 'iface', target: 'impl', relation: 'dispatches_to', confidence: 'candidate' },
  { source: 'impl', target: 'shared', relation: 'calls', confidence: 'source', sourceLine: 20 },
  { source: 'impl', target: 'shared', relation: 'calls', confidence: 'source', sourceLine: 22 },
  { source: 'shared', target: 'root', relation: 'calls', confidence: 'source' },
];
const untouched = JSON.stringify({ nodes, edges });

/** Проверяет фактические порты каждого продолжения и отсутствие пересечения чужих карточек. */
function assertLinks() {
  const references = [...view.positions.values()].filter(item => item.reference);
  const links = viewport.querySelectorAll('.path-reference-link');
  assert.equal(links.length, references.length, 'each visible reference continues to the actual method exactly once');
  for (const reference of references) {
    const link = links.find(path => path.attributes['data-reference-id'] === reference.id);
    assert.ok(link);
    assert.equal(link.attributes['data-callee-id'], reference.focusId);
    const points = link.attributes.d.match(/-?\d+(?:\.\d+)?/g).map(Number);
    const target = view.positions.get(reference.focusId);
    assert.deepEqual(points.slice(0, 2), view.displayProvider === 'flow' ? [reference.x + reference.w / 2, reference.y] : [reference.x, reference.y + reference.h / 2]);
    assert.deepEqual(points.slice(-2), view.displayProvider === 'flow' ? [target.x - target.w / 2, target.y] : [target.x, target.y - target.h / 2]);
    assertClear(link.attributes.d, [...view.positions.values()]);
  }
  return links;
}
view.setDisplayProvider('flow'); view.pathRootId = 'root';
view.renderPathCards(nodes, edges, 'root');
let links = assertLinks();
const branchLink = links.find(path => path.attributes['data-callee-id'] === 'iface');
assert.equal(branchLink.attributes['data-caller-id'], 'root', 'lexical if retains real caller');
const dispatch = links.find(path => path.attributes['data-caller-id'] === 'iface');
assert.equal(dispatch.attributes['stroke-dasharray'], '5 4');
view.setSelected('iface');
assert.ok(dispatch.classList.contains('path-related-outgoing'));
assert.ok(branchLink.classList.contains('path-related-incoming'));
view.camera = { x: 20, y: 30, scale: .8 };
view.expandAllPathNodes();
assert.deepEqual(view.camera, { x: 20, y: 30, scale: .8 });
links = assertLinks();
const repeated = links.find(path => path.attributes['data-caller-id'] === 'impl');
assert.ok(repeated.querySelector('title').textContent.includes('20'));
assert.ok(repeated.querySelector('title').textContent.includes('22'));
assert.ok(links.some(path => path.attributes['data-callee-id'] === 'root'), 'cycle returns to actual existing method');
const count = links.length;
view.expandAllPathNodes();
assert.equal(assertLinks().length, count, 'no accumulated connectors');
view.collapseAllPathNodes();
view.togglePathNode("impl");
assertLinks();
assert.ok(!viewport.querySelectorAll('.path-reference-link').some(path => path.attributes['data-callee-id'] === 'shared'));
view.setDisplayProvider('topology'); view.expandAllPathNodes();
assertLinks();
assert.equal(JSON.stringify({ nodes, edges }), untouched);
console.log('Path reference links ports, dispatch evidence, repeats, cycles, providers and camera PASS');
