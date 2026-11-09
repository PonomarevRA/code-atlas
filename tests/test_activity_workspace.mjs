/** Контракты activity shell и независимого поиска по текущим карточкам карты. */
import assert from 'node:assert/strict';
import { MapFindbar } from '../web/map-findbar.js';
import { findMapItems, centeredMapCamera, createMapFindScene } from '../web/map-find-scene.js';
import { ActivitySidebar } from '../web/activity-sidebar.js';
import { WorkspacePanels } from '../web/workspace-panels.js';
import { MapChrome } from '../web/map-chrome.js';
import { mountWorkspaceLayout } from '../web/workspace-layout.js';
import { readFileSync } from 'node:fs';
import { presentViewMode } from '../web/view-modes.js';

/** Маленький DOM сохраняет identity, events и текст; HTML из карточек не исполняется. */
class Element {
  constructor(tag, document) {
    this.nodeType = 1; this.tagName = tag.toUpperCase(); this.ownerDocument = document;
    this.attributes = {}; this.nodes = []; this.listeners = new Map();
    // Mirror DOMStringMap writes so production data-* selectors still see them.
    this.dataset = new Proxy({}, {
      get: (_, name) => this.getAttribute('data-' + String(name).replace(/[A-Z]/g, letter => '-' + letter.toLowerCase())) ?? undefined,
      set: (_, name, value) => {
        this.setAttribute('data-' + String(name).replace(/[A-Z]/g, letter => '-' + letter.toLowerCase()), value);
        return true;
      },
    });
    this.hidden = false; this.inert = false; this.disabled = false; this.value = '';
    this.clientWidth = 900; this.clientHeight = 600; this.scrollTop = 0; this.scrollLeft = 0;
    this.style = { setProperty() {}, removeProperty() {}, getPropertyValue: () => '' };
    this.classList = {
      contains: name => String(this.attributes.class || '').split(/\s+/).includes(name),
      add: (...names) => { for (const name of names) this.classList.toggle(name, true); },
      remove: (...names) => { for (const name of names) this.classList.toggle(name, false); },
      toggle: (name, force) => {
        const classes = new Set(String(this.attributes.class || '').split(/\s+/).filter(Boolean));
        const enabled = force === undefined ? !classes.has(name) : force;
        if (enabled) classes.add(name); else classes.delete(name);
        this.attributes.class = [...classes].join(' '); return enabled;
      },
    };
  }
  get childNodes() { return this.nodes; }
  get children() { return this.nodes.filter(node => node.nodeType === 1); }
  get parentNode() { return this.parentElement; }
  get id() { return this.attributes.id || ''; }
  set id(value) { this.setAttribute('id', value); }
  get className() { return this.attributes.class || ''; }
  set className(value) { this.setAttribute('class', value); }
  get isConnected() { return this === this.ownerDocument.body || Boolean(this.parentElement?.isConnected); }
  get textContent() { return this.nodes.map(node => node.textContent).join(''); }
  set textContent(value) { this.replaceChildren(String(value)); }
  set innerHTML(value) { throw new Error('Source/card text must never become HTML'); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  hasAttribute(name) { return this[name] === true || name in this.attributes; }
  append(...values) {
    for (let node of values) {
      if (typeof node === 'string') node = { nodeType: 3, nodeValue: node, textContent: node };
      node.parentElement?.removeChild(node); node.parentElement = this; this.nodes.push(node);
    }
  }
  replaceChildren(...nodes) { for (const node of this.nodes) node.parentElement = null; this.nodes = []; this.append(...nodes); }
  prepend(...nodes) { const previous = [...this.nodes]; this.replaceChildren(...nodes, ...previous); }
  after(node) {
    const parent = this.parentElement, position = parent.nodes.indexOf(this);
    node.parentElement?.removeChild(node); node.parentElement = parent; parent.nodes.splice(position + 1, 0, node);
  }
  remove() { this.parentElement?.removeChild(this); }
  removeChild(node) { this.nodes = this.nodes.filter(child => child !== node); node.parentElement = null; }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  addEventListener(name, handler) { if (!this.listeners.has(name)) this.listeners.set(name, new Set()); this.listeners.get(name).add(handler); }
  removeEventListener(name, handler) { this.listeners.get(name)?.delete(handler); }
  dispatch(name, event = {}) { event.target ||= this; for (const handler of this.listeners.get(name) || []) handler(event); return event; }
  focus() { this.ownerDocument.activeElement = this; }
  select() { this.selectionStart = 0; this.selectionEnd = this.value.length; }
  getClientRects() { return this.closest('[hidden],[inert]') ? [] : [this.getBoundingClientRect()]; }
  getBoundingClientRect() { return this.rect || { x: 0, y: 0, left: 0, top: 0, width: this.clientWidth, height: this.clientHeight, right: this.clientWidth, bottom: this.clientHeight }; }
  getBBox() { throw new Error('Map Find must not measure detached cards'); }
  matches(selector) {
    for (let branch of selector.split(',')) {
      branch = branch.trim();
      const exclusions = [...branch.matchAll(/:not\(\[([^\]]+)\]\)/g)];
      if (exclusions.some(match => this.hasAttribute(match[1]))) continue;
      branch = branch.replace(/:not\([^)]*\)/g, '');
      const tag = branch.match(/^[a-z][a-z0-9-]*/i)?.[0];
      if (tag && this.tagName !== tag.toUpperCase()) continue;
      const id = branch.match(/#([\w-]+)/)?.[1]; if (id && this.id !== id) continue;
      const classes = [...branch.matchAll(/\.([\w-]+)/g)];
      if (classes.some(match => !this.classList.contains(match[1]))) continue;
      const attributes = [...branch.matchAll(/\[([\w-]+)(?:=["']?([^"'\]]+)["']?)?\]/g)];
      if (attributes.some(match => match[2] === undefined ? !this.hasAttribute(match[1]) : this.getAttribute(match[1]) !== match[2])) continue;
      return true;
    }
    return false;
  }
  closest(selector) { for (let current = this; current; current = current.parentElement) if (current.matches?.(selector)) return current; return null; }
  querySelectorAll(selector) {
    const result = [];
    for (const child of this.children) { if (child.matches(selector)) result.push(child); result.push(...child.querySelectorAll(selector)); }
    return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

/** Документ поддерживает настоящие document-level keydown handlers и видимые modal roots. */
function documentFixture() {
  const document = {};
  document.createElement = tag => new Element(tag, document);
  document.createElementNS = (_, tag) => new Element(tag, document);
  document.body = document.createElement('body'); document.activeElement = document.body;
  document.documentElement = document.body;
  document.listeners = new Map();
  document.addEventListener = Element.prototype.addEventListener;
  document.removeEventListener = Element.prototype.removeEventListener;
  document.dispatch = Element.prototype.dispatch;
  document.querySelectorAll = selector => document.body.querySelectorAll(selector);
  document.querySelector = selector => document.body.querySelector(selector);
  document.getElementById = id => document.body.querySelector(`#${id}`);
  // The shared window shell owns a deferred refresh and cancels it on disposal.
  document.defaultView = {
    addEventListener() {}, removeEventListener() {}, setTimeout, clearTimeout,
    location: { pathname: '/' },
    // This contract fixture drives changes explicitly; layout observation is browser-only.
    MutationObserver: class { observe() {} disconnect() {} },
  };
  return document;
}

/** Клавиатурное событие показывает, был ли действительно перехвачен browser shortcut. */
function key(target, value = 'f', modifiers = {}) {
  return { target, key: value, ctrlKey: false, metaKey: false, prevented: false, stopped: false,
    preventDefault() { this.prevented = true; this.defaultPrevented = true; },
    stopPropagation() { this.stopped = true; }, ...modifiers };
}

/** Карточка содержит собственный SVG text и может оставаться отсоединённой виртуализатором. */
function card(document, text) {
  const group = document.createElement('g'); group.setAttribute('role', 'button');
  const label = document.createElement('text'); label.textContent = text; group.append(label);
  return group;
}

const document = documentFixture(); globalThis.document = document;
const a = card(document, 'Счёт ACCOUNT .* <img src=x>'), b = card(document, 'account again');
const items = [{ key: 'node:a', text: a.textContent, element: a, center: { x: 100, y: 200 } },
  { key: 'node:a:occurrence2', text: b.textContent, element: b, center: { x: 300, y: 400 } }];
assert.deepEqual(findMapItems(items, 'ACCOUNT').matches.map(item => item.key), ['node:a','node:a:occurrence2']);
assert.deepEqual(findMapItems(items, '.*').matches.map(item => item.key), ['node:a']);
assert.equal(findMapItems(items, 'СЧЁТ').matches[0], items[0]);
assert.equal(findMapItems(items, '<img').matches[0], items[0]);
assert.equal(findMapItems(items, '').matches.length, 0);
assert.equal(findMapItems([items[0],items[0],items[1]], 'account').matches.length, 2);
assert.equal(findMapItems(items, 'account', { maxMatches: 1 }).truncated, true);
assert.equal(findMapItems(items, 'none', { maxItems: 1 }).truncated, true);
assert.equal(findMapItems([{ ...items[0], text: 'prefix tail' }], 'tail', { maxTextChars: 6 }).truncated, true);
assert.equal(findMapItems(items, 'x'.repeat(201)).matches.length, 0);
assert.equal(findMapItems([{ ...items[0], text: 'prefix', textTruncated: true }], 'absent').truncated, true);

const camera = { x: 10, y: 20, scale: .5 };
assert.deepEqual(centeredMapCamera(camera, { x: 1000, y: 500 }, { x: 100, y: 50, width: 800, height: 400 }), { x: 0, y: 0, scale: .5 });
assert.deepEqual(camera, { x: 10, y: 20, scale: .5 });
for (const scale of [0, -1, NaN, Infinity]) assert.equal(centeredMapCamera({ ...camera, scale }, { x: 1, y: 2 }, { x: 0, y: 0, width: 10, height: 10 }), null);
for (const width of [0, -1, NaN, Infinity]) assert.equal(centeredMapCamera(camera, { x: 1, y: 2 }, { x: 0, y: 0, width, height: 10 }), null);
assert.equal(centeredMapCamera(camera, { x: NaN, y: 2 }, { x: 0, y: 0, width: 10, height: 10 }), null);

const nested = card(document, 'nested child only'); a.append(nested);
const community = card(document, 'account community');
const svg = document.createElement('svg'); document.body.append(svg);
const transforms = [], hiddenTooltips = [];
const view = { svg, camera: { ...camera }, selectedId: 'selected', nodeElements: new Map([
  ['a',{ group: a }], ['a:occurrence2',{ group: b }], ['nested',{ group: nested }],
  ['duplicate-element',{ group: a }], ['invalid',{ group: card(document, 'invalid') }],
]), communityElements: new Map([['a',{ group: community }]]),
  positions: new Map([['a',{ cardBounds: { x: 100, y: 200, width: 40, height: 60 } }],
    ['a:occurrence2',{ x: 300, y: 400 }], ['nested',{ x: 10, y: 20 }]]),
  mapTooltip: { hide: force => hiddenTooltips.push(force) }, transform() { transforms.push({ ...this.camera }); },
};
const scene = createMapFindScene(view, { getScope: () => 'p:s', getViewport: () => ({ x: 100, y: 50, width: 800, height: 400 }) });
const inventory = scene.getItems();
assert.equal(inventory.filter(item => item.element === a).length, 1);
assert.ok(inventory.some(item => item.element === b), 'virtualized detached cards remain searchable');
assert.ok(inventory.some(item => item.element === community), 'community cards retain a separate occurrence key');
assert.ok(!inventory.find(item => item.element === a).text.includes('nested child only'), 'parent matches exclude nested card text');
assert.ok(!inventory.some(item => item.text === 'invalid'), 'missing geometry must not create a pan target');
assert.deepEqual(inventory.find(item => item.element === a).center, { x: 120, y: 230 });
view.positions.set('a', { cardBounds: { left: 100, top: 200, right: 140, bottom: 260 } });
assert.deepEqual(scene.getItems().find(item => item.element === a).center, { x: 120, y: 230 }, 'native GraphView bounds use the same world center');
assert.equal(scene.getScope(), 'p:s');
scene.setCamera({ x: 40, y: 50, scale: .5 });
assert.deepEqual(view.camera, { x: 40, y: 50, scale: .5 });
assert.equal(view.selectedId, 'selected'); assert.equal(transforms.length, 1); assert.equal(hiddenTooltips.at(-1), true);
assert.equal(scene.setCamera({ x: 40, y: 50, scale: 3 }), false, 'Map Find must not change scale');
assert.equal(transforms.length, 1);

/** Бар использует свежий inventory; его открытие/ввод не меняет независимые поиски и камеру. */
function findbarFixture({ onOpen = () => {} } = {}) {
  const document = documentFixture(), root = document.createElement('section'); root.hidden = true;
  const trigger = document.createElement('button'), svg = document.createElement('svg');
  const controls = new Map();
  for (const name of ['input','status','previous','next','close']) {
    const control = document.createElement(name === 'input' ? 'input' : name === 'status' ? 'p' : 'button');
    control.setAttribute('data-map-find', name); controls.set(name, control); root.append(control);
  }
  document.body.append(trigger, svg, root); svg.focus();
  const one = card(document, 'Alpha account'), two = card(document, 'Beta ACCOUNT');
  one.classList.add('regex-ring');
  let items = [{ key: 'node:one', text: one.textContent, element: one, center: { x: 100, y: 200 } },
    { key: 'node:two', text: two.textContent, element: two, center: { x: 300, y: 400 } }];
  let camera = { x: 12, y: 24, scale: .75 }, scope = 'p:s', available = true;
  const pans = [];
  const adapter = { getItems: () => items, getCamera: () => camera,
    getViewport: () => ({ x: 0, y: 0, width: 900, height: 600 }), getScope: () => scope,
    setCamera: next => { camera = next; pans.push({ ...next }); return true; } };
  const bar = new MapFindbar({ root, trigger, svg, adapter, document, onOpen, isAvailable: () => available });
  return { document, root, trigger, svg, controls, one, two, bar, pans, getCamera: () => camera,
    setItems: next => { items = next; }, setScope: next => { scope = next; }, setAvailable: value => { available = value; } };
}

const f = findbarFixture(); globalThis.document = f.document;
const initialCamera = { ...f.getCamera() };
let event = key(f.svg, 'f', { ctrlKey: true }); f.document.dispatch('keydown', event);
assert.equal(event.prevented, true); assert.equal(f.bar.opened, true); assert.equal(f.root.hidden, false);
assert.equal(f.document.activeElement, f.controls.get('input')); assert.equal(f.pans.length, 0);
f.controls.get('input').value = 'account'; f.controls.get('input').dispatch('input');
assert.equal(f.pans.length, 0); assert.deepEqual(f.getCamera(), initialCamera);
f.bar.step(1); assert.equal(f.pans.length, 1);
assert.deepEqual(f.getCamera(), { x: 375, y: 150, scale: .75 });
f.bar.step(1); assert.deepEqual(f.getCamera(), { x: 225, y: 0, scale: .75 });
f.bar.step(1); assert.deepEqual(f.getCamera(), { x: 375, y: 150, scale: .75 });
f.bar.step(-1); assert.deepEqual(f.getCamera(), { x: 225, y: 0, scale: .75 });
f.bar.refresh(); f.bar.step(1); assert.deepEqual(f.getCamera(), { x: 375, y: 150, scale: .75 }, 'refresh preserves current occurrence before next');
assert.equal(f.one.classList.contains('regex-ring'), true);
f.bar.close(); assert.equal(f.bar.opened, false); assert.equal(f.document.activeElement, f.svg);
assert.equal(f.one.classList.contains('regex-ring'), true); assert.equal(f.one.classList.contains('map-find-match'), false);

for (const modifier of [{ ctrlKey: true }, { metaKey: true }]) {
  f.document.dispatch('keydown', key(f.svg, 'f', modifier));
  assert.equal(f.bar.opened, true); f.bar.close();
}
for (const modifiers of [{ ctrlKey: true, shiftKey: true }, { ctrlKey: true, altKey: true },
  { ctrlKey: true, repeat: true }, { ctrlKey: true, isComposing: true }, { ctrlKey: true, defaultPrevented: true }]) {
  event = key(f.svg, 'f', modifiers); f.document.dispatch('keydown', event);
  assert.equal(event.prevented, false); assert.equal(f.bar.opened, false);
}
for (const tag of ['input','textarea','select']) {
  const editable = f.document.createElement(tag); f.document.body.append(editable);
  event = key(editable, 'f', { ctrlKey: true }); f.document.dispatch('keydown', event);
  assert.equal(event.prevented, false); assert.equal(f.bar.opened, false);
}
const editor = f.document.createElement('div'), editorChild = f.document.createElement('span');
editor.setAttribute('contenteditable', 'true'); editor.append(editorChild); f.document.body.append(editor);
event = key(editorChild, 'f', { ctrlKey: true }); f.document.dispatch('keydown', event);
assert.equal(event.prevented, false);
const dialog = f.document.createElement('section'); dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true'); f.document.body.append(dialog);
event = key(f.document.body, 'f', { ctrlKey: true }); f.document.dispatch('keydown', event);
assert.equal(event.prevented, false); assert.equal(f.bar.opened, false);
dialog.hidden = true; f.setAvailable(false);
event = key(f.svg, 'f', { ctrlKey: true }); f.document.dispatch('keydown', event);
assert.equal(event.prevented, false); f.setAvailable(true);
for (const className of ['method-workbench-active','method-documentation-active']) {
  f.document.body.classList.add(className);
  event = key(f.document.body, 'f', { metaKey: true }); f.document.dispatch('keydown', event);
  assert.equal(event.prevented, false); assert.equal(f.bar.opened, false);
  f.document.body.classList.remove(className);
}
f.svg.hidden = true;
event = key(f.document.body, 'f', { ctrlKey: true }); f.document.dispatch('keydown', event);
assert.equal(event.prevented, false); f.svg.hidden = false;
f.bar.open();
event = key(f.controls.get('input'), 'f', { ctrlKey: true }); f.document.dispatch('keydown', event);
assert.equal(event.prevented, false, 'even this find input preserves native/browser Ctrl+F');
f.controls.get('input').value = 'account'; f.controls.get('input').dispatch('input');
const beforeEnter = f.pans.length;
event = key(f.controls.get('input'), 'Enter'); f.controls.get('input').dispatch('keydown', event);
assert.equal(event.prevented, true); assert.equal(f.pans.length, beforeEnter + 1);
event = key(f.controls.get('input'), 'Enter', { shiftKey: true }); f.controls.get('input').dispatch('keydown', event);
assert.deepEqual(f.getCamera(), { x: 225, y: 0, scale: .75 }, 'Shift+Enter visits the last hit from the first');
f.controls.get('input').value = 'none'; f.controls.get('input').dispatch('input');
assert.equal(f.controls.get('next').disabled, true); assert.equal(f.controls.get('previous').disabled, true);
const pansBeforeEmpty = f.pans.length; f.bar.step(1); assert.equal(f.pans.length, pansBeforeEmpty);
f.controls.get('input').value = 'account'; f.controls.get('input').dispatch('input');
f.setItems([]); f.bar.step(1); assert.equal(f.pans.length, pansBeforeEmpty, 'scene replacement cannot pan a stale card');
f.setScope('new-project:new-snapshot'); f.bar.refresh();
assert.equal(f.controls.get('input').value, '', 'project/snapshot change clears local find query');
f.bar.dispose();
event = key(f.svg, 'f', { ctrlKey: true }); f.document.dispatch('keydown', event);
assert.equal(event.prevented, false);

const fallback = findbarFixture(), hiddenInvoker = fallback.document.createElement('button');
fallback.document.body.append(hiddenInvoker); hiddenInvoker.focus(); fallback.bar.open();
hiddenInvoker.hidden = true; fallback.bar.close();
assert.equal(fallback.document.activeElement, fallback.svg, 'hidden invoker falls back to the visible map');
fallback.bar.dispose();
const unrelatedFocus = fallback.document.createElement('input'); fallback.document.body.append(unrelatedFocus); unrelatedFocus.focus();
fallback.bar.close(); assert.equal(fallback.document.activeElement, unrelatedFocus, 'already closed Find cannot steal focus');
for (const viewport of [{ x: 0, y: 0, width: 0, height: 600 }, { x: 0, y: 0, width: 900, height: NaN }]) {
  const empty = findbarFixture(); empty.bar.adapter.getViewport = () => viewport;
  event = key(empty.document.body, 'f', { ctrlKey: true }); empty.document.dispatch('keydown', event);
  assert.equal(event.prevented, false, 'a zero/invalid usable viewport leaves browser Find available');
  assert.equal(empty.bar.opened, false); empty.bar.dispose();
}
let reveals = 0;
const lifecycle = findbarFixture({ onOpen: () => { reveals++; } });
lifecycle.bar.open(); assert.equal(reveals, 1); assert.equal(lifecycle.pans.length, 0);
lifecycle.controls.get('input').value = 'account'; lifecycle.controls.get('input').dispatch('input');
assert.equal(lifecycle.one.classList.contains('map-find-match'), true);
const readerFocus = lifecycle.document.createElement('button'); lifecycle.document.body.append(readerFocus); readerFocus.focus();
lifecycle.svg.hidden = true; lifecycle.bar.refresh();
assert.equal(lifecycle.bar.opened, false); assert.equal(lifecycle.root.inert, true);
assert.equal(lifecycle.one.classList.contains('map-find-match'), false);
assert.equal(lifecycle.document.activeElement, readerFocus, 'reading-mode refresh never steals the new reader focus');
assert.equal(lifecycle.pans.length, 0); lifecycle.bar.dispose();

/** Реальные WorkspacePanels сохраняют ownership outer drawer и отдельные mobile preferences. */
function activityFixture({ mobile = false, saved = null } = {}) {
  const document = documentFixture(), workspace = document.createElement('main'), root = document.createElement('nav'), rightRoot = document.createElement('nav');
  const panels = {}, toggles = {}, blocks = {}, writes = [], activations = [];
  const store = new Map(saved ? [['atlas-activity-workspace-v1', saved]] : []);
  const storage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => { writes.push([key, value]); store.set(key, value); } };
  for (const side of ['left','right']) {
    panels[side] = document.createElement('aside'); panels[side].setAttribute('id', `panel-${side}`);
    toggles[side] = document.createElement('button'); document.body.append(toggles[side]); workspace.append(panels[side]);
  }
  for (const [id, side] of [['repository','left'],['search','left'],['map','left'],['details','right'],['health','right'],['tools','right']]) {
    const element = document.createElement('section'), button = document.createElement('button');
    const heading = document.createElement('h2'); heading.id = `heading-${id}`; element.append(heading);
    element.setAttribute('aria-labelledby', heading.id);
    element.setAttribute('id', `activity-${id}`); (side === 'left' ? root : rightRoot).append(button); panels[side].append(element);
    blocks[id] = { element, button, side };
  }
  const input = document.createElement('input'); input.value = 'repo query preserved';
  const details = document.createElement('details'); details.open = true;
  blocks.repository.element.append(input, details);
  const filter = document.createElement('select'); filter.value = 'important-filter'; blocks.map.element.append(filter);
  document.body.append(root, workspace, rightRoot);
  const media = { matches: mobile, addEventListener() {}, removeEventListener() {} };
  let activity;
  const manager = new WorkspacePanels({ workspace, panels, toggles, storage, media, bindToggles: false,
    onLayout: () => activity?.syncLayout() });
  activity = new ActivitySidebar({ root, roots: [root, rightRoot], blocks, panels: manager, storage, onActivate: id => activations.push(id) });
  return { document, root, rightRoot, workspace, panels, toggles, blocks, input, details, filter, writes, activations, manager, activity, media, store };
}

const shell = activityFixture();
assert.equal(shell.blocks.repository.element.hidden, false);
assert.equal(shell.blocks.search.element.hidden, true); assert.equal(shell.blocks.search.element.inert, true);
assert.equal(shell.blocks.tools.element.hidden, true);
shell.input.focus(); shell.activity.activate('search');
assert.equal(shell.blocks.repository.element.hidden, true); assert.equal(shell.blocks.repository.element.inert, true);
assert.equal(shell.document.activeElement, shell.blocks.repository.button, 'focus exits a newly hidden content block');
assert.equal(shell.blocks.search.button.getAttribute('aria-expanded'), 'true');
assert.equal(shell.input.value, 'repo query preserved'); assert.equal(shell.details.open, true);
assert.equal(shell.input.parentElement, shell.blocks.repository.element, 'existing controls retain DOM identity');
shell.activity.activate('repository'); assert.equal(shell.input.value, 'repo query preserved');
shell.activity.activate('map', { focus: true }); assert.equal(shell.document.activeElement, shell.filter);
assert.equal(shell.filter.value, 'important-filter');
shell.activity.activate('map', { toggle: true });
assert.equal(shell.manager.state.left, false); assert.equal(shell.panels.left.hidden, true); assert.equal(shell.panels.left.inert, true);
assert.equal(shell.blocks.map.button.getAttribute('aria-expanded'), 'false');
shell.activity.activate('tools');
const writesBeforeReveal = shell.writes.length;
shell.activity.reveal('details');
assert.equal(shell.activity.selected.right, 'details'); assert.equal(shell.activity.saved.right, 'tools');
assert.equal(shell.writes.length, writesBeforeReveal, 'temporary contextual reveal does not persist preferences');
const stored = JSON.parse(shell.store.get('atlas-activity-workspace-v3'));
assert.deepEqual(stored, { version: 3, left: 'map', right: 'tools' });
assert.equal(JSON.stringify(stored).includes('repo query'), false);
shell.activity.activate('health');
const healthWrites = shell.writes.length;
shell.activity.revealSelection();
assert.equal(shell.activity.selected.right, 'health', 'selecting another card retains the Health activity');
assert.equal(shell.writes.length, healthWrites, 'contextual updates do not rewrite activity preferences');
assert.equal(shell.blocks.details.element.hidden, true);
assert.equal(shell.blocks.health.element.hidden, false);
shell.activity.activate('tools');shell.activity.revealSelection();
assert.equal(shell.activity.selected.right, 'tools', 'card actions also retain their contextual activity');

shell.blocks.repository.button.focus();
event = key(shell.blocks.repository.button, 'End'); shell.root.dispatch('keydown', event);
assert.equal(event.prevented, true); assert.equal(shell.document.activeElement, shell.blocks.map.button);
shell.activity.dispose();
const activationsBeforeDispose = shell.activations.length;
shell.blocks.search.button.dispatch('click'); assert.equal(shell.activations.length, activationsBeforeDispose);
shell.manager.dispose();

const narrow = activityFixture({ mobile: true });
narrow.activity.activate('search'); assert.deepEqual(narrow.manager.state, { left: true, right: false });
narrow.activity.activate('tools'); assert.deepEqual(narrow.manager.state, { left: false, right: true });
assert.equal(narrow.panels.left.inert, true); assert.equal(narrow.blocks.search.button.getAttribute('aria-expanded'), 'false');
const toolControl = narrow.document.createElement('button'); narrow.blocks.tools.element.append(toolControl); toolControl.focus();
event = key(toolControl, 'Escape'); narrow.document.dispatch('keydown', event);
assert.equal(event.prevented, true); assert.equal(narrow.manager.state.right, false);
assert.equal(narrow.document.activeElement, narrow.blocks.tools.button, 'drawer Escape returns to current activity invoker');
narrow.activity.dispose(); narrow.manager.dispose();

const focused = activityFixture();
const chromeElements = {};
for (const name of ['toolbar','search','controls','context','canvas','canvasControls']) chromeElements[name] = focused.document.createElement('div');
const chromeToggles = { search: focused.blocks.search.button };
for (const name of ['controls','focus','canvas']) chromeToggles[name] = focused.document.createElement('button');
const chrome = new MapChrome({ body: focused.document.body, ...chromeElements, discovery: { root: focused.document.createElement('div'), setOpen() {} },
  panels: focused.manager, toggles: chromeToggles, storage: null, activitySearch: true, compact: true,
  onLayout: () => focused.activity.syncLayout() });
focused.activity.activate('map');
chrome.setOpen('controls', false);
assert.equal(focused.blocks.search.button.getAttribute('aria-expanded'), 'false', 'unrelated chrome apply cannot mark an inactive Search activity expanded');
assert.equal(focused.blocks.map.button.getAttribute('aria-expanded'), 'true');
const searchField = focused.document.createElement('input'); chromeElements.search.append(searchField);
const modeSelect = focused.document.createElement('select'); focused.document.body.append(modeSelect); modeSelect.focus();
const chromeWrites = []; chrome.storage = { setItem: (...entry) => chromeWrites.push(entry) };
chrome.setOpen('search', true, { focus: false, remember: false });
assert.equal(focused.document.activeElement, modeSelect, 'temporary search reveal preserves mode selector focus');
assert.equal(chromeWrites.length, 0, 'temporary search reveal leaves saved chrome preferences unchanged');
const activationOptions = [];
focused.activity.onActivate = (id, options) => activationOptions.push({ id, options });
focused.activity.reveal('search');
assert.deepEqual(activationOptions.at(-1), { id: 'search', options: { remember: false, focus: false } });
focused.manager.onLayout = info => { chrome.syncPanels(info); focused.activity.syncLayout(); };
focused.activity.activate('tools');
focused.manager.setOpen('right', false);
chrome.setFocus(true);
focused.activity.reveal('details');
assert.equal(chrome.state.focus, true, 'temporary Details reveal preserves map focus mode');
assert.equal(chrome.restore.right, false, 'contextual reveal cannot overwrite previous outer-panel restore');
assert.equal(focused.activity.saved.right, 'tools');
chrome.setFocus(false);
assert.equal(focused.manager.state.right, false, 'leaving focus restores the state before temporary reveal');
assert.equal(focused.activity.saved.right, 'tools');
chrome.dispose(); focused.activity.dispose(); focused.manager.dispose();

for (const saved of ['{broken', JSON.stringify({ version: 1, left: 'unknown', right: 'repository' }), JSON.stringify({ version: 8, left: 'map' })]) {
  const restored = activityFixture({ saved });
  assert.deepEqual(restored.activity.selected, { left: 'repository', right: 'details' });
  restored.activity.dispose(); restored.manager.dispose();
}

// Restore each supported preference shape without moving a section to the wrong side.
for (const [saved, expected] of [
  [{ version: 1, left: 'map', right: 'tools' }, { left: 'map', right: 'tools' }],
  [{ version: 2, left: 'tools' }, { left: 'repository', right: 'tools' }],
  [{ version: 2, left: 'constructor' }, { left: 'repository', right: 'details' }],
  [{ version: 3, left: 'search', right: 'tools' }, { left: 'search', right: 'tools' }],
  [{ version: 3, left: 'map', right: 'health' }, { left: 'map', right: 'health' }],
]) {
  const restored = activityFixture({ saved: JSON.stringify(saved) });
  assert.deepEqual(restored.activity.selected, expected);
  restored.activity.dispose(); restored.manager.dispose();
}
const keyboard = activityFixture();
keyboard.blocks.details.button.focus();
keyboard.rightRoot.dispatch('keydown', key(keyboard.blocks.details.button, 'ArrowDown'));
assert.equal(keyboard.document.activeElement, keyboard.blocks.health.button, 'Health participates in right-rail keyboard order');
keyboard.blocks.details.button.focus();
keyboard.rightRoot.dispatch('keydown', key(keyboard.blocks.details.button, 'End'));
assert.equal(keyboard.document.activeElement, keyboard.blocks.tools.button, 'right rail keyboard stays on its own edge');
keyboard.rightRoot.dispatch('keydown', key(keyboard.blocks.tools.button, 'Home'));
assert.equal(keyboard.document.activeElement, keyboard.blocks.details.button);
keyboard.activity.dispose(); keyboard.manager.dispose();

/** Pytest supplies the current HTML topology: mounting must retain its real controls and listeners. */
function verifyActualMarkup(tree) {
  const document = documentFixture();
  function build(item) {
    if (typeof item === 'string') return item;
    const element = document.createElement(item.tag);
    for (const [name, value] of item.attrs) {
      element.setAttribute(name, value ?? '');
      if (name === 'hidden') element.hidden = true;
    }
    for (const child of item.children) element.append(build(child));
    return element;
  }
  for (const child of tree.children) document.body.append(build(child));
  const find = id => document.getElementById(id);
  const expected = {
    projects: 'repository', 'repository-add': 'repository', rebuild: 'repository', results: 'repository', more: 'repository',
    'navigation-search': 'search', 'semantic-query': 'search', 'semantic-query-form': 'search',
    'navigation-search-scope': 'search', 'display-provider': 'map', 'view-mode': 'map', search: 'map', kind: 'map', relation: 'map',
    'selection': 'details', 'isolate-node': 'tools', 'source-open': 'tools',
    'saved-views-panel': 'map', 'agent-setup-panel': 'repository', 'documentation-view': 'tools', 'query-form': 'search', 'compare-form': 'repository',
  };
  const before = new Map(Object.keys(expected).map(id => [id, find(id)]));
  const catalogSearch = find('ingress-catalog-search');
  const allIds = document.querySelectorAll('[id]').map(element => element.id);
  let handled = 0;
  before.get('rebuild').addEventListener('click', () => { handled++; });
  before.get('navigation-search').value = 'unchanged query'; before.get('saved-views-panel').open = true;
  const layout = mountWorkspaceLayout(document);
  const projectPicker = document.querySelector('.workspace-brand');
  assert.equal(projectPicker.href, '/', 'project selection uses the existing home route');
  assert.equal(projectPicker.getAttribute('aria-label'), 'Выбрать проект');
  assert.equal(projectPicker.title, 'Выбрать проект');
  const contextualActions = find('source-open').closest('.selected-node-actions');
  assert.equal(contextualActions.hidden, true, 'empty inspector starts without inactive raw-symbol commands');
  for (const mode of ['architecture','groups','system','architecture']) {
    presentViewMode(mode, document);
    assert.equal(contextualActions.hidden, true, `${mode} mode cannot unhide selection-owned inspector controls`);
  }
  for (const [id, activity] of Object.entries(expected)) {
    const element = find(id);
    assert.equal(element, before.get(id), `${id} keeps its existing DOM identity`);
    assert.equal(layout.blocks[activity].element.contains(element), true, `${id} belongs to ${activity}`);
    assert.equal(element.closest('[data-navigation-ignore]'), null, `${id} keeps existing navigation observation`);
  }
  for (const id of allIds) assert.equal(document.querySelectorAll(`[id="${id}"]`).length, 1, `${id} remains unique`);
  assert.equal(find('ingress-catalog-search'), catalogSearch, 'entry popup keeps the existing search input');
  assert.equal(find('ingress-catalog-dialog').contains(catalogSearch), true, 'entry search remains in its own popup');
  before.get('rebuild').dispatch('click'); assert.equal(handled, 1, 'bound listener survives relocation');
  assert.equal(find('navigation-search').value, 'unchanged query'); assert.equal(find('saved-views-panel').open, true);
  assert.equal(find('semantic-query').getAttribute('form'), 'semantic-query-form');
  assert.equal(layout.rail.contains(find('panel-left-toggle')), true);
  assert.equal(layout.rail.contains(find('panel-right-toggle')), false);
  assert.equal(layout.rightRail.contains(find('panel-right-toggle')), true);
  assert.equal(layout.rightRail.contains(find('activity-tools')), true);
  assert.equal(layout.rightRail.contains(find('activity-health')), true);
  assert.equal(layout.blocks.health.element.contains(find('node-health-panel')), true);
  assert.equal(layout.rail.children[0], find('panel-left-toggle'), 'Projects is first even with native navigation');
  assert.equal(find('panel-left-toggle').getAttribute('aria-label'), 'Проекты');
  const railActions = layout.rail.querySelector('.activity-rail-actions');
  assert.ok(railActions.contains(find('workspace-menu')), 'map commands survive removal of the web window toolbar');
  assert.ok(railActions.contains(document.querySelector('.workspace-pages')), 'Atlas navigation stays independent of native capability');
  assert.equal(layout.rail.children.length, 4); assert.equal(layout.rightRail.children.length, 3);
  for (const block of Object.values(layout.blocks)) {
    assert.equal(block.element.parentElement, find(`workspace-${block.side}`));
    const region = block.side === 'left' ? layout.rail : layout.rightRail;
    assert.equal(region.contains(block.button), true);
  }
  assert.equal(find('status').parentElement.classList.contains('workspace-statusbar'), true);
  for (const direction of ['back','forward']) {
    const button = layout.historyControls[direction];
    assert.equal(button, find(`map-history-${direction}`));
    assert.equal(button.disabled, true, 'history controls start disabled until navigation is ready');
    assert.ok(button.closest('[data-navigation-ignore]'), 'history buttons never create a new semantic transition');
  }
  const menu = find('workspace-menu'), summary = menu.querySelector('summary');
  menu.open = true;
  const escape = key(find('map-focus-toggle'), 'Escape'); document.dispatch('keydown', escape);
  assert.equal(menu.open, false); assert.equal(escape.prevented, true); assert.equal(document.activeElement, summary);
  menu.open = true; document.dispatch('pointerdown', { target: before.get('navigation-search') });
  assert.equal(menu.open, false, 'using another surface closes More without additional shell state');
  menu.open = true; document.dispatch('pointerdown', { target: summary });
  assert.equal(menu.open, true, 'menu-local pointer events remain native');
  document.dispatch('focusin', { target: find('projects') });
  assert.equal(menu.open, false, 'keyboard focus outside closes the menu');
}
if (process.argv[2]) verifyActualMarkup(JSON.parse(readFileSync(process.argv[2], 'utf8')));

console.log('Activity workspace local Map Find contracts PASS');
