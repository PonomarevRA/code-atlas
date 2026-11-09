import assert from 'node:assert/strict';
import { Svg } from './svg_fixture.mjs';
import { readerContract, readerStepExplanation, renderReaderContract } from '../web/reading-explanations.js';
import { readingProvenance, renderReadingProvenance } from '../web/reading-provenance.js';
import { renderReaderSteps } from '../web/method-reader-steps.js';

/** Native details/summary structure exposes keyboard-readable source without executing repository text. */
class Html extends Svg {
  constructor(tag) { super(tag); this.style = {}; }
  get className() { return this.attributes.class || ''; }
  set className(value) { this.attributes.class = value; }
}
globalThis.document = { createElement: tag => new Html(tag) };

const description = { nodeId: 'handler', returnType: 'Task<Response>',
  documentation: { summary: 'Produces the selected response.', params: [{ name: 'request', text: 'Original request.' }], returns: 'Selected response.' },
  typeContext: { documentation: { summary: 'Class explanation is not method purpose.' } },
  parameters: [{ name: 'request', type: 'Request' }, { name: 'token', type: 'CancellationToken' }], events: [] };
const contract = readerContract(description);
assert.equal(contract.purpose, 'Produces the selected response.');
assert.deepEqual(contract.parameters[0], { name: 'request', type: 'Request', description: 'Original request.' });
assert.equal(contract.parameters[1].description, '');
assert.equal(contract.result.type, 'Task<Response>');
assert.equal(readerContract({ typeContext: description.typeContext }).purpose, '', 'Type documentation never invents method purpose');
const contractDom = new Html('div'); renderReaderContract(contractDom, description);
assert.ok(contractDom.textContent.includes('Request') && contractDom.textContent.includes('Original request.'));
assert.ok(contractDom.textContent.includes('Описание параметра отсутствует.'));

for (const [condition, phrase] of [['limits.Length == 0', 'равно «0»'], ['amount > 0', 'больше «0»'],
  ['amount >= 0', 'больше или равно «0»'], ['value == null', 'отсутствует'], ['value != null', 'задано']]) {
  const explanation = readerStepExplanation({ kind: 'branch', controlKind: 'if', label: condition }, description);
  assert.ok(explanation.text.includes(phrase)); assert.equal(explanation.exactExpression, condition);
}
const complex = 'Evaluate(a => a > 0) && enabled';
assert.ok(readerStepExplanation({ kind: 'branch', controlKind: 'if', label: complex }).text.includes('Evaluate(a => a > 0)'));
assert.ok(readerStepExplanation({ kind: 'loop', controlKind: 'foreach', label: 'foreach (var source in active)' }).text.includes('«active»'));
assert.ok(readerStepExplanation({ kind: 'loop', controlKind: 'for', label: 'for (;;)' }).text.includes('не задано'));
assert.ok(readerStepExplanation({ kind: 'branch', controlKind: 'try' }).text.includes('при исключении'));
for (const operator of ['&&', '||']) {
  for (const label of [undefined, '', 'null', 'undefined']) {
    const explanation = readerStepExplanation({ kind: 'branch', controlKind: operator, label });
    assert.equal(explanation.source, 'unknown');
    assert.ok(explanation.text.includes('результат проверки слева'));
    assert.ok(!explanation.text.includes('«null»') && !explanation.text.includes('«undefined»'));
    assert.equal(explanation.exactExpression, label || '', 'Incomplete source evidence remains inspectable without inventing a condition');
  }
  const known = readerStepExplanation({ kind: 'branch', controlKind: operator, label: 'request.Filter is not null' });
  assert.equal(known.source, 'code');
  assert.ok(known.text.includes(operator === '&&' ? 'не равно null' : '«request.Filter is not null»'));
}
assert.ok(readerStepExplanation({ kind: 'break' }).text.includes('или switch'));
assert.ok(readerStepExplanation({ kind: 'continue' }).text.includes('ближайшего цикла'));
assert.equal(readerStepExplanation({ kind: 'unknown' }).source, 'unknown');
const comment = readerStepExplanation({ kind: 'branch', controlKind: 'if', label: 'flag', line: 9, column: 5 },
  { events: [{ kind: 'comment', text: '// Author supplied rule', line: 8, column: 5 }] });
assert.equal(comment.comment, 'Author supplied rule');
assert.equal(readerStepExplanation({ kind: 'operation', line: 9, column: 10 },
  { events: [{ kind: 'comment', text: '// Different owner', line: 8, column: 5 }] }).comment, '');
