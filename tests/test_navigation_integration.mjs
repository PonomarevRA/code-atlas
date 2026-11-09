/** Реальные GraphView и NavigationHistory: выбор с клавиатуры, pan и восстановление камеры. */
import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';
import { NavigationHistory, pathDetailToken, readRoute } from '../web/navigation-history.js';
import { Svg } from './svg_fixture.mjs';

/** Минимальный browser сохраняет записи и URL; popstate доставляется тем же listener, что в приложении. */
class Browser {
  constructor() {
    this.location = { href: 'http://localhost/?keep=1#section' };
    this.records = [{ state: null, href: this.location.href }];
    this.index = 0;
    this.listeners = {};
    const browser = this;
    this.history = {
      get state() { return browser.records[browser.index].state; },
      replaceState(state, _, href) { browser.write(state, href, false); },
      pushState(state, _, href) { browser.write(state, href, true); },
    };
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  /** Structured clone предотвращает ложный PASS от общей ссылки на живую камеру. */
  write(state, href, push) {
    const record = { state: structuredClone(state), href };
    if (push) {
      this.records.splice(this.index + 1);
      this.records.push(record);
      this.index += 1;
    } else this.records[this.index] = record;
    this.location.href = href;
  }
  /** Browser Back/Forward изменяет адрес до доставки popstate. */
  go(delta) {
    this.index += delta;
    const record = this.records[this.index];
    this.location.href = record.href;
    this.listeners.popstate({ state: structuredClone(record.state) });
  }
}

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
Svg.prototype.contains = function (target) { return this === target || this.children.some(child => child.contains(target)); };
Svg.prototype.getAttribute = function (name) { return this.attributes[name] ?? null; };
const svg = new Svg('svg');
const viewport = new Svg('g');
viewport.setAttribute('id', 'viewport');
svg.append(viewport);
const browser = new Browser();
const graph = { nodes: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }], edges: [] };
const pathGraph = { nodes: [...graph.nodes, { id: 'c', label: 'C' }], edges: [
  { source: 'a', target: 'b', relation: 'calls' }, { source: 'b', target: 'c', relation: 'calls' },
] };
const view = new GraphView(svg, id => {
  history.checkpoint();
  view.setSelected(id);
  history.commit();
}, () => {});
const failures = [];
const history = new NavigationHistory({
  browser,
  getSnapshot: () => ({ route: { repo: 'repo', node: view.selectedId,
    path: view.pathSceneActive ? 'a' : null,
    detail: view.pathSceneActive ? pathDetailToken(view.pathExpandedIds, view.pathCollapsedIds) : null },
    pathExpanded: view.pathSceneActive ? view.pathExpandedIds : [],
    pathCollapsed: view.pathSceneActive ? view.pathCollapsedIds : [],
    snapshotId: 'snapshot', camera: view.camera }),
  restore: async snapshot => {
    if (snapshot.route.path) {
      view.pathRootId = snapshot.route.path;
      view.pathSnapshotId = snapshot.snapshotId;
      view.renderPathCards(pathGraph.nodes, pathGraph.edges, snapshot.route.node);
      view.pathExpandedIds = new Set(snapshot.pathExpanded);
      view.pathCollapsedIds = new Set(snapshot.pathCollapsed);
      view.renderPathCards(pathGraph.nodes, pathGraph.edges, snapshot.route.node);
    } else view.render(graph.nodes, graph.edges, snapshot.route.node);
    if (snapshot.camera) view.camera = { ...snapshot.camera };
    view.transform();
  },
  onError: error => failures.push(error),
});
view.onCameraChange = () => history.commit({ replace: true });
await history.start({ repo: 'repo' });
view.camera = { x: 120, y: 80, scale: .7 };
history.commit({ replace: true });

/** Доставляет событие сначала карточке, затем SVG, сохраняя native defaultPrevented. */
function press(key, id, modifiers = {}) {
  const target = id ? view.nodeElements.get(id).group : svg;
  const event = { key, target, defaultPrevented: false, ...modifiers,
    preventDefault() { this.defaultPrevented = true; } };
  target.listeners.keydown?.(event);
  if (target !== svg) svg.listeners.keydown(event);
  return event;
}

