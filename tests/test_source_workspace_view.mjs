/** Проверяет реальный SourceViewer: локальные переходы, поиск, фокус и независимые гонки списка. */
import assert from 'node:assert/strict';
import { SourceViewer } from '../web/source-view.js';
import { SourceFileBrowser } from '../web/source-file-browser.js';

/** Текстовый DOM запрещает HTML-вставки; геометрия остаётся обязанностью browser smoke. */
class Element {
  constructor(name, document) {
    this.name = name; this.ownerDocument = document; this.children = []; this.listeners = {};
    this.attributes = {}; this.hidden = false; this.inert = false; this.value = '';
    this.scrollTop = 0; this.scrollLeft = 0; this.offsetTop = 0; this.clientHeight = 100;
  }
  addEventListener(type, action) { this.listeners[type] = action; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text || this.children.map(child => child.textContent).join(''); }
  set innerHTML(value) { throw new Error('Source HTML must never be inserted'); }
  focus() { this.ownerDocument.activeElement = this; }
  closest() { return this.hidden || this.inert ? this : null; }
  getClientRects() { return [{}]; }
  querySelector(selector) { return this.children.find(child => child.className?.split(' ').includes(selector.slice(1))); }
  querySelectorAll() { return this.children; }
}

/** Создаёт controls и фон, чтобы использовать настоящий DialogFocus без нового DOM-пакета. */
function fixture(names) {
  const document = {};
  document.createElement = name => new Element(name, document);
  document.body = document.createElement('body');
  const background = document.createElement('background');
  const trigger = document.createElement('trigger'); background.append(trigger); trigger.focus();
  const root = document.createElement('root');
  const controls = new Map();
  for (const name of names) {
    const control = document.createElement(name); controls.set(name, control); root.append(control);
  }
  root.querySelector = selector => controls.get(selector.match(/"(.*?)"/)?.[1]);
  document.body.append(background, root);
  return { document, background, trigger, root, controls };
}

/** Возвращает HTTP JSON, не превращая исходник в HTML и не вызывая другие endpoints. */
function response(data) { return { ok: true, json: async () => data }; }

const names = ['title','status','close','context','full','previous','next','range','code',
  'back','forward','goto','goto-button','find','find-next','reader-status','maximize','language'];
const ui = fixture(names); globalThis.document = ui.document;
const text = Array.from({ length: 501 }, (_, index) => [20,420].includes(index+1) ? `needle ${index+1}` : `line ${index+1}`).join('\n');
const mapState = { selected: 'a', camera: { x: 12, y: 24, scale: .9 }, filters: ['method'],
  context: { projectId: 'p', snapshotId: 's', nodeId: 'a', line: 12 } };
const savedMap = structuredClone(mapState);
let context = mapState.context;
const requests = [];
const fetcher = async (url, options) => {
  requests.push({ url, options });
  const body = JSON.parse(options.body);
  return response({ text, totalLines: 501, file: `${body.nodeId}.cs`, line: 1, snapshotId: body.snapshotId });
};
const viewer = new SourceViewer(ui.root, () => context, fetcher);
await viewer.open();
assert.equal(ui.background.inert, true);
assert.equal(ui.document.activeElement, ui.controls.get('close'));
assert.equal(viewer.data.line, 12);
const code = ui.controls.get('code');
code.scrollTop = 321; code.scrollLeft = 48;
const find = ui.controls.get('find'); find.value = 'needle'; find.listeners.input();
assert.equal(viewer.data.line, 12, 'typing must preview without changing the selected source line');
assert.equal(code.scrollTop, 321, 'typing must preserve vertical reading position');
assert.equal(code.scrollLeft, 48, 'typing must preserve horizontal reading position');
assert.equal(viewer.history.canBack, false);
viewer.findNext();
assert.equal(viewer.data.line, 20);
assert.equal(viewer.history.canBack, true);
assert.equal(requests.length, 1, 'find and go-to-line use the loaded source');
viewer.goToLine('0'); assert.equal(viewer.data.line, 20);
viewer.goToLine('502'); assert.equal(viewer.data.line, 20);
viewer.goToLine('401'); assert.equal(viewer.data.line, 401); assert.equal(viewer.page, 2);
assert.ok(!ui.controls.get('reader-status').textContent.includes('Введите строку'), 'a valid jump must clear the previous validation error');
assert.equal(ui.controls.get('title').textContent, 'a.cs:401');
assert.equal(String(ui.controls.get('goto').value), '401');
assert.equal(code.children.length, 101, 'the final page contains only remaining source lines');
code.scrollTop = 345; code.scrollLeft = 55;
const savedLocation = { mode: viewer.mode, page: viewer.page, query: viewer.query, matchIndex: viewer.matchIndex };
await viewer.navigate({ ...context, nodeId: 'b', line: 3 });
assert.equal(viewer.data.file, 'b.cs');
assert.equal(ui.controls.get('reader-status').textContent, '', 'the next file must not retain the previous jump message');
await viewer.travel(-1);
assert.equal(viewer.data.file, 'a.cs'); assert.equal(viewer.data.line, 401);
assert.deepEqual({ mode: viewer.mode, page: viewer.page, query: viewer.query, matchIndex: viewer.matchIndex }, savedLocation);
assert.equal(code.scrollTop, 345); assert.equal(code.scrollLeft, 55);
const afterTravel = requests.length;
await viewer.sync();
assert.equal(requests.length, afterTravel, 'background map sync must not replace local reader position');
viewer.maximize(); assert.equal(ui.root.getAttribute('data-maximized'), 'true');
assert.equal(ui.controls.get('maximize').getAttribute('aria-pressed'), 'true');
assert.equal(ui.background.inert, true);
viewer.close();
assert.equal(ui.background.inert, false);
assert.equal(ui.document.activeElement, ui.trigger);
assert.deepEqual(mapState, savedMap, 'local source interactions preserve map selection, camera and filters');

