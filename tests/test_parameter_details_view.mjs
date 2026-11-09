/** Условия и аргументы остаются безопасными фактами, popup сохраняет контекст карты. */
import assert from 'node:assert/strict';
import { renderParameterDetails } from '../web/parameter-details.js';
import { NodeFactsPopup } from '../web/node-facts-popup.js';

/** DOM-фасад поддерживает несколько listeners и обнаруживает HTML, прокрутку и изменение фокуса. */
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.isConnected = true;
    this.hidden = false;
    this.focusCalls = [];
    this.scrollCalls = [];
  }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
  append(...nodes) { this.children.push(...nodes); }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this[name] = value; }
  focus(options) { this.focusCalls.push(options); document.activeElement = this; }
  scrollIntoView(options) { this.scrollCalls.push(options); }
  emit(name, event = {}) {
    for (const callback of this.listeners.get(name) || []) callback(event);
  }
  set innerHTML(value) { throw new Error(`Unsafe HTML: ${value}`); }
}

globalThis.document = { createElement: tag => new Element(tag), activeElement: null };

/** Собирает подписи для проверки экранирования без внутренней структуры представления. */
function textOf(node) {
  let text = node.textContent || '';
  for (const child of node.children) text += ` ${textOf(child)}`;
  return text;
}

/** Находит элементы действия по их публичной подписи. */
function findText(node, text) {
  if (node.textContent === text) return node;
  for (const child of node.children) {
    const found = findText(child, text);
    if (found) return found;
  }
  return null;
}

const sourceCalls = [];
const data = {
  nodeId: 'method-raw', snapshotId: 's', label: 'Calculate', file: 'Calculator.cs',
  signature: 'decimal Calculate(decimal amount)', returnType: 'decimal',
  parameterFacts: {
    parameters: [{ name: 'amount', type: 'decimal', modifier: 'in', defaultValue: '0' }, { name: 'unused', type: 'string' }],
    conditions: [{ kind: 'if', line: 13, expression: 'amount < 0 && name == "<script>"', parameterReferences: ['amount'] }],
    warning: 'Реальные значения и выбранные ветки не вычисляются.', truncated: false,
  },
  documentation: { params: [{ name: 'amount', text: '<img src=x onerror=alert(1)>' }] },
  events: [{ kind: 'call', text: 'Apply(amount)', line: 17, confidence: 'candidate', arguments: [{ position: 1, modifier: 'in', expression: '<script>amount</script>', parameterReferences: ['amount'] }] }],
  incomingCallSites: [{ callerNodeId: 'caller-raw', callerLabel: 'Controller.Calculate', line: 42, confidence: 'candidate', argumentsTruncated: true, arguments: [{ name: 'amount', expression: 'request.Amount', parameterReferences: ['request'] }] }],
  truncation: { incomingCallSites: true },
};
const original = JSON.stringify(data);
const details = renderParameterDetails(data, (id, line) => sourceCalls.push({ id, line }));
const text = textOf(details);
assert.ok(text.includes('in decimal = 0'));
assert.ok(text.includes(data.parameterFacts.conditions[0].expression));
assert.ok(text.includes(data.documentation.params[0].text));
assert.ok(text.includes(data.events[0].arguments[0].expression));
assert.ok(text.includes('request.Amount'));
assert.ok(text.includes('возможная цель'));
assert.ok(text.includes('Прямые упоминания в условиях не найдены'));
assert.ok(text.includes('Места входящих вызовов показаны частично'));
assert.ok(text.includes('Аргументы этого вызова показаны частично'));
const incomingList = details.children.find(child => child.className === 'parameter-incoming');
assert.equal(incomingList.tagName, 'DETAILS');
assert.ok(!incomingList.open, 'incoming callers start collapsed so their headers do not conceal conditions');
const lastParameter = details.children.findLastIndex(child => child.className === 'parameter-row');
assert.ok(details.children.indexOf(incomingList) > lastParameter, 'formal parameters and conditions precede incoming callers');
findText(details, 'if · строка 13').emit('click');
findText(details, 'Место вызова в исходнике').emit('click');
findText(details, 'Исходник вызывающего метода').emit('click');
assert.deepEqual(sourceCalls, [{ id: 'method-raw', line: 13 }, { id: 'method-raw', line: 17 }, { id: 'caller-raw', line: 42 }]);
assert.equal(JSON.stringify(data), original);
assert.ok(textOf(renderParameterDetails({ parameters: [{ name: 'legacy', type: 'int' }] })).includes('legacy'));
const limitedIncoming = renderParameterDetails({ incomingCallSites: [], incomingCallSitesTruncated: true });
assert.ok(textOf(limitedIncoming).includes('0 мест · частично'), 'an empty bounded incoming result must not imply completeness');

