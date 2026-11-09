import assert from 'node:assert/strict';
import { MethodWorkbench } from '../web/method-workbench.js';
import { readerCallEvent, readerPreview, readerDependencyIndex, normalizeReaderState } from '../web/method-reader-model.js';
import { renderReaderSteps } from '../web/method-reader-steps.js';
import { createMethodWorkbench } from '../web/method-workbench-controller.js';
import { navigationSnapshot, normalizeRoute, resolveNavigation, inlineDetailToken } from '../web/navigation-history.js';
import { createWorkspaceNavigation } from '../web/workspace-navigation.js';
import { GraphView } from '../web/graph.js';
import { buildMethodSequence } from '../web/method-sequence.js';
import { InlineHierarchyRenderer } from '../web/inline-hierarchy-renderer.js';
import { inlineFlowLayout } from '../web/inline-flow-layout.js';
import { createMapBlocks } from '../web/map-blocks.js';
import { Svg } from './svg_fixture.mjs';

/** Attribute-aware minimal HTML fixture; tests use the public reader actions and addresses. */
class Html extends Svg {
  constructor(tag) { super(tag); this.style = { setProperty() {} }; this.scrollTop = 0; }
  get className() { return this.attributes.class || ''; }
  set className(value) { this.attributes.class = value; }
  get isConnected() { return Boolean(this.parent) || this.tag === 'section'; }
  get dataset() { return Object.fromEntries(Object.entries(this.attributes).filter(([key]) => key.startsWith('data-')).map(([key, value]) => [key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), value])); }
  matches(selector) {
    const attributes = [...selector.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)];
    if (attributes.some(([, key, value]) => !(key in this.attributes) || value !== undefined && String(this.attributes[key]) !== value)) return false;
    const base = selector.replace(/\[[^\]]+\]/g, '');
    if (!base) return true;
    if (base.startsWith('.')) return this.className.split(' ').includes(base.slice(1));
    if (base.startsWith('#')) return this.attributes.id === base.slice(1);
    return this.tag === base;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map(value => value.trim()), result = [];
    for (const child of this.children) {
      if (selectors.some(value => child.matches(value))) result.push(child);
      result.push(...child.querySelectorAll(selector));
    }
    return result;
  }
  closest(selector) { return this.matches(selector) ? this : this.parent?.closest?.(selector); }
  focus() { globalThis.document.activeElement = this; }
  appendChild(node) { this.append(node); return node; }
}
globalThis.document = { createElement: tag => new Html(tag), createElementNS: (_, tag) => new Html(tag),
  createTextNode: text => { const node = new Html('#text'); node.textContent = text; return node; } };
const tick = async () => { for (let index = 0; index < 16; index++) await Promise.resolve(); };
function rootFixture() {
  const root = new Html('section'); root.hidden = true;
  for (const name of ['diagram', 'back', 'body', 'breadcrumbs']) {
    const child = new Html(['diagram', 'back'].includes(name) ? 'button' : 'div');
    child.setAttribute('data-reader', name); root.append(child);
  }
  return root;
}
function click(node) {
  assert.ok(node, 'The expected public reader action exists');
  const event = { target: node, preventDefault() {}, stopPropagation() {} };
  if (node.listeners.click) node.listeners.click(event);
  else { let parent = node.parent; while (parent && !parent.listeners.click) parent = parent.parent; parent?.listeners.click(event); }
}
const flow = { status: 'parsed', steps: [
  { kind: 'operation', line: 10, label: 'result = null' },
  { kind: 'branch', controlKind: 'if', line: 11, label: 'ready', arms: [
    { label: 'истина', steps: [{ kind: 'operation', line: 12, label: 'result = input' }] },
    { label: 'иначе', steps: [{ kind: 'return', line: 13, label: 'return null;' }] }] },
  { kind: 'loop', controlKind: 'foreach', line: 14, label: 'foreach (var row in rows)', condition: [], update: [],
    body: [{ kind: 'call', label: 'Use()', line: 15, column: 1, site: { line: 15, column: 1 } }] },
  { kind: 'return', line: 16, label: 'return result;' },
] };
// Structural previews and dependency kinds are separate from source execution order.
const preview = readerPreview({ methodFlow: { ...flow, steps: [...flow.steps, { kind: 'throw', label: 'throw error;' }] } });
assert.equal(preview.steps.length, 4); assert.equal(preview.total, 5); assert.equal(preview.limited, true);
assert.equal(preview.steps[1].arms.length, 2, 'Preview preserves the branch, never a flattened execution claim');
const chained = { events: [{ line: 10, column: 1, endLine: 10, endColumn: 12, targetNodeIds: ['first'] },
  { line: 10, column: 1, endLine: 10, endColumn: 19, targetNodeIds: ['second'] }] };
