/** Векторный поиск не интерпретирует документы как HTML и отменяет устаревшие ответы. */
import assert from 'node:assert/strict';
import { SemanticSearchPanel } from '../web/semantic-search.js';
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.value = ''; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  set innerHTML(value) { throw new Error('Unsafe HTML'); }
}
globalThis.document = { createElement: tag => new Element(tag) };
const names = ['form', 'query', 'kind', 'search', 'build', 'status', 'results'];
const fields = new Map(names.map(name => [name, new Element('div')]));
const root = { querySelector: selector => fields.get(selector.match(/"(.*?)"/)[1]) };
let scope = { projectId: 'p', snapshotId: 's' };
const pending = [], selected = [];
const viewer = new SemanticSearchPanel(root, { getScope: () => scope, onSelect: id => selected.push(id), fetcher: (url, options) => new Promise(resolve => pending.push({ url, options, resolve })) });
const tick = () => new Promise(resolve => setImmediate(resolve));
const reply = (i, value) => pending[i].resolve({ ok: true, json: async () => value });
viewer.sync();
reply(0, { snapshotId: 's', index: { chunkCount: 3 }, modelAvailable: true });
await tick();
assert.equal(fields.get('search').disabled, false);
fields.get('query').value = 'Расчёт валюты';
const first = viewer.search();
assert.deepEqual(JSON.parse(pending[1].options.body), { snapshotId: 's', query: 'Расчёт валюты', kind: 'all', limit: 8 });
fields.get('query').value = 'new';
fields.get('query').listeners.input();
assert.equal(pending[1].options.signal.aborted, true);
reply(1, { snapshotId: 's', hits: [{ text: 'STALE', nodeIds: [] }] });
await first;
assert.equal(fields.get('results').children.length, 0);
const second = viewer.search();
reply(2, { snapshotId: 's', hits: [{ text: '<script>attack()</script>', file: '<img onerror=attack()>', line: 3, endLine: 4, kind: 'comment', nodeIds: ['a'], distance: .15 }], codeNodes: [{ id: 'a', label: 'Calculate' }] });
await second;
const card = fields.get('results').children[0];
assert.equal(card.children.find(node => node.tag === 'p').textContent, '<script>attack()</script>');
card.children.find(node => node.tag === 'button').listeners.click();
assert.deepEqual(selected, ['a']);
const third = viewer.search();
scope = { ...scope, snapshotId: 'other' }; viewer.sync();
reply(3, { snapshotId: 's', hits: [{ text: 'OLD_PROJECT', nodeIds: [] }] });
await third;
assert.equal(fields.get('results').children.length, 0);
reply(4, { snapshotId: 'other', modelAvailable: false, message: 'Model missing' });
await tick();
assert.equal(fields.get('search').disabled, true);
assert.equal(fields.get('build').disabled, true);
assert.equal(fields.get('status').textContent, 'Model missing');
// Typing and changing kind must not re-enable a build rejected by model readiness.
const unavailableRequests = pending.length;
fields.get('query').listeners.input();
assert.equal(fields.get('build').disabled, true);
fields.get('kind').listeners.change();
assert.equal(fields.get('build').disabled, true);
await viewer.build();
assert.equal(pending.length, unavailableRequests, 'unavailable model cannot start a request programmatically either');
viewer.render({ hits: [{ documentId: 'document-a', file: 'docs/guide.md', line: 8, endLine: 9, text: 'Captured text', nodeIds: ['missing-code'], kind: 'documentation', distance: .3 }] });
const missing = fields.get('results').children[0];
assert.equal(missing.children.filter(node => node.tag === 'button').length, 0);
assert.ok(missing.children.some(node => String(node.textContent).includes('Связанные узлы отсутствуют')));
const openedDocuments = [];
viewer.onOpenDocument = (id, context) => openedDocuments.push({ id, context });
viewer.render({ hits: [{ documentId: 'document-a', file: 'docs/guide.md', line: 8, endLine: 9, text: '<script>data only</script>', nodeIds: [], kind: 'documentation', distance: .3 }] });
const documentCard = fields.get('results').children[0];
assert.equal(documentCard.children.find(node => node.tag === 'p').textContent, '<script>data only</script>');
documentCard.children.find(node => node.tag === 'button').listeners.click();
assert.deepEqual(openedDocuments, [{ id: 'document-a', context: { projectId: 'p', snapshotId: 'other', line: 8, endLine: 9 } }]);
// Detached/cache cards cannot navigate reused IDs under a new snapshot or changed search context.
viewer.render({ hits: [{ documentId: 'document-a', file: 'docs/guide.md', line: 8, text: 'Captured', nodeIds: ['a'], kind: 'documentation', distance: .3 }], codeNodes: [{ id: 'a', label: 'Calculate' }] });
const cached = fields.get('results').children[0];
assert.ok(cached.children[0].textContent.includes('расстояние 0.300'));
const cachedButtons = cached.children.filter(node => node.tag === 'button');
const selectedBefore = selected.length, documentsBefore = openedDocuments.length;
scope.snapshotId = 'next';
for (const button of cachedButtons) button.listeners.click();
assert.equal(selected.length, selectedBefore);
assert.equal(openedDocuments.length, documentsBefore);
scope.snapshotId = 'other';
const oldKind = fields.get('kind').value;
fields.get('kind').value = 'comment';
for (const button of cachedButtons) button.listeners.click();
assert.equal(selected.length, selectedBefore);
assert.equal(openedDocuments.length, documentsBefore);
fields.get('kind').value = oldKind;
const oldQuery = fields.get('query').value;
fields.get('query').value = 'restored query';
for (const button of cachedButtons) button.listeners.click();
assert.equal(selected.length, selectedBefore);
fields.get('query').value = oldQuery;
const invalidationRequests = pending.length;
viewer.invalidateSearch({ clearResults: false });
assert.equal(fields.get('results').children[0], cached, 'optional retained presentation cannot retain active old callbacks');
for (const button of cachedButtons) button.listeners.click();
assert.equal(selected.length, selectedBefore);
assert.equal(openedDocuments.length, documentsBefore);
assert.equal(pending.length, invalidationRequests, 'context invalidation never runs an automatic embedding query');
viewer.invalidateSearch();
assert.equal(fields.get('results').children.length, 0);
assert.equal(fields.get('query').value, oldQuery);
assert.equal(fields.get('kind').value, oldKind);
// HTTP 404 job после перезапуска восстанавливает persisted status вместо бесконечного polling.
for (const ready of [false, true]) {
  viewer.building = true;
  const offset = pending.length;
  const watching = viewer.watch({ id: 'lost-job' }, viewer.revision, new AbortController().signal);
  pending[offset].resolve({ ok: false, status: 404, json: async () => ({ error: 'Missing job' }) });
  await tick();
  reply(offset + 1, { snapshotId: 'other', modelAvailable: true, index: ready ? { chunkCount: 3 } : null });
  await watching;
  assert.equal(viewer.building, false);
  assert.equal(fields.get('search').disabled, !ready);
  assert.equal(fields.get('build').disabled, false);
  assert.ok(fields.get('status').textContent.includes('после перезапуска'));
}
// Back invalidates before restoring fields: an in-flight old reply cannot refill the new search panel.
fields.get('query').value = 'before history';
const historySearch = viewer.search();
const historyRequest = pending.length - 1;
viewer.invalidateSearch();
fields.get('query').value = 'after history';
fields.get('kind').value = 'documentation';
assert.equal(pending[historyRequest].options.signal.aborted, true);
const historyRequestCount = pending.length;
reply(historyRequest, { snapshotId: 'other', hits: [{ text: 'STALE_BACK_RESULT', nodeIds: [] }] });
await historySearch;
assert.equal(fields.get('results').children.length, 0);
assert.equal(fields.get('query').value, 'after history');
assert.equal(fields.get('kind').value, 'documentation');
assert.equal(pending.length, historyRequestCount);
// Default transport вызван как window.fetch, а не методом экземпляра панели.
const nativeFetch = globalThis.fetch;
try {
  globalThis.fetch = function () {
    assert.ok(!(this instanceof SemanticSearchPanel));
    return Promise.resolve({ ok: true, json: async () => ({ ok: true }) });
  };
  const nativeViewer = new SemanticSearchPanel(root, { getScope: () => scope, onSelect: () => {} });
  assert.deepEqual(await nativeViewer.request('/health'), { ok: true });
} finally {
  globalThis.fetch = nativeFetch;
}
// A deferred build followed by a transport failure must leave a manual retry available.
const deferredStatus = viewer.loadStatus();
reply(pending.length - 1, { snapshotId: scope.snapshotId, modelAvailable: true, index: { chunkCount: 3 }, deferred: true });
await deferredStatus;
assert.equal(viewer.building, true);
assert.equal(fields.get('build').disabled, true);
const unavailableStatus = viewer.loadStatus();
pending[pending.length - 1].resolve({ ok: false, status: 503, json: async () => ({ error: 'Server unavailable' }) });
await unavailableStatus;
assert.equal(viewer.building, false);
assert.equal(fields.get('build').disabled, false);
assert.equal(fields.get('search').disabled, false, 'the published index remains usable');
assert.ok(fields.get('status').textContent.includes('Повторите обновление'));
// Toolbar controls participate in the original form without duplicate input or submit listeners.
const external = new Map([['semantic-query', new Element('input')], ['semantic-search-submit', new Element('button')]]);
const inlineRoot = { ownerDocument: { getElementById: id => external.get(id) },
  querySelector: selector => {
    const name = selector.match(/"(.*?)"/)[1];
    return ['query', 'search'].includes(name) ? null : fields.get(name);
  } };
const inlineViewer = new SemanticSearchPanel(inlineRoot, { getScope: () => scope, onSelect: () => {} });
assert.equal(inlineViewer.fields.query, external.get('semantic-query'));
assert.equal(inlineViewer.fields.search, external.get('semantic-search-submit'));
assert.equal(typeof external.get('semantic-query').listeners.input, 'function');
// A server stalled in JSON parsing has the same bounded wait as transport.
const originalTimeout = globalThis.setTimeout;
globalThis.setTimeout = (callback, delay, ...args) => originalTimeout(callback, delay === 30000 ? 8 : delay, ...args);
try {
  for (const transport of [() => new Promise(() => {}), () => Promise.resolve({ ok: true, json: () => new Promise(() => {}) })]) {
    const bounded = new SemanticSearchPanel(inlineRoot, { getScope: () => scope, onSelect: () => {}, fetcher: transport });
    await assert.rejects(bounded.request('/semantic-search'), error => error.name === 'TimeoutError');
  }
} finally { globalThis.setTimeout = originalTimeout; }
console.log('Semantic search safe rendering and stale context PASS');
