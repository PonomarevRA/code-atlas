/** Flow реализаций сохраняет управление кодом, но имеет отдельную вертикальную геометрию. */
import assert from 'node:assert/strict';
import { implementationFlowLayout } from '../web/implementation-flow-layout.js';
import { buildMethodFrameTree, packMethodFrames } from '../web/path-row-layout.js';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';

/** Проверяет отсутствие пересечений карточек и ограничение ширины, а не конкретные координаты алгоритма. */
function assertCardsReadable(layout, maxWidth = 1000) {
  const cards = layout.placed;
  for (let left = 0; left < cards.length; left += 1) {
    for (let right = left + 1; right < cards.length; right += 1) {
      const a = cards[left], b = cards[right];
      assert.ok(Math.abs(a.x - b.x) >= (a.w + b.w) / 2 || Math.abs(a.y - b.y) >= (a.h + b.h) / 2, `${a.id} overlaps ${b.id}`);
    }
  }
  const minX = Math.min(...cards.map(card => card.x - card.w / 2));
  const maxX = Math.max(...cards.map(card => card.x + card.w / 2));
  assert.ok(maxX - minX <= maxWidth);
}

const nodes = [
  { id: 'root', w: 280, h: 154 },
  { id: 'root::branch:10:0', decision: true, kind: 'if', w: 240, h: 96 },
  { id: 'left', w: 280, h: 154 },
  { id: 'right', w: 280, h: 154 },
  { id: 'left::branch:20:0', decision: true, kind: 'foreach', w: 240, h: 96 },
  { id: 'apply', w: 280, h: 154 },
  { id: 'left::exit:30:0', exit: true, w: 230, h: 66 },
];
const edges = [
  { source: 'root', target: 'root::branch:10:0' },
  { source: 'root::branch:10:0', target: 'left', caption: 'истина' },
  { source: 'root::branch:10:0', target: 'right', caption: 'иначе' },
  { source: 'left', target: 'left::branch:20:0' },
  { source: 'left::branch:20:0', target: 'apply', caption: 'тело цикла' },
  { source: 'left', target: 'left::exit:30:0' },
  { source: 'right', target: 'apply' },
  { source: 'apply', target: 'root' },
];
const tree = buildMethodFrameTree(nodes, edges, nodes.map(node => node.id));
const before = JSON.stringify(tree);
const flow = implementationFlowLayout(tree);
assertCardsReadable(flow);
assert.equal(JSON.stringify(tree), before);
assert.deepEqual(implementationFlowLayout(tree), flow, 'Layout is deterministic');
assert.ok(flow.placed.some(card => card.reference && card.focusId === 'root'), 'Recursion stays a local reference');
assert.ok(flow.placed.some(card => card.reference && card.focusId === 'apply'), 'Shared method stays a local reference');
assert.equal(flow.placed.filter(card => card.id === 'apply').length, 1);
const byId = new Map(flow.placed.map(card => [card.id, card]));
assert.ok(byId.get('root::branch:10:0').y - 48 > byId.get('root').y + 77, 'Body lies below method header');
assert.ok(byId.get('right').panelIndex > byId.get('left').panelIndex, 'Original tree preorder is retained across panels');
const localLeft = flow.placed.find(card => card.reference && card.focusId === 'left');
assert.ok(localLeft.id.startsWith('left::ref:root::branch:10:0:'), 'Detached implementation keeps a short local call reference');
assert.equal(localLeft.targetPanelIndex, byId.get('left').panelIndex);
for (const frame of flow.frames) {
  const header = byId.get(frame.id);
  assert.ok(header.x - header.w / 2 >= frame.x - .001);
  assert.ok(header.x + header.w / 2 <= frame.x + frame.w + .001);
  assert.ok(header.y - header.h / 2 >= frame.y - .001);
  assert.ok(header.y + header.h / 2 <= frame.y + frame.h + .001);
}
assert.notDeepEqual(flow.placed, packMethodFrames(tree).placed, 'Flow is a separate layout, not a style alias');

// Раскрытие глубокой цепочки не прибавляет горизонтальный отступ на каждом уровне.
let deep = { id: 'last', w: 280, h: 154, children: [] };
for (let index = 0; index < 80; index += 1) deep = { id: `level-${index}`, w: 280, h: 154, children: [deep] };
const deepLayout = implementationFlowLayout([deep]);
assertCardsReadable(deepLayout, 6000);
assert.ok(deepLayout.frames.every(frame => frame.w <= 304));
assert.ok(new Set(deepLayout.placed.filter(item => !item.reference).map(item => item.x)).size > 1, 'Long chains wrap across method panels');
const deepHeight = Math.max(...deepLayout.frames.map(frame => frame.y + frame.h)) - 80;
assert.ok(deepHeight < 6000, 'Eighty methods avoid an unbroken vertical column');
const constrained = implementationFlowLayout([deep], { maxWidth: 1000 });
assertCardsReadable(constrained);