assert.deepEqual(readerCallEvent(chained, { site: { line: 10, column: 1, endLine: 10, endColumn: 19 } }).targetNodeIds, ['second']);
assert.equal(readerCallEvent(chained, { site: { line: 10, column: 1, endLine: 10, endColumn: 99 } }), undefined);
const dependencies = readerDependencyIndex({ edges: ['calls', 'reads', 'writes', 'inherits', 'implements', 'dispatches_to'].map(relation => ({ source: 'outside', target: relation, relation })) });
assert.deepEqual(dependencies.get('outside').map(edge => edge.relation), ['calls', 'reads', 'writes', 'inherits', 'implements', 'dispatches_to']);
assert.equal(normalizeReaderState({ mode: 'method', focusId: 'raw', trail: Array.from({ length: 25 }, () => ({ mode: 'method', focusId: 'raw' })) }).trail.length, 16);
const regions = new Html('div');
renderReaderSteps(regions, [{ kind: 'branch', label: 'ready', arms: [
  { label: 'Skipped', steps: [], emptyReason: 'skip' }, { label: 'Unknown', steps: [], emptyReason: 'not-extracted' }] },
  { kind: 'loop', controlKind: 'do', label: 'do', body: [{ kind: 'operation', label: 'Work()' }],
    condition: [{ kind: 'call', label: 'Check()', line: 3, column: 1 }], update: [] },
  { kind: 'throw', label: 'throw error;' }], { nodeId: 'raw', events: [] }, { onSource() {}, onCall() {} });
const arms = regions.querySelectorAll('.reader-arm');
assert.ok(arms.find(arm => arm.attributes['data-arm-label'] === 'Skipped').textContent.includes('пропускает'));
assert.ok(arms.find(arm => arm.attributes['data-arm-label'] === 'Unknown').textContent.includes('не извлечено'));
assert.ok(arms.findIndex(arm => arm.textContent.includes('Work()')) < arms.findIndex(arm => arm.textContent.includes('Check()')),
  'A do-loop body is read before its condition');
assert.ok(regions.querySelector('[data-kind="throw"]'));
const bounded = new Html('div');
renderReaderSteps(bounded, flow.steps, { nodeId: 'raw' }, { onSource() {}, onCall() {}, budget: { remaining: 1 } });
assert.ok(bounded.querySelector('.reader-notice'), 'The rendering budget cannot masquerade as a complete method');
const switchLoop = new Html('div');
renderReaderSteps(switchLoop, [{ kind: 'loop', controlKind: 'while', label: 'while (ready)', condition: [], update: [], body: [
  { kind: 'branch', controlKind: 'switch', label: 'code', arms: [{ label: 'case 1', steps: [{ kind: 'break', label: 'break;' }] }] },
  { kind: 'operation', label: 'AfterSwitch()' }, { kind: 'continue', label: 'continue;' }] }],
  { nodeId: 'raw' }, { onCall() {}, onSource() {} });
const switchRegion = switchLoop.querySelector('[data-kind="branch"]');
assert.ok(switchRegion.textContent.includes('switch'));
assert.ok(switchRegion.querySelector('[data-kind="break"]'));
assert.equal(switchRegion.querySelector('[data-kind="continue"]'), undefined, 'Continue remains in the loop, outside the switch arm');
assert.ok(switchLoop.textContent.includes('цикла или switch'), 'Loop explanation acknowledges a nearer switch break');
const desc = (nodeId, snapshotId = 's') => ({ nodeId, snapshotId, label: `${nodeId}()`, file: 'C.cs', line: 8,
  documentation: { summary: `Purpose of ${nodeId}` }, methodFlow: flow, sourceKind: 'working-tree',
  events: [{ kind: 'call', text: 'Use()', callee: 'Use', line: 15, column: 1, targetNodeIds: ['helper'] }],
  calls: { incoming: [], outgoing: [] }, complexity: { score: 4, level: 'low' } });
const items = Array.from({ length: 50 }, (_, i) => ({ id: `impl${i}`, label: `Module${i}.Load()`, file: `Module${i}.cs`, line: 8,
  scope: i < 5 || i >= 10 ? 'source' : i === 5 ? 'delegate' : 'tests' }));
const origin = { originMode: 'path', occurrenceId: 'caller::call:10:1', ownerNodeId: 'caller', line: 10,
  expression: 'Load(input)', targetIds: ['iface'], implementationItems: items, candidateCount: 50 };
let context = { projectId: 'p', snapshotId: 's' };
const graph = { nodes: [{ id: 'caller', label: 'Caller()' }, { id: 'field', label: 'State' }],
  edges: [{ source: 'impl6', target: 'field', relation: 'reads' }, { source: 'impl6', target: 'helper', relation: 'calls' },
    ...items.map(item => ({ source: 'iface', target: item.id, relation: 'dispatches_to' }))] };
