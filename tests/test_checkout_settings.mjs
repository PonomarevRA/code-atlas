import assert from 'node:assert/strict';
import test from 'node:test';
import {CheckoutSettings} from '../web/checkout-settings.js';

/** Small literal DOM fixture; any HTML parsing is an explicit test failure. */
class Element {
  constructor(tag, document) {
    this.tag = tag; this.ownerDocument = document; this.children = []; this.dataset = {};
    this.attributes = {}; this.listeners = new Map(); this.disabled = false; this._text = '';
  }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = String(value); for (const child of this.children) child.parent = null; this.children = []; }
  set innerHTML(value) { throw new Error('HTML parsing forbidden'); }
  append(...nodes) { for (const node of nodes) { node.remove(); node.parent = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.textContent = ''; this.append(...nodes); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); this.parent = null; }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  get isConnected() { return this.ownerDocument.body.contains(this); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(type, callback) { const values = this.listeners.get(type) || new Set(); values.add(callback); this.listeners.set(type, values); }
  removeEventListener(type, callback) { this.listeners.get(type)?.delete(callback); }
  fire(type, target = this) { for (const callback of this.listeners.get(type) || []) callback({target}); }
  focus() { if (!this.disabled) this.ownerDocument.activeElement = this; }
  matches(selector) {
    const key = {'[data-checkout-list]': 'checkoutList', '[data-checkout-status]': 'checkoutStatus',
      '[data-checkout-refresh]': 'checkoutRefresh', '[data-checkout-delete]': 'checkoutDelete'}[selector];
    return key && Object.hasOwn(this.dataset, key);
  }
  closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector); }
  querySelector(selector) { for (const child of this.children) { if (child.matches(selector)) return child; const nested = child.querySelector(selector); if (nested) return nested; } return null; }
}

/** Own one section and deferred fetches; ignored aborts can be resolved deliberately. */
function fixture(localeContext) {
  const document = {createElement(tag) { return new Element(tag, this); }};
  document.body = new Element('body', document); document.activeElement = document.body;
  const root = document.createElement('section'); document.body.append(root);
  for (const [tag, key] of [['button', 'checkoutRefresh'], ['p', 'checkoutStatus'], ['tbody', 'checkoutList']]) {
    const node = document.createElement(tag); node.dataset[key] = ''; root.append(node);
  }
  const requests = [];
  const controller = new CheckoutSettings(root, {localeContext,
    fetcher: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject}))});
  return {document, root, requests, controller};
}

const A = 'a'.repeat(32), B = 'b'.repeat(32);
const session = (sessionId = A, extra = {}) => ({sessionId, label: 'Checkout', temporary: true,
  checkout: '/repo', path: '/isolated/maps', expiresAt: '2026-10-30T15:00:00Z', state: 'ready',
  project: {snapshotId: 'snap'}, ...extra});
const respond = (request, data, status = 200) => request.resolve({ok: status < 400, status, json: async () => data});
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

/** Resolve precisely one catalog read without accidentally granting a TTL keepalive. */
async function load(f, sessions = [session()]) {
  const promise = f.controller.start(); respond(f.requests.at(-1), {sessions}); await promise;
}

test('literal seven-column temporary rows, metadata, Intl date and GET-only passive reads', async () => {
  const f = fixture();
  await load(f, [session(A, {label: '<img onerror=evil>', path: '<script>path</script>'})]);
  const row = f.controller.rows.get(A);
  assert.equal(row.cells.length, 7);
  assert.equal(row.cells[0].textContent, '<img onerror=evil>');
  assert.equal(row.cells[1].textContent, 'Временная');
  assert.match(row.cells[2].textContent, /\/repo.*snap/);
  assert.equal(row.cells[3].textContent, '<script>path</script>');
  assert.notEqual(row.cells[4].textContent, '—');
  assert.equal(row.button.attributes['aria-label'], 'Удалить: <img onerror=evil> · Временная');
  await tick(); assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].url, '/api/checkout-sessions');
  assert.equal(f.requests[0].options.method, undefined);
  assert.equal(f.requests[0].options.cache, 'no-store');
  f.controller.dispose();
});

