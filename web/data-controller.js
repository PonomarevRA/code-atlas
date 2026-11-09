import {withLoading} from './loading-state.js';
import {discoveredDataSelection} from './data-file-navigation.js';
import {requestLoadingLabel} from './loading-labels.js';
/** Data map lifecycle: discovery reads names, imports require a button click. */
import { DataScene } from './data-scene.js';
import { DataKeyExplorer } from './data-key-controller.js';
const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const projectId = params.get('project');
let active = null, jobId = null, generation = 0, next = null, moreRelations = null;
const scene = new DataScene($('graph'), selectNode);
const keys = new DataKeyExplorer({ bar: $('key-controls'), select: $('key-select'), moreKeys: $('more-keys'),
  moreValues: $('more-values'), isolate: $('isolate-path'), moreRows: $('more-key-rows'), info: $('key-info') },
  scene, query, inspectKeyPath, restoreStructure, message);
/** Показывает дату снимка в локальной зоне читателя. */
export function date(value) { return value ? new Date(value).toLocaleString('ru-RU') : 'ещё не построена'; }
/** Дата остаётся доступной и после изменения исходников. */
export function freshnessText(value) {
  const unavailable = value.freshness?.status === 'unavailable';
  const reason = unavailable ? ` · ${value.freshness.message || 'Снимок недоступен. Обновите карту.'} (${value.freshness.code || 'SNAPSHOT_UNAVAILABLE'})` : '';
  return `Обновлена: ${date(value.builtAt)}${value.freshness?.status === 'stale' ? ' · Исходные файлы изменились. Можно читать снимок или обновить карту.' : ''}${reason}`;
}
/**
 * Читает локальный JSON с неблокирующим учётом всей операции.
 *
 * path — существующий API-маршрут; body задаёт POST, отсутствие body — GET.
 * Возвращает декодированный ответ; исходные сообщения и типы ошибок этой
 * страницы сохранены. Токен освобождается после JSON либо ошибки в finally.
 */