const fields = new Map(['close', 'retry', 'title', 'status', 'body'].map(name => [name, new Element('div')]));
const root = new Element('section');
root.hidden = true;
root.append(...fields.values());
root.querySelector = selector => fields.get(selector.match(/"(.*?)"/)[1]);
const requests = [];
const popup = new NodeFactsPopup(root, {
  onSource: (id, line) => sourceCalls.push({ id, line }),
  fetcher: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })),
});
const card = new Element('svg');
card.focus();
const mapState = { selected: 'different-node', architecture: { level: 'projects' }, camera: { x: 10, y: 20, scale: 0.9 } };
const beforeState = JSON.stringify(mapState);
const contextA = { projectId: 'project', snapshotId: 's', nodeId: 'A', label: 'A' };
const first = popup.open(contextA);
assert.equal(document.activeElement, fields.get('close'));
assert.deepEqual(fields.get('close').focusCalls.at(-1), { preventScroll: true });
assert.equal(root.hidden, false);
assert.deepEqual(JSON.parse(requests[0].options.body), { nodeId: 'A', snapshotId: 's' });
assert.equal(contextA.nodeId, 'A');

// Повторное открытие из уже активного popup сохраняет исходную карточку для возврата фокуса.
const second = popup.open({ ...contextA, nodeId: 'method-raw' });
assert.ok(requests[0].options.signal.aborted);
requests[1].resolve({ ok: true, json: async () => data });
await second;
requests[0].resolve({ ok: true, json: async () => ({ ...data, nodeId: 'A', label: 'OLD_RESPONSE' }) });
await first;
assert.ok(textOf(fields.get('body')).includes('Calculate'));
assert.ok(!textOf(fields.get('body')).includes('OLD_RESPONSE'));
assert.equal(root.scrollCalls.length, 0, 'fixed popup must never scroll the graph page on render');
assert.equal(JSON.stringify(mapState), beforeState);
findText(fields.get('body'), 'Исходник вызывающего метода').emit('click');
assert.deepEqual(sourceCalls.at(-1), { id: 'caller-raw', line: 42 });

root.emit('keydown', { key: 'Escape', preventDefault() {} });
assert.equal(root.hidden, true);
assert.equal(document.activeElement, card);
assert.deepEqual(card.focusCalls.at(-1), { preventScroll: true });

// Смена снимка скрывает окно и аннулирует незавершённый запрос.
card.focus();
const snapshotRequest = popup.open({ ...contextA, nodeId: 'method-raw' });
popup.sync('project', 'new-snapshot');
assert.ok(requests[2].options.signal.aborted);
assert.equal(root.hidden, true);
requests[2].resolve({ ok: true, json: async () => data });
await snapshotRequest;
assert.equal(fields.get('body').children.length, 0);

// Строка старого снимка не принимается как описание текущего контекста.
card.focus();
const mismatch = popup.open({ ...contextA, nodeId: 'method-raw' });
requests[3].resolve({ ok: true, json: async () => ({ ...data, snapshotId: 'old' }) });
await mismatch;
assert.equal(fields.get('retry').hidden, false);
assert.equal(fields.get('body').children.length, 0);
fields.get('close').emit('click');
assert.equal(root.hidden, true);
assert.equal(document.activeElement, card);

card.focus();
const disconnected = popup.open({ ...contextA, nodeId: 'method-raw' });
card.isConnected = false;
const focusCount = card.focusCalls.length;
popup.close();
assert.equal(card.focusCalls.length, focusCount, 'removed map card must not receive focus');
requests[4].resolve({ ok: true, json: async () => data });
await disconnected;
assert.equal(fields.get('body').children.length, 0);

// Кнопка hover-подсказки может исчезнуть: окно возвращает фокус явно переданной исходной карточке.
const explicitOpener = new Element('svg');
const transientButton = new Element('button');
transientButton.focus();
const explicitRequest = popup.open({ ...contextA, nodeId: 'method-raw' }, explicitOpener);
assert.equal(document.activeElement, fields.get('close'));
requests.at(-1).resolve({ ok: true, json: async () => data });
await explicitRequest;
root.emit('keydown', { key: 'Escape', preventDefault() {} });
assert.equal(root.hidden, true);
assert.equal(document.activeElement, explicitOpener);
assert.deepEqual(explicitOpener.focusCalls.at(-1), { preventScroll: true });
assert.equal(transientButton.focusCalls.length, 1, 'hidden hover action is never chosen as the return target');
console.log('Parameter details expressions, safe text, original source IDs and popup isolation PASS');
