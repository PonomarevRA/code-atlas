/** Реальный renderer: раскрытие на месте, геометрия, клавиатура и исходные ID. */
import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg');
const viewport = new Svg('g');
viewport.setAttribute('id', 'viewport');
svg.append(viewport);
const selected = [];
const sources = [];
const notifications = [];
const view = new GraphView(svg, id => selected.push(id), () => {});
view.pathRootId = 'root';
view.onPathSource = (id, line) => sources.push({ id, line });
view.onPathScopeChange = info => notifications.push(info);

const nodes = [
  { id: 'root', label: '.Calculate()', file: 'src/RootController.cs', codeProjectId: 'p', controlFlow: {
    branches: [
      { kind: 'foreach', line: 10, condition: 'item in items', arms: [{ id: 'loop-body', kind: 'body' }] },
      { kind: 'if', line: 12, condition: 'item.Valid', parentArmIds: ['loop-body'], arms: [{ id: 'if-true', kind: 'true' }, { id: 'if-false', kind: 'false' }] },
      { kind: 'switch-expression', line: 20, selector: 'product.Kind', arms: [{ id: 'case-total', kind: 'case', label: 'Total' }] },
    ], terminals: [{ kind: 'return', line: 99, text: 'return result;', branchIds: [] }],
  } },
  { id: 'left', label: '.Build()', file: 'src/LeftBuilder.cs', codeProjectId: 'p', controlFlow: { branches: [{ kind: 'while', line: 30, condition: 'pending.Count > 0', arms: [{ id: 'left-body', kind: 'body' }] }] } },
  { id: 'right', label: '.Build()', file: 'src/RightBuilder.cs', codeProjectId: 'p' },
  { id: 'shared', label: '.Map()', file: 'src/SharedMapper.cs', codeProjectId: 'p' },
];
const edges = [
  { source: 'root', target: 'left', relation: 'calls', confidence: 'source', sourceLine: 13, branchIds: ['loop-body', 'if-true'] },
  { source: 'root', target: 'right', relation: 'calls', confidence: 'graph', sourceLine: 21, branchIds: ['case-total'] },
  { source: 'left', target: 'shared', relation: 'calls', confidence: 'source', sourceLine: 31, branchIds: ['left-body'] },
  { source: 'right', target: 'shared', relation: 'calls', confidence: 'graph', sourceLine: 41 },
  { source: 'shared', target: 'root', relation: 'calls', confidence: 'graph' },
];
const original = JSON.stringify({ nodes, edges });

/** Собирает видимые подписи без предположений о вложенности SVG-групп. */
function textOf(node) {
  let text = node.textContent || '';
  for (const child of node.children) text += ` ${textOf(child)}`;
  return text;
}

/** Проверяет настоящие прямоугольники карточек, включая ссылки и control-flow узлы. */
function assertReadableGeometry() {
  const cards = [...view.positions.values()];
  for (let left = 0; left < cards.length; left += 1) {
    for (let right = left + 1; right < cards.length; right += 1) {
      const a = cards[left];
      const b = cards[right];
      assert.ok(Math.abs(a.x - b.x) >= (a.w + b.w) / 2
        || Math.abs(a.y - b.y) >= (a.h + b.h) / 2, `${a.id} overlaps ${b.id}`);
    }
  }
  assert.ok(view.flowBounds.width <= 1100, `width ${view.flowBounds.width} exceeds compact budget`);
}

/** Сравнивает экранную позицию раскрываемой карточки до и после пересчёта геометрии. */
function screenPosition(id) {
  const point = view.positions.get(id);
  return { x: point.x * view.camera.scale + view.camera.x,
    y: point.y * view.camera.scale + view.camera.y, scale: view.camera.scale };
}

