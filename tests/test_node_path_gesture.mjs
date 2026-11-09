import assert from 'node:assert/strict';
import { bindNodePathGesture, uniqueNodeEntry } from '../web/node-path-gesture.js';
import { appendSequenceRow } from '../web/method-sequence-renderer.js';
import { Svg } from './svg_fixture.mjs';

const listeners = new Map(), nodes = [], entries = [];
const svg = {
  setAttribute() {},
  contains: card => card.attached !== false,
  addEventListener: (type, callback, capture) => listeners.set(type, { callback, capture }),
  removeEventListener: (type, callback, capture) => {
    assert.deepEqual(listeners.get(type), { callback, capture }); listeners.delete(type);
  },
};
const timers = new Map(); let nextTimer = 0;
const controller = bindNodePathGesture(svg, { onNode: id => nodes.push(id), onEntry: id => entries.push(id),
  schedule: callback => { timers.set(++nextTimer, callback); return nextTimer; }, cancel: id => timers.delete(id) });
/** Model bubbling targets, including nested actions, without invoking their click callbacks. */
function gesture(dataset, { type = 'dblclick', nestedAction = false, ...keys } = {}) {
  const card = { dataset, ...keys };
  const event = { type, eventPhase: 2, ...keys, prevented: false, stopped: false,
    target: { closest: selector => selector.includes('.graph-node') ? card : nestedAction ? {} : card },
    preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; },
  };
  listeners.get(type).callback(event);
  return event;
}
gesture({ nodeId: 'method' });
gesture({ nodeId: 'occurrence', inspectionId: 'callee', rawId: 'owner' });
gesture({ nodeId: 'aggregate', rawId: 'actual-method' });
assert.deepEqual(nodes, ['method', 'callee', 'actual-method']);
let disclosures = 0;
const firstClick = gesture({ nodeId: 'single' }, { type: 'click', detail: 1 });
await Promise.resolve();
controller.revealSelection(() => disclosures++);
assert.equal(disclosures, 0, 'Only drawer disclosure is postponed; the module never intercepts selection');
assert.equal(timers.size, 1);
gesture({ nodeId: 'method' });
assert.equal(timers.size, 0, 'Double-click cancels the drawer before opening the path');
firstClick.eventPhase = 0;
controller.revealSelection(() => disclosures++);
assert.equal(disclosures, 1, 'Non-pointer selections disclose immediately');
gesture({ nodeId: 'single' }, { type: 'click', detail: 1 });
controller.revealSelection(() => disclosures++);
timers.values().next().value(); timers.clear();
assert.equal(disclosures, 2, 'An ordinary single click eventually opens its inspector');
assert.ok(listeners.get('keydown').capture, 'Shortcut precedes ordinary card Enter handlers');
for (const modifiers of [{ ctrlKey: true }, { metaKey: true }]) {
  const event = gesture({ nodeId: 'keyboard' }, { type: 'keydown', key: 'Enter', ...modifiers });
  assert.ok(event.prevented && event.stopped);
}
const count = nodes.length;
for (const options of [
  { nestedAction: true }, { attached: false },
  { type: 'keydown', key: 'Enter' }, { type: 'keydown', key: 'ArrowRight', ctrlKey: true },
  { type: 'keydown', key: 'Enter', ctrlKey: true, repeat: true },
]) assert.equal(gesture({ nodeId: 'ignored' }, options).prevented, false);
assert.equal(nodes.length, count, 'Nested source/expand buttons, detached nodes and ordinary keys do not open paths');
assert.equal(gesture({}).prevented, false);
gesture({ ingressId: 'http-entry' });
assert.deepEqual(entries, ['http-entry']);
assert.equal(nodes.length, count);

const one = { id: 'one', handlerNodeIds: ['method'] }, two = { id: 'two', handlerNodeIds: ['method'] };
assert.equal(uniqueNodeEntry([one], 'method'), one);
assert.equal(uniqueNodeEntry([one, two], 'method'), null);
assert.equal(uniqueNodeEntry([one, { id: 'unmapped' }], 'other'), null);
controller.destroy(); assert.equal(listeners.size, 0);
globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
for (const centered of [false, true]) {
  const row = new Svg('g');
  appendSequenceRow(row, { w: 280, h: 100, label: 'ToString()', line: 78, compactRow: true }, centered);
  const target = row.querySelector('.method-row-hit-target');
  assert.equal(target.attributes.width, 280); assert.equal(target.attributes.height, 100);
  assert.equal(target.attributes.x, centered ? -140 : 0); assert.equal(target.attributes.y, centered ? -50 : 0);
  assert.equal(target.attributes['pointer-events'], 'all');
  assert.equal(row.firstChild, target, 'Hit surface stays behind the row text and explicit actions');
}
console.log('Path gesture: real IDs, nested actions, keyboard capture and unambiguous ingress PASS');
