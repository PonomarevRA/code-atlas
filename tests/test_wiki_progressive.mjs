/** Документы доступны раньше optional MiniLM; поздний ответ и ошибка не разрушают читатель. */
import assert from 'node:assert/strict';
import { WikiViewer } from '../web/wiki-view.js';

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.hidden = false; this.disabled = false; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(event, listener) { this.listeners[event] = listener; }
  focus() { document.activeElement = this; }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  setAttribute(name, value) { this[name] = value; }
}
globalThis.document = { createElement: tag => new Element(tag), activeElement: new Element('button') };
const fields = new Map(['title', 'status', 'content', 'close'].map(name => [name, new Element('div')]));
const root = new Element('section'); root.hidden = true;
root.querySelector = selector => fields.get(selector.match(/"(.*?)"/)[1]);
const pending = [], navigation = [];
globalThis.fetch = (url, options) => new Promise(resolve => pending.push({ body: JSON.parse(options.body), resolve }));
const scope = { projectId: 'project', snapshotId: 'snapshot' };
const viewer = new WikiViewer(root, { getScope: () => scope, onNavigate: () => navigation.push(viewer.nodeId) });
const wikiData = { snapshotId: 'snapshot', node: { id: 'raw', label: 'Calculate()' },
  directDocuments: [{ id: 'doc', title: 'Captured guide' }], neighborDocuments: [], graphNeighbors: [],
  recommendationStatus: 'not-requested', recommendations: [], index: {} };
const opening = viewer.openNode('raw');
assert.equal(pending.length, 1); assert.equal(pending[0].body.includeRecommendations, false);
pending[0].resolve({ ok: true, json: async () => wikiData }); await opening;
const directSection = fields.get('content').children[0];
assert.equal(directSection.children.some(child => child.tag === 'article'), true);
const recommendationSection = fields.get('content').children[2];
const button = recommendationSection.children.find(child => child.tag === 'button');
assert.equal(button.textContent, 'Найти похожие материалы');
assert.equal(pending.length, 1, 'initial reading does not schedule semantic search');

button.focus();
const searching = button.listeners.click();
assert.equal(button['aria-disabled'], 'true'); assert.equal(pending[1].body.includeRecommendations, true);
button.listeners.click(); assert.equal(pending.length, 2, 'repeated click cannot schedule duplicate inference');
pending[1].resolve({ ok: false, status: 503, json: async () => ({ error: 'Модель недоступна' }) }); await searching;
assert.equal(button['aria-disabled'], 'false'); assert.equal(document.activeElement, button); assert.equal(button.textContent, 'Повторить смысловой поиск');
assert.equal(fields.get('content').children[0], directSection, 'semantic error preserves captured documents');
assert.equal(navigation.length, 1, 'semantic search does not create a document navigation');

const retrying = button.listeners.click();
pending[2].resolve({ ok: true, json: async () => ({ ...wikiData, recommendationStatus: 'ready',
  recommendations: [{ id: 'similar', title: 'Similar guide', reason: 'semantic-similarity' }] }) }); await retrying;
assert.equal(fields.get('content').children[0], directSection);
assert.equal(recommendationSection.children.at(-1).children[0].children.some(child => child.tag === 'article'), true);
assert.equal(document.activeElement, button, 'stable launch control retains focus after success');
const unavailable = button.listeners.click();
pending[3].resolve({ ok: true, json: async () => ({ ...wikiData, recommendationStatus: 'model-unavailable', warnings: ['Local model missing'] }) }); await unavailable;
assert.equal(button.textContent, 'Повторить смысловой поиск');
assert.equal(document.activeElement, button);
assert.equal(recommendationSection.children[2].textContent, 'Local model missing');
assert.equal(fields.get('content').children[0], directSection);

const refreshing = button.listeners.click();
recommendationSection.children.at(-1).children[0].children.find(child => child.tag === 'article').children[0].focus();
pending[4].resolve({ ok: true, json: async () => ({ ...wikiData, recommendationStatus: 'ready', recommendations: [] }) }); await refreshing;
assert.equal(document.activeElement, button, 'refresh replaces a focused result safely');

const oldOpening = viewer.openNode('raw'); pending[5].resolve({ ok: true, json: async () => wikiData }); await oldOpening;
const oldSection = fields.get('content').children[2];
const lateSearch = oldSection.children.find(child => child.tag === 'button').listeners.click();
const nextOpening = viewer.openDocument('another');
pending[7].resolve({ ok: true, json: async () => ({ snapshotId: 'snapshot', document: { id: 'another', title: 'Another guide' }, text: 'Captured text', totalChars: 13 }) });
await nextOpening;
pending[6].resolve({ ok: true, json: async () => ({ ...wikiData, recommendations: [{ id: 'stale', title: 'Old recommendation' }] }) });
await lateSearch;
assert.equal(fields.get('title').textContent, 'Another guide');
assert.equal(fields.get('content').children.includes(oldSection), false, 'late recommendation cannot replace another document');
console.log('Progressive wiki PASS');
