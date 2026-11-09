import assert from 'node:assert/strict';
import { appendNodeActions } from '../web/node-actions.js';
import { Svg } from './svg_fixture.mjs';

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
const group = new Svg('g');
const selected = [], entered = [], toggled = [], dragged = [];
group.addEventListener('click', () => selected.push(true));
group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') selected.push(true); });
group.addEventListener('pointerdown', () => dragged.push(true));
const rawId = 'raw:<img onerror="bad()">';
const label = '<script>Calculate()</script>';
const controls = appendNodeActions(group, { id: rawId, label, enter: id => entered.push(id),
  onToggle: id => toggled.push(id), expanded: false, x: 132, y: -38 });

/** Реальное bubbling моделируется отдельно от listener, чтобы проверить запрет parent action. */
function dispatch(target, type, key, repeat = false) {
  let stopped = false, prevented = false;
  const event = { key, repeat, stopPropagation() { stopped = true; }, preventDefault() { prevented = true; } };
  for (let node = target; node && !stopped; node = node.parent) node.listeners[type]?.(event);
  return { stopped, prevented };
}

assert.equal(controls.toggle.attributes['data-owner-id'], rawId);
assert.equal(controls.toggle.attributes['data-node-action'], 'inline');
assert.equal(controls.enter.attributes['data-node-action'], 'enter');
assert.equal(controls.toggle.attributes['aria-expanded'], 'false');
assert.equal(controls.toggle.attributes.tabindex, 0);
assert.equal(controls.toggle.attributes.role, 'button');
assert.equal(controls.toggle.querySelector('.node-action-hit').attributes.r, 12);
assert.equal(controls.toggle.querySelector('.node-action-circle').attributes.r, 9);
assert.equal(controls.enter.attributes.transform, 'translate(162,-38)');
assert.ok(controls.enter.querySelector('title').textContent.includes(label));
assert.equal(controls.enter.querySelectorAll('script').length, 0);
dispatch(controls.enter, 'click');
dispatch(controls.toggle, 'keydown', 'Enter');
dispatch(controls.toggle, 'keydown', ' ');
dispatch(controls.toggle, 'keydown', 'Enter', true);
assert.deepEqual(entered, [rawId]);
assert.deepEqual(toggled, [rawId, rawId]);
assert.deepEqual(selected, [], 'Action activation must not select the parent card');
assert.deepEqual(dispatch(controls.toggle, 'pointerdown'), { stopped: true, prevented: false });
assert.deepEqual(dragged, [], 'Action pointerdown must not start canvas dragging');
assert.deepEqual(dispatch(controls.toggle, 'keydown', 'Tab'), { stopped: false, prevented: false }, 'Native Tab remains available');
const collapsed = appendNodeActions(new Svg('g'), { id: rawId, onToggle() {}, expanded: true });
assert.equal(collapsed.toggle.attributes['aria-expanded'], 'true');
assert.ok(collapsed.toggle.attributes['aria-label'].startsWith('Свернуть'));
assert.equal(collapsed.enter, null);
assert.deepEqual(appendNodeActions(new Svg('g'), { id: rawId }), { enter: null, toggle: null });
console.log('Node actions separated selection, safe IDs and keyboard PASS');