/** Проверяет отдельные panels и lexical ownership, включая листья без controls. */
function assertMethodPanels(layout, tree) {
  const frames = new Map(layout.frames.filter(frame => !frame.decision).map(frame => [frame.id, frame]));
  const owners = new Map();
  const visit = (node, owner) => {
    if (!node.decision && !node.exit && !node.reference) owner = node.id;
    owners.set(node.id, owner);
    for (const child of node.children || []) visit(child, owner);
  };
  for (const root of tree) visit(root, root.id);
  const panels = [...frames.values()];
  for (let left = 0; left < panels.length; left += 1) {
    for (let right = left + 1; right < panels.length; right += 1) {
      const a = panels[left], b = panels[right];
      assert.ok(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y,
        `${a.id} panel overlaps ${b.id}`);
    }
  }
  for (const card of layout.placed) {
    const owner = owners.get(card.id);
    if (!owner) continue; // New detached-call references are verified through their source panel below.
    const frame = frames.get(owner);
    assert.ok(frame, `Missing method frame ${owner}`);
    assert.ok(card.x - card.w / 2 >= frame.x && card.x + card.w / 2 <= frame.x + frame.w);
    assert.ok(card.y - card.h / 2 >= frame.y && card.y + card.h / 2 <= frame.y + frame.h);
  }
  for (const card of layout.placed.filter(item => item.reference)) {
    const frame = panels.find(item => item.panelIndex === card.panelIndex);
    assert.ok(card.x - card.w / 2 >= frame.x && card.x + card.w / 2 <= frame.x + frame.w);
    assert.ok(card.y - card.h / 2 >= frame.y && card.y + card.h / 2 <= frame.y + frame.h);
  }
}
assertMethodPanels(flow, tree);
assertMethodPanels(deepLayout, [deep]);

// 386 связанных реализаций — synthetic regression реального масштаба, без зависимости от /tmp fixtures.
let full = { id: 'method-385', w: 280, h: 154, children: [
  { id: 'cycle-ref', focusId: 'method-0', reference: true, w: 200, h: 58, children: [] },
] };
for (let index = 384; index >= 0; index -= 1) {
  full = { id: `method-${index}`, w: 280, h: 154, children: [
    { id: `method-${index}::branch:1:0`, decision: true, kind: index % 2 ? 'if' : 'foreach', w: 240, h: 96, children: [full] },
  ] };
}
const fullBefore = JSON.stringify(full);
const allMethods = implementationFlowLayout([full]);
assertCardsReadable(allMethods, 6000);
assertMethodPanels(allMethods, [full]);
assert.equal(allMethods.frames.filter(frame => !frame.decision).length, 386);
assert.equal(allMethods.placed.filter(card => !card.reference && card.id.includes('::branch:')).length, 385);
assert.equal(allMethods.placed.filter(card => card.reference).length, 386);
assert.equal(allMethods.placed.find(card => card.id === 'cycle-ref').focusId, 'method-0');
assert.deepEqual(allMethods.placed.filter(card => !card.reference && !card.id.includes('::branch:')).map(card => card.panelIndex),
  Array.from({ length: 386 }, (_, index) => index + 1));
const fullHeight = Math.max(...allMethods.frames.map(frame => frame.y + frame.h)) - 80;
assert.ok(fullHeight < 20000, `Large chain remains compact: ${fullHeight}`);
assert.deepEqual(implementationFlowLayout([full]), allMethods);
assert.equal(JSON.stringify(full), fullBefore);
const oversized = implementationFlowLayout([{ id: 'wide', w: 1200, h: 100, children: [] }]);
assert.equal(oversized.placed[0].w, 1200, 'Oversized input is preserved, not clipped');
assert.deepEqual(implementationFlowLayout([]), { placed: [], frames: [] });

