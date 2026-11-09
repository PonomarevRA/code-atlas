import assert from 'node:assert/strict';
import { InlineHierarchyRenderer } from '../web/inline-hierarchy-renderer.js';
import { GraphView } from '../web/graph.js';
import { nodeColor } from '../web/colors.js';
import { buildInlineHierarchy, inlineFileId } from '../web/inline-hierarchy.js';
import { inlineHierarchyLayout } from '../web/inline-hierarchy-layout.js';
import { Svg } from './svg_fixture.mjs';
import { curveCrossesCards } from '../web/inline-flow-edges.js';
import { RectangleIndex, cardRectangle } from '../web/rectangle-index.js';

class FocusSvg extends Svg {
  getAttribute(name) { return this.attributes[name] ?? null; }
  focus(options) { globalThis.document.activeElement = this; this.focusOptions = options; }
  contains(target) { for (let node = target; node; node = node.parent) if (node === this) return true; return false; }
  set innerHTML(value) { throw new Error('Unsafe HTML'); }
}
globalThis.document = { createElementNS: (_, tag) => new FocusSvg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new FocusSvg('svg'), viewport = new FocusSvg('g');
viewport.setAttribute('id', 'viewport');
svg.append(viewport);
const selected = [], entered = [], toggled = [];
const view = new GraphView(svg, id => selected.push(id), () => {});
view.onBlockSelect = id => selected.push(id);
view.onEnterBlock = id => entered.push(id);
view.onToggleBlock = id => toggled.push(id);
view.colorProjects = [{ id: 'p', label: 'Project' }];
view.camera = { x: 35, y: 89, scale: .83 };
view.transform();
const camera = { ...view.camera }, transform = viewport.attributes.transform;
const scene = {
  items: [
    { id: 'project:p', rawId: null, kind: 'project', label: 'Project', node: { codeProjectId: 'p' },
      parentId: null, expanded: true, totalChildren: 1, visibleChildren: 1, totalMembers: 3, memberIds: ['a', 'b', 'hidden-method-attribute'], enter: { kind: 'project' } },
    { id: 'a', rawId: 'a', kind: 'raw', label: '<script>Calculate()</script>', node: { id: 'a', label: '.Calculate()', file: 'Domain/Calc.cs', codeProjectId: 'p' },
      parentId: 'project:p', expanded: false, totalChildren: 1, visibleChildren: 0, totalMembers: 2, memberIds: ['a', 'hidden-method-attribute'], enter: { kind: 'raw' } },
    { id: 'b', rawId: 'b', kind: 'raw', label: 'Calculate()', node: { id: 'b', label: '.Calculate()', file: 'Contracts/Calc.cs', codeProjectId: 'p' },
      parentId: null, expanded: false, totalChildren: 0, visibleChildren: 0, totalMembers: 1, memberIds: ['b'], enter: { kind: 'raw' } },
  ],
  membership: new Map([['hidden-method-attribute', 'a']]),
  edges: [{ source: 'a', target: 'b', relation: 'dispatches_to', weight: 2.5, confidence: 'INFERRED', evidence: { file: 'Calc.cs', line: 37 } },
    { source: 'b', target: 'a', relation: 'references', weight: 1 }],
};
const parentBox = { x: 0, y: 0, width: 400, height: 270 };
const aBox = { x: 20, y: 132, width: 320, height: 112 }, bBox = { x: 460, y: 0, width: 320, height: 112 };
const layout = { positions: new Map([
  ['project:p', { x: 200, y: 56, width: 400, height: 270, headerBounds: { x: 0, y: 0, width: 400, height: 112 }, cardBounds: parentBox }],
  ['a', { x: 180, y: 188, width: 320, height: 112, headerBounds: aBox, cardBounds: aBox }],
  ['b', { x: 620, y: 56, width: 320, height: 112, headerBounds: bBox, cardBounds: bBox }],
]), frames: [{ id: 'project:p', parentId: null, depth: 0, cardBounds: parentBox }], bounds: { x: 0, y: 0, width: 780, height: 270 } };
const original = JSON.stringify({ scene, positions: [...layout.positions], frames: layout.frames });
const renderer = new InlineHierarchyRenderer(view);
view.pathSceneActive = true;
renderer.render(scene, layout);
assert.equal(view.pathSceneActive, false, 'Entering hierarchy after execution-path Back clears the previous rendering mode');
assert.ok([...view.blockElements.values()].every(card => card.group.attributes['aria-pressed'] === 'false'), 'Empty selection must not select every synthetic null-rawId container');
renderer.render(scene, layout, 'a');
assert.equal(view.blockElements.size, 3);
assert.equal(view.nodeElements.size, 2);
assert.equal(view.communityElements.size, 1);
assert.equal(viewport.querySelectorAll('.inline-hierarchy-frame').length, 1);
assert.equal(viewport.querySelectorAll('.inline-hierarchy-card').length, 3);
assert.equal(viewport.querySelectorAll('.inline-hierarchy-edge').length, 2, 'Hierarchy must not fabricate calls');
const a = view.nodeElements.get('a').group;
assert.ok(a.querySelector('title').textContent.includes('<script>Calculate()</script>'));
assert.equal(a.querySelectorAll('script').length, 0);
assert.equal(a.attributes['data-code-role'], 'logic');
assert.equal(a.querySelector('.inline-hierarchy-card-body').attributes.stroke, nodeColor(scene.items[1].node, view.colorProjects));
assert.equal(a.attributes['aria-pressed'], 'true');
renderer.setSelected('hidden-method-attribute');
assert.equal(renderer.selectedId, 'hidden-method-attribute', 'The actual selected raw ID must not become its visible owner');
assert.equal(a.attributes['aria-pressed'], 'true');
assert.equal(view.blockElements.get('project:p').group.attributes['aria-pressed'], 'false', 'Only the deepest visible owner is highlighted');
assert.equal(view.nodeElements.has('hidden-method-attribute'), false, 'Selection must not reveal a hidden attribute');
assert.equal(view.blockElements.size, 3);
assert.equal(viewport.querySelector('.inline-hierarchy-edge').classList.contains('active'), true);
assert.deepEqual(view.camera, camera);
assert.equal(viewport.attributes.transform, transform);
renderer.setSelected(null);
assert.ok([...view.blockElements.values()].every(card => card.group.attributes['aria-pressed'] === 'false'));
renderer.setSelected('a');
a.listeners.click();
assert.deepEqual(selected, ['a']);
const action = renderer.cards.get('project:p').actions.toggle;
action.listeners.keydown({ key: ' ', preventDefault() {}, stopPropagation() {} });
assert.deepEqual(toggled, ['project:p']);
renderer.cards.get('a').actions.enter.listeners.click({ preventDefault() {}, stopPropagation() {} });
assert.deepEqual(entered, ['a']);
assert.deepEqual(selected, ['a']);
assert.equal(renderer.setSearchMatches(['a']), 1, 'Nested membership counts a raw match only once');
assert.equal(view.blockElements.get('project:p').group.attributes['data-match-count'], 1);
assert.equal(a.attributes['data-match-count'], 1);
const paths = viewport.querySelectorAll('.inline-hierarchy-edge');
assert.equal(paths[0].attributes['stroke-dasharray'], '5 4');
assert.equal(paths[0].attributes['data-weight'], 2.5);
assert.equal(paths[0].attributes['data-confidence'], 'INFERRED');
assert.equal(paths[0].attributes['data-relation'], 'dispatches_to');
assert.ok(paths[0].children[0].textContent.includes('Статическая связь'));
assert.ok(paths[0].attributes.d.startsWith('M 340 188'), 'Source OUT port matches the ordinary Flow right boundary');
assert.ok(paths[0].attributes.d.endsWith('460 56'), 'Target IN port matches the ordinary Flow left boundary');
const bounds = view.geometryBounds();
assert.ok(bounds.maxX >= 780, 'Fit includes headers and their internal enter controls');
assert.ok(Object.values(bounds).every(Number.isFinite));
action.focus();
renderer.render(scene, layout, 'a');
assert.equal(document.activeElement.attributes['data-node-action'], 'inline');
assert.equal(document.activeElement.attributes['data-owner-id'], 'project:p');
assert.deepEqual(document.activeElement.focusOptions, { preventScroll: true });
assert.deepEqual(view.camera, camera);
assert.equal(viewport.attributes.transform, transform);
assert.equal(JSON.stringify({ scene, positions: [...layout.positions], frames: layout.frames }), original);
const signatures = () => viewport.querySelectorAll('.inline-hierarchy-card').map(card => [card.attributes['data-node-id'], card.attributes.transform]);
const beforeProvider = signatures();
view.displayProvider = 'flow';
renderer.render(scene, layout, 'a');
assert.deepEqual(signatures(), beforeProvider, 'The dedicated hierarchy has identical node actions in both providers');
renderer.setSelected('project:p');
assert.equal(view.blockElements.get('project:p').group.attributes['aria-pressed'], 'true');
assert.equal(view.nodeElements.get('a').group.attributes['aria-pressed'], 'false');
assert.deepEqual(view.camera, camera);

// Exercise the actual projection and layout seam, including nested source ownership.
const sourceGraph = {
  nodes: [{ id: 'type', label: 'Calculator', kind: 'class', codeProjectId: 'p', file: 'A.cs' },
    { id: 'method', label: '.Calculate()', kind: 'method', codeProjectId: 'p', file: 'A.cs' },
    { id: 'other', label: '.Load()', kind: 'method', codeProjectId: 'p', file: 'B.cs' }],
  edges: [{ source: 'type', target: 'method', relation: 'method' },
    { source: 'method', target: 'other', relation: 'calls' }],
};
const sourceBefore = JSON.stringify(sourceGraph);
const sourceScene = buildInlineHierarchy(sourceGraph, {
  roots: [{ id: 'project:p', kind: 'project', label: 'Project', memberIds: sourceGraph.nodes.map(node => node.id), node: { codeProjectId: 'p' } }],
  expandedIds: new Set(['project:p', inlineFileId('project:p', 'p', 'A.cs'), inlineFileId('project:p', 'p', 'B.cs'), 'type']),
});
const sourceLayout = inlineHierarchyLayout(sourceScene);
renderer.render(sourceScene, sourceLayout, 'method');
assert.equal(view.blockElements.size, sourceScene.items.length);
assert.equal(view.nodeElements.size, 3);
assert.equal(viewport.querySelectorAll('.inline-hierarchy-edge').length, sourceScene.edges.length);
for (const point of sourceLayout.positions.values()) {
  const action = renderer.cards.get(point.id).actions.enter;
  if (!action) continue;
  const box = { x: point.headerBounds.x + point.headerBounds.width, y: point.headerBounds.y + 6, width: 24, height: 24 };
  for (const [otherId, otherPoint] of sourceLayout.positions) {
    if (otherId === point.id) continue;
    const other = otherPoint.headerBounds;
    const overlap = box.x < other.x + other.width && other.x < box.x + box.width
      && box.y < other.y + other.height && other.y < box.y + box.height;
    assert.equal(overlap, false, `Enter control must not overlap another header: ${point.id} / ${otherId}`);
  }
}
assert.equal(JSON.stringify(sourceGraph), sourceBefore);
assert.deepEqual(view.camera, camera);

// Skip-over siblings and nested/external edges must travel through gutters, never another header.
const routeGraph = {
  nodes: [{ id: 'owner', label: 'Owner', kind: 'class', file: 'Many.cs' },
    ...['a', 'b', 'c'].map(id => ({ id, label: `.${id}()`, kind: 'method', file: 'Many.cs' })),
    { id: 'external', label: '.External()', kind: 'method', file: 'Other.cs' }],
  edges: [...['a', 'b', 'c'].map(target => ({ source: 'owner', target, relation: 'method' })),
    { source: 'a', target: 'c', relation: 'calls' }, { source: 'b', target: 'external', relation: 'calls' },
    { source: 'c', target: 'c', relation: 'references' }],
};
const routeScene = buildInlineHierarchy(routeGraph, {
  roots: ['owner', 'external'].map(id => ({ id, rawId: id, kind: 'raw' })),
  expandedIds: new Set(['owner']), preset: 'all',
});
const routeLayout = inlineHierarchyLayout(routeScene);
renderer.render(routeScene, routeLayout, 'a');
for (const path of viewport.querySelectorAll('.inline-hierarchy-edge')) {
  const numbers = path.attributes.d.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi)?.map(Number) || [];
  assert.ok(numbers.length >= 4, 'Every retained edge needs a visible route');
  for (const [id, point] of routeLayout.positions) {
    if ([path.attributes['data-source'], path.attributes['data-target']].includes(id)) continue;
    const box = point.headerBounds;
    // Cubic control points are not polyline vertices: inspect the actual curve.
    const cards = new RectangleIndex([{ x: box.x + box.width / 2, y: box.y + box.height / 2, w: box.width, h: box.height }], cardRectangle);
    assert.equal(curveCrossesCards(path.attributes.d, cards), false,
      `${path.attributes['data-source']}→${path.attributes['data-target']} crossed header ${id}`);
  }
}
assert.deepEqual(view.camera, camera);