const originalGraph = JSON.stringify(graph), requests = [], closures = [], sourceActions = [];
const fetcher = (url, options) => new Promise(resolve => requests.push({ url, options, id: JSON.parse(options.body).nodeId,
  resolve: data => resolve({ ok: true, json: async () => data }) }));
const root = rootFixture();
const reader = new MethodWorkbench(root, { getContext: () => context, getNode: id => graph.nodes.find(node => node.id === id),
  getGraph: () => graph, onSource: (...args) => sourceActions.push(args), onClose: value => closures.push(value), onChange: () => {}, fetcher });

// All bounded names appear immediately; loading bodies cannot erase sibling alternatives.
reader.open(origin); await tick();
assert.equal(root.hidden, false);
assert.equal(root.querySelectorAll('[data-reader-candidate]').length, 50);
assert.equal(requests.length, 2, 'Initial preview loading obeys the two-request limit');
assert.ok(requests.every(request => items.find(item => item.id === request.id).scope === 'source'));

// The seventh candidate is outside the selected global graph and still has a prioritized direct read.
const reading = reader.read('impl6'); await tick();
requests[0].resolve(desc(requests[0].id)); await tick();
assert.equal(requests[2].id, 'impl6', 'Explicit read precedes queued automatic previews');
requests[2].resolve(desc('impl6')); await reading; await tick();
assert.equal(reader.snapshot().mode, 'method');
assert.equal(reader.snapshot().focusId, 'impl6');
assert.ok(root.textContent.includes('Purpose of impl6'));
assert.equal(root.querySelectorAll('.reader-arm').filter(node => node.attributes['data-arm-label'] === 'истина').length, 1);
assert.equal(root.querySelectorAll('.reader-arm').filter(node => node.attributes['data-arm-label'] === 'иначе').length, 1);
assert.ok(root.querySelector('[data-kind="loop"]'), 'Loop is a separate lexical region');
assert.ok(root.querySelector('[data-kind="return"]'), 'Terminal return stays visible');
assert.ok(root.textContent.includes('State'), 'Full snapshot data dependencies supplement description calls');
assert.equal(JSON.stringify(graph), originalGraph);
click(root.querySelector('[data-reader-action="source"][data-node-id="impl6"]'));
assert.equal(sourceActions.at(-1)[0], 'impl6');
click(root.querySelector('[data-reader-action="dependency"][data-node-id="field"]'));
assert.equal(sourceActions.at(-1)[0], 'field', 'A data dependency opens its source rather than treating it as a called method');
assert.equal(reader.snapshot().focusId, 'impl6');

// Local navigation restores the exact occurrence and reading scroll; global map state is untouched.
root.scrollTop = 137;
const localRead = reader.read('helper', { ownerNodeId: 'impl6', occurrenceId: 'impl6::call:15:1', line: 15 }); await tick();
while (!requests.some(request => request.id === 'helper')) {
  const pending = requests.find(request => !request.done && request.id !== 'impl6');
  assert.ok(pending); pending.done = true; pending.resolve(desc(pending.id)); await tick();
}
requests.find(request => request.id === 'helper').resolve(desc('helper'));
await localRead; await tick();
assert.equal(reader.snapshot().focusId, 'helper');
await reader.back(); await tick();
assert.equal(reader.snapshot().focusId, 'impl6');
assert.equal(root.scrollTop, 137);
assert.equal(reader.snapshot().origin.occurrenceId, origin.occurrenceId);
assert.ok(reader.snapshot().trail.length <= 16);
const saved = reader.snapshot();
assert.equal(saved.projectId, 'p'); assert.equal(saved.snapshotId, 's');
assert.ok(!JSON.stringify(saved).includes('Purpose of'), 'History contains bounded addresses, never descriptions');
click(root.querySelector('[data-reader="diagram"]'));
assert.equal(root.hidden, true);
assert.equal(closures.at(-1).occurrenceId, origin.occurrenceId);
await reader.restore(saved); await tick();
assert.equal(root.hidden, false); assert.equal(reader.snapshot().focusId, 'impl6');
assert.equal(root.scrollTop, 137);
const beforeWrongRestore = reader.snapshot();
try { await reader.restore({ ...saved, snapshotId: 'other' }); } catch { /* Explicit rejection is also valid. */ }
assert.ok(reader.snapshot() === null && root.hidden || JSON.stringify(reader.snapshot()) === JSON.stringify(beforeWrongRestore),
  'Incompatible history is rejected or closes the reader; it cannot display the wrong revision');
