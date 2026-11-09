/** Факты атрибутов безопасны, разделены по владельцам и сохраняют точную строку исходника. */
import assert from 'node:assert/strict';
import { renderAttributeFacts } from '../web/attribute-details.js';
import { NodeAttributesViewer } from '../web/node-attributes.js';

class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  set innerHTML(value) { throw new Error('Unsafe HTML'); }
}
globalThis.document = { createElement: tag => new Element(tag) };
const text = node => [node.textContent || '', ...node.children.map(text)].join(' ');
const all = node => [node, ...node.children.flatMap(all)];
const data = { nodeId: 'method', snapshotId: 's', attributeFacts: {
  status: 'parsed', truncated: false, groups: [
    { scope: 'declaration', name: 'Calculate', attributes: [{ name: 'HttpPost', text: '[HttpPost("<img src=x onerror=bad()>")]', arguments: [{ position: 1, expression: '"<img src=x onerror=bad()>"' }], file: 'Controller.cs', line: 42 }] },
    { scope: 'type', name: 'Controller', attributes: [{ name: 'ApiVersion', arguments: [{ position: 1, name: 'Deprecated', expression: 'true' }], target: 'return' }] },
    { scope: 'parameter', name: 'request', attributes: [{ name: 'FromBody' }] },
  ],
} };
const locations = [];
const rendered = renderAttributeFacts(data, (...location) => locations.push(location));
assert.match(text(rendered), /Выбранное объявление · Calculate/);
assert.match(text(rendered), /Содержащий тип · Controller/);
assert.match(text(rendered), /Параметр · request/);
assert.match(text(rendered), /Deprecated true/);
assert.match(text(rendered), /Цель: return/);
assert.match(text(rendered), /<img src=x onerror=bad\(\)>/);
all(rendered).find(node => node.tagName === 'BUTTON').listeners.click();
assert.deepEqual(locations, [['method', 42]]);
const inherited = renderAttributeFacts({nodeId:'method',attributeFacts:{status:'parsed',groups:[{scope:'type',ownerNodeId:'base-type',attributes:[{name:'ExtendWith',file:'Base.java',line:27}]}]}},(...value)=>locations.push(value));
all(inherited).find(node=>node.tagName==='BUTTON').listeners.click();
assert.deepEqual(locations.at(-1),['base-type',27]);
assert.match(text(renderAttributeFacts({})), /недоступны/);
assert.doesNotMatch(text(renderAttributeFacts({})), /не найдены/);
assert.match(text(renderAttributeFacts({ attributeFacts: { status: 'parsed', groups: [] } })), /не найдены/);
assert.match(text(renderAttributeFacts({ attributeFacts: { status: 'partial', truncated: true, groups: data.attributeFacts.groups } })), /HttpPost/);
assert.match(text(renderAttributeFacts({ attributeFacts: { status: 'partial', truncated: true, groups: [] } })), /показана часть/);
const partialEmpty = renderAttributeFacts({ attributeFacts: { status: 'partial', truncated: false, groups: [{ scope: 'declaration', attributes: [] }] } });
assert.match(text(partialEmpty), /В доступной части/);
assert.match(text(partialEmpty), /Извлечение неполное/);
assert.doesNotMatch(text(partialEmpty), /атрибуты отсутствуют/);
assert.match(text(renderAttributeFacts({ attributeFacts: { ...data.attributeFacts, status: 'partial', truncated: false } })), /Извлечение неполное/);

const elements = new Map(['close', 'retry', 'title', 'status', 'body'].map(name => [name, new Element('div')]));
const root = { hidden: true, querySelector: selector => elements.get(selector.match(/"(.*?)"/)[1]) };
let context = { projectId: 'p', snapshotId: 's', nodeId: 'old', label: 'Old' };
const requests = [];
const viewer = new NodeAttributesViewer(root, () => context, { scrollOnRender: false, fetcher: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })) });
const first = viewer.open();
assert.match(elements.get('title').textContent, /Атрибуты · Old/);
context = { ...context, nodeId: 'method', label: 'Calculate' };
viewer.sync();
assert.equal(requests[0].options.signal.aborted, true);
const second = viewer.open();
requests[1].resolve({ ok: true, json: async () => data });
await second;
requests[0].resolve({ ok: true, json: async () => ({ ...data, nodeId: 'old' }) });
await first;
assert.match(text(elements.get('body')), /HttpPost/);
assert.equal(elements.get('title').textContent, 'Атрибуты · Calculate');
const stale = viewer.open();
requests[2].resolve({ ok: true, json: async () => ({ ...data, snapshotId: 'other' }) });
await stale;
assert.equal(elements.get('retry').hidden, false);
assert.equal(elements.get('body').children.length, 0);
console.log('Attribute scopes, source lines, safe text and stale snapshot PASS');
