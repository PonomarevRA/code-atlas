/** Named inputs, literal distinctions, snapshot paging and exact class ownership. */
import assert from 'node:assert/strict';
import { appendDatasetTable } from '../web/test-dataset-table.js';
import { appendTestEntryDetails } from '../web/test-entry-details.js';
import { renderTestClassOverview, testsForContainer } from '../web/test-class-overview.js';
class Element {
  constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(name, value) { this[name] = value; }
  set innerHTML(_) { throw Error('Unsafe HTML'); }
}
globalThis.document = { createElement: tag => new Element(tag) };
const all = node => [node, ...node.children.flatMap(all)];
const text = node => all(node).map(item => item.textContent || '').join(' ');
const button = (root, label) => all(root).find(item => item.textContent === label);
const string = value => ({ kind: 'string', value });
const map = entries => ({ kind: 'map', entries: Object.entries(entries).map(([key, value]) => ({ key: string(key), value })) });
const entry = { kind: 'test', label: 'ClientId', handlerNodeIds: ['test'], containerNodeId: 'class', framework: 'junit5', parameters: [
  { id: 'scenario', name: 'scenarioName', role: 'indexed' },
  { id: 'query', name: 'queryParams', role: 'indexed' },
  { id: 'dsl', name: 'dsl', type: 'DSLContext', role: 'context' },
] };
const source = { id: 'dataset', nodeIds: ['provider'], declaredRowCount: 4, rows: [
  { id: 'one', index: 0, sourceLocation: { nodeId: 'provider', line: 12 }, bindings: [ { parameterId: 'scenario', name: 'scenarioName', value: string('Missing') }, { parameterId: 'query', name: 'queryParams', value: map({ Period: string('All') }) } ] },
  { id: 'two', index: 1, sourceLocation: { nodeId: 'provider', line: 14 }, bindings: [ { parameterId: 'scenario', name: 'scenarioName', value: string('Empty') }, { parameterId: 'query', name: 'queryParams', value: map({ ClientId: string('') }) } ] },
  { id: 'three', index: 2, bindings: [ { parameterId: 'scenario', name: 'scenarioName', value: string('Null') }, { parameterId: 'query', name: 'queryParams', value: map({ ClientId: { kind: 'null' } }) } ] },
  { id: 'four', index: 3, bindings: [ { parameterId: 'query', name: 'queryParams', value: map({ ClientId: string('null') }) } ] },
] };
const root = new Element(), locations = [];
appendDatasetTable(root, source, entry, { onSource: (...args) => locations.push(args) });
assert.match(text(root), /scenarioName/); assert.match(text(root), /queryParams/); assert.doesNotMatch(text(root), /DSLContext/);
assert.match(text(root), /ключ отсутствует/); assert.match(text(root), /"" · пустая строка/); assert.match(text(root), /"null"/); assert.match(text(root), /привязка не установлена/);
button(root, 'Строка 12').listeners.click(); assert.deepEqual(locations, [['provider', 12]]);
button(root, 'Вариант 1').listeners.click(); button(root, 'Вариант 2').listeners.click();
assert.equal(button(root, 'Вариант 1')['aria-pressed'], 'false'); assert.equal(button(root, 'Вариант 2')['aria-pressed'], 'true');
const detail = new Element(), containerTargets = [];
appendTestEntryDetails(detail, { ...entry, dataSources: [source] }, { onNode: id => containerTargets.push(id) });
button(detail, 'Все тесты класса').listeners.click(); assert.deepEqual(containerTargets, ['class']);
assert.match(text(detail), /dsl · DSLContext · контекст расширения; не ячейка/);
const requests = [], pending = [], paged = new Element(); let current = true;
appendDatasetTable(paged, { ...source, rowsTruncated: true }, entry, { isCurrent: () => current, loadRows: (...args) => { requests.push(args); return new Promise((resolve, reject) => pending.push({ resolve, reject })); } });
const first = button(paged, 'Открыть страницы данных').listeners.click();
assert.match(text(paged), /Читаем объявленные строки/); pending[0].reject(Error('offline')); await first;
assert.match(text(paged), /offline/); assert.equal(button(paged, 'Повторить загрузку строк').disabled, false);
const retry = button(paged, 'Повторить загрузку строк').listeners.click(); pending[1].resolve({ rows: [source.rows[1]], parameters: [{id:'scenario',name:'canonicalName',role:'indexed'}], nextCursor: 'cursor2', declaredRowCount: 4 }); await retry;
assert.deepEqual(requests[1], ['dataset', null]); assert.match(text(paged), /показано 1 из 4/); assert.match(text(paged), /canonicalName/);
const next = button(paged, 'Следующие строки').listeners.click(); current = false; pending[2].resolve({ rows: [], nextCursor: null }); await next;
assert.deepEqual(requests[2], ['dataset', 'cursor2']); assert.match(text(paged), /показано 1 из 4/);
const tests = [{ ...entry, id: 'derived-1', line: 3 }, { ...entry, id: 'neighbor', handlerNodeIds: ['nearby'], containerNodeId: 'another', file: 'Same.java' }, { ...entry, id: 'nested', handlerNodeIds: ['nested-test'], containerNodeId: 'nested-class' }];
assert.equal(testsForContainer('class', tests).length, 1);
assert.equal(testsForContainer('class', [{ ...entry, containerNodeId: undefined }]).length, 0);
const overview = new Element(), opened = [];
assert.equal(renderTestClassOverview(overview, { id: 'class', label: 'Tests' }, tests, { onTest: test => opened.push(test.handlerNodeIds[0]) }), true);
assert.match(text(overview), /Тесты класса · 1/); button(overview, 'ClientId').listeners.click(); assert.deepEqual(opened, ['test']);
assert.doesNotMatch(text(overview), /neighbor|nested-test/);
console.log('Named test bindings, literal map distinctions, context, paging/retry/stale and exact class ownership PASS');
const boundedDetail = new Element();
appendTestEntryDetails(boundedDetail, { ...entry, parameters: undefined, dataSources: [{ id: 'source', rows: [], declaredRowCount: 1 }], detailTruncated: true }, {
  loadRows: async () => ({ rows: [source.rows[0]], parameters: entry.parameters, declaredRowCount: 1 }),
});
await button(boundedDetail, 'Открыть страницы данных').listeners.click();
assert.match(text(boundedDetail), /dsl · DSLContext · контекст расширения; не ячейка/);
assert.match(text(boundedDetail), /scenarioName/);
console.log('Canonical dataset page restores context after bounded graph-node detail PASS');
