/** Контракты после обновления: монотонный масштаб, ограниченные запросы и история поиска. */
import assert from 'node:assert/strict';
import { Svg } from './svg_fixture.mjs';
import { SourceViewer } from '../web/source-view.js';
import { bindGraphExport } from '../web/graph-export.js';

/** Минимальные CSS-свойства нужны экспортной копии, а не геометрии исходной карты. */
function style() {
  return { setProperty(name, value) { this[name] = value; }, removeProperty(name) { delete this[name]; } };
}

/** DOM фиксирует события, размеры и копирование SVG без нового browser/test framework. */
class Element extends Svg {
  constructor(tag, ownerDocument) {
    super(tag); this.ownerDocument = ownerDocument; this.tagName = tag.toUpperCase(); this.style = style();
    this.value = ''; this.hidden = false; this.inert = false; this.disabled = false;
    this.scrollTop = 0; this.scrollLeft = 0; this.offsetTop = 0; this.clientWidth = 600; this.clientHeight = 400;
  }
  get parentElement() { return this.parent; }
  get childNodes() { return this.children; }
  get id() { return this.attributes.id || ''; }
  get className() { return this.attributes.class || ''; }
  set className(value) { this.attributes.class = value; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  focus() { this.ownerDocument.activeElement = this; }
  closest() { return this.hidden || this.inert ? this : null; }
  contains(target) { return this === target || this.children.some(child => child.contains?.(target)); }
  getClientRects() { return [{}]; }
  getBBox() { return { x: 0, y: 0, width: 100, height: 60 }; }
  set innerHTML(value) { throw new Error('Project text must not become HTML'); }
  click() { return this.listeners.click?.(); }
  append(...nodes) {
    for (const value of nodes) {
      if (typeof value !== 'string') { super.append(value); continue; }
      const text = new Element('text', this.ownerDocument); text.textContent = value; super.append(text);
    }
  }
  querySelectorAll(selector) {
    if (selector !== '*') return super.querySelectorAll(selector);
    const descendants = [];
    for (const child of this.children) descendants.push(child, ...child.querySelectorAll('*'));
    return descendants;
  }
  insertBefore(child, next) {
    if (child.parent) child.remove();
    const index = next ? this.children.indexOf(next) : this.children.length;
    this.children.splice(index, 0, child); child.parent = this;
  }
  cloneNode(deep) {
    const copy = new Element(this.tag, this.ownerDocument);
    copy.attributes = { ...this.attributes }; copy.textContent = this._textContent || '';
    if (deep) for (const child of this.children) copy.append(child.cloneNode(true));
    return copy;
  }
  getBoundingClientRect() {
    const width = this.style.width === '100%' ? this.parent?.clientWidth || this.clientWidth
      : parseFloat(this.style.width) || this.clientWidth;
    return { width, height: parseFloat(this.style.height) || this.clientHeight, top: 0, bottom: this.clientHeight };
  }
}

/** Документ хранит только локальные controls; сетевой транспорт задаётся каждым случаем отдельно. */
function documentFixture(ids) {
  const document = { activeElement: null, fonts: { ready: Promise.resolve() } };
  document.createElement = tag => new Element(tag, document);
  document.createElementNS = (_, tag) => new Element(tag, document);
  document.body = document.createElement('body');
  const fields = new Map();
  for (const id of ids) { const field = document.createElement('div'); field.setAttribute('id', id); fields.set(id, field); }
  document.getElementById = id => fields.get(id);
  document.defaultView = { addEventListener() {}, getComputedStyle: () => ({ getPropertyValue: () => '' }),
    XMLSerializer: class { serializeToString() { return '<svg xmlns="http://www.w3.org/2000/svg"/>'; } } };
  return { document, fields };
}

/** Возвращает HTTP JSON с тем же интерфейсом, что локальные read endpoints. */
function response(data) { return { ok: true, json: async () => data }; }

const saved = { id: 'post-update', title: 'Captured view', snapshotId: 'snapshot', createdAt: 'now',
  nodes: [], edges: [], annotations: [], metadata: {}, layout: { width: 1000, height: 500, nodes: {}, annotations: {} } };
const pageIds = ['saved-graph','saved-details','saved-title','saved-query','saved-provenance','saved-status',
  'saved-png','saved-zoom-in','saved-zoom-out','saved-fit','saved-retry'];
let importNumber = 0;

/** Загружает настоящий page module заново, поскольку у страницы один локальный controller. */
async function savedPage(fetcher) {
  const fixture = documentFixture(pageIds);
  const canvas = fixture.document.createElement('div'); canvas.className = 'saved-view-canvas';
  canvas.append(fixture.fields.get('saved-graph')); fixture.document.body.append(canvas);
  fixture.document.querySelector = selector => selector === '.saved-view-canvas' ? canvas : null;
  fixture.fields.get('saved-status').textContent = 'Загружаем снимок…';
  globalThis.document = fixture.document; globalThis.window = fixture.document.defaultView;
  globalThis.location = { search: '?id=post-update' }; globalThis.fetch = fetcher;
  const moduleUrl = new URL('../web/view-page.js', import.meta.url); moduleUrl.searchParams.set('regression', ++importNumber);
  await import(moduleUrl.href);
  await new Promise(resolve => setImmediate(resolve));
  return { ...fixture, canvas };
}

const viewBefore = JSON.stringify(saved);
const page = await savedPage(async () => response(saved));
const graph = page.fields.get('saved-graph');
const fittedWidth = graph.getBoundingClientRect().width;
assert.ok(fittedWidth > 0 && fittedWidth <= page.canvas.clientWidth);
page.fields.get('saved-zoom-out').click();
const smallerWidth = graph.getBoundingClientRect().width;
assert.ok(smallerWidth < fittedWidth, `Minus must reduce fitted width (${fittedWidth} → ${smallerWidth})`);
page.fields.get('saved-fit').click();
assert.ok(Math.abs(graph.getBoundingClientRect().width - fittedWidth) < 1);
page.fields.get('saved-zoom-in').click();
assert.ok(graph.getBoundingClientRect().width > fittedWidth, 'Plus must increase fitted width');
page.fields.get('saved-fit').click();
assert.ok(Math.abs(graph.getBoundingClientRect().width - fittedWidth) < 1);
assert.equal(JSON.stringify(saved), viewBefore, 'zoom must not rewrite immutable saved geometry');

// Сокращаем именно production deadline; transport и JSON намеренно игнорируют AbortSignal.
const originalTimeout = globalThis.setTimeout;
globalThis.setTimeout = (callback, delay, ...args) => originalTimeout(callback, delay === 30000 ? 8 : delay, ...args);
try {
  for (const kind of ['transport', 'json']) {
    let finish, signal, calls = 0;
    const timed = await savedPage((url, options) => {
      assert.equal(url, '/api/views/post-update'); calls++; signal = options?.signal;
      if (calls > 1) return Promise.resolve(response(saved));
      return kind === 'transport' ? new Promise(resolve => { finish = resolve; })
        : Promise.resolve({ ok: true, json: () => new Promise(resolve => { finish = resolve; }) });
    });
    await new Promise(resolve => originalTimeout(resolve, 25));
    assert.match(timed.fields.get('saved-status').textContent, /Сервер долго отвечает/);
    assert.equal(signal?.aborted, true);
    assert.equal(timed.fields.get('saved-retry').hidden, false);
    await timed.fields.get('saved-retry').click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls, 2);
    assert.equal(timed.fields.get('saved-title').textContent, saved.title);
    const late = { ...saved, title: 'Late timed-out view' };
    finish(kind === 'transport' ? response(late) : late);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(timed.fields.get('saved-title').textContent, saved.title);
  }

