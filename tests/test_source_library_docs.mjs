/** Проверяет явность поиска библиотек, сохранённые источники и отмену устаревших ответов. */
import assert from 'node:assert/strict';
import { SourceLibraryDocs } from '../web/source-library-docs.js';

/** Небольшой DOM запрещает HTML и сохраняет реальные текстовые дочерние элементы. */
class Element {
  constructor(tag, ownerDocument) {
    this.tag = tag; this.ownerDocument = ownerDocument; this.children = []; this.listeners = {};
    this.value = ''; this.text = ''; this.attributes = {}; this.hidden = false;
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw new Error('Unsafe HTML insertion'); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.text = ''; this.children = nodes; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(type, callback) { this.listeners[type] = callback; }
}

const document = {
  createElement(tag) { return new Element(tag, this); },
  createTextNode(text) { const node = new Element('#text', this); node.textContent = text; return node; },
};
globalThis.document = document;
const context = { projectId: 'one / scoped', snapshotId: 'snapshot-a', nodeId: 'caller', label: 'Caller' };
const library = { package: 'Acme.Widget', version: '1.2.3', assembly: 'Acme.dll', sourcePath: '/cache/Acme.xml', sourceHash: 'abc123' };
const hit = { packId: 'pack / one', documentId: 'doc?one', title: 'Run(int)', signature: 'Acme.Run(System.Int32)', excerpt: '<script>attack()</script>', library, version: '1.2.3', locale: 'en', localeFallback: true };

/** Собирает элементы выбранного тега без предположений об их DOM положении. */
function descendants(root, tag) {
  const result = [];
  for (const child of root.children) {
    if (child.tag === tag) result.push(child);
    result.push(...descendants(child, tag));
  }
  return result;
}

/** Ответы не исполняют source/XML; API возвращает только JSON. */
function response(data, status = 200) { return { ok: status < 400, status, json: async () => data }; }

/** Создаёт отдельный host и транспорт с наблюдаемыми явными запросами. */
function fixture(handler) {
  const calls = [], root = document.createElement('div');
  const fetcher = (url, options) => { calls.push({ url, options }); return handler(url, options, calls.length); };
  const widget = new SourceLibraryDocs(root, { fetcher });
  return { widget, root, calls };
}

/** Управляет ответами, даже если транспорт намеренно игнорирует AbortSignal. */
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

// Настройка контекста и выделенного текста никогда не ищет и не импортирует.
const basic = fixture(async () => response({ projectId: context.projectId, results: [hit], capturedAt: '2026-10-16T10:00:00Z' }));
assert.equal(basic.widget.submit.disabled, true);
basic.widget.setContext(context);
basic.widget.setQuery('Run');
assert.equal(basic.calls.length, 0);
assert.equal(basic.widget.query.value, 'Run');
assert.ok(basic.widget.catalogLink.href.endsWith('repo=one%20%2F%20scoped'));
await basic.widget.search();
assert.equal(basic.calls.length, 1);
assert.equal(basic.calls[0].url, '/api/projects/one%20%2F%20scoped/library-documentation/search');
assert.deepEqual(JSON.parse(basic.calls[0].options.body), { query: 'Run', locale: 'ru', mode: 'fulltext', limit: 5 });
assert.equal(basic.calls[0].options.method, 'POST');
assert.ok(basic.root.textContent.includes('привязка к вызову не подтверждена'));
for (const expected of ['1.2.3', 'Acme.dll', '/cache/Acme.xml', '2026-10-16T10:00:00Z', 'abc123', 'исходный язык: en', 'Acme.Run(System.Int32)']) assert.ok(basic.root.textContent.includes(expected), expected);
assert.equal(descendants(basic.root, 'script').length, 0);
assert.ok(basic.root.textContent.includes('<script>attack()</script>'));
assert.equal(descendants(basic.widget.results, 'article').length, 1);

// Длина и пустой ввод валидируются до транспорта; подстановка ограничена 500 символами.
basic.widget.setQuery('x'.repeat(900));
assert.equal(basic.widget.query.value.length, 500);
basic.widget.query.value = ' ';
await basic.widget.search();
basic.widget.query.value = 'x'.repeat(501);
await basic.widget.search();
assert.equal(basic.calls.length, 1);
assert.ok(basic.widget.status.textContent.includes('1 до 500'));

// Ограничение кандидатов применяется также к неожиданно большому ответу сервера.
const capped = fixture(async () => response({ results: Array.from({ length: 9 }, (_, index) => ({ ...hit, title: `Overload ${index}` })), capturedAt: 'captured', truncated: true }));
capped.widget.setContext(context); capped.widget.setQuery('Run'); await capped.widget.search();
assert.equal(descendants(capped.widget.results, 'article').length, 5);
assert.ok(capped.widget.status.textContent.includes('часть результатов'));

// Документ читается только отдельным кликом, с кодированными ID и ограниченной страницей.
const page = fixture(async (_url, _options, count) => response(count === 1
  ? { projectId: context.projectId, results: [hit], capturedAt: 'capture' }
  : { projectId: context.projectId, title: 'Run(int)', library, pack: { version: '1.2.3', locale: 'en' }, capturedAt: 'capture', text: '## Remarks\n\nHello <img onerror="attack()">\n\n```csharp\nRun();\n```\n\n' + 'tail '.repeat(3000), nextOffset: 12000 }));
page.widget.setContext(context); page.widget.setQuery('Run'); await page.widget.search();
assert.equal(page.calls.length, 1);
await descendants(page.widget.results, 'button')[0].listeners.click();
assert.equal(page.calls[1].options.method, undefined);
assert.equal(page.calls[1].url, '/api/projects/one%20%2F%20scoped/library-documentation/pack%20%2F%20one/documents/doc%3Fone?offset=0&limit=12000');
assert.equal(page.widget.documentPanel.hidden, false);
assert.equal(descendants(page.widget.documentPanel, 'img').length, 0);
assert.ok(page.widget.documentPanel.textContent.includes('Hello <img onerror="attack()">'));
assert.ok(page.widget.documentPanel.textContent.includes('Показана часть документа'));
const rawText = descendants(page.widget.documentPanel, 'details')[0];
assert.equal(descendants(rawText, 'pre')[0].textContent.length, 12000);
assert.equal(descendants(page.widget.documentPanel, 'h3')[0].textContent, 'Примечания');

// Пустой каталог честно называет .NET XML и не запускает capture.
const empty = fixture(async () => response({ results: [], capturedAt: null }));
empty.widget.setContext(context); empty.widget.setQuery('Run'); await empty.widget.search();
assert.ok(empty.widget.status.textContent.includes('Сохранённой документации нет'));
assert.ok(empty.widget.status.textContent.includes('.NET'));
assert.equal(empty.calls.length, 1);
assert.ok(empty.calls.every(call => !call.url.includes('/capture')));

// Ошибка поиска остаётся видимой; повторный явный поиск доступен.
const failed = fixture(async () => response({ error: 'Local XML unavailable' }, 404));
failed.widget.setContext(context); failed.widget.setQuery('Run'); await failed.widget.search();
assert.equal(failed.widget.status.textContent, 'Local XML unavailable');
assert.equal(failed.widget.submit.disabled, false);

// Смена узла/снимка/проекта/файла отменяет запрос и игнорирует поздний ответ без нового fetch.
for (const next of [{ ...context, nodeId: 'other' }, { ...context, snapshotId: 'snapshot-b' }, { ...context, projectId: 'two' }, { ...context, file: 'new.py' }]) {
  const wait = deferred(), race = fixture(() => wait.promise);
  race.widget.setContext(context); race.widget.setQuery('Run');
  const pending = race.widget.search();
  race.widget.setContext(next);
  assert.equal(race.calls[0].options.signal.aborted, true);
  wait.resolve(response({ projectId: context.projectId, results: [hit], capturedAt: 'old' }));
  await pending;
  assert.equal(race.widget.results.children.length, 0);
  assert.equal(race.widget.query.value, '');
  assert.equal(race.calls.length, 1);
}

// Новый поиск имеет приоритет перед старым ответом; одинаковый context не сбрасывает ввод.
const oldSearch = deferred(), searchRace = fixture(async (_url, _options, count) => count === 1 ? oldSearch.promise : response({ results: [{ ...hit, title: 'New result' }], capturedAt: 'new' }));
searchRace.widget.setContext(context); searchRace.widget.setQuery('Old');
const oldPending = searchRace.widget.search();
searchRace.widget.setContext({ ...context });
assert.equal(searchRace.widget.query.value, 'Old');
searchRace.widget.setQuery('New'); await searchRace.widget.search();
oldSearch.resolve(response({ results: [hit], capturedAt: 'old' })); await oldPending;
assert.equal(searchRace.calls[0].options.signal.aborted, true);
assert.ok(searchRace.widget.results.textContent.includes('New result'));

// Закрытие отменяет документ; повторное открытие того же scope остаётся доступным.
const oldDoc = deferred(), closing = fixture(() => oldDoc.promise);
closing.widget.setContext(context);
const pendingDocument = closing.widget.openDocument(hit);
closing.widget.close();
assert.equal(closing.calls[0].options.signal.aborted, true);
oldDoc.resolve(response({ text: 'Old document', projectId: context.projectId })); await pendingDocument;
assert.equal(closing.widget.documentPanel.hidden, true);
assert.equal(closing.widget.documentPanel.children.length, 0);
assert.equal(closing.widget.submit.disabled, true);
await closing.widget.search();
assert.equal(closing.calls.length, 1);
closing.widget.setContext(context);
assert.equal(closing.widget.submit.disabled, false);

// Случайный ответ чужого repository не показывается даже при совпадении транспортной ревизии.
const foreign = fixture(async () => response({ projectId: 'foreign', results: [hit], capturedAt: 'capture' }));
foreign.widget.setContext(context); foreign.widget.setQuery('Run'); await foreign.widget.search();
assert.equal(foreign.widget.results.children.length, 0);
assert.ok(foreign.widget.status.textContent.includes('другому проекту'));
console.log('Source library docs: explicit scoped reads, provenance, literal DOM, limits and cancellation PASS');
