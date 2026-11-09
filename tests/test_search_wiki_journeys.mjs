import assert from 'node:assert/strict';
import { createSearchNavigation } from '../web/search-navigation.js';
import { WikiViewer, renderWikiText } from '../web/wiki-view.js';
import { navigationSnapshot, normalizeRoute, readRoute, writeRoute, navigationInteraction } from '../web/navigation-history.js';

// Системный вид начинает без raw-графа; переход ждёт загрузку и отменяется сменой снимка.
let scope = { projectId: 'repo', snapshotId: 'snapshot' }, ready = false;
let complete, navigated = [], errors = [];
const navigation = createSearchNavigation({ getScope: () => scope, getNode: id => ready ? { id } : null,
  ensureGraph: () => new Promise(resolve => { complete = () => { ready = true; resolve(); }; }),
  navigate: node => navigated.push(node.id), onError: error => errors.push(error),
});
const first = navigation.select('raw'); assert.equal(navigated.length, 0); complete();
assert.equal(await first, true); assert.deepEqual(navigated, ['raw']);
const stale = navigation.select('old'); scope = { projectId: 'another', snapshotId: 'new' }; complete();
assert.equal(await stale, false); assert.deepEqual(navigated, ['raw']);
assert.deepEqual(errors, []);
const cancelled = navigation.select('cancelled'); navigation.cancel(); complete();
assert.equal(await cancelled, false, 'same-snapshot Back cancels pending search navigation');
assert.deepEqual(navigated, ['raw']);

const route = { repo: 'repo', wiki: 'doc:<&>?', wikiNode: 'raw', wikiOffset: 12000 };
assert.deepEqual(readRoute(writeRoute(route, 'http://localhost/?foreign=keep')), normalizeRoute(route));
assert.equal(normalizeRoute({ ...route, repo: null }).wiki, null);
assert.equal(normalizeRoute({ ...route, wikiOffset: -1 }).wikiOffset, 0);
assert.equal(normalizeRoute({ ...route, wiki: null }).wikiOffset, 0);
const snapshot = navigationSnapshot({ route, searchContext: { mode: 'meaning', codeQuery: 'Calculate', semanticQuery: 'Как считается доходность?', entryQuery: '/api', scope: 'current', kind: 'documentation', results: ['private'] } });
assert.equal(snapshot.searchContext.semanticQuery, 'Как считается доходность?');
assert.equal(snapshot.searchContext.results, undefined);
assert.equal(navigationInteraction({ type: 'click', target: { closest: () => ({}) } }), null, 'panel controls are ignored');

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.hidden = false; this.isConnected = true; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  focus() { document.activeElement = this; }
  closest() { return null; }
  set innerHTML(value) { throw Error('unsafe HTML'); }
}
globalThis.document = { createElement: tag => new Element(tag), activeElement: new Element('button') };
const root = new Element('section'); root.hidden = true;
const fields = new Map(['title', 'status', 'content', 'close'].map(name => [name, new Element('div')]));
root.querySelector = selector => fields.get(selector.match(/"(.*?)"/)[1]);
const pending = [], commits = [];
globalThis.fetch = (url, options) => new Promise(resolve => pending.push({ url, options, resolve }));
const wiki = new WikiViewer(root, { getScope: () => scope, onNavigate: () => commits.push({ documentId: wiki.documentId, nodeId: wiki.nodeId }) });
const load = wiki.openDocument('captured-doc', { nodeId: null });
assert.equal(root.hidden, false); assert.equal(commits.length, 1);
pending[0].resolve({ ok: true, json: async () => ({ snapshotId: scope.snapshotId, document: { id: 'captured-doc', title: 'Guide', format: 'markdown', sourceHash: 'abc', nodeIds: [] }, text: '# Guide\n\n<script>bad()</script>\n\n```js\na < b\n```', textOffsetStart: 0, textOffsetEnd: 60, totalChars: 60 }) });
await load;
assert.equal(fields.get('title').textContent, 'Guide');
const article = fields.get('content').children.find(node => node.tag === 'article');
assert.equal(article.children.some(node => node.textContent === '<script>bad()</script>'), true);
assert.equal(article.children.some(node => node.tag === 'script'), false);
assert.equal(article.children.some(node => node.tag === 'pre' && node.textContent === 'a < b'), true);
const old = wiki.openDocument('old-doc');
const current = wiki.openDocument('new-doc');
pending[2].resolve({ ok: true, json: async () => ({ document: { title: 'New', format: 'text' }, text: 'new text', totalChars: 8 }) }); await current;
pending[1].resolve({ ok: true, json: async () => ({ document: { title: 'OLD' }, text: 'old text' }) }); await old;
assert.equal(fields.get('title').textContent, 'New', 'late response does not replace current wiki');
const restoring = new AbortController();
const interrupted = wiki.openNode('raw', { notify: false, signal: restoring.signal });
restoring.abort(); assert.equal(root.hidden, true);
pending[3].resolve({ ok: true, json: async () => ({ node: { label: 'OLD' } }) }); await interrupted;
assert.equal(root.hidden, true);
const container = new Element('article'); renderWikiText(container, '<img src=https://remote/>', 'markdown');
assert.equal(container.children[0].tag, 'p');
let linkedDocument, linkedCode;
wiki.openDocument = id => { linkedDocument = id; };
wiki.onCode = id => { linkedCode = id; };
fields.get('content').replaceChildren();
wiki.documentCodeLinks({ nodeIds: [], sourceLinks: [{ file: 'Other.md', documentId: 'captured-other' }, { file: 'Code.cs', scope: 'file', nodeIds: ['raw-code'] }, { file: 'Unknown.md', nodeIds: [] }] });
const linkSection = fields.get('content').children[0];
linkSection.children.find(node => node.tag === 'button' && node.textContent === 'Wiki · Other.md').listeners.click();
assert.equal(linkedDocument, 'captured-other');
linkSection.children.find(node => node.tag === 'button' && node.textContent === 'Узел файла · raw-code').listeners.click();
assert.equal(linkedCode, 'raw-code');
assert.equal(linkSection.children.filter(node => node.tag === 'button').length, 2, 'unresolved href stays text');
console.log('Search wiki journeys PASS');
