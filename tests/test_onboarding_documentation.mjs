import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Svg } from './svg_fixture.mjs';
import { MethodDocumentation } from '../web/method-documentation.js';

/** Render against native DOM-shaped details, attributes and safe text. */
class Html extends Svg {
  constructor(tag) { super(tag); this.style = {}; }
  get className() { return this.attributes.class || ''; }
  set className(value) { this.attributes.class = value; }
  focus() { this.focused = true; }
}
globalThis.document = { createElement: tag => new Html(tag) };
const fields = new Map(['diagram', 'root', 'method', 'body', 'title', 'status'].map(name => [name, new Html('div')]));
const root = { hidden: true, querySelector: selector => fields.get(selector.match(/"(.*?)"/)[1]) };
const selections = [], sources = []; let diagrams = 0, refreshes = 0;
const viewer = new MethodDocumentation(root, { onSelect: id => selections.push(id),
  onSource: (id, line) => sources.push([id, line]), onDiagram: () => diagrams++, onRefreshMap: () => refreshes++ });
const captured = 'a'.repeat(64), current = 'b'.repeat(64);
const fallback = { snapshotId: 's', rootNodeId: 'run', edges: [], truncation: {}, steps: [{ nodeId: 'run', label: 'Run()',
  sourceStatus: 'parsed', sourceKind: 'working-tree', parameters: [{ name: 'request', type: 'Request' }], returnType: 'Task<Result>',
  documentation: { params: [{ name: 'request', text: 'Original request.' }] },
  sourceProvenance: { status: 'changed', sourceKind: 'working-tree', snapshotId: 's', snapshotCreatedAt: 'then',
    capturedSourceHash: captured, currentSourceHash: current, currentReadAt: 'now' },
  events: [{ kind: 'call', callee: 'Calculate', text: 'Calculate()', line: 10, column: 19, targetNodeIds: ['calc'] },
    { kind: 'call', callee: 'Ok', text: 'Ok()', line: 10, column: 8, targetNodeIds: ['ok'] }],
  methodFlow: { status: 'parsed', steps: [{ kind: 'branch', controlKind: 'if', label: 'activity != null', line: 7, arms: [
    { label: 'истина', steps: [{ kind: 'call', label: 'Tag()', line: 8 }] }, { label: 'иначе', steps: [] }] },
    { kind: 'call', label: 'BeginScope()', line: 9 },
    { kind: 'branch', controlKind: 'try', line: 10, arms: [{ label: 'обычное выполнение', steps: [
      { kind: 'call', label: 'Calculate()', line: 10, column: 19 }, { kind: 'await', label: 'await Calculate()', line: 10 },
      { kind: 'call', label: 'Ok()', line: 10, column: 8 }, { kind: 'return', label: 'return Ok(await Calculate());', line: 10 }] }] }] } },
  { nodeId: 'calc', label: 'Calculate()', documentation: { summary: 'Compute the requested value.' } },
  { nodeId: 'ok', label: 'Ok()', documentation: { summary: 'Produce the response.' } }] };
// Pytest passes a real SourceAnalysis/methodFlow response; standalone invocation retains a compact contract fixture.
const data = process.argv[2] ? JSON.parse(readFileSync(process.argv[2], 'utf8')) : fallback;
viewer.display(data, 'run', true);
const body = fields.get('body'), sequence = body.querySelector('.method-doc-evaluation');
assert.ok(sequence, 'Parsed methodFlow chooses the shared evaluation renderer');
assert.equal(sequence.children[0].attributes['data-kind'], 'branch');
assert.equal(sequence.children[1].attributes['data-kind'], 'call', 'BeginScope remains outside activity guard');
const normal = sequence.children[2].querySelector('.reader-arm');
const actions = normal.children.filter(child => child.tag === 'article');
assert.deepEqual(actions.map(action => action.attributes['data-kind']), ['call', 'await', 'call', 'return']);
assert.ok(actions[0].textContent.includes('Calculate') && actions[2].textContent.includes('Ok'));
const step = data.steps.find(item => item.nodeId === 'run');
const events = step.events.filter(event => ['Calculate', 'Ok'].includes(event.callee));
assert.equal(events[0].line, events[1].line, 'Nested calls really share one source line');
assert.ok(events.find(event => event.callee === 'Ok').column < events.find(event => event.callee === 'Calculate').column,
  'Lexical column order is opposite to evaluation order');
assert.ok(body.textContent.includes('Комментарий о назначении метода не найден'));
assert.ok(body.textContent.includes('Request') && body.textContent.includes('Original request.'));
assert.ok(body.textContent.includes('Task<Result>'));
assert.ok(body.textContent.includes('отличается от снимка'));
assert.ok(body.querySelectorAll('.reader-exact-source').every(node => !node.open));
assert.equal(refreshes, 0, 'Provenance is nonblocking and never initiates a rebuild');
body.querySelectorAll('button').find(button => button.textContent === 'Обновить карту').listeners.click();
assert.equal(refreshes, 1);
actions[0].querySelector('.method-doc-call').listeners.click();
assert.deepEqual(selections, ['calc'], 'Explicit target selection preserves onSelect semantics');
actions[0].querySelector('.reader-source-link').listeners.click();
assert.deepEqual(sources, [['run', events.find(event => event.callee === 'Calculate').line]]);
fields.get('diagram').listeners.click(); assert.equal(diagrams, 1);

const unknown = { nodeId: 'unknown', label: 'Unknown()', sourceStatus: 'unavailable',
  documentation: {}, controlFlow: { branches: [], terminals: [] }, events: [] };
viewer.display({ rootNodeId: 'unknown', steps: [unknown], edges: [], snapshotId: 's' }, 'unknown', true);
assert.ok(!fields.get('body').querySelector('.method-doc-evaluation'), 'Legacy fallback remains available');
assert.ok(fields.get('body').textContent.includes('Совпадение со снимком не установлено'));
assert.ok(fields.get('body').textContent.includes('Тело метода недоступно'));
assert.ok(fields.get('status').textContent.includes('порядок вычисления выражений не установлен'));
assert.ok(!fields.get('body').textContent.includes('Compute the requested value.'), 'Earlier purpose does not leak into an unknown method');
console.log('Onboarding documentation actual evaluation order, source trust and existing callbacks PASS');
