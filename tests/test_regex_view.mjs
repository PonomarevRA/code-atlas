/** Проверяет реальные методы подсветки: меняются только оверлеи, не геометрия и связи. */
import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';

/** Минимальная реализация операций SVG, которыми пользуется метод оверлеев. */
class SvgElement {
  constructor(tag) { this.tag = tag; this.attributes = {}; this.children = []; }
  setAttribute(name, value) { this.attributes[name] = value; }
  append(child) { this.children.push(child); child.parent = this; }
  querySelector(selector) { return this.children.find(child => child.attributes.class === selector.slice(1)); }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
}
globalThis.document = { createElementNS: (_, tag) => new SvgElement(tag) };
const nodeGroup = new SvgElement('g');
nodeGroup.setAttribute('transform', 'translate(100,200)');
const existingCircle = new SvgElement('circle');
nodeGroup.append(existingCircle);
const group = new SvgElement('g');
const existingEdge = new SvgElement('line');
const classes = new Set();
const view = {
  nodeElements: new Map([['selected', { group: nodeGroup, radius: 20 }]]),
  communityElements: new Map(),
  positions: new Map([['selected', { x: 100, y: 200 }]]),
  camera: { x: 31, y: -72, scale: 1.3 },
  svg: { classList: { toggle(name, active) { if (active) classes.add(name); else classes.delete(name); } } },
  edges: [existingEdge],
};
const camera = { ...view.camera };
const positions = [...view.positions];
assert.equal(GraphView.prototype.setSearchMatches.call(view, ['selected', 'off-screen']), 1);
assert.ok(nodeGroup.querySelector('.regex-ring'));
assert.equal(nodeGroup.children[0], existingCircle);
assert.equal(nodeGroup.attributes.transform, 'translate(100,200)');
GraphView.prototype.setShowLabels.call(view, true);
assert.ok(classes.has('labels-visible'));
GraphView.prototype.setShowLabels.call(view, false);
assert.ok(!classes.has('labels-visible'));
assert.equal(GraphView.prototype.setSearchMatches.call(view, []), 0);
assert.equal(nodeGroup.children.length, 1);
view.nodeElements.clear();
view.communityElements.set('billing', { group, radius: 40 });
assert.equal(GraphView.prototype.setSearchMatches.call(view, ['a', 'b', 'c'], new Map([['billing', 2], ['not-rendered', 1]])), 2);
assert.equal(group.querySelector('.regex-count').textContent, '⌕ 2');
assert.deepEqual(view.camera, camera);
assert.deepEqual([...view.positions], positions);
assert.deepEqual(view.edges, [existingEdge]);
console.log('Regex overlays preserve geometry PASS');