if (root.hidden) await reader.restore(saved);
await reader.back(); await tick();
assert.equal(reader.snapshot().mode, 'compare');
assert.equal(root.querySelectorAll('[data-reader-candidate]').length, 50);
for (const candidate of root.querySelectorAll('.reader-preview')) assert.ok(candidate.querySelectorAll('.reader-step').length <= 4);
reader.close();

// Settle ignored-abort transports before the next context; active requests still own concurrency slots.
for (let turn = 0; turn < 20; turn++) {
  const pending = requests.filter(request => !request.done);
  if (!pending.length) break;
  for (const request of pending) { request.done = true; request.resolve(desc(request.id)); }
  await tick();
}

// New contexts close the reader; ignored late replies cannot put old facts into the DOM.
reader.open({ ...origin, implementationItems: [{ ...items[49], id: 'stale' }], candidateCount: 1, targetIds: ['stale'] }); await tick();
const stale = requests.find(request => request.id === 'stale');
assert.ok(stale);
const oldText = root.textContent;
context = { projectId: 'p', snapshotId: 'new' }; reader.sync(); await tick();
assert.equal(root.hidden, true);
stale.resolve(desc('stale')); await tick();
assert.equal(root.hidden, true); assert.equal(root.textContent, oldText);

// A failed preview retains siblings and offers an explicit retry, rather than an empty method.
const failureRoot = rootFixture(), failures = new Map();
const failureReader = new MethodWorkbench(failureRoot, { getContext: () => ({ projectId: 'p', snapshotId: 's' }),
  getNode: () => undefined, getGraph: () => ({ nodes: [], edges: [] }), onSource() {}, onClose() {}, onChange() {},
  fetcher: async (_url, options) => {
    const id = JSON.parse(options.body).nodeId, count = failures.get(id) || 0; failures.set(id, count + 1);
    return id === 'impl0' && count === 0 ? { ok: false, status: 503, json: async () => ({ error: 'Source temporarily unavailable' }) }
      : { ok: true, json: async () => desc(id) };
  } });
failureReader.open({ ...origin, implementationItems: items.slice(0, 7), candidateCount: 7 }); await tick();
assert.equal(failureRoot.querySelectorAll('[data-reader-candidate]').length, 7);
const retryAction = failureRoot.querySelector('[data-reader-action="retry"][data-node-id="impl0"]');
assert.ok(retryAction);
click(retryAction); await tick();
assert.equal(failures.get('impl0'), 2);
assert.equal(failureRoot.querySelectorAll('[data-reader-candidate]').length, 7);
assert.ok(failureRoot.textContent.includes('Purpose of impl0'));
failureReader.close();
failureReader.open({ ...origin, implementationItems: items.slice(0, 2), candidateCount: 2 }); await tick();
assert.equal(failureRoot.querySelectorAll('[data-reader-candidate]').length, 2);

// Nested comparison must restore the caller's own candidate inventory, scopes and exact origin.
const nestedRoot = rootFixture(), ten = items.slice(0, 10);
const nestedGraph = { nodes: [{ id: 'otherIface', label: 'Other.Load()' }, { id: 'childA', label: 'A.Load()' }, { id: 'childB', label: 'B.Load()' }],
  edges: [{ source: 'otherIface', target: 'childA', relation: 'dispatches_to' }, { source: 'otherIface', target: 'childB', relation: 'dispatches_to' }] };
const nestedDescription = id => id === 'impl0' ? { ...desc(id), events: [{ kind: 'call', text: 'Other.Load()', line: 20, column: 1,
  targetNodeIds: [], implementationTargetIds: ['otherIface'] }], methodFlow: { status: 'parsed', steps: [
    { kind: 'call', label: 'Other.Load()', line: 20, column: 1, site: { line: 20, column: 1 } }, { kind: 'return', label: 'return result;', line: 21 }] } } : desc(id);
const nestedReader = new MethodWorkbench(nestedRoot, { getContext: () => ({ projectId: 'p', snapshotId: 's' }),
  getNode: id => nestedGraph.nodes.find(node => node.id === id), getGraph: () => nestedGraph, onSource() {}, onClose() {}, onChange() {},
  fetcher: async (_url, options) => ({ ok: true, json: async () => nestedDescription(JSON.parse(options.body).nodeId) }) });