  for (const kind of ['transport', 'json']) {
    const fixture = documentFixture(['button','status']);
    globalThis.document = fixture.document;
    const svg = fixture.document.createElement('svg'), viewport = fixture.document.createElement('g');
    viewport.setAttribute('id', 'viewport'); viewport.setAttribute('transform', 'translate(12,24)');
    viewport.append(fixture.document.createElement('rect')); svg.append(viewport);
    let finish, signal, calls = 0;
    fixture.document.defaultView.fetch = (url, options) => {
      assert.equal(url, '/api/map-export'); calls++; signal = options.signal;
      if (calls > 1) return Promise.resolve(response({ url: '/api/map-export/ready.svg' }));
      return kind === 'transport' ? new Promise(resolve => { finish = resolve; })
        : Promise.resolve({ ok: true, json: () => new Promise(resolve => { finish = resolve; }) });
    };
    const button = fixture.fields.get('button'), status = fixture.fields.get('status');
    bindGraphExport({ button, status, svg, name: () => 'Project', format: () => 'svg' });
    await button.click();
    assert.equal(button.disabled, false, 'a timed-out export must allow retry');
    assert.match(status.textContent, /Сервер долго отвечает/);
    assert.equal(signal?.aborted, true);
    await button.click();
    assert.equal(calls, 2); assert.equal(button.disabled, false);
    assert.match(status.textContent, /SVG готов/);
    const successfulStatus = status.textContent;
    const late = { url: '/api/map-export/late.svg' };
    finish(kind === 'transport' ? response(late) : late);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(status.textContent, successfulStatus, 'late export completion must not replace the current result');
    assert.equal(viewport.getAttribute('transform'), 'translate(12,24)');
    status.children.at(-1).click();
    if (kind === 'json') {
      // FastAPI HTTPException использует detail: пользователь должен увидеть способ исправить 413.
      const tooLarge = 'SVG слишком большой. Экспортируйте меньше раскрытых элементов.';
      fixture.document.defaultView.fetch = async () => ({ ok: false, status: 413, json: async () => ({ detail: tooLarge }) });
      await button.click();
      assert.equal(button.disabled, false);
      assert.ok(status.textContent.includes(tooLarge), 'deadline reuse must preserve actionable HTTP error detail');
    }
  }
} finally { globalThis.setTimeout = originalTimeout; }

