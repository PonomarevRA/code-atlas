/** Panel lifecycle: native controls, focus, responsive preferences and no graph/history coupling. */
import assert from 'node:assert/strict';
import { WorkspacePanels } from '../web/workspace-panels.js';

/** Сохраняет несколько listeners и native focus для проверки dispose и Escape. */
class Target {
  constructor() { this.listeners = new Map(); }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name).add(callback);
  }
  removeEventListener(name, callback) { this.listeners.get(name)?.delete(callback); }
  emit(name, event = {}) { for (const callback of this.listeners.get(name) || []) callback(event); }
}
class Element extends Target {
  constructor(id, doc) {
    super(); this.id = id; this.ownerDocument = doc; this.children = []; this.attributes = {};
    this.classes = new Set();
    this.classList = { toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name) };
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  contains(target) { return target === this || this.children.some(child => child.contains(target)); }
  focus() { this.ownerDocument.activeElement = this; }
  querySelector() { return this.children.find(child => !child.disabled) || null; }
}

/** Две aside и внешние кнопки имитируют реальные root ID без чтения DOM приложения. */
function fixture(mobile = false, storage) {
  const doc = new Target();
  const workspace = new Element('workspace', doc);
  const panels = { left: new Element('workspace-left', doc), right: new Element('workspace-right', doc) };
  const toggles = { left: new Element('panel-left-toggle', doc), right: new Element('panel-right-toggle', doc) };
  const input = new Element('query', doc);
  input.value = 'CostChange';
  panels.left.children.push(input);
  workspace.children.push(...Object.values(panels));
  const media = new Target(); media.matches = mobile;
  const layouts = [];
  const controller = new WorkspacePanels({ workspace, panels, toggles, storage, media, onLayout: info => layouts.push(info) });
  return { controller, doc, workspace, panels, toggles, input, media, layouts };
}

const values = new Map();
const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
const { controller, doc, workspace, panels, toggles, input, media, layouts } = fixture(false, storage);
assert.equal(panels.left.hidden, false);
assert.equal(panels.right.hidden, false);
assert.equal(toggles.left.attributes['aria-controls'], 'workspace-left');
assert.equal(toggles.left.attributes['aria-expanded'], 'true');
input.focus();
toggles.left.emit('click'); // Native Enter/Space on a button delivers one click, not a second custom keydown.
assert.equal(panels.left.hidden, true);
assert.equal(panels.left.inert, true);
assert.equal(panels.left.attributes['aria-hidden'], 'true');
assert.equal(toggles.left.attributes['aria-expanded'], 'false');
assert.equal(doc.activeElement, toggles.left);
assert.equal(panels.right.hidden, false, 'desktop sides are independent');
assert.ok(workspace.classes.has('panels-left-collapsed'));
assert.equal(input.value, 'CostChange', 'collapse does not clear search');
assert.equal(toggles.left.listeners.get('keydown'), undefined, 'native keyboard activation must not double-toggle');
assert.equal(values.has('atlas-workspace-panels-v1'), true);
const remembered = JSON.parse(values.get('atlas-workspace-panels-v1'));
assert.deepEqual(remembered.desktop, { left: false, right: true });
controller.reveal('left');
assert.equal(panels.left.hidden, false);
assert.equal(values.get('atlas-workspace-panels-v1'), JSON.stringify(remembered), 'programmatic reveal does not rewrite preference');
controller.setOpen('left', true, { focus: true });
assert.equal(doc.activeElement, input);

media.matches = true; media.emit('change');
assert.equal(panels.left.hidden, true, 'mobile starts with the map visible');
assert.equal(panels.right.hidden, true);
controller.setOpen('left', true, { focus: true });
assert.equal(doc.activeElement, input);
controller.setOpen('right', true);
assert.equal(panels.left.hidden, true);
assert.equal(panels.right.hidden, false, 'mobile admits at most one drawer');
let prevented = 0;
doc.emit('keydown', { key: 'Escape', target: panels.right, preventDefault() { prevented++; } });
assert.equal(panels.right.hidden, true);
assert.equal(doc.activeElement, toggles.right);
assert.equal(prevented, 1);
controller.reveal('right');
doc.emit('keydown', { key: 'Escape', target: new Element('external-modal', doc), preventDefault() { prevented++; } });
assert.equal(panels.right.hidden, false, 'Escape in an external dialog is handled by its owner');
doc.emit('keydown', { key: 'Escape', target: panels.right, defaultPrevented: true });
assert.equal(panels.right.hidden, false);
media.matches = false; media.emit('change');
assert.deepEqual(controller.state, { left: true, right: true }, 'responsive transitions retain desktop state');
assert.equal(input.value, 'CostChange');
assert.ok(layouts.some(layout => layout.reason === 'viewport'));
const count = layouts.length;
controller.dispose();
toggles.left.emit('click'); media.emit('change');
assert.equal(controller.setOpen('left', false), false);
assert.equal(layouts.length, count, 'dispose removes all callbacks');