nestedReader.open({ ...origin, implementationItems: ten, candidateCount: 10 }); await tick();
await nestedReader.read('impl0'); await tick();
click(nestedRoot.querySelector('[data-reader-action="call"]')); await tick();
assert.equal(nestedReader.snapshot().mode, 'compare', 'Implementation-only target metadata enables local comparison');
assert.equal(nestedRoot.querySelectorAll('[data-reader-candidate]').length, 2);
assert.ok(nestedRoot.textContent.includes('A.Load()') && nestedRoot.textContent.includes('B.Load()'));
await nestedReader.back(); await tick();
assert.equal(nestedReader.snapshot().focusId, 'impl0');
await nestedReader.back(); await tick();
assert.equal(nestedReader.snapshot().mode, 'compare');
assert.equal(nestedRoot.querySelectorAll('[data-reader-candidate]').length, 10);
assert.ok(nestedRoot.textContent.includes('В проекте · 5'));
assert.ok(nestedRoot.textContent.includes('Переадресуют вызов · 1'));
assert.ok(nestedRoot.textContent.includes('В тестовых исходниках · 4'));
assert.equal(nestedReader.snapshot().origin.occurrenceId, origin.occurrenceId);
const dependencyRoot = rootFixture(), dependencySources = [];
const dependencyReader = new MethodWorkbench(dependencyRoot, { getContext: () => ({ projectId: 'p', snapshotId: 's' }),
  getNode: id => graph.nodes.find(node => node.id === id), getGraph: () => graph, onSource: id => dependencySources.push(id), onClose() {}, onChange() {},
  fetcher: async (_url, options) => ({ ok: true, json: async () => desc(JSON.parse(options.body).nodeId) }) });
await dependencyReader.open({ ...origin, targetIds: ['impl6'], implementationItems: [{ id: 'impl6', label: 'Read()' }], candidateCount: 1 });
click(dependencyRoot.querySelector('[data-reader-action="dependency"][data-node-id="field"]')); await tick();
assert.deepEqual(dependencySources, ['field']); assert.equal(dependencyReader.snapshot().focusId, 'impl6');
click(dependencyRoot.querySelector('[data-reader-action="dependency"][data-node-id="helper"]')); await tick();
assert.equal(dependencyReader.snapshot().focusId, 'helper', 'A calls dependency changes only reader focus');
await dependencyReader.back(); assert.equal(dependencyReader.snapshot().focusId, 'impl6');

// Evicted method bodies must refetch; the workbench cannot bypass the 32-body source cache with a second unbounded copy.
const cacheRoot = rootFixture(), cacheFetches = [];
const cacheReader = new MethodWorkbench(cacheRoot, { getContext: () => ({ projectId: 'p', snapshotId: 's' }),
  getNode: () => undefined, getGraph: () => ({ nodes: [], edges: [] }), onSource() {}, onClose() {}, onChange() {},
  fetcher: async (_url, options) => { const id = JSON.parse(options.body).nodeId; cacheFetches.push(id); return { ok: true, json: async () => desc(id) }; } });
await cacheReader.open({ ...origin, implementationItems: [{ id: 'body0', label: 'First()' }], targetIds: ['body0'], candidateCount: 1 });
for (let index = 1; index < 40; index++) await cacheReader.read(`body${index}`);
await cacheReader.read('body0');
assert.equal(cacheFetches.filter(id => id === 'body0').length, 2, 'The oldest body is loaded again after 40 distinct reads');
assert.ok(cacheReader.snapshot().trail.length <= 16);
await cacheReader.read('recursiveA', { ownerNodeId: 'body0', occurrenceId: 'body0::call:A', line: 10 });
await cacheReader.read('recursiveB', { ownerNodeId: 'recursiveA', occurrenceId: 'recursiveA::call:B', line: 11 });
await cacheReader.read('recursiveA', { ownerNodeId: 'recursiveB', occurrenceId: 'recursiveB::call:A', line: 12 });
await cacheReader.back();
assert.equal(cacheReader.snapshot().focusId, 'recursiveB', 'Recursion returns to its immediate caller rather than dropping the intermediate frame');
assert.equal(cacheReader.snapshot().callSite.occurrenceId, 'recursiveA::call:B');

// Explicit read actions retain their occurrence and geometry; expand actions reveal diagram cards.
globalThis.ResizeObserver = class { observe() {} };
const caller = { id: 'caller', label: 'Caller()', file: 'C.cs', events: [{ kind: 'call', text: 'Callee()', line: 10, column: 1, targetNodeIds: ['callee'] }],
  methodFlow: { status: 'parsed', steps: [{ kind: 'call', label: 'Callee()', line: 10, column: 1, site: { line: 10, column: 1 } }] } };