// Настоящий renderer сохраняет ветви, источники, scopes и камеру при переключении провайдера.
globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g'); viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const sources = [], selections = [];
const view = new GraphView(svg, id => selections.push(id), () => {});
view.pathRootId = 'root'; view.pathSnapshotId = 'snapshot';
view.onPathSource = (id, line) => sources.push({ id, line });
const rawNodes = [
  { id: 'root', label: 'Calculate', file: 'Domain/Calculator.cs', controlFlow: { branches: [
    { kind: 'if', line: 10, condition: 'item.Valid', arms: [{ id: 'valid', kind: 'true' }, { id: 'invalid', kind: 'false' }] },
    { kind: 'switch-expression', line: 40, selector: 'product.Type', arms: [{ id: 'total', kind: 'case', label: 'Total' }] },
  ], terminals: [{ kind: 'return', line: 60, text: 'return result;', branchIds: [] }] } },
  { id: 'left', label: 'Build', file: 'Domain/Builder.cs', controlFlow: { branches: [
    { kind: 'foreach', line: 20, condition: '', header: 'foreach (var item in items)', collection: 'items', arms: [{ id: 'body', kind: 'body' }] },
  ] } },
  { id: 'right', label: 'Build', file: 'Domain/Other.cs' },
  { id: 'apply', label: 'Apply', file: 'Domain/Apply.cs' },
];
const rawEdges = [
  { source: 'root', target: 'left', relation: 'calls', sourceLine: 11, confidence: 'graph', branchIds: ['valid'] },
  { source: 'root', target: 'right', relation: 'calls', sourceLine: 12, confidence: 'candidate', branchIds: ['invalid'] },
  { source: 'left', target: 'apply', relation: 'calls', sourceLine: 21, confidence: 'graph', branchIds: ['body'] },
];
const rawBefore = JSON.stringify({ rawNodes, rawEdges });
view.setDisplayProvider('flow'); view.renderPathCards(rawNodes, rawEdges, 'left');
assert.equal(view.pathLayoutProvider, 'flow');
assert.ok(viewport.querySelectorAll('.implementation-flow-card').length > 0);
assert.equal(viewport.querySelectorAll('polygon').length, 0);
assert.deepEqual(viewport.querySelectorAll('.implementation-flow-decision').map(group => group.attributes['data-control-kind']).sort(), ['if', 'switch']);
const rootCard = view.nodeElements.get('root').group;
assert.equal(rootCard.querySelectorAll('.implementation-flow-port').length, 2);
assert.ok(rootCard.querySelector('.implementation-flow-role'));
const branch = view.nodeElements.get('root::branch:10:0').group;
branch.listeners.click({ stopPropagation() {} });
assert.deepEqual(sources.at(-1), { id: 'root', line: 10 });
const rootExit = view.nodeElements.get('root::exit:60:0').group;
assert.equal(rootExit.querySelectorAll('.implementation-flow-port').length, 1);
assert.ok(viewport.querySelectorAll('.implementation-flow-edge').some(path => path.children[0].textContent.includes('Строка вызова: 12')));
view.camera = { x: 15, y: -20, scale: .85 };
const previous = view.positions.get('left');
const anchor = { x: previous.x * .85 + 15, y: previous.y * .85 - 20 };
view.togglePathNode('left');
assert.ok(view.pathExpandedIds.has('left'));
assert.ok(view.nodeElements.has('left::branch:20:0'));
assert.ok(view.nodeElements.get('left::branch:20:0').group.querySelectorAll('title').some(title => title.textContent.includes('foreach (var item in items)')), 'Loop shows its extracted lexical header instead of an invented boolean condition');
assert.ok(view.nodeElements.has('apply'));
const current = view.positions.get('left');
assert.ok(Math.abs(current.x * view.camera.scale + view.camera.x - anchor.x) < .001);
assert.ok(Math.abs(current.y * view.camera.scale + view.camera.y - anchor.y) < .001);
assert.equal(view.selectedId, 'left');
assert.deepEqual(selections, []);
const camera = { ...view.camera };
view.setDisplayProvider('topology'); view.renderPathCards(rawNodes, rawEdges, 'left');
assert.equal(view.pathLayoutProvider, 'topology');
assert.ok(viewport.querySelectorAll('polygon').length > 0);
assert.equal(viewport.querySelectorAll('.implementation-flow-card').length, 0);
assert.ok(view.pathExpandedIds.has('left'));
assert.ok(view.nodeElements.has('apply'));
assert.deepEqual(view.camera, camera);
view.setDisplayProvider('flow'); view.renderPathCards(rawNodes, rawEdges, 'left');
assert.ok(view.nodeElements.has('left::branch:20:0'));
assert.deepEqual(view.camera, camera);
assert.equal(JSON.stringify({ rawNodes, rawEdges }), rawBefore);
const manyControls = [{ id: 'many', label: 'Many', controlFlow: { branches: Array.from({ length: 16 }, (_, index) => ({ kind: 'if', condition: `value > ${index}`, line: index + 1, arms: [] })) } }];
view.pathRootId = 'many';
view.renderPathCards(manyControls, [], 'many');
assert.equal(viewport.querySelectorAll('.implementation-flow-decision').length, 16, 'Flow preserves controls beyond the legacy twelve-branch overview');
view.setDisplayProvider('topology'); view.renderPathCards(manyControls, [], 'many');
assert.equal(viewport.querySelectorAll('.path-decision').length, 12);
assert.equal(viewport.querySelectorAll('.path-omission').length, 1, 'Topology keeps its explicit compact-view omission');
console.log('Implementation Flow branching, loops, cycles, bounds, sources, scopes and provider switch PASS');