// Back восстанавливает порядковый номер поиска до Next, чтобы повторный переход не пропускал совпадение.
const source = documentFixture([]), sourceRoot = source.document.createElement('section');
const sourceFields = new Map();
for (const name of ['title','status','close','retry','context','full','previous','next','range','code','find','reader-status']) {
  const field = source.document.createElement('div'); sourceFields.set(name, field); sourceRoot.append(field);
}
sourceRoot.querySelector = selector => sourceFields.get(selector.match(/"(.*?)"/)?.[1]);
source.document.body.append(sourceRoot); globalThis.document = source.document;
const context = { projectId: 'p', snapshotId: 's', nodeId: 'n', line: 2 };
const viewer = new SourceViewer(sourceRoot, () => context,
  async () => response({ text: 'needle\nstart\nneedle', file: 'n.cs', line: 2, totalLines: 3, snapshotId: 's' }));
await viewer.open();
sourceFields.get('find').value = 'needle'; sourceFields.get('find').listeners.input();
viewer.findNext(); assert.equal(viewer.data.line, 1); assert.equal(viewer.matchIndex, 0);
await viewer.travel(-1); assert.equal(viewer.data.line, 2); assert.equal(viewer.matchIndex, -1);
viewer.findNext(); assert.equal(viewer.data.line, 1, 'Back → Next must revisit the same first match');
viewer.findNext(); assert.equal(viewer.data.line, 3); assert.equal(viewer.matchIndex, 1);
await viewer.travel(-1); assert.equal(viewer.data.line, 1); assert.equal(viewer.matchIndex, 0);
viewer.findNext(); assert.equal(viewer.data.line, 3);
viewer.close();

