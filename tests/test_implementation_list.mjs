import assert from 'node:assert/strict';
import { ImplementationList } from '../web/implementation-list.js';
import { buildMethodSequence } from '../web/method-sequence.js';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';

globalThis.document = { createElement: tag => new Svg(tag), createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const root = new Svg('section'), title = new Svg('strong'), close = new Svg('button'), body = new Svg('div');
root.querySelector = selector => ({ '[data-implementations="title"]': title, '[data-implementations="close"]': close, '[data-implementations="body"]': body })[selector];
let focused = 0; close.focus = () => focused++;
const opened = [], opener = { isConnected: true, focus: () => focused++ };
const list = new ImplementationList(root, { onSource: (...args) => opened.push(args) });
const items = Array.from({ length: 7 }, (_, i) => ({ id: `loader${i}`, label: `Loader${i}.LoadAsync()`, file: `Loader${i}.cs`, line: 19,
  scope: i < 5 ? 'source' : i === 5 ? 'delegate' : 'tests' }));
const method = { id: 'caller', label: 'Run()', events: [{ text: '_sources.LoadAsync()', line: 1, column: 1, targetNodeIds: [], implementationTargetIds: ['iface'] }],
  methodFlow: { status: 'parsed', steps: [{ kind: 'call', label: 'LoadAsync()', site: { line: 1, column: 1 }, line: 1, column: 1 }] } };
const catalog = { iface: { total: 7, items, truncated: false } };
const scene = buildMethodSequence({ nodes: [method], edges: [] }, 'caller', { implementationCatalog: catalog });
const call = scene.visuals.find(n => n.kind === 'call');
assert.equal(call.candidateCount, 7); assert.equal(call.implementationItems.length, 7);
list.open(call, { projectId: 'p', snapshotId: 's' }, opener);
assert.equal(body.children.filter(n => n.tag === 'button').length, 7);
assert.ok(title.textContent.includes('7'));
body.children.find(n => n.attributes['data-implementation-id'] === 'loader6').listeners.click();
assert.deepEqual(opened, [['loader6', 19]]); assert.equal(root.hidden, true);
list.open(call, { projectId: 'p', snapshotId: 's' }, opener);
root.listeners.keydown({ key: 'Escape', preventDefault() {} }); assert.equal(root.hidden, true); assert.ok(focused >= 3);
list.open(call, { projectId: 'p', snapshotId: 's' }, opener); list.sync('p', 'new'); assert.equal(root.hidden, true);
const svg = new Svg('svg'), viewport = new Svg('g'); viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const view = new GraphView(svg, () => {}, () => {}); view.pathRootId = 'caller'; view.pathImplementationCatalog = catalog;
view.onImplementationList = node => assert.equal(node.candidateCount, 7);
view.renderPathCards([method], [], 'caller');
viewport.querySelector('.method-implementation-list').listeners.click({ stopPropagation() {}, preventDefault() {} });
assert.ok(viewport.querySelector('.method-implementation-list').textContent.includes('Реализации: 7'));
assert.equal(viewport.querySelector('.method-implementation-summary').textContent, '5 в проекте · 1 переадресует · 1 в тестах');
console.log('Implementation names outside path budget, source navigation, Escape, snapshot sync and actual map action PASS');
