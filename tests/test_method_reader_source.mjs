import assert from 'node:assert/strict';
import { MethodReaderSource } from '../web/method-reader-source.js';

/** Deferred transport exercises cancellation even when fetch ignores its AbortSignal. */
function transport() {
  const requests = [];
  const fetcher = (url, options) => new Promise((resolve, reject) => {
    const body = JSON.parse(options.body);
    requests.push({ url, options, body, resolve: data => resolve({ ok: true, json: async () => data }), reject });
  });
  return { requests, fetcher };
}
const tick = async () => { for (let index = 0; index < 8; index++) await Promise.resolve(); };
const description = (id, snapshotId = 's') => ({ nodeId: id, snapshotId, label: `${id}()`,
  documentation: { summary: `Reads ${id}` }, methodFlow: { status: 'parsed', steps: [] } });
const settled = promise => promise.then(value => ({ value }), error => ({ error }));

// An unbound or empty request is an error, never a request against an inferred context.
const unboundHttp = transport(), unbound = new MethodReaderSource({ fetcher: unboundHttp.fetcher });
await assert.rejects(unbound.load('a'));
unbound.setContext({ projectId: 'p', snapshotId: 's' });
await assert.rejects(unbound.load(''));
assert.equal(unboundHttp.requests.length, 0);

// Bounded concurrency and explicit reading priority must hold independently of map size.
const http = transport();
const source = new MethodReaderSource({ fetcher: http.fetcher, maxEntries: 32, concurrency: 2 });
source.setContext({ projectId: 'project/one', snapshotId: 's' });
const a = source.load('a'), b = source.load('b'), background = source.load('background');
const selected = source.load('selected');
const selectedAgain = source.load('selected', { priority: true });
await tick();
assert.equal(http.requests.length, 2, 'Only two previews may fetch simultaneously');
assert.ok(http.requests.every(r => r.url.includes('project%2Fone') && r.body.snapshotId === 's'));
http.requests.find(r => r.body.nodeId === 'a').resolve(description('a'));
await a; await tick();
assert.equal(http.requests[2].body.nodeId, 'selected', 'Explicit read outranks queued automatic previews');
http.requests.find(r => r.body.nodeId === 'b').resolve(description('b'));
await b; await tick();
assert.equal(http.requests[3].body.nodeId, 'background');
http.requests.find(r => r.body.nodeId === 'selected').resolve(description('selected'));
http.requests.find(r => r.body.nodeId === 'background').resolve(description('background'));
assert.deepEqual(await selected, await selectedAgain);
await background;

// A duplicate request shares work, cached facts need no fetch, and force bypasses the cache.
const before = http.requests.length;
assert.equal((await source.load('a')).nodeId, 'a');
assert.equal(http.requests.length, before);
const first = source.load('new'), duplicate = source.load('new');
await tick();
assert.equal(http.requests.filter(r => r.body.nodeId === 'new').length, 1);
http.requests.at(-1).resolve(description('new'));
assert.deepEqual(await first, await duplicate);
const forced = source.load('a', { force: true }); await tick();
assert.equal(http.requests.length, before + 2);
http.requests.at(-1).resolve(description('a')); await forced;

// An incompatible response must never enter the cache, including a seeded description.
for (const wrong of [description('other'), description('mismatch', 'other-snapshot')]) {
  const attempt = settled(source.load('mismatch', { force: true })); await tick();
  http.requests.at(-1).resolve(wrong);
  assert.ok((await attempt).error, 'Reject a different node or snapshot');
}
source.seed('seeded', description('seeded'));
assert.equal((await source.load('seeded')).nodeId, 'seeded');
try { source.seed('seeded', description('wrong')); } catch { /* Rejecting the incompatible seed is also valid. */ }
assert.equal((await source.load('seeded')).nodeId, 'seeded', 'An incompatible seed cannot replace valid evidence');

// Late transport responses cannot contaminate a different project/snapshot context.
const old = settled(source.load('late'));
await tick(); const staleRequest = http.requests.at(-1);
const queuedOld = settled(source.load('queued-old'));
source.setContext({ projectId: 'new-project', snapshotId: 'new-snapshot' });
await tick();
assert.ok(staleRequest.options.signal.aborted);
assert.ok((await old).error);
assert.ok((await queuedOld).error);
staleRequest.resolve(description('late')); await tick();
const fresh = source.load('late'); await tick();
assert.equal(http.requests.at(-1).body.snapshotId, 'new-snapshot');
http.requests.at(-1).resolve(description('late', 'new-snapshot'));
assert.equal((await fresh).snapshotId, 'new-snapshot');

// Cache eviction is bounded; requesting an evicted oldest entry obtains fresh evidence.
const smallHttp = transport(), small = new MethodReaderSource({ fetcher: smallHttp.fetcher, maxEntries: 3, concurrency: 2 });
small.setContext({ projectId: 'p', snapshotId: 's' });
for (const id of ['one', 'two', 'three', 'four']) small.seed(id, description(id));
const evicted = small.load('one'); await tick();
assert.equal(smallHttp.requests.length, 1, 'Fourth insertion evicts oldest of a three-entry cache');
smallHttp.requests[0].resolve(description('one')); await evicted;
const cancelled = settled(small.load('pending')); await tick();
small.cancel(false); await tick();
assert.ok((await cancelled).error);
assert.equal((await small.load('one')).nodeId, 'one', 'Closing preserves valid same-context cache');
smallHttp.requests.at(-1).resolve(description('pending')); await tick();

// Failed transport is retryable and never retained as a successful description.
let attempts = 0;
const retry = new MethodReaderSource({ fetcher: async (_url, options) => {
  attempts++;
  if (attempts === 1) throw new Error('Temporary transport failure');
  return { ok: true, json: async () => description(JSON.parse(options.body).nodeId) };
} });
retry.setContext({ projectId: 'p', snapshotId: 's' });
await assert.rejects(retry.load('retry')); await tick();
assert.equal((await retry.load('retry')).nodeId, 'retry');
assert.equal(attempts, 2);

// The production default bounds successful bodies to 32, not only the configurable small fixture.
const boundedHttp = transport(), boundedSource = new MethodReaderSource({ fetcher: boundedHttp.fetcher });
boundedSource.setContext({ projectId: 'p', snapshotId: 's' });
for (let index = 0; index < 40; index++) boundedSource.seed(`method${index}`, description(`method${index}`));
assert.equal((await boundedSource.load('method39')).nodeId, 'method39');
assert.equal(boundedHttp.requests.length, 0);
const oldest = boundedSource.load('method0'); await tick();
assert.equal(boundedHttp.requests.length, 1);
boundedHttp.requests[0].resolve(description('method0')); await oldest;
console.log('Method reader source identity, priority, two-request bound, cache and stale cancellation PASS');