// Повтор после transport/JSON timeout запрашивает тот же raw-ID, не создавая нового шага истории.
globalThis.setTimeout = (callback, delay, ...args) => originalTimeout(callback, delay === 30000 ? 8 : delay, ...args);
try {
  for (const kind of ['transport', 'json']) {
    let calls = 0, finish;
    viewer.fetcher = (url, options) => {
      calls++;
      assert.deepEqual(JSON.parse(options.body), { nodeId: 'n', snapshotId: 's' });
      if (calls > 1) return Promise.resolve(response({ text: 'fresh\nstart', file: 'fresh.cs', line: 2, totalLines: 2, snapshotId: 's' }));
      return kind === 'transport' ? new Promise(resolve => { finish = resolve; })
        : Promise.resolve({ ok: true, json: () => new Promise(resolve => { finish = resolve; }) });
    };
    await viewer.open();
    assert.match(sourceFields.get('status').textContent, /Сервер долго отвечает/);
    assert.equal(sourceFields.get('retry').hidden, false);
    const failedHistory = JSON.stringify(viewer.history.items);
    sourceFields.get('retry').click();
    assert.equal(sourceFields.get('retry').hidden, true, 'retry hides while reading the same source');
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls, 2); assert.equal(viewer.data.file, 'fresh.cs'); assert.equal(viewer.data.line, 2);
    assert.equal(sourceFields.get('retry').hidden, true);
    assert.equal(JSON.stringify(viewer.history.items), failedHistory, 'retry must not push or replace a reader history location');
    const late = { text: 'late', file: 'late.cs', line: 1, snapshotId: 's' };
    finish(kind === 'transport' ? response(late) : late);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(viewer.data.file, 'fresh.cs');
    viewer.close();
  }
} finally { globalThis.setTimeout = originalTimeout; }

// В stacked layout строка видна внутри code, но обрезана внешним source-workspace.
const workspace = source.document.createElement('div'); workspace.className = 'source-workspace';
const sourceCode = sourceFields.get('code'); sourceRoot.append(workspace); workspace.append(sourceCode);
source.document.documentElement = source.document.createElement('html');
source.document.documentElement.scrollTop = 555; source.document.body.scrollTop = 72;
source.document.defaultView.scrollTo = () => assert.fail('source centering must not scroll the page');
await viewer.open();
const selectedRow = sourceCode.querySelector('.source-line-selected');
selectedRow.offsetTop = 90; selectedRow.offsetHeight = 22;
sourceCode.clientHeight = 220; sourceCode.scrollTop = 0;
workspace.getBoundingClientRect = () => ({ top: 552, bottom: 789 });
for (const [rowTop, rowBottom, expectedScroll] of [[816,838,149], [530,552,78], [600,622,100]]) {
  workspace.scrollTop = 100;
  selectedRow.getBoundingClientRect = () => ({ top: rowTop, bottom: rowBottom });
  viewer.centerSelectedLine(true);
  assert.equal(workspace.scrollTop, expectedScroll, 'only the clipping source workspace should move');
  assert.equal(sourceCode.scrollTop, 0, 'a locally visible selected row must not scroll the code viewport');
  assert.equal(source.document.documentElement.scrollTop, 555);
  assert.equal(source.document.body.scrollTop, 72);
}

// Preview перерисовывает строки, но сохраняет внешний scroll, даже если row сначала обрезан.
workspace.scrollTop = 137.5;
sourceFields.get('find').value = 'fresh'; sourceFields.get('find').listeners.input();
assert.equal(workspace.scrollTop, 137.5, 'typing must retain the outer reader viewport');
assert.equal(sourceCode.scrollTop, 0);
viewer.fetcher = async (url, options) => {
  const body = JSON.parse(options.body);
  return response({ text: 'fresh\nstart', file: `${body.nodeId}.cs`, line: 2, totalLines: 2, snapshotId: 's' });
};
await viewer.navigate({ ...context, nodeId: 'other' });
workspace.scrollTop = 210.5;
await viewer.travel(-1);
await new Promise(resolve => setImmediate(resolve));
assert.equal(workspace.scrollTop, 137.5, 'Back must restore outer scroll after source rendering settles');
assert.equal(viewer.query, 'fresh');
await viewer.travel(1);
assert.equal(workspace.scrollTop, 210.5, 'Forward must restore its own outer viewport');
assert.equal(source.document.documentElement.scrollTop, 555);
assert.equal(source.document.body.scrollTop, 72);
viewer.close();

