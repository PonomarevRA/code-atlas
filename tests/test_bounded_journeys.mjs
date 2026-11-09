/** Pending транспорт/JSON не оставляет поиск, анализ или Browser Back в вечной загрузке. */
import assert from 'node:assert/strict';
import { requestJson } from '../web/bounded-request.js';
import { createIngressController } from '../web/ingress-controller.js';
import { createWorkspaceNavigation } from '../web/workspace-navigation.js';
import { NavigationHistory, navigationSnapshot } from '../web/navigation-history.js';
import { ExecutionPathViewer } from '../web/execution-path.js';

class Element {
  constructor() { this.value = ''; this.hidden = false; this.checked = true; this.disabled = false; this.options = [{ value: '' }]; this.children = []; this.listeners = {}; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute() {}
  replaceChildren(...nodes) { this.children = nodes; }
  append(...nodes) { this.children.push(...nodes); }
}
const fields = new Map();
/** Lazy fixture elements support real controllers without booting the application. */
function field(id) { if (!fields.has(id)) fields.set(id, new Element()); return fields.get(id); }
globalThis.document = { getElementById: field, createElement: () => new Element() };
for (const [id, value] of Object.entries({ 'view-mode': 'architecture', 'display-provider': 'flow', 'system-scale': 'repositories', 'architecture-preset': 'all', 'discovery-mode': 'code', 'semantic-kind': 'all', 'navigation-search-scope': 'all' })) field(id).value = value;
const raw = { id: 'root', label: 'Root()', file: 'Root.cs', codeProjectId: 'p' };
/** Each case owns a fresh project/generation so stale work cannot accidentally pass via previous fixtures. */
function projectState() {
  return { project: { id: 'repo' }, projects: [{ id: 'repo' }],
    graph: { metadata: { snapshotId: 'snap' }, nodes: [raw], edges: [], codeProjects: [] },
    nodes: new Map([[raw.id, raw]]), architecture: { level: 'projects', projectId: null, file: null, page: 0 },
    selected: 'root', algorithm: 'expert', algorithms: [{ id: 'expert' }],
    ingress: { data: null, revision: 0, status: 'idle' }, inlineExpanded: new Set(), codeProjectIds: null };
}
/** Real timeout code uses a short deadline; the transport deliberately ignores AbortSignal. */
function pendingRequest(kind) {
  return (url, options) => requestJson(url, options, { timeoutMs: 8, fetcher: () => kind === 'transport'
    ? new Promise(() => {}) : Promise.resolve({ ok: true, json: () => new Promise(() => {}) }) });
}
for (const kind of ['transport', 'json']) {
  const state = projectState();
  let draws = 0, ready = 0;
  const ingress = createIngressController({ state, view: {}, filteredNodes: () => state.graph.nodes,
    draw: () => draws++, controls: () => {}, selectNode: () => {}, onReady: () => ready++, request: pendingRequest(kind) });
  await ingress.load();
  assert.equal(state.ingress.status, 'error', kind);
  assert.match(state.ingress.message, /Сервер долго отвечает/);
  assert.equal(field('ingress-catalog-retry').hidden, false);
  assert.equal(draws, 0); assert.equal(ready, 0); assert.equal(state.ingress.data, null);
}
// A rejection from a former repository cannot replace the new catalogue's status.
{
  const state = projectState();
  let reject;
  const ingress = createIngressController({ state, view: {}, filteredNodes: () => [], draw: () => assert.fail('stale draw'),
    controls: () => {}, selectNode: () => {}, request: () => new Promise((_, fail) => { reject = fail; }) });
  const old = ingress.load();
  state.project = { id: 'new-repo' }; state.ingress.status = 'ready'; state.ingress.message = 'New catalogue';
  reject(new Error('old failure')); await old;
  assert.equal(state.ingress.status, 'ready'); assert.equal(state.ingress.message, 'New catalogue');
}

/** Browser-history fixture exercises the real restoring flag, not only a request helper. */
function workspaceFixture(request) {
  const state = projectState(), calls = [];
  const record = name => () => calls.push(name);
  const view = { camera: { x: 0, y: 0, scale: 1 }, pathExpandedIds: new Set(), pathCollapsedIds: new Set(), setSelected() {}, transform() {} };
  const viewers = { wikiViewer: { close() {} }, pathViewer: { close() {}, entry: null }, factsPopup: { close() {} },
    documentationViewer: { select() {} }, systemView: { page: 0 }, mapBlocks: {} };
  const controller = createWorkspaceNavigation({ state, view, viewers, request,
    search: { searchNavigation: { cancel() {} }, navigationSearch: { refresh() {} }, semanticSearch: { invalidateSearch() {} } },
    actions: { updateDisplayProvider() {}, updateDiscovery() {}, openProject: async () => assert.fail('unneeded reload'),
      draw: record('draw'), filteredNodes: () => state.graph.nodes, displayedAggregate: () => ({ communities: [] }),
      renderCodeProjects() {}, renderSelection() {}, loadIngress: async () => {}, paintPathGraph: record('paint'),
      controls: record('controls'), status: message => calls.push(message), closeTransientStudy: record('close-transient') } });
  const browser = { addEventListener() {}, location: { href: 'http://127.0.0.1/?repo=repo' },
    history: { replaceState() {}, pushState() {} } };
  const history = new NavigationHistory({ browser, getSnapshot: controller.capture, restore: controller.restore });
  const saved = navigationSnapshot({ route: { repo: 'repo', view: 'architecture', path: 'root' }, snapshotId: 'snap' });
  return { state, calls, history, saved };
}
for (const kind of ['transport', 'json']) {
  const { state, calls, history, saved } = workspaceFixture(pendingRequest(kind));
  await history.restoreEntry(saved);
  assert.equal(history.restoring, false); assert.equal(history.ready, true);
  assert.equal(state.pathOnGraph, null); assert.ok(calls.includes('draw')); assert.ok(calls.includes('controls'));
  assert.equal(calls[0], 'close-transient'); assert.ok(!calls.includes('paint'));
  assert.ok(calls.some(message => message.includes('Сервер долго отвечает')));
}
// A newer navigation cancels the old one; an adapter ignoring abort still cannot publish.
{
  let resolve;
  const { state, calls, history, saved } = workspaceFixture(() => new Promise(done => { resolve = done; }));
  const old = history.restoreEntry(saved);
  await history.restoreEntry(navigationSnapshot({ route: { repo: 'repo', view: 'architecture' }, snapshotId: 'snap' }));
  resolve({ snapshotId: 'snap', rootNodeId: 'root', steps: [{ nodeId: 'root' }], edges: [] });
  await old;
  assert.equal(state.pathOnGraph, null); assert.ok(!calls.includes('paint'));
  assert.equal(history.restoring, false);
}

// ExecutionPathViewer has a production 30-second budget; fake clocks shorten that same timer in this fixture.
const originalTimeout = globalThis.setTimeout;
globalThis.setTimeout = (callback, delay, ...args) => originalTimeout(callback, delay === 30000 ? 8 : delay, ...args);
try {
  for (const kind of ['transport', 'json']) {
    const pathFields = new Map();
    const root = new Element(); root.querySelector = selector => {
      const name = selector.match(/"(.*?)"/)[1];
      if (!pathFields.has(name)) pathFields.set(name, new Element()); return pathFields.get(name);
    };
    let published = 0, errors = [];
    const viewer = new ExecutionPathViewer(root, () => ({ projectId: 'repo', snapshotId: 'snap', nodeId: 'root' }), {
      fetcher: () => kind === 'transport' ? new Promise(() => {}) : Promise.resolve({ ok: true, json: () => new Promise(() => {}) }),
      onResult: () => published++, onError: message => errors.push(message),
    });
    await viewer.open();
    assert.equal(pathFields.get('build').disabled, false); assert.equal(pathFields.get('retry').hidden, false);
    assert.equal(published, 0); assert.equal(errors.length, 1); assert.match(errors[0], /Сервер долго отвечает/);
  }
} finally { globalThis.setTimeout = originalTimeout; }
console.log('Bounded journeys PASS');