const callee = { id: 'callee', label: 'Callee()', file: 'C.cs', events: [], methodFlow: { status: 'parsed', steps: [{ kind: 'return', label: 'return 1;', line: 20 }] } };
const renderGraph = { nodes: [caller, callee], edges: [{ source: 'caller', target: 'callee', relation: 'calls' }] };
for (const provider of ['natural', 'graphistry']) {
  const svg = new Html('svg'), viewport = new Html('g'); viewport.setAttribute('id', 'viewport'); svg.append(viewport);
  const graphView = new GraphView(svg, () => {}, () => {}), reads = [];
  graphView.pathRootId = 'caller'; graphView.pathSnapshotId = 's'; graphView.displayProvider = 'flow'; graphView.groupingProvider = provider;
  graphView.onReadCall = node => reads.push(node);
  graphView.renderPathCards(renderGraph.nodes, renderGraph.edges, 'caller');
  const camera = { ...graphView.camera }, expanded = [...graphView.pathExpandedIds], bounds = { ...graphView.flowBounds };
  click(viewport.querySelector('.path-card-read'));
  assert.equal(reads.length, 1); assert.equal(reads[0].sourceOwnerId, 'caller');
  assert.equal(reads[0].line, 10); assert.deepEqual(reads[0].readTargetIds, ['callee']);
  assert.deepEqual(graphView.camera, camera); assert.deepEqual([...graphView.pathExpandedIds], expanded); assert.deepEqual(graphView.flowBounds, bounds);

  const scene = buildMethodSequence(renderGraph, 'caller');
  const owner = { id: 'caller', rawId: 'caller', node: caller, kind: 'raw', label: 'Caller()', expanded: true, children: [],
    totalChildren: 0, memberIds: ['caller'], enter: { kind: 'raw' } };
  const hierarchy = { roots: ['caller'], items: [owner], edges: [], methodBodies: new Map([['caller', scene]]) };
  new InlineHierarchyRenderer(graphView).render(hierarchy, inlineFlowLayout(hierarchy, new Map([['caller', { x: 0, y: 0, width: 260, height: 154 }]])));
  const inlineBounds = { ...graphView.flowBounds };
  click(viewport.querySelector('.path-card-read'));
  assert.equal(reads.length, 2); assert.equal(reads[1].id, reads[0].id);
  assert.deepEqual(graphView.flowBounds, inlineBounds); assert.deepEqual(graphView.camera, camera);
}

// Controller wiring opens catalog-only scene calls without creating a global execution path.
const controllerRoot = rootFixture(), controllerRequests = [];
globalThis.fetch = async (_url, options) => { const id = JSON.parse(options.body).nodeId; controllerRequests.push(id); return { ok: true, json: async () => desc(id) }; };
const fullNodes = [{ id: 'caller', label: 'Caller()', codeProjectId: 'code', file: 'C.cs' }, ...items.slice(0, 2).map(item => ({ ...item, kind: 'method', codeProjectId: 'code' }))];
const controllerState = { project: { id: 'p' }, graph: { nodes: fullNodes, edges: [], metadata: { snapshotId: 's' } },
  nodes: new Map(fullNodes.map(node => [node.id, node])), selected: 'caller', pathOnGraph: null,
  inlineExpanded: new Set(['caller']), methodPresentation: 'diagram' };
const controllerView = { camera: { x: 33, y: 44, scale: .8 }, pathExpandedIds: new Set(['retained']), pathControlFilters: { conditions: false, loops: true } };
const retained = structuredClone({ selected: controllerState.selected, inline: controllerState.inlineExpanded,
  camera: controllerView.camera, expanded: controllerView.pathExpandedIds, filters: controllerView.pathControlFilters });
let checkpoints = 0, commits = 0;
const connected = createMethodWorkbench({ root: controllerRoot, state: controllerState, view: controllerView, onSource() {},
  checkpoint: () => checkpoints++, commit: () => commits++, sync() {}, status() {} });
connected.open({ id: 'caller::call:10:1', sourceOwnerId: 'caller', line: 10, readTargetIds: [],
  implementationItems: items.slice(0, 2), candidateCount: 2, label: 'Load()' }); await tick();
assert.equal(controllerRoot.hidden, false, 'Catalog targets outside the bounded sequence still open the reader');
assert.equal(controllerRoot.querySelectorAll('[data-reader-candidate]').length, 2);
assert.equal(controllerState.pathOnGraph, null);
assert.equal(controllerState.methodPresentation, 'methods');
assert.equal(checkpoints, 1); assert.ok(commits >= 1);
connected.reader.close();
assert.deepEqual({ selected: controllerState.selected, inline: controllerState.inlineExpanded, camera: controllerView.camera,
  expanded: controllerView.pathExpandedIds, filters: controllerView.pathControlFilters }, retained);
assert.equal(controllerState.methodPresentation, 'diagram');