// A focused action remains inside the map keyboard scope; only its activation stops bubbling.
const keyTarget = renderer.cards.get('a').actions.enter;
const selectionCount = selected.length;
const enterCount = entered.length;
function bubbleKey(key, modifiers = {}) {
  let stopped = false;
  const event = { key, target: keyTarget, defaultPrevented: false, ...modifiers,
    stopPropagation() { stopped = true; }, preventDefault() { this.defaultPrevented = true; } };
  for (let node = keyTarget; node && !stopped; node = node.parent) node.listeners.keydown?.(event);
  return event;
}
assert.equal(bubbleKey('ArrowRight').defaultPrevented, true);
assert.equal(view.camera.x, camera.x - 64);
assert.equal(view.camera.scale, camera.scale);
assert.equal(bubbleKey('ArrowRight', { shiftKey: true }).defaultPrevented, true);
assert.equal(view.camera.x, camera.x - 256);
const panned = { ...view.camera };
assert.equal(bubbleKey('ArrowLeft', { altKey: true }).defaultPrevented, false);
assert.deepEqual(view.camera, panned, 'Alt+Left remains browser navigation');
assert.equal(bubbleKey('Enter').defaultPrevented, true);
assert.equal(entered.length, enterCount + 1);
assert.equal(selected.length, selectionCount, 'Action activation and panning must not select another node');
console.log('Inline hierarchy actual GraphView identity, framing, callbacks and camera PASS');