// Смена snapshot должна удалить доступ к прежней локальной истории.
context = { ...context, snapshotId: 'new', nodeId: 'new-node' };
await viewer.open();
assert.equal(viewer.history.canBack, false);
assert.equal(viewer.history.canForward, false);
assert.equal(viewer.data.snapshotId, 'new');
viewer.close();

// Safari оставляет focus на body после pointer-click: закрытие всё равно возвращает к source-open.
const responsive = fixture(names);
responsive.document.getElementById = id => id === 'source-open' ? responsive.trigger : null;
responsive.document.activeElement = responsive.document.body;
globalThis.document = responsive.document;
const previousObserver = globalThis.ResizeObserver;
let resize;
const layoutCallbacks = [];
responsive.document.defaultView = { addEventListener: (type, callback) => {
  assert.equal(type, 'resize'); resize = callback;
} };
globalThis.ResizeObserver = class {
  constructor(callback) { layoutCallbacks.push(callback); }
  observe() {}
};
try {
  const responsiveViewer = new SourceViewer(responsive.root, () => context, fetcher);
  await responsiveViewer.open();
  const viewport = responsive.controls.get('code');
  assert.equal(typeof resize, 'function', 'the real window resize event must be connected');
  assert.equal(layoutCallbacks.length, 0, 'status and content reflow must not register a recentering observer');
  const selected = viewport.querySelector('.source-line-selected');
  selected.offsetTop = 300; selected.offsetHeight = 20;
  viewport.offsetTop = 1000; viewport.clientHeight = 100; viewport.scrollTop = 260;
  resize();
  assert.equal(viewport.scrollTop, 260, 'resize must preserve an already visible selected row');
  viewport.scrollTop = 0;
  resize();
  assert.equal(viewport.scrollTop, 260, 'row offsets belong to the positioned viewport, not its parent');

  // Читатель мог уйти далеко от indexed line: Back обязан сохранить и дробную прокрутку.
  viewport.scrollTop = 4197.5; viewport.scrollLeft = 23;
  await responsiveViewer.navigate({ ...context, nodeId: 'other-file' });
  await responsiveViewer.travel(-1);
  assert.equal(viewport.scrollTop, 4197.5);
  responsive.controls.get('status').textContent = 'A wrapped warning changes the available reading height.';
  viewport.clientHeight = 80;
  for (const callback of layoutCallbacks) callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(viewport.scrollTop, 4197.5, 'settled status/layout changes must not override Back restoration');
  assert.equal(viewport.scrollLeft, 23);
  const restoredSelected = viewport.querySelector('.source-line-selected');
  restoredSelected.offsetTop = 300; restoredSelected.offsetHeight = 20;
  viewport.scrollTop = 0; viewport.clientHeight = 100;
  responsiveViewer.maximize();
  assert.equal(viewport.scrollTop, 260, 'explicit maximize may reveal an offscreen selected row');
  responsiveViewer.close();
  assert.equal(responsive.document.activeElement, responsive.trigger, 'pointer activation must retain a visible map return target');
  viewport.scrollTop = 0; resize();
  assert.equal(viewport.scrollTop, 0, 'a closed reader must ignore resize callbacks');
} finally {
  if (previousObserver === undefined) delete globalThis.ResizeObserver;
  else globalThis.ResizeObserver = previousObserver;
}

