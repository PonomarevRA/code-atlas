import assert from 'node:assert/strict';
import { Svg } from './svg_fixture.mjs';
import { canvasViewportRect, revealCanvasAction, focusCanvasOccurrence, ensureCanvasVisible } from '../web/canvas-viewport.js';
import { bindPathAction } from '../web/execution-path-renderer.js';
import { createMethodWorkbench } from '../web/method-workbench-controller.js';

/** Overlay fixtures preserve actual CSS-selector identity and inherited hidden state. */
function overlay(left, top, right, bottom) {
  return { hidden: false, parentHidden: false, closest() { return this.parentHidden ? {} : null; },
    getBoundingClientRect: () => ({ left, top, right, bottom, width: right - left, height: bottom - top }) };
}
const boxes = new Map([
  ['workspace-left', overlay(100, 100, 300, 750)], ['workspace-right', overlay(900, 100, 1200, 750)],
  ['.workspace-controls', overlay(0, 0, 1300, 150)], ['.map-controls', overlay(350, 650, 800, 700)],
]);
const doc = { defaultView: { innerWidth: 1300, innerHeight: 900 },
  getElementById: id => boxes.get(id), querySelector: selector => boxes.get(selector) };
const svg = { ownerDocument: doc, clientWidth: 1000, clientHeight: 700,
  getBoundingClientRect: () => ({ left: 100, top: 50, right: 1100, bottom: 750, width: 1000, height: 700 }) };
assert.deepEqual(canvasViewportRect(svg), { x: 200, y: 100, width: 600, height: 492 });
boxes.get('workspace-right').parentHidden = true;
assert.deepEqual(canvasViewportRect(svg), { x: 200, y: 100, width: 800, height: 492 });
boxes.get('workspace-right').parentHidden = false;
boxes.set('workspace-left', overlay(100, 100, 1050, 750));
assert.equal(canvasViewportRect(svg).width, 0, 'Covered space never falls back to a full, obscured SVG');
boxes.set('workspace-left', overlay(100, 100, 300, 750));

/** Focus panning makes the original control visible; pointer click keeps its original callback. */
const control = new Svg('text'), cameraChanges = [];
const view = { svg, camera: { x: 0, y: 0, scale: .5 }, transform() {}, onCameraChange: () => cameraChanges.push(1),
  getCanvasViewportRect: () => canvasViewportRect(svg) };
control.getBoundingClientRect = () => ({ left: 1040 + view.camera.x, right: 1100 + view.camera.x,
  top: 710 + view.camera.y, bottom: 730 + view.camera.y, width: 60, height: 20 });
let clicks = 0;
let keyboardFocus = false;
control.matches = selector => { assert.equal(selector, ':focus-visible'); return keyboardFocus; };
bindPathAction(view, control, () => clicks++);
control.listeners.focus();
assert.deepEqual(view.camera, { x: 0, y: 0, scale: .5 }, 'Pointer focus cannot move a target before its click');
assert.equal(cameraChanges.length, 0);
control.listeners.click({ stopPropagation() {} });
assert.equal(clicks, 1, 'Pointer click retains its original callback after focus');
keyboardFocus = true;
control.listeners.focus();
assert.equal(view.camera.scale, .5);
assert.ok(control.getBoundingClientRect().right <= 100 + 800 - 18);
assert.ok(control.getBoundingClientRect().bottom <= 50 + 592 - 18);
assert.equal(revealCanvasAction(view, control), false, 'Repeated focus of a visible action is stable');
control.listeners.click({ stopPropagation() {} });
assert.equal(clicks, 2);
control.listeners.keydown({ key: 'Enter', stopPropagation() {}, preventDefault() {} });
assert.equal(clicks, 3);
assert.equal(cameraChanges.length, 1);
const large = { getBoundingClientRect: () => ({ left: 200 + view.camera.x, right: 1700 + view.camera.x,
  top: 200 + view.camera.y, bottom: 220 + view.camera.y, width: 1500, height: 20 }) };
assert.equal(revealCanvasAction(view, large), true);
assert.equal(revealCanvasAction(view, large), false, 'An oversized control does not oscillate between clipped edges');
let offscreenTop = -964, scrolls = 0;
const scrolledSvg = { ownerDocument: { defaultView: { innerHeight: 900, innerWidth: 1300 } },
  clientWidth: 1000, clientHeight: 659,
  getBoundingClientRect: () => ({ left: 100, right: 1100, top: offscreenTop, bottom: offscreenTop + 659,
    width: 1000, height: 659 }),
  scrollIntoView(options) { assert.equal(options.block, 'start'); scrolls++; offscreenTop = 150; } };
const scrolledView = { svg: scrolledSvg, camera: { x: 0, y: 0, scale: .7 }, transform() {} };
const scrolledControl = { getBoundingClientRect: () => ({ left: 200, right: 260,
  top: offscreenTop + 20, bottom: offscreenTop + 40, width: 60, height: 20 }) };
assert.equal(revealCanvasAction(scrolledView, scrolledControl), false,
  'An offscreen canvas is restored before measuring its already-visible local action');
