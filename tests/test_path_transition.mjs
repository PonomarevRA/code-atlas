/** Скрытые тела не создают пустот; раскрытие анимирует дерево вокруг неподвижного метода. */
import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';
import { capturePathTransition, animatePathTransition } from '../web/path-transition.js';
import { assertClear } from './curve_geometry.mjs';

const animations = [];
Svg.prototype.animate = function (frames, options) { animations.push({ group: this, frames, options }); };
globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const nodes = [{ id: 'root', label: 'Root' }, { id: 'left', label: 'Left', controlFlow: { branches: [
  { kind: 'if', line: 10, arms: [{ id: 'yes', kind: 'true' }, { id: 'no', kind: 'false' }] },
  { kind: 'foreach', line: 12, parentArmIds: ['yes'], arms: [{ id: 'body', kind: 'body' }] },
], terminals: [{ kind: 'return', line: 20, branchIds: ['no'], text: 'return null;' }] } },
{ id: 'right', label: 'Right' }, { id: 'deep', label: 'Deep', controlFlow: { branches: [] } }];
for (let index = 0; index < 100; index++) nodes[3].controlFlow.branches.push({ kind: 'if', line: index + 30, arms: [] });
const edges = [{ source: 'root', target: 'left', relation: 'calls', sourceLine: 1 },
  { source: 'root', target: 'right', relation: 'calls', sourceLine: 2 },
  { source: 'left', target: 'deep', relation: 'calls', branchIds: ['yes', 'body'], sourceLine: 13, confidence: 'candidate' }];
const facts = JSON.stringify({ nodes, edges });

/** Только завершённая геометрия сравнивается с исходными связями, без промежуточных эффектов. */
function assertGeometry(view, viewport) {
  const cards = [...view.positions.values()];
  for (let i = 0; i < cards.length; i++) for (let j = i + 1; j < cards.length; j++) {
    const a = cards[i], b = cards[j];
    assert.ok(Math.abs(a.x - b.x) >= (a.w + b.w) / 2 || Math.abs(a.y - b.y) >= (a.h + b.h) / 2);
  }
  for (const path of viewport.querySelectorAll('.path-edge')) assertClear(path.attributes.d, cards);
}

for (const provider of ['graphistry', 'natural']) {
  const svg = new Svg('svg'), viewport = new Svg('g'); viewport.setAttribute('id', 'viewport'); svg.append(viewport);
  const view = new GraphView(svg, () => {}, () => {});
  view.groupingProvider = provider; view.pathReading = true; view.pathRootId = 'root'; view.setDisplayProvider('flow');
  view.renderPathCards(nodes, edges, 'root');
  const initial = new Map(view.positions), cache = view.entryPathLayout;
  assert.equal(cache.positions.size, 3, 'the hundred hidden conditions reserve no space');
  assert.ok(Math.max(...[...initial.values()].map(point => point.x)) - Math.min(...[...initial.values()].map(point => point.x)) < 700);
  view.camera = { x: 33, y: 44, scale: .8 };
  const anchor = initial.get('left'), screen = { x: anchor.x * .8 + 33, y: anchor.y * .8 + 44 };
  const numbers = new Map(cache.methodNumbers); animations.length = 0;
  view.togglePathNode('left');
  const current = view.positions.get('left');
  assert.ok(Math.abs(current.x * view.camera.scale + view.camera.x - screen.x) < 1e-7);
  assert.ok(Math.abs(current.y * view.camera.scale + view.camera.y - screen.y) < 1e-7);
  assert.equal(view.camera.scale, .8);
  assert.ok(view.positions.has('deep')); assert.notEqual(view.entryPathLayout, cache);
  for (const [id, number] of numbers) assert.equal(view.entryPathLayout.methodNumbers.get(id), number);
  assert.ok(animations.some(animation => animation.group.classList.contains('path-decision') && animation.frames[0].opacity === 0));
  assert.ok(animations.some(animation => animation.group.classList.contains('path-edge')));
  assert.ok(!animations.some(animation => animation.group === view.nodeElements.get('left').group), 'clicked card does not move during the effect');
  assert.ok(animations.every(animation => animation.options.duration <= 300));
  assertGeometry(view, viewport);
  const expanded = new Map(view.positions), expandedCache = view.entryPathLayout, camera = { ...view.camera };
  view.setPathControlFilters({ conditions: false, loops: false });
  assert.equal(view.entryPathLayout, expandedCache);
  for (const [id, point] of view.positions) assert.deepEqual(point, expanded.get(id));
  assert.deepEqual(view.camera, camera);
  view.setPathControlFilters({ conditions: true, loops: true });
  globalThis.matchMedia = () => ({ matches: true }); animations.length = 0;
  view.togglePathNode('left');
  assert.equal(animations.length, 0, 'reduced motion skips effects');
  assert.equal(view.positions.size, 3); assertGeometry(view, viewport);
  assert.deepEqual(view.positions, initial, 'collapse returns the compact initial map');
  delete globalThis.matchMedia;
  const left = view.nodeElements.get('left').group;
  left.getAnimations = () => [{ playState: 'running' }];
  globalThis.getComputedStyle = () => ({ transform: 'matrix(1, 0, 0, 1, 150, 250)', opacity: '.6' });
  const during = capturePathTransition(view).get('left');
  assert.equal(during.x, 150 + anchor.w / 2); assert.equal(during.y, 250 + anchor.h / 2);
  delete globalThis.getComputedStyle;
  // Отсутствие Web Animations не препятствует самому раскрытию.
  for (const item of view.nodeElements.values()) item.group.animate = undefined;
  assert.doesNotThrow(() => animatePathTransition(view, initial));
}
assert.equal(JSON.stringify({ nodes, edges }), facts);
console.log('Compact visible paths, anchored expansion, animations and reduced motion PASS');
