import assert from 'node:assert/strict';
import { Svg } from './svg_fixture.mjs';
import { MethodWorkbench } from '../web/method-workbench.js';
import { qualifiedMethodLabel, readingCallContext, readingBreadcrumbs } from '../web/reading-navigation.js';
import { focusProgressiveDetails, restoreProgressiveFocus, revealProgressiveDetails } from '../web/progressive-navigation.js';
import { appendMethodReadAction, appendProgressiveDetailsAction } from '../web/method-sequence-renderer.js';

/** Minimal reader DOM exposes public buttons, addresses and focus without a browser dependency. */
class Html extends Svg {
  constructor(tag) { super(tag); this.scrollTop = 0; this.style = {}; }
  get className() { return this.attributes.class || ''; }
  set className(value) { this.attributes.class = value; }
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
  getAttribute(name) { return this.attributes[name]; }
  focus() { document.activeElement = this; }
}
globalThis.document = { createElement: tag => new Html(tag), createElementNS: (_, tag) => new Html(tag),
  createTextNode: text => { const node = new Html('#text'); node.textContent = text; return node; } };

const nodes = new Map([
  ['entry', { id: 'entry', label: 'CostChange()', callableFacts: { declaringType: 'Api.PortfolioController' } }],
  ['caller', { id: 'caller', label: 'LoadAsync()', callableFacts: { declaringType: 'Domain.PifLoader' } }],
  ['target', { id: 'target', label: 'LoadAsync()', callableFacts: { declaringType: 'Domain.PifClient' } }],
  ['nested', { id: 'nested', label: 'Prepare()', callableFacts: { declaringType: 'Domain.PifPreparation' } }],
]);
const getNode = id => nodes.get(id);
assert.equal(qualifiedMethodLabel(getNode('target')), 'PifClient.LoadAsync()');
assert.equal(qualifiedMethodLabel({ label: 'PifClient.LoadAsync()', callableFacts: { declaringType: 'Domain.PifClient' } }), 'PifClient.LoadAsync()');
assert.equal(qualifiedMethodLabel({ label: 'Unknown()' }), 'Unknown()', 'Missing owner facts do not invent a role');
const first = { ownerNodeId: 'caller', occurrenceId: 'caller::call:10:4:10:30', line: 10, targetIds: ['target'] };
const second = { ...first, occurrenceId: 'caller::call:20:4:20:30', line: 20 };
assert.notEqual(readingCallContext(first, getNode).title, readingCallContext(second, getNode).title);
assert.ok(readingCallContext(second, getNode).title.includes(second.occurrenceId));
assert.deepEqual(readingBreadcrumbs({ origin: second, callSite: second, focusId: 'target', entryNodeId: 'entry' }, getNode)
  .map(item => item.label), ['PortfolioController.CostChange()', 'PifLoader.LoadAsync()', 'PifClient.LoadAsync()']);

/** Reader return actions preserve exact caller addresses; local back restores expression and scroll. */
function rootFixture() {
  const root = new Html('section'); root.hidden = true;
  for (const name of ['diagram', 'back', 'body', 'breadcrumbs']) {
    const node = new Html(['diagram', 'back'].includes(name) ? 'button' : 'div');
    node.setAttribute('data-reader', name); root.append(node);
  }
  return root;
}
const root = rootFixture(), returns = [], descriptions = id => ({ nodeId: id, label: nodes.get(id)?.label,
  snapshotId: 'snapshot', file: 'Pif.cs', line: 1, documentation: { summary: 'Declared purpose' },
  methodFlow: { status: 'parsed', steps: [] }, events: [], sourceKind: 'working-tree' });
const reader = new MethodWorkbench(root, { getContext: () => ({ projectId: 'project', snapshotId: 'snapshot', entryNodeId: 'entry' }),
  getNode, getGraph: () => ({ nodes: [...nodes.values()], edges: [] }), onSource() {}, onClose() {},
  onReturnToCall: origin => returns.push(['call', origin]), onReturnToEntry: origin => returns.push(['entry', origin]),
  fetcher: async (_url, options) => ({ ok: true, json: async () => descriptions(JSON.parse(options.body).nodeId) }) });
await reader.open({ ...second, expression: 'LoadAsync(secondInput)' }, { autoload: false });
root.scrollTop = 184;
await reader.read('nested', { ownerNodeId: 'target', occurrenceId: 'target::call:25:1', line: 25,
  targetIds: ['nested'], expression: 'Prepare(input)' });
reader.back();
assert.equal(root.scrollTop, 184);
assert.equal(reader.expression, 'LoadAsync(secondInput)');
assert.equal(reader.snapshot().callSite.occurrenceId, second.occurrenceId);
assert.equal(reader.snapshot().origin.occurrenceId, second.occurrenceId);
assert.ok(root.querySelector('.reader-call-context').textContent.includes('PifLoader.LoadAsync() · строка 20'));
assert.equal(root.querySelector('.reader-call-context').getAttribute('data-occurrence-id'), second.occurrenceId);
root.querySelector('[data-reader-action="return-call"]').listeners.click();
assert.equal(reader.active, false);
assert.equal(returns[0][1].occurrenceId, second.occurrenceId);
await reader.open(second, { autoload: false });
root.querySelector('[data-reader-action="return-entry"]').listeners.click();
assert.equal(returns[1][1].entryNodeId, 'entry');
const undocumented = new Html('section');
reader.source.cache.set('target', { ...descriptions('target'), documentation: {} });
reader.details('target', undocumented, false);
assert.ok(undocumented.textContent.includes('Описание назначения метода отсутствует'),
  'An absent purpose stays explicit after contract rendering');

const geometry = new Map([['occurrence', { x: 100, y: 100, w: 200, h: 120 }],
  ['body', { x: 1400, y: 100, w: 300, h: 140 }]]);