test('explicit EN fallback, unknown dates/states and cleanup failure stay visible', async () => {
  const f = fixture({locale: 'de', availableLocales: ['ru', 'en'], fallbackLocale: 'en'});
  await load(f, [session(A, {state: 'cleanup_failed', cleanupError: '<b>denied</b>', expiresAt: 'bad'}),
    session(B, {state: 'toString'})]);
  assert.equal(f.root.lang, 'en');
  assert.equal(f.controller.refreshButton.textContent, 'Refresh list');
  assert.equal(f.controller.rows.get(A).cells[4].textContent, '—');
  assert.match(f.controller.rows.get(A).cells[5].textContent, /Cleanup failed.*<b>denied<\/b>/);
  assert.equal(f.controller.rows.get(B).cells[5].textContent, 'Unknown: toString');
  assert.equal(f.controller.rows.get(A).button.disabled, false);
  f.controller.dispose();
});

test('active lifecycle retains actual queued/failed extraction status', async () => {
  const f = fixture();
  await load(f, [session(A, {state:'active',project:{status:'queued'}}),
    session(B, {state:'active',project:{status:'failed'}})]);
  assert.equal(f.controller.rows.get(A).cells[5].textContent, 'Построение');
  assert.equal(f.controller.rows.get(B).cells[5].textContent, 'Ошибка построения');
  assert.equal(f.controller.rows.get(A).button.disabled, false, 'a queued map may still be retired explicitly');
  f.controller.dispose();
});

test('keyed refresh retains a surviving focused button and removed focus returns to refresh', async () => {
  const f = fixture(); await load(f, [session(), session(B)]);
  const original = f.controller.rows.get(A).button;
  original.focus(); await load(f, [session(B), session(A, {label: 'Updated'})]);
  assert.equal(f.document.activeElement, original); assert.equal(f.controller.rows.get(A).button, original);
  await load(f, [session(B)]);
  assert.equal(f.document.activeElement, f.controller.refreshButton);
  f.controller.dispose();
});

test('failed or malformed reads preserve evidence and never assert a false empty catalog', async () => {
  const f = fixture(); const first = f.controller.start();
  respond(f.requests.at(-1), {error: 'Storage unavailable', code: 'CHECKOUT_STORAGE'}, 503); await first;
  assert.match(f.controller.status.textContent, /Storage unavailable/);
  assert.equal(f.controller.emptyRow, undefined);
  await load(f);
  const button = f.controller.rows.get(A).button;
  for (const data of [{}, {sessions: [session(), session()]}, {sessions: [session(A, {temporary: false})]}]) {
    const request = f.controller.refresh(); respond(f.requests.at(-1), data); await request;
    assert.match(f.controller.status.textContent, /Прежние строки сохранены/);
    assert.equal(f.controller.rows.get(A).button, button);
  }
  assert.equal(f.controller.emptyRow, undefined);
  f.controller.dispose();
});

test('newest catalog wins even when the superseded fetch ignores abort', async () => {
  const f = fixture(); const old = f.controller.start(); const oldRequest = f.requests[0];
  const current = f.controller.refresh(); respond(f.requests[1], {sessions: [session(B)]}); await current;
  respond(oldRequest, {sessions: [session()]}); await old;
  assert.equal(f.controller.rows.has(A), false); assert.equal(f.controller.rows.has(B), true);
  assert.equal(oldRequest.options.signal.aborted, true);
  f.controller.dispose();
});

test('non-string session IDs cannot replace accepted rows or change delete identity', async () => {
  const f = fixture(); await load(f);
  const button = f.controller.rows.get(A).button;
  for (const invalidId of [[A], {value: A}, 123, null]) {
    const request = f.controller.refresh();
    respond(f.requests.at(-1), {sessions: [session(invalidId)]}); await request;
    assert.match(f.controller.status.textContent, /Прежние строки сохранены/);
    assert.equal(f.controller.sessions.size, 1);
    assert.equal(f.controller.rows.get(A).button, button);
    assert.equal(f.controller.sessions.has(invalidId), false);
  }
  const requestCount = f.requests.length;
  f.root.fire('click', button);
  assert.equal(f.requests.length, requestCount + 1);
  assert.equal(f.requests.at(-1).url, '/api/checkout-sessions/' + A);
  assert.equal(f.requests.at(-1).options.method, 'DELETE');
  f.controller.dispose(); await tick();
});

