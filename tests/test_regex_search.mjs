/** Реальные Worker-проверки regex без DOM, сети и дополнительных npm-пакетов. */
import assert from 'node:assert/strict';
import { Worker as NodeWorker } from 'node:worker_threads';
import { matchNodeLabels } from '../web/regex-match.js';
import { RegexSearch } from '../web/regex-search.js';

const nodes = [
  { id: 'first', label: 'InvoiceProvider', file: 'one.cs' },
  { id: 'INVOICE-ID', label: 'Other', file: 'Invoice.cs' },
  { id: 'third', label: 'Пример', file: '' },
];
const before = JSON.stringify(nodes);
assert.deepEqual(matchNodeLabels(nodes, '^invoice'), ['first']);
assert.deepEqual(matchNodeLabels(nodes, 'пРИМЕР'), ['third']);
assert.deepEqual(matchNodeLabels(nodes, ''), []);
assert.throws(() => matchNodeLabels(nodes, '['), SyntaxError);
assert.equal(JSON.stringify(nodes), before);

/** Адаптирует browser Worker к worker_threads и исполняет настоящий модуль worker. */
class BrowserWorker {
  static instances = [];
  constructor(url) {
    this.terminated = false;
    this.thread = new NodeWorker(`
      const { parentPort } = require('node:worker_threads');
      global.self = { postMessage: data => parentPort.postMessage(data) };
      import(${JSON.stringify(url.href)}).then(() => {
        parentPort.on('message', data => self.onmessage({ data }));
      });
    `, { eval: true });
    this.thread.on('message', data => this.onmessage?.({ data }));
    this.thread.on('error', error => this.onerror?.(error));
    BrowserWorker.instances.push(this);
  }
  postMessage(data) { this.thread.postMessage(data); }
  terminate() { this.terminated = true; return this.thread.terminate(); }
}
globalThis.Worker = BrowserWorker;
/** Ожидает проверяемое событие с ограниченным временем, не блокируя event loop. */
async function until(predicate, timeout = 2500) {
  const deadline = Date.now() + timeout;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error('Expected worker event did not arrive');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
const results = [], statuses = [];
const search = new RegexSearch(ids => results.push(ids), (message, error) => statuses.push({ message, error }));
try {
  search.search(nodes, 'provider$');
  await until(() => results.some(ids => ids.includes('first')));
  assert.equal(JSON.stringify(nodes), before);
  assert.ok(BrowserWorker.instances.at(-1).terminated);

  search.search(nodes, '[');
  await until(() => statuses.some(item => item.error && item.message.includes('Некорректное')));
  assert.deepEqual(results.at(-1), []);

  const oldCount = BrowserWorker.instances.length;
  search.search(nodes, 'Invoice');
  await until(() => BrowserWorker.instances.length > oldCount);
  const oldWorker = BrowserWorker.instances.at(-1);
  const staleReply = oldWorker.onmessage;
  search.search(nodes, 'Пример');
  staleReply({ data: { ids: ['stale-id'] } });
  await until(() => results.at(-1).includes('third'));
  assert.ok(oldWorker.terminated);
  assert.ok(!results.some(ids => ids.includes('stale-id')));

  let heartbeat = false;
  const started = Date.now();
  search.search([{ id: 'danger', label: 'a'.repeat(40000) + '!' }], '(a+)+$');
  setTimeout(() => { heartbeat = true; }, 40);
  await until(() => statuses.some(item => item.error && item.message.includes('500')));
  assert.ok(heartbeat, 'Expensive regex must not block the main thread');
  assert.ok(Date.now() - started < 2000, 'Dangerous worker must terminate promptly');
  assert.ok(BrowserWorker.instances.at(-1).terminated);
  assert.deepEqual(results.at(-1), []);

  const instances = BrowserWorker.instances.length;
  search.search(nodes, '');
  await new Promise(resolve => setTimeout(resolve, 180));
  assert.equal(BrowserWorker.instances.length, instances);
  assert.deepEqual(results.at(-1), []);
} finally {
  search.cancel();
  await Promise.all(BrowserWorker.instances.map(worker => worker.terminate()));
}
console.log('Regex worker isolation PASS');