assert.equal(scrolls, 1);
assert.deepEqual(scrolledView.camera, { x: 0, y: 0, scale: .7 });
assert.equal(ensureCanvasVisible(scrolledSvg), false, 'A visible canvas does not cause more page scrolling');

/** Minimal reader DOM keeps contract tests independent of layout/render libraries. */
class Html extends Svg {
  constructor(tag) { super(tag); this.scrollTop = 0; this.style = {}; }
  get className() { return this.attributes.class || ''; }
  set className(value) { this.attributes.class = value; }
  getAttribute(name) { return this.attributes[name]; }
  matches(selector) {
    const attributes = [...selector.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)];
    if (attributes.some(([, key, value]) => !(key in this.attributes) || value !== undefined && String(this.attributes[key]) !== value)) return false;
    const base = selector.replace(/\[[^\]]+\]/g, '');
    return !base || base.startsWith('.') && this.className.split(' ').includes(base.slice(1)) || this.tag === base;
  }
  querySelectorAll(selector) {
    const result = [];
    for (const child of this.children) {
      if (child.matches(selector)) result.push(child);
      result.push(...child.querySelectorAll(selector));
    }
    return result;
  }
  focus() { document.activeElement = this; }
}
globalThis.document = { createElement: tag => new Html(tag), createElementNS: (_, tag) => new Html(tag),
  createTextNode: text => { const node = new Html('#text'); node.textContent = text; return node; } };
const root = new Html('section');
for (const name of ['diagram', 'back', 'body', 'breadcrumbs']) {
  const child = new Html(['diagram', 'back'].includes(name) ? 'button' : 'div');
  child.setAttribute('data-reader', name); root.append(child);
}
const nodes = ['entry', 'caller', 'implementation'].map(id => ({ id, label: `${id}()` }));
const state = { project: { id: 'project' }, graph: { nodes, edges: [], metadata: { snapshotId: 'snapshot' } },
  nodes: new Map(nodes.map(node => [node.id, node])), selected: 'caller', methodPresentation: 'diagram',
  pathOnGraph: { rootNodeId: 'entry', snapshotId: 'snapshot', steps: [{ nodeId: 'implementation', snapshotId: 'snapshot',
    label: 'implementation()', documentation: { summary: 'Captured purpose' }, methodFlow: { status: 'parsed', steps: [] } }] },
  inlineExpanded: new Set(['caller']) };
const first = new Html('g'), second = new Html('g'), entry = new Html('g');
view.nodeElements = new Map([['caller::call:10:1', { group: first }], ['caller::call:20:1', { group: second }], ['entry', { group: entry }]]);
view.positions = new Map([['caller::call:10:1', { x: 100, y: 100 }], ['caller::call:20:1', { x: 200, y: 150 }], ['entry', { x: -300, y: -200 }]]);
view.camera = { x: 22, y: 44, scale: .7 };
const originalCamera = { ...view.camera }, originalExpanded = [...state.inlineExpanded], path = state.pathOnGraph;
const history = [], notices = [];
let rebuilds = 0;
const connected = createMethodWorkbench({ root, state, view, onSource() {}, onRefreshMap: () => rebuilds++,
  checkpoint: () => history.push('checkpoint'), commit: () => history.push('commit'), sync() {}, status: text => notices.push(text) });
connected.open({ id: 'caller::call:20:1', sourceOwnerId: 'caller', line: 20, readTargetIds: ['implementation'], label: 'Call()' });
await Promise.resolve();
assert.equal(connected.reader.context.entryNodeId, 'entry');
view.camera = { x: -1200, y: -900, scale: 2 };
root.querySelector('[data-reader-action="return-call"]').listeners.click();
assert.equal(document.activeElement, second, 'Return selects the second exact occurrence, not the same-named first call');
assert.deepEqual(view.camera, originalCamera);
assert.equal(state.methodPresentation, 'diagram');
assert.equal(state.selected, 'caller');
assert.equal(state.pathOnGraph, path);
assert.deepEqual([...state.inlineExpanded], originalExpanded);
assert.deepEqual(history.slice(-2), ['checkpoint', 'commit']);
connected.open({ id: 'caller::call:20:1', sourceOwnerId: 'caller', line: 20, readTargetIds: ['implementation'] });
await Promise.resolve();
root.querySelector('[data-reader-action="return-entry"]').listeners.click();
assert.equal(document.activeElement, entry);
assert.equal(view.camera.scale, originalCamera.scale);
assert.equal(view.positions.get('entry').x * view.camera.scale + view.camera.x, 500);
assert.equal(view.positions.get('entry').y * view.camera.scale + view.camera.y, 346);
assert.equal(notices.length, 0);
connected.reader.onRefreshMap();
assert.equal(rebuilds, 1, 'Map refresh delegates only to the explicitly injected rebuild callback');
assert.equal(focusCanvasOccurrence(view, 'missing', { camera: originalCamera }), false);
assert.deepEqual(view.camera, originalCamera);
console.log('Onboarding workspace clipped canvas, original focus targets, exact caller and entry returns PASS');