view.setDisplayProvider('flow');
view.renderPathCards(nodes, edges, 'root');
assert.ok(view.nodeElements.has('root'));
assert.ok(view.nodeElements.has('left'));
assert.ok(view.nodeElements.has('right'));
assert.ok(!view.nodeElements.has('shared'));
assert.equal(viewport.querySelectorAll('.path-decision').length, 3);
assert.ok(view.nodeElements.has('root::exit:99:0'), 'unconditional return is part of the method');
assert.ok(textOf(view.nodeElements.get('left').group).includes('LeftBuilder'));
assert.ok(textOf(view.nodeElements.get('right').group).includes('RightBuilder'));
assertReadableGeometry();
view.setSelected('left');
assert.ok(view.nodeElements.get('left').group.classList.contains('selected'), 'path selection must bypass an inactive flow renderer');
assert.equal(view.selectedId, 'left');

const loopFrame = viewport.querySelectorAll('.path-decision-frame').find(group => group.attributes['data-method-id'] === 'root::branch:10:0').children[0].attributes;
const nestedFrame = viewport.querySelectorAll('.path-decision-frame').find(group => group.attributes['data-method-id'] === 'root::branch:12:1').children[0].attributes;
assert.ok(nestedFrame.x >= loopFrame.x && nestedFrame.y >= loopFrame.y);
assert.ok(nestedFrame.x + nestedFrame.width <= loopFrame.x + loopFrame.width);
assert.ok(nestedFrame.y + nestedFrame.height <= loopFrame.y + loopFrame.height, 'nested if must stay inside its foreach');

const sourceButton = view.nodeElements.get('left').group.querySelector('.path-card-source');
sourceButton.listeners.click({ stopPropagation() {} });
assert.deepEqual(sources.at(-1), { id: 'left', line: undefined });
const innerIf = view.nodeElements.get('root::branch:12:1').group;
innerIf.listeners.click({ stopPropagation() {} });
assert.deepEqual(sources.at(-1), { id: 'root', line: 12 });

view.camera = { x: 40, y: 50, scale: 1.2 };
view.transform();
const anchor = screenPosition('left');
const expand = view.nodeElements.get('left').group.querySelector('.path-card-expand');
assert.ok(expand);
assert.equal(expand.attributes.tabindex, 0);
assert.equal(expand.attributes.role, 'button');
expand.listeners.keydown({ key: 'Enter', stopPropagation() {}, preventDefault() {} });
assert.ok(view.nodeElements.has('shared'));
assert.ok(view.nodeElements.has('root'));
assert.ok(view.nodeElements.has('right'));
assert.equal(viewport.querySelectorAll('.path-decision').length, 4);
const expandedAnchor = screenPosition('left');
assert.equal(expandedAnchor.scale, anchor.scale);
assert.ok(Math.abs(expandedAnchor.x - anchor.x) < 0.001);
assert.ok(Math.abs(expandedAnchor.y - anchor.y) < 0.001);
assertReadableGeometry();

view.togglePathNode('right');
view.togglePathNode('left');
assert.ok(view.nodeElements.has('shared'), 'shared target remains through another expanded owner');
view.togglePathNode('shared');
assert.ok(viewport.querySelectorAll('.path-reference').length > 0, 'cycle remains a finite reference');
view.collapseAllPathNodes();
assert.ok(!view.nodeElements.has('shared'));
assert.ok(view.nodeElements.has('root'));
assert.ok(notifications.length >= 5);
assert.equal(JSON.stringify({ nodes, edges }), original);

// Различаем статического кандидата реализации и подтверждённый вызов исходника.
view.pathRootId = 'api';
view.renderPathCards([
  { id: 'api', label: '.Calculate()', file: 'Api.cs' },
  { id: 'interface', label: '.Calculate()', file: 'ICalculator.cs' },
  { id: 'implementation', label: '.Calculate()', file: 'RealCalculator.cs' },
], [
  { source: 'api', target: 'interface', relation: 'calls', confidence: 'source', sourceLine: 4 },
  { source: 'interface', target: 'implementation', relation: 'dispatches_to', confidence: 'candidate' },
], 'api');
assert.ok(textOf(viewport).toLowerCase().includes('возможная реализация'));
assert.ok(textOf(viewport).includes('candidate'));
assert.ok(textOf(viewport).includes('Строка вызова: 4'));
assert.ok(textOf(view.nodeElements.get('implementation').group).includes('RealCalculator'));
console.log('Path cards inline expansion, camera anchors, geometry, source IDs and candidates PASS');
