/** Реальные действия GraphView: локальные вызовы, сравнение реализаций и текст исходных комментариев. */
import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';
import { assertClear } from './curve_geometry.mjs';
import { cardDocumentation } from '../web/card-documentation.js';
import { projectExecutionPath } from '../web/path-graph-projection.js';
import { appendFlowCard } from '../web/flow-card.js';

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };

/** Проверяет карточки вместе с резервами пустых ветвей и исходными маршрутами. */
function clearGeometry(view, viewport) {
  const points = [...view.positions.values(), ...view.entryPathLayout.positions.stubPositions.values()];
  for (const a of points) for (const b of points) {
    if (a === b) continue;
    assert.ok(Math.abs(a.x - b.x) >= (a.w + b.w) / 2 || Math.abs(a.y - b.y) >= (a.h + b.h) / 2);
  }
  for (const path of viewport.querySelectorAll('.path-edge')) assertClear(path.attributes.d, [...view.positions.values()]);
}

const nodes = [
  { id: 'root', label: 'Root' }, { id: 'contract', label: 'Load' },
  { id: 'a', label: 'Load', controlFlow: { branches: [{ kind: 'if', line: 2, arms: [{ id: 'yes', kind: 'true' }, { id: 'no', kind: 'false' }] }] } },
  { id: 'b', label: 'Load', documentation: { summary: 'Читает стоимость продуктов по договору.' },
    typeContext: { name: 'ProductSource', documentation: { summary: 'Получает стоимость и доход из одного чтения договоров.' } } },
  { id: 'c', label: 'Load' }, { id: 'first', label: 'First' }, { id: 'second', label: 'Second' },
  { id: 'far', label: 'Far' },
];
const edges = [
  { source: 'root', target: 'contract', relation: 'calls', sourceLine: 1 },
  { source: 'root', target: 'far', relation: 'calls', sourceLine: 2 },
  ...['a', 'b', 'c'].map(target => ({ source: 'contract', target, relation: 'dispatches_to', confidence: 'candidate' })),
  { source: 'b', target: 'first', relation: 'calls', sourceLine: 4 },
  { source: 'b', target: 'second', relation: 'calls', sourceLine: 5 },
  { source: 'first', target: 'b', relation: 'calls', sourceLine: 6 },
];
const raw = JSON.stringify({ nodes, edges });
const pinned = projectExecutionPath({ rootNodeId: 'root', steps: nodes.map(node => ({ ...node, nodeId: node.id })), edges },
  new Map(nodes.map(node => [node.id, { id: node.id, label: node.label }])));