// A step whose owning execution-path snapshot is stale must not be relabelled with the current revision.
controllerState.pathOnGraph = { snapshotId: 'old', steps: [{ nodeId: 'impl0', documentation: { summary: 'STALE PATH DESCRIPTION' }, methodFlow: flow }] };
connected.open({ id: 'impl0', targetIds: ['impl0'] }); await tick();
assert.ok(!controllerRoot.textContent.includes('STALE PATH DESCRIPTION'));
assert.ok(controllerRoot.textContent.includes('Purpose of impl0'));
connected.reader.close(); controllerState.pathOnGraph = null;

// Reader history is valid without a path, but incompatible project/revision/focus envelopes are discarded.
const readerAddress = { ...saved, focusId: 'impl1', origin: { ...saved.origin, originMode: 'diagram' }, projectId: 'p', snapshotId: 's' };
const inlineRoute = { repo: 'p', view: 'architecture', reading: 'methods', methodFocus: 'impl1', node: 'caller',
  level: 'projects', inline: inlineDetailToken(['caller']) };
assert.equal(normalizeRoute(inlineRoute).reading, 'methods');
assert.equal(normalizeRoute(inlineRoute).path, null);
const readerHistory = navigationSnapshot({ route: inlineRoute, snapshotId: 's', reader: readerAddress,
  camera: controllerView.camera, inlineExpanded: ['caller'], pathExpanded: ['retained'] });
assert.equal(readerHistory.reader.focusId, 'impl1');
assert.ok(!JSON.stringify(readerHistory).includes('Purpose of'));
for (const incompatible of [{ projectId: 'other' }, { snapshotId: 'other' }, { focusId: 'impl0' }]) {
  const result = resolveNavigation({ ...readerHistory, reader: { ...readerAddress, ...incompatible } },
    { projects: [{ id: 'p' }], nodes: fullNodes, snapshotId: 's' });
  assert.equal(result.snapshot.reader, null);
}
const newRevision = resolveNavigation(readerHistory, { projects: [{ id: 'p' }], nodes: fullNodes, snapshotId: 'new' });
assert.equal(newRevision.snapshot.reader, null); assert.equal(newRevision.snapshot.route.reading, null);

// Workspace restoration restores retained map geometry before delegating local reading.
const elements = new Map();
for (const id of ['view-mode', 'display-provider', 'system-scale', 'architecture-preset', 'project-blocks', 'search', 'kind', 'relation',
  'system-search', 'system-systems', 'system-packages', 'solution-kind', 'discovery-mode', 'navigation-search', 'semantic-query',
  'ingress-catalog-search', 'semantic-kind', 'navigation-search-scope', 'navigation-search-results', 'answer', 'selection', 'algorithm']) {
  const node = new Html('div'); node.value = ''; node.options = [{ value: '' }]; elements.set(id, node);
}
globalThis.document.getElementById = id => elements.get(id);
const historyState = { ...controllerState, projects: [{ id: 'p' }], algorithms: [{ id: 'natural' }], algorithm: 'natural',
  architecture: { level: 'projects', projectId: null, file: null, page: 0 }, codeProjectIds: new Set(), loading: false,
  ingress: { data: null }, methodPresentation: 'methods' };
const order = [], navView = { ...controllerView, camera: { x: 0, y: 0, scale: 1 }, transform: () => order.push('camera'), setSelected() {} };
const noOp = () => {};
const nav = createWorkspaceNavigation({ state: historyState, view: navView,
  viewers: { wikiViewer: { close: noOp }, pathViewer: { close: noOp }, factsPopup: { close: noOp }, documentationViewer: { select: noOp },
    systemView: { page: 0 }, mapBlocks: { restoreSelection: noOp }, methodWorkbench: {
      snapshot: () => readerAddress, leave: () => order.push('leave'), restore: async () => { order.push('reader'); assert.deepEqual(navView.camera, readerHistory.camera); } } },
  search: { searchNavigation: { cancel: noOp }, navigationSearch: { refresh: noOp }, semanticSearch: { invalidateSearch: noOp } },
  actions: { closeTransientStudy: noOp, updateDisplayProvider: noOp, updateDiscovery: noOp, openProject: async () => {},
    draw: () => order.push('diagram'), filteredNodes: () => fullNodes, displayedAggregate: () => ({ communities: [] }), renderCodeProjects: noOp,
    renderSelection: noOp, loadIngress: async () => {}, paintPathGraph: noOp, controls: noOp, status: noOp } });
await nav.restore(readerHistory, { signal: new AbortController().signal, isCurrent: () => true });
assert.ok(order.indexOf('leave') < order.indexOf('diagram'));
assert.ok(order.indexOf('camera') < order.indexOf('reader'));
assert.equal(historyState.pathOnGraph, null);
assert.equal(nav.capture().reader.focusId, 'impl1');
const readsBefore = order.filter(item => item === 'reader').length;
await nav.restore(readerHistory, { signal: new AbortController().signal, isCurrent: () => false });
assert.equal(order.filter(item => item === 'reader').length, readsBefore, 'Obsolete restoration cannot reopen the reader');