assert.ok(press('Enter', 'a').defaultPrevented, 'existing Enter selects the raw node');
assert.equal(view.selectedId, 'a');
assert.equal(browser.records.length, 2);
assert.equal(readRoute(browser.location.href).node, 'a');
const scale = view.camera.scale;
for (let index = 0; index < 5; index += 1) press('ArrowRight', 'a');
assert.equal(browser.records.length, 2, 'repeated arrows never push navigation entries');
assert.deepEqual(view.camera, { x: -200, y: 80, scale });
assert.equal(view.selectedId, 'a', 'pan does not trigger selection callback');
const cameraA = { ...view.camera };
assert.equal(press('ArrowLeft', 'a', { altKey: true }).defaultPrevented, false);
assert.deepEqual(view.camera, cameraA, 'browser Alt+Left is not captured');

assert.ok(press(' ', 'b').defaultPrevented, 'existing Space selects the raw node');
assert.equal(view.selectedId, 'b');
assert.equal(browser.records.length, 3);
press('ArrowDown', 'b', { shiftKey: true });
const cameraB = { ...view.camera };
assert.deepEqual(cameraB, { x: -200, y: -112, scale });

browser.go(-1);
await Promise.resolve();
assert.equal(view.selectedId, 'a');
assert.deepEqual(view.camera, cameraA, 'Back restores exact camera before B selection');
assert.equal(readRoute(browser.location.href).node, 'a');
assert.equal(browser.records.length, 3, 'restore never creates a navigation entry');
browser.go(1);
await Promise.resolve();
assert.equal(view.selectedId, 'b');
assert.deepEqual(view.camera, cameraB, 'Forward restores later pan state');
assert.equal(readRoute(browser.location.href).node, 'b');
assert.equal(new URL(browser.location.href).searchParams.get('keep'), '1');
assert.equal(new URL(browser.location.href).hash, '#section');
assert.deepEqual(failures, []);

// Раскрытие реального блока сохраняется вместе с камерой, а не только как строка в codec-тесте.
view.onPathScopeChange = () => queueMicrotask(() => history.commit());
history.checkpoint();
view.pathRootId = 'a';
view.pathSnapshotId = 'snapshot';
view.renderPathCards(pathGraph.nodes, pathGraph.edges, 'a');
history.commit();
await Promise.resolve();
assert.ok(!view.nodeElements.has('c'));
history.checkpoint();
view.nodeElements.get('b').group.querySelector('.path-card-expand').listeners.click({ stopPropagation() {} });
await Promise.resolve();
assert.ok(view.nodeElements.has('a') && view.nodeElements.has('b') && view.nodeElements.has('c'));
assert.equal(view.pathScope.stats.get('b').expanded, true);
press('ArrowRight', 'b');
const expandedCamera = { ...view.camera };
const expansionRecords = browser.records.length;
history.checkpoint();
view.togglePathNode('b');
await Promise.resolve();
assert.ok(!view.nodeElements.has('c'));
assert.equal(browser.records.length, expansionRecords + 1);
const collapsedCamera = { ...view.camera };
browser.go(-1);
await Promise.resolve();
assert.ok(view.nodeElements.has('c'), 'Back restores the actual expanded implementation');
assert.equal(view.pathScope.stats.get('b').expanded, true);
assert.equal(view.selectedId, 'a');
assert.deepEqual(view.camera, expandedCamera);
browser.go(1);
await Promise.resolve();
assert.ok(!view.nodeElements.has('c'), 'Forward restores collapsed context without changing root');
assert.equal(view.pathScope.stats.get('b').expanded, false);
assert.deepEqual(view.camera, collapsedCamera);
assert.deepEqual(failures, []);
console.log('Graph keyboard activation, history pan replacement, Back/Forward camera, raw selection and expanded path PASS');