// Initial retry раскрывает indexed line; retry неудачного Back восстанавливает ручную прокрутку.
context.line = 150;
const largeText = Array.from({ length: 501 }, (_, index) => `line ${index + 1}`).join('\n');
const sourceResponse = () => response({ text: largeText, file: 'large.cs', line: 150, totalLines: 501, snapshotId: 's' });
const createSourceElement = source.document.createElement;
source.document.createElement = tag => {
  const element = createSourceElement(tag);
  if (tag === 'div') {
    element.offsetHeight = 20;
    Object.defineProperty(element, 'offsetTop', { get() {
      const number = Number(this.children[0]?.textContent);
      const first = viewer.mode === 'full' ? viewer.page * 200 + 1 : Math.max(1, (viewer.data?.line || 150) - 40);
      return Number.isFinite(number) ? (number - first) * 20 : 0;
    } });
  }
  return element;
};
globalThis.setTimeout = (callback, delay, ...args) => originalTimeout(callback, delay === 30000 ? 8 : delay, ...args);
try {
  let calls = 0;
  viewer.fetcher = async () => ++calls === 1 ? new Promise(() => {}) : sourceResponse();
  await viewer.open();
  assert.equal(sourceFields.get('retry').hidden, false);
  sourceFields.get('retry').click(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(viewer.data.line, 150);
  assert.ok(sourceCode.scrollTop > 0, 'initial retry must reveal a distant indexed line instead of restoring zero scroll');
  sourceCode.scrollTop = 725; workspace.scrollTop = 137.5;
  await viewer.navigate({ ...context, nodeId: 'other', line: 20 });
  calls = 0;
  await viewer.travel(-1);
  assert.equal(sourceFields.get('retry').hidden, false);
  sourceFields.get('retry').click(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(sourceCode.scrollTop, 725, 'retry after failed Back must retain the captured inner viewport');
  assert.equal(workspace.scrollTop, 137.5, 'retry after failed Back must retain the captured outer viewport');
} finally { globalThis.setTimeout = originalTimeout; }

// Только явный Go получает один дополнительный reveal; поздний кадр не перебивает новые действия.
const frames = [];
source.document.defaultView.requestAnimationFrame = callback => frames.push(callback);
const centerSelectedLine = viewer.centerSelectedLine.bind(viewer);
let reveals = 0;
viewer.centerSelectedLine = (...args) => { reveals++; return centerSelectedLine(...args); };
viewer.goToLine(20);
assert.equal(frames.length, 1);
let beforeFrame = reveals; frames.shift()();
assert.equal(reveals, beforeFrame + 1, 'the same explicit Go may reveal once after layout');
sourceFields.get('find').value = 'line'; sourceFields.get('find').listeners.input();
assert.equal(frames.length, 0, 'preview must not enqueue a delayed reveal');
await viewer.travel(-1);
assert.equal(frames.length, 0, 'Back must not enqueue a delayed reveal');
viewer.goToLine(21); sourceCode.scrollTop += 1;
beforeFrame = reveals; frames.shift()();
assert.equal(reveals, beforeFrame, 'manual source scrolling must cancel the delayed reveal');
viewer.goToLine(22); viewer.goToLine(23);
beforeFrame = reveals; frames.shift()();
assert.equal(reveals, beforeFrame, 'a changed selected line must cancel the earlier frame');
frames.shift()(); assert.equal(reveals, beforeFrame + 1);
viewer.goToLine(24);
await viewer.navigate({ ...context, nodeId: 'changed', line: 25 });
beforeFrame = reveals; frames.shift()();
assert.equal(reveals, beforeFrame, 'a new source revision must cancel the earlier frame');
viewer.goToLine(26); viewer.close();
beforeFrame = reveals; frames.shift()();
assert.equal(reveals, beforeFrame, 'a closed reader must ignore the queued frame');

console.log('Post-update fitted zoom, read/export deadline recovery and search history PASS');
