/** Контракты bounded reducer, точной привязки и единственной WS-подписки. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { AgentActivityModel, activityIdentity, activityLabel, activityTarget, activityOperations } from '../web/agent-activity-model.js';
import { AgentActivityStream } from '../web/agent-activity-stream.js';
import { AgentActivity } from '../web/agent-activity.js';

const fixture = JSON.parse(await readFile(new URL('../contracts/agent-activity/v1/examples.json', import.meta.url), 'utf8'));
const event = fixture.events[0];
const pairs = activityOperations([{ ...event, phase: 'started', eventId: 'start' }, { ...event, operationId: 'parallel', phase: 'started', eventId: 'parallel' }, event]);
assert.equal(pairs.length, 2, 'Started/finished share one operation card while parallel calls remain separate');
assert.equal(pairs[0].started.phase, 'started');
assert.equal(pairs[0].event.phase, 'finished');
assert.equal(pairs[1].finished, null);
const model = new AgentActivityModel(3);
model.apply([event], { journalEpoch: event.journalEpoch, sequence: event.sequence });
model.apply([event], model.cursor);
assert.equal(model.events.length, 1, 'Replay deduplicates');
for (let sequence = 2; sequence <= 8; sequence++) model.apply([{ ...event, eventId: `event-${sequence}`, sequence: String(sequence) }]);
assert.deepEqual(model.events.map(item => item.sequence), ['6', '7', '8']);
assert.equal(model.ids.size, 3, 'Deduplication storage is bounded with rows');
model.apply([event]);
assert.equal(model.events.length, 3, 'Old replay remains ignored after dedupe eviction');
assert.equal(model.events.at(-1).sequence, '8');
model.apply([{ ...event, sequence: '9007199254740993', eventId: 'large' }]);
model.apply([{ ...event, sequence: '9007199254740992', eventId: 'older' }]);
assert.equal(model.events.at(-1).eventId, 'large', 'Sequence uses exact decimal integers');
const context = { projectId: event.target.projectId, snapshotId: event.target.snapshotId };
assert.equal(activityTarget(event, context, new Set([event.target.nodeId])).status, 'visible');
assert.equal(activityTarget(event, context, new Set()).status, 'outside');
assert.equal(activityTarget(event, { ...context, snapshotId: 'new' }, new Set([event.target.nodeId])).status, 'stale');
assert.equal(activityTarget(event, { ...context, projectId: 'other' }, new Set([event.target.nodeId])).status, 'other-project');
assert.equal(activityTarget({ ...event, target: { ...event.target, status: 'unavailable' } }, context, new Set([event.target.nodeId])).status, 'unavailable');
assert.equal(activityIdentity({ ...event, agentId: 'untrusted' }), activityIdentity(event), 'Process identity never invents agent identity');
assert.match(activityLabel({ ...event, query: { mode: 'preview', preview: '<script>search</script>', truncated: true } }), /<script>search<\/script>…/);

class Socket {
  constructor(url) { this.url = url; this.sent = []; this.closed = false; }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.closed = true; this.onclose?.(); }
}
const sockets = [], statuses = [], packets = [];
const streamModel = new AgentActivityModel();
const stream = new AgentActivityStream({ model: streamModel, baseUrl: 'http://127.0.0.1:8765/',
  socketFactory: url => { const socket = new Socket(url); sockets.push(socket); return socket; },
  onStatus: text => statuses.push(text),
  onChange: packet => { if (packet.events) assert.equal(streamModel.events.length, 1, 'Reducer is applied before notification/ACK'); packets.push(packet); },
});
stream.configure(false, 'project');
assert.equal(sockets.length, 0, 'Defaults/off produces no network');
stream.configure(true, 'project');
stream.configure(true, 'project');
assert.equal(sockets.length, 1, 'Two consumers share one socket');
sockets[0].onopen();
assert.deepEqual(sockets[0].sent[0], { type: 'subscribe' });
const packet = { type: 'snapshot', messageId: 'message-1', journalEpoch: event.journalEpoch, cursor: { journalEpoch: event.journalEpoch, sequence: '1' }, events: [event] };
sockets[0].onmessage({ data: JSON.stringify(packet) });
assert.deepEqual(sockets[0].sent[1], { type: 'ack', messageId: packet.messageId, cursor: packet.cursor });
sockets[0].onmessage({ data: JSON.stringify({ ...packet, type: 'events', messageId: 'loss', events: [], health: { status: 'ok', dropped: { full_queue: 2 } } }) });
assert.match(statuses.at(-1), /неполной/, 'Queue drops remain visible even when disk status is healthy');
stream.configure(false, 'project');
assert.equal(sockets[0].closed, true, 'Last consumer closes socket');
stream.configure(true, 'project');
sockets[1].onopen();
assert.deepEqual(sockets[1].sent[0].cursor, packet.cursor, 'Resume cursor survives minimized/hidden panel');
sockets[0].onmessage({ data: JSON.stringify({ ...packet, messageId: 'stale' }) });
assert.equal(sockets[0].sent.length, 3, 'Old socket callbacks cannot mutate/ACK');
const statusCount = statuses.length;
sockets[0].onerror();
assert.equal(statuses.length, statusCount, 'Old socket errors cannot overwrite current status');
sockets[1].onmessage({ data: JSON.stringify({ type: 'resetRequired', journalEpoch: 'next', reason: 'expired' }) });
assert.equal(streamModel.cursor, null);
stream.configure(false, 'project');
stream.configure(true, 'other-project');
assert.equal(streamModel.events.length, 0, 'Switching projects clears prior rows');
const invalid = sockets.at(-1);
invalid.onmessage({ data: JSON.stringify({ ...packet, journalEpoch: 'wrong' }) });
assert.equal(invalid.closed, true, 'Invalid epoch closes bounded stream without ACK');
stream.stop();
assert.equal(fixture.uiDefaults.chatEnabled, false);
assert.equal(fixture.uiDefaults.markersEnabled, false);

// UI wiring is exercised without a browser; actual layout is verified separately in CUA.
class Element {
  constructor() { this.children = []; this.parts = new Map(); this.dataset = {}; this.classList = { toggle() {} }; this.style = { setProperty() {}, removeProperty() {} }; }
  setAttribute(key, value) { this[key] = value; }
  addEventListener() {}
  get firstElementChild() { return this.children[0] || null; }
  replaceChildren(...nodes) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    this.append(...nodes);
  }
  append(...nodes) { for (const node of nodes) this.insertBefore(node, null); }
  insertBefore(node, current) {
    node.remove();
    const index = current ? this.children.indexOf(current) : this.children.length;
    this.children.splice(index, 0, node);
    node.parentNode = this;
  }
  remove() {
    if (!this.parentNode) return;
    const siblings = this.parentNode.children;
    siblings.splice(siblings.indexOf(this), 1);
    this.parentNode = null;
  }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  focus() { this.focused = true; }
  querySelector(selector) {
    if (!this.parts.has(selector)) this.parts.set(selector, new Element());
    return this.parts.get(selector);
  }
}
globalThis.document = { createElement: () => new Element(), body: new Element() };
globalThis.window = { addEventListener() {} };
globalThis.location = { href: 'http://127.0.0.1:8777/' };
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.requestAnimationFrame = () => 1;
const uiSockets = [];
globalThis.WebSocket = class extends Socket { constructor(url) { super(url); uiSockets.push(this); } };
const ui = new AgentActivity({ view: {}, getContext: () => ({ projectId: 'project', snapshotId: 'snapshot' }), onNavigate() {} });
assert.equal(ui.mode, 'hidden');
assert.equal(ui.markersEnabled, false);
assert.equal(ui.panel.hidden, true);
assert.equal(ui.part('toggle').hidden, false);
assert.equal(ui.part('filter-options').hidden, true, 'Verbose filters start collapsed');
assert.equal(uiSockets.length, 0, 'Actual UI defaults create no stream');
ui.action('toggle');
assert.equal(ui.mode, 'compact');
assert.equal(ui.part('toggle').hidden, true, 'Open popup hides redundant chat launcher');
assert.equal(ui.part('markers').hidden, undefined, 'Independent blue marker control remains accessible');
assert.equal(uiSockets.length, 1);
assert.equal(ui.part('scope').textContent, 'Проект: project');
assert.match(ui.emptyMessage(false), /Ожидаем/);
ui.streamState = 'ready';
assert.match(ui.emptyMessage(true), /по выбранным фильтрам/);
assert.equal(ui.panel.querySelector('strong').focused, true);
ui.action('markers');
assert.equal(ui.markersEnabled, true);
assert.equal(uiSockets.length, 1, 'Chat and marker toggle share transport');
ui.action('expand'); assert.equal(ui.mode, 'expanded');
ui.action('minimize'); assert.equal(ui.panel.hidden, true);
assert.equal(uiSockets[0].closed, false, 'Minimized chat preserves active marker consumer');
ui.action('markers'); assert.equal(uiSockets[0].closed, true);
ui.action('toggle'); assert.equal(ui.mode, 'expanded', 'Reopen restores previous size');
ui.action('close'); assert.equal(uiSockets.at(-1).closed, true);
assert.equal(ui.part('toggle').hidden, false, 'Closed popup restores chat launcher');
assert.equal(ui.part('toggle').focused, true, 'Closing returns keyboard focus to launcher');
ui.view.nodeElements = new Map();
const summaryRow = ui.eventRow({ ...event, request: { tool: 'search_nodes', summary: 'Поиск First' }, response: { summary: 'Найден 1 узел', truncated: true, items: [{label:'First',file:'service.py'}, {label:'<unsafe>'}, {label:'Third'}, {label:'Fourth'}] } });
assert.equal(summaryRow.children[1].textContent, 'Запрос: Поиск First');
assert.equal(summaryRow.children[2].textContent, 'Ответ: Найден 1 узел · показана часть ответа');
assert.equal(summaryRow.children[3].textContent, 'First · service.py');
assert.equal(summaryRow.children[4].textContent, '<unsafe>', 'Result label is safe text, not markup');
assert.equal(summaryRow.children.filter(child => child.className === 'agent-activity-response-item').length, 3, 'Response details are independently bounded');
const pendingRow = ui.eventRow({ ...event, phase: 'started', response: undefined });
assert.equal(pendingRow.children[2].textContent, 'Ответ: пока не наблюдался');
const failedRow = ui.eventRow({ ...event, outcome: 'failed', response: { summary: 'Объект недоступен', items: [] } });
assert.equal(failedRow.children[2].textContent, 'Ответ · ошибка: Объект недоступен');
assert.equal(failedRow.children[3].textContent, 'Фрагменты ответа не сохранены');
// Existing rows and options survive no-op packets; target semantics follow the visible graph.
const linked = { ...event, target: { ...event.target, projectId: 'project', snapshotId: 'snapshot' } };
ui.view.nodeElements.set(linked.target.nodeId, {});
assert.equal(ui.renderRows([linked]), 1);
const originalRow = ui.list.firstElementChild;
assert.equal(ui.renderRows([linked]), 1);
assert.equal(ui.list.firstElementChild, originalRow);
ui.view.nodeElements.clear();
ui.renderRows([linked]);
const hiddenTargetRow = ui.list.firstElementChild;
assert.notEqual(hiddenTargetRow, originalRow);
assert.equal(hiddenTargetRow.children.at(-1).title, 'Узел вне текущего вида');
ui.renderRows([]);
ui.renderRows([]);
assert.equal(ui.list.children.length, 1, 'Empty state stays singular across redraws');
ui.renderRows([linked]);
assert.equal(ui.list.children.length, 1, 'Populated state removes empty placeholder');
ui.part('session').value = linked.sessionId;
ui.sessionOptions([linked]);
const originalOption = ui.part('session').firstElementChild;
ui.sessionOptions([linked]);
assert.equal(ui.part('session').firstElementChild, originalOption);
assert.equal(ui.part('session').value, linked.sessionId);
ui.action('filters'); assert.equal(ui.part('filter-options').hidden, false);
ui.action('filters'); assert.equal(ui.part('filter-options').hidden, true);
const chrome = ui.root.innerHTML;
assert.ok(chrome.indexOf('data-activity="close"') < chrome.indexOf('data-activity="minimize"') && chrome.indexOf('data-activity="minimize"') < chrome.indexOf('data-activity="expand"'), 'Panel controls retain close/minimize/expand order');
assert.match(chrome, /aria-label="Показывать работу агентов на карте"/);
const css = await readFile(new URL('../web/agent-activity.css', import.meta.url), 'utf8');
assert.match(css, /width:320px;height:320px/);
assert.doesNotMatch(css, /#ff6058|#ffbd2e|#28c840/);
assert.match(css, /background:#0a84ff/);
console.log('Agent activity reducer/target/stream contracts passed');

// The transport can complete after abort. Only the current command owns history.
const deferredHistory = [];
globalThis.fetch = (url, options) => new Promise(resolve => deferredHistory.push({url, options, resolve}));
ui.action('toggle');
const pendingHistory = ui.loadHistory();
assert.equal(ui.list['aria-busy'], 'true');
assert.match(ui.part('history-status').textContent, /Загружаем/);
await ui.loadHistory();
assert.equal(deferredHistory.length, 1, 'double activation starts one request');
ui.action('live');
assert.equal(deferredHistory[0].options.signal.aborted, true);
deferredHistory[0].resolve({ok:true,json:async()=>({events:[event],hasMore:true})});
await pendingHistory;
assert.equal(ui.historyPage, null, 'late history cannot switch the user back from live');
assert.equal(ui.list['aria-busy'], 'false');
const retryHistory = ui.loadHistory();
deferredHistory[1].resolve({ok:false});
await retryHistory;
ui.stream.onStatus('Журнал Atlas подключён', 'ready');
assert.match(ui.part('history-status').textContent, /История недоступна/);
assert.match(ui.status.textContent, /подключён/);
const validHistory = ui.loadHistory();
deferredHistory[2].resolve({ok:true,json:async()=>({events:[event],hasMore:true})});
await validHistory;
assert.equal(ui.historyPage.events.length,1);
ui.part('query').value='different search';
await ui.loadHistory(event.sequence);
assert.equal(deferredHistory.length,3,'old cursor is fenced by applied filters');
ui.action('close');
console.log('Agent history single flight, late completion, independent status and cursor scope PASS');
