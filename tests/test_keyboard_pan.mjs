/** Стрелки управляют только сфокусированной картой и сохраняют выбор, масштаб и browser shortcuts. */
import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';
import { keyboardPanDelta } from '../web/keyboard-pan.js';
import { Svg } from './svg_fixture.mjs';

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
/** DOM contains/getAttribute нужны для реального scope-теста команды, а не подмены самой функции. */
Svg.prototype.contains = function (target) { return this === target || this.children.some(child => child.contains(target)); };
Svg.prototype.getAttribute = function (name) { return this.attributes[name] ?? null; };
const svg = new Svg('svg'), viewport = new Svg('g');
viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const selected = [], changes = [];
const view = new GraphView(svg, id => selected.push(id), () => {});
view.render([{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }], [{ source: 'a', target: 'b', relation: 'calls' }], 'a');
view.camera = { x: 90, y: 120, scale: .4 };
view.onCameraChange = (camera, origin) => changes.push({ camera, origin });
const positions = JSON.stringify([...view.positions]);
const nodes = [...viewport.children];
const active = view.nodeElements.get('a').group;

/** Проводит keydown через настоящий listener GraphView и сохраняет наблюдаемый preventDefault. */
function press(key, target = svg, modifiers = {}) {
  const event = { key, target, ...modifiers, prevented: false, preventDefault() { this.prevented = true; } };
  svg.listeners.keydown(event);
  return event;
}

assert.equal(svg.attributes.tabindex, 0);
assert.ok(press('ArrowRight', active).prevented);
assert.deepEqual(view.camera, { x: 26, y: 120, scale: .4 });
assert.ok(press('ArrowDown', active, { shiftKey: true }).prevented);
assert.deepEqual(view.camera, { x: 26, y: -72, scale: .4 });
assert.ok(press('ArrowLeft').prevented);
assert.ok(press('ArrowUp').prevented);
assert.deepEqual(view.camera, { x: 90, y: -8, scale: .4 });
assert.equal(view.selectedId, 'a');
assert.deepEqual(selected, []);
assert.equal(JSON.stringify([...view.positions]), positions);
for (let index = 0; index < nodes.length; index += 1) assert.equal(viewport.children[index], nodes[index]);
assert.deepEqual(changes[0], { camera: { x: 26, y: 120, scale: .4 }, origin: { kind: 'pan', source: 'keyboard' } });
view.camera.scale = 3;
press('ArrowRight', active);
assert.equal(view.camera.x, 26, 'Step is in screen pixels, independent of zoom');
assert.equal(view.camera.scale, 3);
assert.equal(changes[0].camera.scale, .4, 'History callback receives a stable camera copy');

const stable = { ...view.camera };
const callbackCount = changes.length;
for (const modifier of ['altKey', 'metaKey', 'ctrlKey']) {
  assert.equal(press('ArrowLeft', active, { [modifier]: true }).prevented, false);
}
for (const tag of ['input', 'textarea', 'select']) {
  const input = new Svg(tag); input.tagName = tag.toUpperCase(); input.parentElement = svg; svg.append(input);
  assert.equal(press('ArrowRight', input).prevented, false);
}
const editable = new Svg('g'), nested = new Svg('text');
editable.setAttribute('contenteditable', 'true'); editable.append(nested); nested.parentElement = editable; svg.append(editable);
assert.equal(press('ArrowDown', nested).prevented, false);
assert.equal(press('ArrowDown', active, { defaultPrevented: true }).prevented, false);
assert.equal(press('ArrowDown', { tagName: 'BUTTON' }).prevented, false, 'Popup outside SVG keeps its own keyboard events');
for (const key of ['Enter', ' ', 'Tab', 'a']) assert.equal(press(key, active).prevented, false);
assert.deepEqual(view.camera, stable);
assert.equal(changes.length, callbackCount);
assert.equal(keyboardPanDelta({ key: 'ArrowRight', target: active, altKey: true }, svg), null);
assert.equal(view.panCameraBy(NaN, 0), false);

let hiddenTooltip;
view.mapTooltip = { hide: force => { hiddenTooltip = force; } };
press('ArrowRight', active);
assert.equal(hiddenTooltip, true, 'Moving map closes an anchored popup instead of leaving it in the wrong location');
let focusOptions;
svg.focus = options => { focusOptions = options; };
svg.setPointerCapture = () => {};
svg.listeners.pointerdown({ target: { closest: () => null }, clientX: 10, clientY: 20, pointerId: 1 });
assert.deepEqual(focusOptions, { preventScroll: true });
svg.listeners.pointercancel();

// Клавиатурный pan в раскрытом окружении обновляет закреплённую миникарту без смены выбранного символа.
view.mapTooltip = null;
view.renderExpansion({ nodes: [{ id: 'expanded', label: 'Expanded' }], edges: [], selectedId: 'expanded' });
const frame = view.expansion.frame;
const frameBefore = frame.attributes.transform;
const scale = view.camera.scale;
press('ArrowRight', view.expansion.child.nodeElements.get('expanded').group);
assert.notEqual(frame.attributes.transform, frameBefore);
assert.equal(view.camera.scale, scale);
assert.equal(view.expansion.selectedId, 'expanded');
assert.equal(view.expansion.child.nodeElements.size, 1);
console.log('Keyboard map pan scope, editable exclusions, browser shortcuts, camera and expansion PASS');
