/** Проверяет чистую геометрию интерфейса без браузера и npm-зависимостей. */
import assert from 'node:assert/strict';
import { communityGeometry, nodeGeometry } from '../web/layout.js';
import {groupColor,nodeColor} from '../web/colors.js';

const groups = [
  { id: 'billing', nodeCount: 120, x: 0, y: 0 },
  { id: 'identity', nodeCount: 50, x: 0, y: 0 },
  { id: 'external', nodeCount: 1, x: NaN, y: Infinity },
];
const before = JSON.stringify(groups);
const geometry = communityGeometry(groups);
assert.equal(geometry.size, groups.length);
assert.equal(JSON.stringify(groups), before);
assert.deepEqual(geometry, communityGeometry(groups));
assert.equal(geometry.get('billing').color, groupColor(groups[0]));
assert.ok(geometry.get('billing').r > geometry.get('identity').r);
for (const point of geometry.values()) {
  assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y));
  assert.ok(point.r > 0);
}
const first = geometry.get('billing'), second = geometry.get('identity');
assert.ok(Math.hypot(first.x - second.x, first.y - second.y) >= first.r + second.r);

const nodes = [];
for (let index = 0; index < 240; index++) {
  nodes.push({ id: `node-${index}`, community: index < 120 ? 'billing' : 'identity', x: Math.cos(index) * .9, y: Math.sin(index) * .9 });
}
const originalNodes = JSON.stringify(nodes);
const details = nodeGeometry(nodes, groups);
assert.equal(details.size, nodes.length);
assert.equal(JSON.stringify(nodes), originalNodes);
assert.deepEqual(details, nodeGeometry(nodes, groups));
for (const node of nodes) {
  const point = details.get(node.id);
  assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y));
  assert.equal(point.community, node.community);
  assert.equal(point.color, nodeColor(node));
}
assert.equal(communityGeometry([]).size, 0);
assert.equal(nodeGeometry([]).size, 0);
console.log('Layout geometry PASS');