assert.equal(readerStepExplanation({ kind: 'branch', line: 9, column: 5 },
  { events: [{ kind: 'comment', text: '// End of another statement', line: 9, column: 50 }] }).comment, '');
const callEvidence = readerStepExplanation({ kind: 'call', label: 'Calculate()', line: 9, column: 5,
  site: { line: 9, column: 5, endLine: 9, endColumn: 35 } },
  { events: [{ line: 9, column: 5, endLine: 9, endColumn: 35, callee: 'Calculate', sourceExpression: 'Calculate(request, token)' }] });
assert.equal(callEvidence.exactExpression, 'Calculate(request, token)', 'Call fragments retain real arguments despite compact diagram labels');

// Exact source is inert and collapsed; lexical branch regions remain alternatives.
const steps = [{ kind: 'branch', controlKind: 'if', label: 'activity != null', line: 10, arms: [
  { label: 'истина', steps: [{ kind: 'call', label: 'Tag()', line: 11 }] }, { label: 'иначе', steps: [] }] },
{ kind: 'call', label: 'BeginScope()', line: 12 },
{ kind: 'branch', controlKind: 'try', line: 13, arms: [{ label: 'обычное выполнение', steps: [
  { kind: 'call', label: 'Calculate(request)', line: 14 }, { kind: 'await', label: 'await Calculate(request)', line: 14 },
  { kind: 'call', label: 'Ok(await Calculate(request))', line: 14 }, { kind: 'return', label: 'return Ok(await Calculate(request));', line: 14 }] },
{ label: 'catch (Exception error)', exception: true, steps: [{ kind: 'throw', label: 'throw;', line: 15 }] }] },
{ kind: 'unknown', label: '<script>alert(1)</script>', line: 16 }];
const body = new Html('div'); renderReaderSteps(body, steps, description, { onCall() {}, onSource() {} });
const exactSources = body.querySelectorAll('.reader-exact-source');
assert.equal(exactSources.length, 9);
assert.ok(exactSources.every(node => node.attributes.open === undefined && node.open !== true));
assert.equal(body.querySelectorAll('script').length, 0);
assert.equal(body.children[1].attributes['data-kind'], 'call', 'BeginScope is a sibling after activity guard');
const normal = body.children[2].querySelector('.reader-arm');
assert.deepEqual(normal.children.filter(child => child.tag === 'article').map(child => child.attributes['data-kind']), ['call', 'await', 'call', 'return']);
assert.ok(body.children[2].textContent.includes('Возможная обработка исключения'));
assert.ok(body.textContent.includes('Тогда') && body.textContent.includes('Иначе'));

const captured = 'a'.repeat(64), current = 'b'.repeat(64);
const facts = { status: 'changed', snapshotId: 's', snapshotCreatedAt: 'yesterday', sourceKind: 'working-tree',
  capturedSourceHash: captured, currentSourceHash: current, currentReadAt: 'today' };
assert.equal(readingProvenance(facts).status, 'changed');
assert.equal(readingProvenance({ ...facts, capturedSourceHash: null }).status, 'unknown');
assert.equal(readingProvenance({ ...facts, sourceKind: 'snapshot-captured-source' }).status, 'unknown');
assert.equal(readingProvenance({ ...facts, status: 'match' }).status, 'unknown', 'Inconsistent match claim is not repeated');
assert.equal(readingProvenance({ sourceHash: captured, snapshotId: 's' }).status, 'unknown');
let refresh = 0, continued = 0;
const provenanceDom = new Html('div');
renderReadingProvenance(provenanceDom, facts, { onRefresh() { refresh++; }, onContinue() { continued++; } });
assert.equal(refresh, 0, 'Displaying changed provenance never rebuilds');
const buttons = provenanceDom.querySelectorAll('button');
buttons.find(button => button.textContent === 'Обновить карту').listeners.click();
buttons.find(button => button.textContent === 'Продолжить чтение').listeners.click();
assert.equal(refresh, 1); assert.equal(continued, 1);
assert.ok(provenanceDom.textContent.includes('yesterday') && provenanceDom.textContent.includes('today'));
const unknownDom = new Html('div'); renderReadingProvenance(unknownDom, {}, { onRefresh() { refresh++; } });
assert.equal(unknownDom.querySelectorAll('button').length, 0);
assert.ok(unknownDom.textContent.includes('Совпадение со снимком не установлено'));
console.log('Onboarding exact provenance, conservative explanations, contracts and collapsed source PASS');