async function request(path, body) {
  return withLoading(requestLoadingLabel(path, {method: body === undefined ? 'GET' : 'POST'}), async () => {
    const response = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || value.detail?.[0]?.msg || 'Не удалось выполнить запрос');
    return value;
  });
}
/** Сохраняет понятное сообщение в доступной области статуса. */
function message(error) {
  if (error?.name === 'AbortError') return;
  $('message').textContent = error instanceof Error ? error.message : error;
}
/** Обработчик формы или кнопки оставляет ошибки в текущем экране. */
function action(fn) { return async event => { event?.preventDefault(); try { await fn(event); } catch (error) { message(error); } }; }
/** Подсвечивает stale, не скрывая содержимое сохранённого снимка. */
function showFreshness(value) {
  $('freshness').textContent = freshnessText(value);
  $('freshness').classList.toggle('stale', ['stale', 'unavailable'].includes(value.freshness?.status));
}
/** Закрепляет чтение за выбранной картой и отклоняет поздний ответ другой карты. */
async function query(operation, body = {}) {
  const map = active;
  const selectionRevision = keys.revision;
  if (!map?.snapshotId) throw new Error('Дождитесь завершения построения карты.');
  if (map.freshness?.status === 'unavailable') throw new Error(map.freshness.message || 'Снимок недоступен. Обновите карту.');
  const result = await request(`/api/data-maps/${map.id}/query/${operation}`, { snapshotId: map.snapshotId, ...body });
  if (active !== map) throw new Error('Выбранная карта изменилась.');
  if (keys.revision !== selectionRevision) throw new DOMException('Выбор ключа изменился.', 'AbortError');
  showFreshness(result);
  return result;
}
/** Показывает уже созданные карты без импорта источников. */
async function catalog() {
  const result = await request('/api/data-maps' + (projectId ? '?projectId=' + encodeURIComponent(projectId) : ''));
  $('catalog').replaceChildren();
  for (const map of result.maps) {
    const button = document.createElement('button'); button.className = 'map-link'; button.textContent = map.title;
    const info = document.createElement('small'); info.textContent = `${date(map.builtAt)} · ${map.freshness.status === 'stale' ? 'файлы изменились' : map.status}`;
    button.append(info); button.addEventListener('click', action(() => openMap(map.id))); $('catalog').append(button);
  }
}
/** Открывает опубликованную структуру; незавершённый импорт только наблюдается. */
async function openMap(id) {
  const revision = ++generation;
  const value = await request('/api/data-maps/' + encodeURIComponent(id));
  if (revision !== generation) return;
  active = value; jobId = null; next = null; keys.reset(); scene.reset(); $('records').replaceChildren(); $('more').hidden = true; $('cancel').hidden = true;
  $('map-title').textContent = value.title; showFreshness(value);
  const readable = Boolean(value.snapshotId) && value.freshness?.status !== 'unavailable';
  $('refresh').hidden = false; $('svg-export').hidden = !readable; $('json-export').hidden = !readable;
  if (readable) $('json-export').href = `/api/data-maps/${id}/export?snapshotId=${value.snapshotId}`;
  else $('json-export').removeAttribute('href');
  history.replaceState(null, '', `/data.html?map=${id}${projectId ? '&project=' + encodeURIComponent(projectId) : ''}`);
  if (readable) {
    const result = await query('schema', { nodeId: 'data:' + id, limit: 50 });
    if (revision !== generation) return;
    scene.add(result.items); await relations(); await keys.run(() => keys.loadKeys());
  }
  if (['queued', 'running'].includes(value.status) && value.jobId) await monitor(value.jobId, revision);
}
/** Наблюдает явно запрошенный импорт до завершения, отмены или смены карты. */
async function monitor(id, revision = generation) {
  jobId = id; $('cancel').hidden = false;
  while (revision === generation && jobId === id) {
    const job = await request('/api/data-jobs/' + encodeURIComponent(id));
    if (revision !== generation || jobId !== id) return;
    $('progress').textContent = `Импорт: ${job.phase} · ${job.records || 0} записей · ${Math.round((job.bytesRead || 0) / 1024 / 1024)} МБ`;
    if (!['queued', 'running'].includes(job.status)) {
      jobId = null; $('cancel').hidden = true; $('progress').textContent = job.status === 'completed' ? 'Карта построена.' : job.error || 'Импорт отменён.';
      await catalog(); if (job.status === 'completed') await openMap(job.mapId); return;
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
}
/**
 * Читает имена/размеры и выделяет только явно запрошенный файл, подтверждённый discovery.
 * @returns {Promise<void>} Ничего не импортирует; даже preselected-файл требует native submit формы.
 */
async function discover() {
  const result = projectId ? await request(`/api/projects/${encodeURIComponent(projectId)}/data-files`) : await request('/api/data-files', { root: $('root').value.trim() });
  $('files').replaceChildren();
  const requested=params.get('file');
  const selected=discoveredDataSelection(result.files,requested);
  for (const file of result.files) {
    const label = document.createElement('label'); label.className = 'file-option';
    const input = document.createElement('input'); input.type = 'checkbox'; input.value = file.path; input.checked = file.path===selected;
    label.append(input, document.createTextNode(`${file.path} · ${(file.sizeBytes / 1024).toFixed(1)} КБ`)); $('files').append(label);
  }
  $('create-form').hidden = result.files.length === 0;
  message(selected ? 'Выбран файл из ссылки: '+selected+'. Карта данных ещё не построена. Проверьте выбор и нажмите «Построить выбранные файлы».'
    :requested?'Файл из ссылки не найден в текущем доступном списке. Ничего не выбрано; проверьте путь или выберите файл вручную.'
    :result.truncated ? 'Список ограничен. Для остальных файлов выберите более узкий каталог.' : `${result.files.length} файлов найдено. Карта ещё не строилась.`);
}
/** Построение начинается лишь после отправки формы с выбранными файлами. */
async function create() {
  const files = [...$('files').querySelectorAll('input:checked')].map(input => input.value);
  if (!files.length) throw new Error('Выберите файлы для карты.');
  const body = { files, title: $('title').value, encoding: $('encoding').value, delimiter: $('delimiter').value };
  if (projectId) body.projectId = projectId; else body.root = $('root').value.trim();
  const result = await request('/api/data-maps', body);
  await catalog(); await openMap(result.map.id);
}
/** Продолжение сохраняет точные параметры текущей страницы. */
function setNext(callback) { next = callback; $('more').hidden = !callback; }
/** Выводит значения как текст, сохраняя ведущие нули и координаты источника. */
function table(items, columns, click) {
  const table = document.createElement('table'), head = document.createElement('thead'), header = document.createElement('tr');
  for (const column of columns) { const cell = document.createElement('th'); cell.textContent = column; header.append(cell); }
  head.append(header); table.append(head);
  const body = document.createElement('tbody');
  for (const item of items) {
    const row = document.createElement('tr');
    for (const column of columns) {
      const cell = document.createElement('td'); const value = column === 'Источник' ? item.provenance?.file + ':' + (item.provenance?.line ?? item.provenance?.pointer ?? '') : item.values?.[column];
      cell.textContent = value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value); row.append(cell);
    }
    if (click) { const cell = document.createElement('td'), button = document.createElement('button'); button.textContent = 'Открыть'; button.addEventListener('click', action(() => click(item))); cell.append(button); row.append(cell); }
    body.append(row);
  }
  table.append(body); return table;
}
/** Загружает страницу коллекции, включая выбор конкретного Examples. */
async function records(node, cursor = null) {
  const result = await query('records', { collectionId: node.id, cursor, limit: 20 });
  const columns = [...new Set(result.items.flatMap(item => Object.keys(item.values || {})))].slice(0, 12);
  $('records').replaceChildren(table(result.items, [...columns, 'Источник'], item => node.kind === 'examples'
    ? scenario({ id: item.provenance.scenarioId, label: 'Сценарий с выбранным примером' }, item.id) : trace(item)));
  $('selection-info').textContent = result.items.some(item => item.contentTruncated) ? 'Крупные записи сокращены. Выбранные поля можно прочитать через MCP get_data_record.' : 'Значения и координаты относятся к снимку карты.';
  setNext(result.nextCursor ? () => records(node, result.nextCursor) : null);
}
/** Разделяет описание ожиданий и факты исполнения, которого импорт не подтверждает. */
async function scenario(node, exampleId = null, cursor = null) {
  const result = await query('scenario', { scenarioId: node.id, exampleId, cursor, limit: 20 });
  $('selection-title').textContent = result.scenario?.name || node.label;
  $('selection-info').textContent = [result.scenario?.description, 'Описание сценария и ожиданий. Выполнение не подтверждено.'].filter(Boolean).join('\n');
  $('records').replaceChildren();
  for (const step of result.items) {
    const article = document.createElement('article'), text = document.createElement('p');
    const shown = step.resolved || step; text.textContent = (shown.keyword || '') + (shown.text || shown.label || ''); article.append(text);
    if (shown.docString) { const pre = document.createElement('pre'); pre.textContent = shown.docString.content; article.append(pre); }
    if (step.dataTableId) { const button = document.createElement('button'); button.textContent = 'Таблица аргументов'; button.addEventListener('click', action(() => selectNode({ id: step.dataTableId, kind: 'data-table', label: 'Аргументы шага' }))); article.append(button); }
    $('records').append(article);
  }
  for (const example of result.scenario?.examples || []) {
    const button = document.createElement('button'); button.textContent = 'Выбрать пример Examples'; button.addEventListener('click', action(() => selectNode({ id: example.collectionId, kind: 'examples', label: 'Examples' }))); $('records').append(button);
  }
  setNext(result.nextCursor ? () => scenario(node, exampleId, result.nextCursor) : null);
}
/** Раскрывает локальное дерево с ограничением числа отрисованных карточек. */
async function children(node, cursor = null) {
  const result = await query('schema', { nodeId: node.id, cursor, limit: 30 });
  const full = scene.add(result.items);
  $('records').replaceChildren();
  for (const child of result.items.filter(child => child.id !== node.id)) {
    const button = document.createElement('button'); button.className = 'map-link'; button.textContent = `${child.label || child.name} · ${child.kind}`;
    button.addEventListener('click', action(() => selectNode(child))); $('records').append(button);
  }
  if (full) $('selection-info').textContent = 'На карте 120 узлов. Остальные доступны в списке и через MCP.';
  setNext(result.nextCursor ? () => children(node, result.nextCursor) : null);
}
/** Выбирает представление по смыслу узла, не помещая записи в схему. */
async function selectNode(node) {
  try {
    if (await keys.select(node)) return;
    if (node.kind === 'data-record') { await inspectRecord(node); return; }
    $('selection-title').textContent = node.label || node.name || node.kind;
    $('selection-info').textContent = node.description || '';
    if (['collection', 'examples', 'data-table'].includes(node.kind)) { await children(node); await records(node); }
    else if (['scenario', 'scenario-outline'].includes(node.kind)) { await children(node); await scenario(node); }
    else await children(node);
  } catch (error) { message(error); }
}
/** Возвращает структуру без импорта и без накопления узлов прежнего пути. */
async function restoreStructure() {
  const result = await query('schema', { nodeId: 'data:' + active.id, limit: 50 });
  if ($('key-select').value) return;
  scene.reset(); scene.add(result.items); await relations();
  $('selection-title').textContent = 'Файлы, коллекции и сценарии';
  $('records').replaceChildren(); $('selection-info').textContent = ''; setNext(null);
}
/** Путь остаётся на карте, а его строки можно сравнить в таблице ниже. */
function inspectKeyPath(result) {
  if (!result) {
    $('selection-title').textContent = 'Выберите значение ключа';
    $('records').replaceChildren(); $('selection-info').textContent = ''; setNext(null); return;
  }
  const rows = result.nodes.filter(node => node.kind === 'data-record');
  const columns = [...new Set(rows.flatMap(item => Object.keys(item.values || {})))].slice(0, 12);
  $('selection-title').textContent = 'Строки выбранного пути';
  $('selection-info').textContent = result.truncated ? result.warning : 'Показаны соответствующие строки и связанные данные. Совпадение значений не доказывает бизнес-смысл.';
  $('records').replaceChildren(table(rows, [...columns, 'Источник'], inspectRecord)); setNext(null);
}
/** Читает выбранную запись отдельно, сохраняя колонки и изоляцию пути. */
async function inspectRecord(node) {
  const result = await query('record', { recordId: node.id });
  const row = result.items[0];
  $('selection-title').textContent = node.label || 'Запись';
  $('records').replaceChildren(table([row], [...Object.keys(row.values || {}).slice(0, 12), 'Источник']));
  $('selection-info').textContent = row.contentTruncated ? 'Запись сокращена. Полные выбранные поля доступны через MCP.' : 'Значения из сохранённого снимка.';
  setNext(null);
}
/** Показывает ограниченный путь записей с явным продолжением fan-out. */
async function trace(item, cursor = null) {
  const result = await query('trace', { recordId: item.id, cursor, maxNodes: 30, maxDepth: 2 });
  $('selection-title').textContent = 'Связи выбранной записи'; $('records').replaceChildren();
  const pre = document.createElement('pre'); pre.textContent = JSON.stringify({ nodes: result.nodes, links: result.links }, null, 2); $('records').append(pre);
  $('selection-info').textContent = result.truncated ? 'Показана часть связей. Продолжите следующей страницей или через continuations в MCP.' : 'Родительские записи и совпадения ключей.';
  setNext(result.nextCursor ? () => trace(item, result.nextCursor) : null);
}
/** Отличает кандидатов совпадения значений от заданных пользователем правил. */
async function relations(cursor = null) {
  const result = await query('relations', { cursor, limit: 30 });
  scene.setRelations(result.items);
  $('relation-list').replaceChildren();
  for (const relation of result.items) {
    const article = document.createElement('article');
    article.textContent = `${relation.sourceFile || relation.sourceCollection} ${relation.sourceField} → ${relation.targetFile || relation.targetCollection} ${relation.targetField} · ${relation.status === 'candidate' ? 'кандидат' : 'заданное правило'} · совпало ${relation.coverage.matched} из ${relation.coverage.nonNullKeys}; без совпадения: ${relation.coverage.unmatched}`;
    $('relation-list').append(article);
  }
  moreRelations = result.nextCursor; $('more-relations').hidden = !moreRelations;
}
$('discover-form').addEventListener('submit', action(discover)); $('create-form').addEventListener('submit', action(create));
$('select-files').addEventListener('click', () => $('files').querySelectorAll('input').forEach(input => { input.checked = true; }));
$('clear-files').addEventListener('click', () => $('files').querySelectorAll('input').forEach(input => { input.checked = false; }));
$('refresh').addEventListener('click', action(async () => {
  if (!active) return;
  const result = await request(`/api/data-maps/${active.id}/refresh`, {});
  if (result.job) await monitor(result.job.id); else { showFreshness(result.map); message('Хеши проверены. Обновление не требуется.'); }
}));
$('cancel').addEventListener('click', action(() => jobId && request(`/api/data-jobs/${jobId}/cancel`, {})));
$('more').addEventListener('click', action(() => next?.())); $('more-relations').addEventListener('click', action(() => relations(moreRelations)));
$('svg-export').addEventListener('click', () => scene.export());
window.addEventListener('pagehide', () => { generation++; jobId = null; });
await action(async () => {
  await catalog();
  if (projectId) { $('creation').open = true; $('root').required = false; $('root').disabled = true; $('root').placeholder = 'Каталог выбранного проекта'; await discover(); }
  if (params.get('map')) await openMap(params.get('map'));
  if (params.get('saved') && active) {
    const result = await request(`/api/data-maps/${active.id}/views/${encodeURIComponent(params.get('saved'))}`);
    showFreshness(result); $('selection-title').textContent = 'Сохранённый исторический вид';
    const pre = document.createElement('pre'); pre.textContent = JSON.stringify(result, null, 2); $('records').replaceChildren(pre);
  }
})();