const restored = fixture(false, storage);
assert.deepEqual(restored.controller.state, { left: false, right: true }, 'temporary reveal never leaks into a later preference write');
restored.controller.dispose();
const bodyEscape = fixture(true, null);
bodyEscape.doc.body = new Element('body', bodyEscape.doc);
bodyEscape.controller.reveal('left');
bodyEscape.doc.emit('keydown', { key: 'Escape', target: bodyEscape.doc.body, preventDefault() {} });
assert.equal(bodyEscape.panels.left.hidden, true, 'Escape works even when a pointer click left focus on body');
bodyEscape.controller.dispose();
for (const invalid of ['not JSON', JSON.stringify({ desktop: { left: 'false', right: true } }), 'x'.repeat(600)]) {
  const fresh = fixture(false, { getItem: () => invalid, setItem() {} });
  assert.deepEqual(fresh.controller.state, { left: true, right: true });
  fresh.controller.dispose();
}
const broken = fixture(true, { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } });
assert.equal(broken.controller.toggle('left'), true);
assert.equal(broken.panels.left.hidden, false);
broken.controller.dispose();
assert.throws(() => new WorkspacePanels({ workspace: {} }), TypeError);
// Toolbar wrapping and sticky scrolling change drawer anchor without touching map state.
const anchored = fixture(true, null);
anchored.controller.dispose();
let bottom = 225, observed = null, disconnected = false;
const properties = new Map(), viewport = new Target();
const toolbar = { getBoundingClientRect: () => ({ bottom }) };
viewport.ResizeObserver = class {
  constructor(callback) { this.callback = callback; observed = this; }
  observe(element) { assert.equal(element, toolbar); }
  disconnect() { disconnected = true; }
};
anchored.doc.defaultView = viewport;
anchored.doc.querySelector = () => toolbar;
anchored.doc.documentElement = { style: { setProperty: (key, value) => properties.set(key, value), removeProperty: key => properties.delete(key) } };
const anchoredController = new WorkspacePanels({ workspace: anchored.workspace, panels: anchored.panels,
  toggles: anchored.toggles, storage: null, media: anchored.media });
assert.equal(properties.get('--workspace-toolbar-bottom'), '225px');
bottom = 184.2; observed.callback(); assert.equal(properties.get('--workspace-toolbar-bottom'), '185px');
bottom = 61; viewport.emit('scroll'); assert.equal(properties.get('--workspace-toolbar-bottom'), '61px');
bottom = 137; viewport.emit('resize'); assert.equal(properties.get('--workspace-toolbar-bottom'), '137px');
anchoredController.dispose();
assert.equal(disconnected, true); assert.equal(properties.has('--workspace-toolbar-bottom'), false);
viewport.emit('resize'); assert.equal(properties.has('--workspace-toolbar-bottom'), false);
console.log('Workspace panels focus, responsive visibility and preferences PASS');

// Atlas opts out of an empty right inspector on first open; explicit saved choices still win.
{
  const fresh=fixture(false,{getItem:()=>null,setItem(){}});
  fresh.controller.dispose();
  const collapsed=new WorkspacePanels({workspace:fresh.workspace,panels:fresh.panels,toggles:fresh.toggles,media:fresh.media,storage:{getItem:()=>null,setItem(){}},defaultOpen:{left:true,right:false}});
  assert.equal(fresh.panels.right.hidden,true);collapsed.reveal('right');assert.equal(fresh.panels.right.hidden,false);collapsed.dispose();
  const explicit=new WorkspacePanels({workspace:fresh.workspace,panels:fresh.panels,toggles:fresh.toggles,media:fresh.media,storage:{getItem:()=>JSON.stringify({desktop:{left:true,right:true},mobile:{left:false,right:false}}),setItem(){}},defaultOpen:{left:true,right:false}});
  assert.equal(fresh.panels.right.hidden,false,'saved user visibility is never overwritten by the new default');explicit.dispose();
}