// Запрос старого фильтра становится устаревшим сразу при вводе, ещё до debounce нового запроса.
const list = fixture(['file-query','file-status','files','file-more','file-previous']);
globalThis.document = list.document;
const pending = [], opened = [];
const browser = new SourceFileBrowser(list.root, value => opened.push(value), (url, options) => new Promise(resolve => pending.push({ url, options, resolve })));
browser.setContext({ projectId: 'p', snapshotId: 's', nodeId: 'map-node' });
list.controls.get('file-query').value = 'alpha';
const oldLoad = browser.load();
list.controls.get('file-query').value = 'beta';
list.controls.get('file-query').listeners.input();
assert.equal(pending[0].options.signal.aborted, true, 'input must cancel the previous query before debounce elapses');
pending[0].resolve(response({ snapshotId: 's', files: [{ file: 'alpha.cs', nodeId: 'alpha', line: 1 }], total: 1, nextOffset: null }));
await oldLoad;
assert.equal(list.controls.get('files').children.length, 0, 'late old-query response must not populate the new filter');
const newLoad = browser.load();
pending[1].resolve(response({ snapshotId: 's', files: [{ file: 'beta.cs', nodeId: 'beta', line: 9 }], total: 1, nextOffset: null }));
await newLoad;
list.controls.get('files').children[0].listeners.click();
assert.deepEqual(opened, [{ projectId: 'p', snapshotId: 's', nodeId: 'beta', label: 'beta.cs', line: 9 }]);
assert.equal(list.controls.get('file-previous').hidden, true);
assert.equal(list.controls.get('file-more').hidden, true);
browser.close();

// Тот же production deadline ограничивает и транспорт, и JSON; поздний ответ после timeout не публикуется.
const originalTimeout = globalThis.setTimeout;
globalThis.setTimeout = (callback, delay, ...args) => originalTimeout(callback, delay === 30000 ? 8 : delay, ...args);
try {
  for (const kind of ['transport', 'json']) {
    const timed = fixture(names); globalThis.document = timed.document;
    let finish, sourceSignal;
    const sourceFetcher = (url, options) => {
      sourceSignal = options.signal;
      return kind === 'transport' ? new Promise(resolve => { finish = resolve; })
        : Promise.resolve({ ok: true, json: () => new Promise(resolve => { finish = resolve; }) });
    };
    const timedViewer = new SourceViewer(timed.root, () => context, sourceFetcher);
    await timedViewer.open();
    assert.match(timed.controls.get('status').textContent, /Сервер долго отвечает/);
    assert.equal(sourceSignal.aborted, true);
    assert.equal(timedViewer.data, null);
    assert.equal(timed.controls.get('code').children.length, 0);
    const lateSource = { text: 'late', file: 'late.cs', line: 1, snapshotId: context.snapshotId };
    finish(kind === 'transport' ? response(lateSource) : lateSource);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(timedViewer.data, null, 'late source after deadline must not replace the timeout state');
    timedViewer.close();
    assert.equal(timed.background.inert, false);

    const timedList = fixture(['file-query','file-status','files','file-more','file-previous']);
    globalThis.document = timedList.document;
    let listSignal;
    const listFetcher = (url, options) => {
      listSignal = options.signal;
      return kind === 'transport' ? new Promise(resolve => { finish = resolve; })
        : Promise.resolve({ ok: true, json: () => new Promise(resolve => { finish = resolve; }) });
    };
    const timedBrowser = new SourceFileBrowser(timedList.root, () => assert.fail('timeout must not open a file'), listFetcher);
    timedBrowser.setContext(context);
    await timedBrowser.load();
    assert.match(timedList.controls.get('file-status').textContent, /Сервер долго отвечает/);
    assert.equal(listSignal.aborted, true);
    const lateCatalog = { snapshotId: context.snapshotId, files: [{ file: 'late.cs', nodeId: 'late', line: 1 }], total: 1, nextOffset: null };
    finish(kind === 'transport' ? response(lateCatalog) : lateCatalog);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(timedList.controls.get('files').children.length, 0, 'late catalogue after deadline must not render');
    timedBrowser.close();
  }
} finally { globalThis.setTimeout = originalTimeout; }

console.log('Source workspace integration, local context and immediate list cancellation PASS');