const view = { svg: { clientWidth: 1000, clientHeight: 700 }, viewport: new Html('g'),
  positions: geometry, progressiveDetails: new Map([['occurrence', 'body']]), camera: { x: 32, y: 28, scale: .3 },
  transform() {}, onCameraChange() {}, bindPathAction(control, action) {
    control.addEventListener('click', event => { event.stopPropagation(); action(); });
    control.addEventListener('keydown', event => {
      if (!['Enter', ' '].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation(); action();
    });
  } };
const oldCamera = { ...view.camera }, oldGeometry = JSON.stringify([...geometry]);
const focusChanges = [];
view.onReadableFocusChange = frame => focusChanges.push(frame);
assert.equal(revealProgressiveDetails(view, 'occurrence', true), true);
assert.equal(view.camera.scale, .3, 'Ordinary reveal never changes zoom');
assert.equal(view.readableFocusTrail, undefined);
view.camera = { ...oldCamera };
assert.equal(focusProgressiveDetails(view, 'missing'), false);
assert.equal(focusProgressiveDetails(view, 'occurrence'), true);
assert.equal(view.camera.scale, 1);
assert.equal(restoreProgressiveFocus(view, 'other'), false);
assert.equal(restoreProgressiveFocus(view, 'occurrence'), true);
assert.deepEqual(view.camera, oldCamera);
assert.equal(focusChanges[0].occurrenceId, 'occurrence');
assert.equal(focusChanges[1], null, 'Fixed workspace controls can clear the camera return action');
assert.equal(JSON.stringify([...geometry]), oldGeometry, 'Readable focus does not change grouping or world geometry');
const canvas = { x: 120, y: 70, width: 700, height: 450 };
view.getCanvasViewportRect = () => canvas;
focusProgressiveDetails(view, 'occurrence');
assert.equal(geometry.get('body').x * view.camera.scale + view.camera.x, 470,
  'Explicit focus centers inside the unobscured area, including its left offset');
assert.equal(geometry.get('body').y * view.camera.scale + view.camera.y, 295,
  'Toolbar clipping contributes the vertical offset');
restoreProgressiveFocus(view, 'occurrence');
view.progressiveAreas = new Map([['occurrence', { kind: 'candidates', y: 30, height: 300 }]]);
focusProgressiveDetails(view, 'occurrence');
assert.equal(30 + 150 + view.camera.y, 295,
  'A short candidate inventory is centered as a whole in the safe canvas');
assert.ok(30 + view.camera.y >= canvas.y + 32);
assert.ok(330 + view.camera.y <= canvas.y + canvas.height - 32);
assert.equal(view.camera.scale, 1);
restoreProgressiveFocus(view, 'occurrence');
view.progressiveAreas.set('occurrence', { kind: 'candidates', y: 30, height: 900 });
focusProgressiveDetails(view, 'occurrence');
assert.equal((geometry.get('body').y - geometry.get('body').h / 2) + view.camera.y, canvas.y + 32,
  'A tall candidate inventory starts with its first choice near the safe top edge');
assert.equal(view.camera.scale, 1, 'Tall inventories retain explicit readable scale instead of shrinking');
restoreProgressiveFocus(view, 'occurrence');
view.progressiveAreas.clear();
view.camera = { x: -10, y: -100, scale: .3 };
assert.equal(revealProgressiveDetails(view, 'occurrence', true), true);
assert.equal(view.camera.scale, .3);
for (const point of geometry.values()) {
  const left = (point.x - point.w / 2) * view.camera.scale + view.camera.x;
  const right = (point.x + point.w / 2) * view.camera.scale + view.camera.x;
  const top = (point.y - point.h / 2) * view.camera.scale + view.camera.y;
  const bottom = (point.y + point.h / 2) * view.camera.scale + view.camera.y;
  assert.ok(left >= canvas.x + 32 && right <= canvas.x + canvas.width - 32);
  assert.ok(top >= canvas.y + 32 && bottom <= canvas.y + canvas.height - 32);
}
view.camera = { ...oldCamera };

const group = new Html('g'), call = { id: 'occurrence', label: 'LoadAsync()', canExpand: true, w: 300, h: 180 };
let readOccurrence;
view.onReadCall = node => { readOccurrence = node.id; };
appendMethodReadAction(view, group, call);
appendProgressiveDetailsAction(view, group, call);
const read = group.querySelector('.path-card-read'), focus = group.querySelector('.path-card-details');
assert.ok(read.textContent.includes('Объяснение'));
assert.ok(read.getAttribute('aria-label').includes('Прочитать объяснение'));
assert.equal(read.getAttribute('data-call-occurrence-id'), 'occurrence');
read.listeners.focus();
assert.equal(group.querySelector('.sequence-action-hint').getAttribute('visibility'), 'visible');
read.listeners.blur();
assert.equal(group.querySelector('.sequence-action-hint').getAttribute('visibility'), 'hidden');
const event = { key: 'Enter', preventDefault() {}, stopPropagation() {} };
read.listeners.keydown(event); assert.equal(readOccurrence, 'occurrence');
focus.listeners.keydown(event); assert.equal(view.camera.scale, 1);
focus.listeners.keydown(event); assert.deepEqual(view.camera, oldCamera);
assert.ok(focus.textContent.includes('Прочитать этот блок'));
view.getCanvasViewportRect = () => ({ x: 0, y: 0, width: 0, height: 0 });
focus.listeners.keydown(event);
assert.ok(focus.firstChild.textContent.includes('Прочитать этот блок'),
  'Failed focus keeps the reading action label instead of claiming a return frame');
assert.equal(view.readableFocusTrail.length, 0);
console.log('Onboarding navigation qualified occurrences, local return, keyboard hints and reversible camera PASS');
