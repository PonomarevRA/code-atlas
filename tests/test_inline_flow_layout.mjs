import assert from 'node:assert/strict';
import { inlineFlowLayout } from '../web/inline-flow-layout.js';
import { implementationEdgeRouter } from '../web/implementation-edge-routing.js';
import { implementationFlowLayout } from '../web/implementation-flow-layout.js';

const root = { id: 'root', expanded: true, children: ['a', 'b', 'c'] };
const items = [root, { id: 'a', children: [] }, { id: 'b', children: [] }, { id: 'c', children: [] }, { id: 'neighbor', children: [] }];
const scene = { roots: ['root', 'neighbor'], items, edges: [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }] };
const baseline = new Map([['root', { x: 0, y: 0, width: 260, height: 124 }], ['neighbor', { x: 356, y: 0, width: 260, height: 124 }]]);
const before = JSON.stringify({ scene, baseline: [...baseline] });
const layout = inlineFlowLayout(scene, baseline);
assert.ok(layout.positions.get('a').x < layout.positions.get('b').x);
assert.ok(layout.positions.get('b').x < layout.positions.get('c').x, 'Children follow actual directed ranks');
assert.deepEqual(layout.positions.get('root').headerBounds, baseline.get('root'));
assert.ok(layout.positions.get('neighbor').headerBounds.x >= layout.positions.get('root').cardBounds.width);
assert.equal(layout.positions.get('neighbor').headerBounds.y, 0, 'Only original horizontal separation changes');
for (const id of root.children) {
  const header = layout.positions.get(id).headerBounds, frame = layout.positions.get('root').cardBounds;
  assert.ok(header.x >= frame.x && header.x + header.width <= frame.x + frame.width);
  assert.ok(header.y >= frame.y && header.y + header.height <= frame.y + frame.height);
  assert.equal(header.width, 260); assert.equal(header.height, 154, 'Child methods use the entry-path card height');
}
assert.deepEqual(inlineFlowLayout(scene, baseline), layout);
assert.equal(JSON.stringify({ scene, baseline: [...baseline] }), before);
const collapsed = inlineFlowLayout({ ...scene, items: [{ ...root, expanded: false }, ...items.slice(1)] }, baseline);
assert.equal(collapsed.positions.size, 2);
assert.deepEqual(collapsed.positions.get('neighbor').headerBounds, baseline.get('neighbor'));
assert.equal(inlineFlowLayout(scene, new Map([['root', baseline.get('root')]])).positions.size, 5, 'Partial baseline cannot lose a root');
const cyclic = inlineFlowLayout({ ...scene, edges: [...scene.edges, { source: 'c', target: 'a' }] }, baseline);
assert.ok([...cyclic.positions.values()].every(point => Number.isFinite(point.x) && Number.isFinite(point.y)));

const tree = [{ id: 'method', w: 260, h: 154, children: [{ id: 'if', decision: true, w: 260, h: 124, children: [
  { id: 'called', w: 260, h: 154, children: [] },
] }] }];
const path = implementationFlowLayout(tree, { direction: 'flow' });
const byId = new Map(path.placed.map(item => [item.id, item]));
assert.ok(byId.get('if').x > byId.get('method').x, 'Control moves to the right inside a bounded method panel');
const ref = path.placed.find(item => item.reference);
assert.equal(ref.focusId, 'called'); assert.equal(ref.w, 260); assert.equal(ref.h, 124);
for (const frame of path.frames) {
  const header = byId.get(frame.id);
  assert.ok(header.x - header.w / 2 >= frame.x && header.x + header.w / 2 <= frame.x + frame.w);
}
const adjacent = new Map([['a', { id: 'a', x: 130, y: 100, w: 260, h: 124, panelIndex: 1 }], ['b', { id: 'b', x: 486, y: 220, w: 260, h: 124, panelIndex: 1 }]]);
const routed = implementationEdgeRouter(adjacent, [{ panelIndex: 1, x: 0, y: 0, w: 620, h: 1200 }], [], { horizontal: true })(adjacent.get('a'), adjacent.get('b'));
const coordinates = routed.d.match(/-?\d+(?:\.\d+)?/g).map(Number);
assert.ok(coordinates.filter((_, index) => index % 2).every(y => y >= 100 && y <= 220), 'Adjacent calls do not detour under the whole method frame');
console.log('Inline Flow ranks, stable headers, cycles, containment and horizontal control frames PASS');
