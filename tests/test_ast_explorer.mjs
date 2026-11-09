/** Lazy syntax navigation keeps node/snapshot identity and never inserts source as HTML. */
import assert from 'node:assert/strict';
import { renderAstExplorer } from '../web/ast-explorer.js';
import { renderNodeSemanticStatus } from '../web/node-semantic-status.js';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
  set innerHTML(_) { throw Error('Source must remain inert'); }
}
globalThis.document = { createElement: tag => new Element(tag) };
const nodes = root => [root, ...root.children.flatMap(nodes)];
const text = root => nodes(root).map(node => node.textContent || '').join(' ');
const button = (root, label) => nodes(root).find(node => node.textContent === label);
const context = { projectId: 'project/one', snapshotId: 'snapshot', nodeId: 'method' };
const controller = new AbortController();
let current = true;
const requests = [];
const fetcher = (url, options) => new Promise(resolve => requests.push({ url, options, resolve }));
const panel = renderAstExplorer(context, { fetcher, signal: controller.signal, isCurrent: () => current });
assert.equal(requests.length, 0);
const initial = button(panel, 'Показать дочерние узлы').listeners.click();
assert.equal(requests[0].url, '/api/projects/project%2Fone/method-flow-chunk');
assert.deepEqual(JSON.parse(requests[0].options.body), { snapshotId: 'snapshot', nodeId: 'method', limit: 24 });
const page = { ...context, available: true, current: { type: 'method' }, childCount: 65, nextRef: 'next',
  children: [{ type: 'block', label: '<script>unsafe()</script>', ref: 'child', hasChildren: true }] };
requests[0].resolve({ ok: true, json: async () => page });
await initial;
assert.ok(text(panel).includes('<script>unsafe()</script>'));
assert.ok(text(panel).includes('не вычисляются'));
const deeper = button(panel, 'Глубже').listeners.click();
assert.equal(JSON.parse(requests[1].options.body).chunkRef, 'child');
requests[1].resolve({ ok: true, json: async () => ({ ...page, parentRef: 'parent', nextRef: null, children: [] }) });
await deeper;
assert.ok(button(panel, 'На уровень выше'));
const stale = button(panel, 'На уровень выше').listeners.click();
current = false;
requests[2].resolve({ ok: true, json: async () => ({ ...page, current: { type: 'STALE' } }) });
await stale;
assert.ok(!text(panel).includes('STALE'));

current = true;
const semantic = renderNodeSemanticStatus(context, { providers: [{ language: 'python', availability: { status: 'missing' } }] },
  { fetcher, signal: controller.signal, isCurrent: () => current });
assert.ok(text(semantic).includes('не найден'));
const update = button(semantic, 'Уточнить выбранный узел').listeners.click();
assert.deepEqual(JSON.parse(requests[3].options.body), { snapshotId: 'snapshot', nodeIds: ['method'] });
controller.abort();
requests[3].resolve({ ok: true, json: async () => ({ providers: [{ language: 'STALE' }] }) });
await update;
assert.ok(!text(semantic).includes('STALE'));
console.log('AST lazy navigation, syntax boundary, source safety and selected-node cancellation PASS');
