import assert from 'node:assert/strict';
import {renderHtmlFacts} from '../web/html-facts.js';

class TextElement {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.value = ''; }
  set textContent(value) { this.value = String(value); }
  get textContent() { return this.value + this.children.map(child => child.textContent).join(' '); }
  set innerHTML(_) { throw new Error('HTML source must remain untrusted text'); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(event, listener) { this.listeners[event] = listener; }
}
globalThis.document = {createElement: tag => new TextElement(tag)};
const panel = new TextElement('div');
let selected;
renderHtmlFacts(panel, {htmlFacts: {role: 'duplicate-group', occurrenceCount: 2,
  recommendation: 'Выделить layout', occurrences: [{file: '<img onerror=alert(1)>.html', line: 3, endLine: 6, nodeId: 'exact:raw'}],
  warnings: ['Неполный анализ'], truncated: true}}, id => { selected = id; });
assert.match(panel.textContent, /2 мест/);
assert.match(panel.textContent, /<img onerror=alert\(1\)>/);
assert.match(panel.textContent, /неполные/);
panel.children.find(child => child.tag === 'button').listeners.click();
assert.equal(selected, 'exact:raw');
const unknown = new TextElement('div');
renderHtmlFacts(unknown, {htmlFacts: {sourceRole: 'unknown-output', title: '<script>bad()</script>'}}, () => {});
assert.match(unknown.textContent, /Дубли не оценивались/);
assert.match(unknown.textContent, /<script>bad\(\)<\/script>/);
const tree = new TextElement('div');
let request;
renderHtmlFacts(tree, {id: 'html:raw', htmlFacts: {role: 'block', parser: 'html5lib-1.1', generated: true,
  cssSelector: 'html > body > table > tbody', parentNodeId: 'parent'}}, id => { selected = id; }, {
  projectId: 'p', snapshotId: 's', fetcher: async (url, options) => {
    request = {url, body: JSON.parse(options.body)};
    return {ok: true, json: async () => ({total: 1, nextOffset: null, nodes: [{id: 'found:raw', label: '<b>'}]})};
  }
});
assert.match(tree.textContent, /добавлен HTML5-парсером/);
assert.match(tree.textContent, /CSS-селектор/);
const form = tree.children.find(child => child.tag === 'form');
form.children[0].children[0].value = 'table > tbody';
await form.listeners.submit({preventDefault() {}});
assert.equal(request.body.selector, 'table > tbody');
assert.equal(request.body.snapshotId, 's');
form.children[2].children.find(child => child.tag === 'button').listeners.click();
assert.equal(selected, 'found:raw');
console.log('PASS HTML captured facts, safe text, unknown output and exact navigation');