// Raw expansion stays on the map; its separate read action opens the method reader.
const rawSvg = new Html('svg'), rawViewport = new Html('g'); rawViewport.setAttribute('id', 'viewport'); rawSvg.append(rawViewport);
const rawView = new GraphView(rawSvg, noOp, noOp), rawReads = [];
rawView.displayProvider = 'flow'; rawView.camera = { x: 70, y: 20, scale: .9 };
const rawState = { ...controllerState, graph: controllerState.graph, nodes: controllerState.nodes, pathOnGraph: null, selected: 'caller' };
let rawDraws = 0;
const blocks = createMapBlocks({ state: rawState, view: rawView, filteredNodes: () => fullNodes, communityId: () => 'one',
  selectNode: noOp, navigate: noOp, openCommunity: noOp, analyzePath: () => assert.fail('Reading must not build a global path'),
  isolate: noOp, draw: () => rawDraws++, controls: noOp, checkpoint: noOp, commit: noOp, reveal: noOp,
  fetcher: () => assert.fail('Opening an executable for reading must not prefetch an inline body'), readMethod: node => rawReads.push(node) });
rawView.prepareInline(fullNodes, false);
const rawCamera = { ...rawView.camera }, rawExpansions = [...rawState.inlineExpanded];
blocks.toggle('impl0');
assert.equal(rawReads.length, 0); assert.ok(rawState.inlineExpanded.has('impl0'));
assert.equal(rawDraws, 1);
rawView.onReadInlineTarget('impl1');
assert.equal(rawReads.length, 1); assert.deepEqual(rawReads[0].targetIds, ['impl1']);
assert.equal(rawState.pathOnGraph, null); assert.equal(rawState.selected, 'caller'); assert.equal(rawDraws, 1);
assert.deepEqual(rawView.camera, rawCamera); assert.deepEqual([...rawState.inlineExpanded], [...rawExpansions, 'impl0']);

// Losing navigation ownership while HTTP is pending cannot reopen the previous reader.
let finishLate, navigationCurrent = true;
globalThis.fetch = () => new Promise(resolve => { finishLate = resolve; });
const lateRoot = rootFixture(), lateNode = { id: 'late-method', label: 'Late()', kind: 'method' };
const lateState = { ...controllerState, graph: { ...controllerState.graph, nodes: [...fullNodes, lateNode] },
  nodes: new Map([...controllerState.nodes, [lateNode.id, lateNode]]), pathOnGraph: null, readerState: null };
const lateController = createMethodWorkbench({ root: lateRoot, state: lateState, view: controllerView,
  onSource: noOp, checkpoint: noOp, commit: noOp, sync: noOp, status: noOp });
const lateAddress = { ...readerAddress, focusId: lateNode.id, trail: [], origin: { ...readerAddress.origin, targetIds: [lateNode.id] } };
const restoring = lateController.restore({ route: { reading: 'methods', methodFocus: lateNode.id }, reader: lateAddress },
  { isCurrent: () => navigationCurrent });
await tick(); assert.ok(finishLate);
navigationCurrent = false; finishLate({ ok: true, json: async () => desc(lateNode.id) });
await restoring; await tick();
assert.equal(lateRoot.hidden, true); assert.equal(lateController.snapshot(), null); assert.equal(lateState.readerState, null);

// A late older restore must not invalidate a newer reader opened by another user action.
const racedRequests = [];
globalThis.fetch = (_url, options) => new Promise(resolve => racedRequests.push({ id: JSON.parse(options.body).nodeId,
  resolve: () => resolve({ ok: true, json: async () => desc(JSON.parse(options.body).nodeId) }) }));
let firstCurrent = true;
const raceRoot = rootFixture();
const raceController = createMethodWorkbench({ root: raceRoot, state: lateState, view: controllerView,
  onSource: noOp, checkpoint: noOp, commit: noOp, sync: noOp, status: noOp });
const older = raceController.restore({ route: { reading: 'methods', methodFocus: lateNode.id }, reader: lateAddress },
  { isCurrent: () => firstCurrent });
await tick(); firstCurrent = false;
raceController.open({ id: 'impl1', targetIds: ['impl1'] }); await tick();
assert.ok(racedRequests.some(request => request.id === 'impl1'));
racedRequests.find(request => request.id === 'impl1').resolve(); await tick();
racedRequests.find(request => request.id === lateNode.id).resolve(); await older; await tick();
assert.equal(raceRoot.hidden, false); assert.equal(raceController.snapshot().focusId, 'impl1');
console.log('Method workbench bounded comparison, outside-path reading, lexical controls, local return and context isolation PASS');