assert.deepEqual(pinned.nodes.find(node => node.id === 'b').documentation, nodes[3].documentation);
assert.deepEqual(pinned.nodes.find(node => node.id === 'b').typeContext, nodes[3].typeContext);
for (const provider of ['natural', 'graphistry']) {
  const svg = new Svg('svg'), viewport = new Svg('g'); viewport.setAttribute('id', 'viewport'); svg.append(viewport);
  const view = new GraphView(svg, () => {}, () => {});
  view.setDisplayProvider('flow'); view.pathReading = true; view.groupingProvider = provider; view.pathRootId = 'root';
  view.renderPathCards(pinned.nodes, pinned.edges, 'b'); view.togglePathNode('contract'); view.togglePathNode('a');
  const before = new Map(view.positions), anchor = before.get('b');
  const text = view.nodeElements.get('b').group;
  assert.ok(text.querySelector('.flow-card-role').textContent.includes('реализация'));
  assert.ok(text.querySelectorAll('.flow-card-description').some(item => item.textContent.startsWith('Читает')));
  assert.ok(text.querySelectorAll('.flow-card-description').some(item => item.textContent.startsWith('О классе:')));
  assert.equal(text.querySelector('.flow-card-context').textContent, 'ProductSource');
  assert.ok(view.positions.get('b').h > view.positions.get('c').h);
  for (const item of text.querySelectorAll('.flow-card-description')) {
    assert.ok(item.attributes.y < text.querySelector('.path-card-expand').attributes.y - 12);
  }
  view.camera = { x: 20, y: 30, scale: .75 };
  view.togglePathNode('b');
  const owner = view.positions.get('b'), first = view.positions.get('first'), second = view.positions.get('second');
  assert.equal(first.x - first.w / 2 - owner.x - owner.w / 2, 48, 'direct call unfolds immediately opposite the clicked card');
  assert.equal(first.y, owner.y, 'the first direct call is in the clicked lane');
  assert.equal(first.x, second.x); assert.ok(second.y > first.y, 'parallel calls fan into neighbouring lanes');
  assert.equal(owner.x * .75 + view.camera.x, anchor.x * .75 + 20);
  assert.equal(owner.y * .75 + view.camera.y, anchor.y * .75 + 30);
  assert.equal(view.positions.get('a').x, owner.x); assert.equal(view.positions.get('c').x, owner.x);
  clearGeometry(view, viewport);
  const geometry = new Map(view.positions);
  view.setPathControlFilters({ conditions: false, loops: false });
  for (const [id, point] of view.positions) assert.deepEqual(point, geometry.get(id));
  view.setPathControlFilters({ conditions: true, loops: true });
  view.togglePathNode('b'); assert.deepEqual(view.positions, before, 'collapse restores this exact neighbourhood');
  assert.equal(view.positions.get('far').x, before.get('far').x);
  const withReturn = pinned.nodes.map(node => node.id === 'b' ? { ...node,
    controlFlow: { terminals: [{ kind: 'return', line: 9, text: 'return result;' }] } } : node);
  view.pathSnapshotId = 'with-return'; view.renderPathCards(withReturn, pinned.edges, 'b');
  view.togglePathNode('contract'); view.togglePathNode('b');
  const returnOwner = view.positions.get('b'), called = view.positions.get('first');
  assert.equal(called.x - called.w / 2 - returnOwner.x - returnOwner.w / 2, 48,
    'a return occupies a separate lane without pushing the call into a distant column');
  clearGeometry(view, viewport);
  // Загруженная раскрытая схема ещё имеет общую геометрию; повторное нажатие должно перейти к локальной.
  view.pathSnapshotId = 'loaded'; view.renderPathCards(withReturn, pinned.edges, 'b');
  view.pathExpandedIds = new Set(['contract', 'b']); view.renderPathCards(withReturn, pinned.edges, 'b');
  view.togglePathNode('b'); view.togglePathNode('b');
  const loadedOwner = view.positions.get('b'), loadedCall = view.positions.get('first');
  assert.equal(loadedCall.x - loadedCall.w / 2 - loadedOwner.x - loadedOwner.w / 2, 48);
  clearGeometry(view, viewport);
}
assert.equal(JSON.stringify({ nodes, edges }), raw);
assert.equal(cardDocumentation({}).height, 0);
assert.equal(cardDocumentation({ documentation: { comments: [{ text: '// Читает договоры.' }] } }).text, 'Читает договоры.');
assert.equal(cardDocumentation({ documentation: { comments: [{ text: '// Значение < порога.' }] } }).text, 'Значение < порога.');
assert.equal(cardDocumentation({ documentation: { unsupported: true, comments: [{ text: '/// <broken>' }] } }).height, 0);
const long = cardDocumentation({ documentation: { summary: '<script>alert(1)</script> ' + 'Длинный комментарий '.repeat(80) } });
assert.equal(long.lines.length, 3); assert.ok(long.lines.at(-1).endsWith('…')); assert.ok(long.text.includes('<script>'));
const safeCard = new Svg('g');
appendFlowCard(safeCard, { width: 260, height: 124 + long.height, title: 'Read', documentation: long,
  showSemantics: false, palette: { fill: '#fff', text: '#111' }, color: '#111' });
assert.equal(safeCard.querySelectorAll('script').length, 0);
assert.ok(safeCard.querySelector('.flow-card-description').textContent.includes('<script>'));
console.log('Local neighbourhood expansion, vertical alternatives and source comment cards PASS');