test('one DELETE per SID, post-delete read, stale GET cannot resurrect a deleted row', async () => {
  const f = fixture(); await load(f);
  const old = f.controller.refresh(); const oldRequest = f.requests.at(-1);
  const deletion = f.controller.remove(A); const deleteRequest = f.requests.at(-1);
  assert.equal(deleteRequest.url, '/api/checkout-sessions/' + A);
  assert.equal(deleteRequest.options.method, 'DELETE');
  assert.equal(f.controller.rows.get(A).button.disabled, true);
  const count = f.requests.length; await f.controller.remove(A); assert.equal(f.requests.length, count);
  respond(deleteRequest, {sessionId: A, state: 'deleted'}); await tick();
  const freshRequest = f.requests.at(-1);
  assert.equal(freshRequest.url, '/api/checkout-sessions');
  // Even a lagging catalog started after the acknowledgment cannot revive that exact nonce.
  respond(freshRequest, {sessions: [session()]}); await deletion;
  respond(oldRequest, {sessions: [session()]}); await old;
  assert.equal(f.controller.rows.has(A), false);
  assert.match(f.controller.status.textContent, /Исходники checkout сохранены/);
  assert.equal(f.controller.emptyRow.children[0].colSpan, 7);
  f.controller.dispose();
});

test('deleting receipt is not success or retry authority; explicit refresh exposes cleanup failure', async () => {
  const f = fixture(); await load(f);
  const deletion = f.controller.remove(A); respond(f.requests.at(-1), {state: 'deleting'}); await tick();
  respond(f.requests.at(-1), {sessions: [session(A, {state: 'deleting'})]}); await deletion;
  assert.equal(f.controller.rows.get(A).button.disabled, true);
  assert.match(f.controller.rows.get(A).cells[5].textContent, /ожидается/);
  const count = f.requests.length; await f.controller.remove(A); await tick(); assert.equal(f.requests.length, count);
  await load(f, [session(A, {state: 'cleanup_failed', cleanupError: 'busy storage'})]);
  assert.equal(f.controller.rows.get(A).button.disabled, false);
  assert.match(f.controller.rows.get(A).cells[5].textContent, /busy storage/);
  f.controller.dispose();
});

test('uncertain mutation and failed reconciliation preserve rows with accurate outcomes', async () => {
  const f = fixture(); await load(f);
  const failed = f.controller.remove(A); f.requests.at(-1).reject(new TypeError('network lost')); await failed;
  assert.match(f.controller.status.textContent, /Результат удаления неизвестен/);
  assert.equal(f.controller.rows.has(A), true);
  const deleted = f.controller.remove(A); respond(f.requests.at(-1), {state: 'deleted'}); await tick();
  respond(f.requests.at(-1), {error: '503 after delete'}, 503); await deleted;
  assert.match(f.controller.status.textContent, /503 after delete/);
  assert.equal(f.controller.rows.get(A).button.disabled, true);
  const count = f.requests.length; await f.controller.remove(A); assert.equal(f.requests.length, count);
  f.controller.dispose();
});

test('dispose aborts GET/DELETE, fences late results, removes handlers and schedules no keepalive', async () => {
  const f = fixture(); await load(f, [session(), session(B)]);
  const deletion = f.controller.remove(A); const deleteRequest = f.requests.at(-1);
  const reading = f.controller.refresh(); const getRequest = f.requests.at(-1);
  const text = f.root.textContent;
  f.controller.dispose(); f.controller.dispose();
  assert.equal(deleteRequest.options.signal.aborted, true); assert.equal(getRequest.options.signal.aborted, true);
  respond(deleteRequest, {state: 'deleted'}); respond(getRequest, {sessions: []}); await Promise.all([deletion, reading]);
  assert.equal(f.root.textContent, text);
  const count = f.requests.length;
  f.controller.refreshButton.fire('click'); f.root.fire('click', f.controller.rows.get(B).button);
  await f.controller.start(); await f.controller.remove(B); await tick();
  assert.equal(f.requests.length, count); assert.equal(f.controller.transports.size, 0);
});

test('delete event delegates only an accepted current session ID within the root', async () => {
  const f = fixture(); await load(f);
  const foreign = f.document.createElement('button'); foreign.dataset.checkoutDelete = A;
  f.document.body.append(foreign); f.root.fire('click', foreign);
  assert.equal(f.requests.length, 1);
  f.root.fire('click', f.controller.rows.get(A).button);
  assert.equal(f.requests.at(-1).options.method, 'DELETE');
  f.controller.dispose(); await tick();
});
