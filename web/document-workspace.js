import {withLoading} from './loading-state.js';
import {requestLoadingLabel} from './loading-labels.js';
/** Документный workspace: только явный импорт, навигация читает immutable indexes. */
import { DataScene } from './data-scene.js';

const byId = id => document.getElementById(id);
const state = { map: null, snapshot: null, project: '', manifest: null, selected: null, parent: null,
  document: null, cursor: null, search: '', mode: 'tree', generation: 0, selection: 0,
  job: null, pollGeneration: 0, contentOffset: 0, sourceRoot: null, sourceProject: null,
  catalogCursor: null, catalogGeneration: 0, fileCursor: null, discoveryScope: null, discoveryGeneration: 0, savedView: null, restoreRequest: 0 };
const scene = new DataScene(byId('graph'), node => perform(() => selectNode(node.id, node.documentId)));

/** Содержимое документа попадает в DOM только как textContent. */
function element(tag, text = '', className = '') {
  const result = document.createElement(tag);
  result.textContent = text;
  if (className) result.className = className;
  return result;
}

/** Все HTTP ошибки становятся видимыми сообщениями, без необработанных Promise. */
async function perform(action) {
  try { await action(); }
  catch (error) { byId('document-message').textContent = error.message || String(error); }
}

/**
 * Читает локальный JSON с неблокирующим учётом всей операции.
 *
 * path — существующий API-маршрут; body задаёт POST, отсутствие body — GET.
 * Возвращает декодированный ответ; исходные сообщения и типы ошибок этой
 * страницы сохранены. Токен освобождается после JSON либо ошибки в finally.
 */
async function api(path, body) {
  return withLoading(requestLoadingLabel(path, {method: body === undefined ? 'GET' : 'POST'}), async () => {
    const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30000) });
    let result;
    try { result = await response.json(); }
    catch { throw new Error(`Ответ сервера не является JSON (${response.status}).`); }
    if (!response.ok) {
      const error = new Error(typeof result.error === 'string' ? result.error : JSON.stringify(result.detail || result));
      error.code = result.code; throw error;
    }
    return result;
  });
}

/** Scope и snapshot входят в каждый запрос; переключение UI не делает ensure. */
function query(operation, values = {}) {
  if (!state.map || !state.snapshot) throw new Error('Сначала выберите опубликованный снимок.');
  return api(`/api/document-maps/${encodeURIComponent(state.map)}/query/${operation}`, {
    snapshotId: state.snapshot, ...(state.project ? { projectId: state.project } : {}), ...values });
}

/** URL содержит сохранённый snapshot и узел, поэтому Back/Forward восстанавливают чтение. */
function address(push = true) {
  const url = new URL(location.href);
  const previousMap = url.searchParams.get('map');
  for (const [key, value] of [['map', state.map], ['snapshot', state.snapshot], ['node', state.selected?.id]]) {
    if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
  }
  if (push || previousMap !== state.map || (url.searchParams.has('view') && state.savedView?.snapshotId !== state.snapshot)) {
    url.searchParams.delete('view');
    state.savedView = null;
  }
  if (url.href !== location.href) history[push ? 'pushState' : 'replaceState']({}, '', url);
}

/** Список карт берётся из каталога, без discovery или повторного парсинга. */
async function catalog(cursor = null) {
  const generation = cursor ? state.catalogGeneration : ++state.catalogGeneration;
  const parameters = new URLSearchParams({ limit: '50' });
  if (cursor) parameters.set('cursor', cursor);
  const result = await api('/api/document-maps?' + parameters);
  if (generation !== state.catalogGeneration) return;
  state.catalogCursor = result.nextCursor;
  byId('document-catalog-more').hidden = !state.catalogCursor;
  const target = byId('document-catalog');
  if (!cursor) target.replaceChildren();
  for (const item of result.maps || []) {
    const button = element('button', item.title || item.id, 'map-link');
    button.append(element('small', `${item.status || 'нет снимка'}${item.snapshotId ? ' · ' + item.snapshotId.slice(0, 10) : ''}`));
    button.addEventListener('click', () => perform(() => loadMap(item.id)));
    target.append(button);
  }
  if (!result.maps?.length) target.append(element('p', 'Сохранённых карт пока нет.', 'muted'));
}

/** Статусы полноты и свежести показываются независимо. */
function showManifest(manifest) {
  byId('document-title').textContent = manifest.title || 'Документы';
  const freshness = manifest.freshness?.status || 'не проверена';
  byId('document-status').textContent = `Снимок ${manifest.snapshotId.slice(0, 12)} · покрытие: ${manifest.coverage?.status || 'unknown'} · источник: ${freshness}`;
  byId('document-coverage').textContent = JSON.stringify({ coverage: manifest.coverage,
    failures: manifest.failures || [], freshness: manifest.freshness, truncated: manifest.truncated || false }, null, 2);
  for (const id of ['document-tools', 'document-refresh', 'document-save-view']) byId(id).hidden = false;
}

/** При смене карты устаревшие запросы не заменяют новый UI. */
async function loadMap(mapId, snapshotId = null, nodeId = null, push = true, savedView = null) {
  const generation = ++state.generation;
  state.restoreRequest++;
  state.selection++; state.pollGeneration++;
  byId('document-message').textContent = '';
  byId('document-progress').textContent = 'Загрузка сохранённого снимка…';
  const suffix = snapshotId ? '?snapshotId=' + encodeURIComponent(snapshotId) : '';
  let manifest;
  try { manifest = await api(`/api/document-maps/${encodeURIComponent(mapId)}${suffix}`); }
  catch (error) {
    if (error.code !== 'NOT_READY') throw error;
    const info = await api(`/api/document-maps/${encodeURIComponent(mapId)}/info`);
    if (generation !== state.generation) return;
    Object.assign(state, { map: mapId, snapshot: null, selected: null, project: info.projectId || '' });
    resetInspector(); scene.reset(); byId('document-tree').replaceChildren();
    byId('document-tools').hidden = true; byId('document-save-view').hidden = true;
    byId('document-refresh').hidden = false; byId('document-title').textContent = info.title || 'Документы';
    byId('document-status').textContent = ''; address(push);
    byId('document-progress').textContent = 'У карты пока нет опубликованного снимка.' + (info.error ? ' ' + info.error : '');
    if (info.jobId && ['queued', 'running'].includes(info.status)) await monitorJob(info.jobId, mapId);
    return;
  }
  if (generation !== state.generation) return;
  Object.assign(state, { map: mapId, snapshot: manifest.snapshotId, project: manifest.projectId || '', manifest,
    selected: null, parent: null, document: null, cursor: null, search: '', savedView });
  resetInspector(); showManifest(manifest); address(push);
  byId('document-progress').textContent = '';
  byId('document-search-text').value = '';
  if (nodeId) await selectNode(nodeId, null, false);
  else await loadTree();
  if (savedView) {
    const documents = new Set((savedView.nodes || []).map(node => node.documentId));
    if (!state.document && documents.size === 1) state.document = [...documents][0];
    await mode('graph');
    byId('document-title').textContent = savedView.title || manifest.title;
  } else if (state.mode === 'graph') await showGraph();
}

/** Пустой inspector не сохраняет координаты узла из другой ревизии. */
function resetInspector() {
  byId('document-node-title').textContent = 'Выберите узел';
  for (const id of ['document-node-kind', 'document-preview', 'document-locator', 'document-attributes', 'document-content-info', 'document-coverage']) byId(id).textContent = '';
  for (const id of ['document-content', 'document-content-more', 'document-table-panel', 'document-parent']) byId(id).hidden = true;
}

/** Одна страница дерева или результатов поиска; остаток доступен явной кнопкой. */
async function loadTree(cursor = null) {
  const generation = state.generation, selection = state.selection;
  const values = { limit: 50, ...(state.document ? { documentId: state.document } : {}), ...(cursor ? { cursor } : {}) };
  if (state.search) values.query = state.search;
  else if (state.parent) values.parentId = state.parent;
  const result = await query(state.search ? 'search' : 'tree', values);
  if (generation !== state.generation || selection !== state.selection) return;
  state.cursor = result.nextCursor;
  byId('document-more').hidden = !state.cursor;
  byId('document-list-label').textContent = state.search ? `Результаты поиска: ${state.search}` : state.parent ? 'Дочерние узлы выбранного раздела' : 'Документы снимка';
  const target = byId('document-tree'); target.replaceChildren();
  for (const node of result.items || []) {
    const button = element('button', node.label || node.preview?.slice(0, 120) || node.kind, 'document-node');
    button.append(element('small', `${node.kind} · ${node.sourcePath || ''}`));
    if (node.preview) button.append(element('small', node.preview.slice(0, 180)));
    button.addEventListener('click', () => perform(() => selectNode(node.id, node.documentId)));
    target.append(button);
  }
  if (!result.items?.length) target.append(element('p', state.search ? 'Совпадений нет.' : 'Дочерних узлов нет. Текст и координаты доступны справа.', 'document-empty'));
}

/** Inspector, дерево и content принадлежат одному выбранному узлу и snapshot. */
async function selectNode(nodeId, documentId = null, push = true) {
  const generation = state.generation, selection = ++state.selection;
  const node = await query('node', { nodeId, ...(documentId ? { documentId } : {}) });
  if (generation !== state.generation || selection !== state.selection) return;
  state.selected = node; state.parent = node.id; state.document = node.documentId;
  state.search = ''; state.contentOffset = 0;
  byId('document-search-text').value = '';
  byId('document-node-title').textContent = node.label || node.kind;
  byId('document-node-kind').textContent = `${node.kind} · ${node.sourcePath || ''}`;
  byId('document-preview').textContent = node.text || node.preview || '';
  byId('document-locator').textContent = JSON.stringify({ sourcePath: node.sourcePath, sourceHash: node.sourceHash,
    revisionId: node.revisionId, documentId: node.documentId, sourceLocator: node.sourceLocator }, null, 2);
  byId('document-attributes').textContent = JSON.stringify(node.attributes || {}, null, 2);
  const binding = state.manifest.documents?.find(item => item.documentId === node.documentId);
  byId('document-coverage').textContent = JSON.stringify({ coverage: binding?.coverage || state.manifest.coverage,
    warnings: binding?.warnings || [], freshness: state.manifest.freshness }, null, 2);
  byId('document-content').hidden = false; byId('document-content-more').hidden = true;
  byId('document-content-info').textContent = node.contentTruncated ? 'Показано начало текста. «Читать текст» открывает сохранённые части.' : '';
  byId('document-parent').hidden = !node.parentId;
  const isTable = ['table', 'sheet'].includes(node.kind);
  byId('document-table-panel').hidden = !isTable;
  address(push);
  await loadTree();
  if (isTable) {
    byId('document-row').value = '1'; byId('document-column').value = '1';
    await showTable();
  }
}

/** Граф ограничен сервером; containment рисует существующая DataScene. */
async function showGraph() {
  const generation = state.generation, selection = state.selection;
  const result = await query('graph', { maxNodes: 120, ...(state.document ? { documentId: state.document } : {}) });
  if (generation !== state.generation || selection !== state.selection || state.mode !== 'graph') return;
  displayGraph(result);
}

/** Сохранённый граф отображается буквально; эту функцию не сопровождает запрос к индексу. */
function displayGraph(result) {
  scene.setGraph((result.nodes || []).map(node => ({ ...node, owner: node.parentId,
    label: node.label || node.preview?.slice(0, 50) || node.kind })), (result.edges || []).filter(edge => edge.relation !== 'contains'));
  byId('document-list-label').textContent = `Граф: ${result.nodes?.length || 0} узлов${result.truncated ? ' · показана ограниченная проекция' : ''}`;
}

/** Переключение представления читает готовый индекс и не создаёт задания. */
async function mode(value) {
  state.mode = value;
  byId('document-tree').hidden = value === 'graph';
  byId('document-more').hidden = value === 'graph' || !state.cursor;
  byId('graph').hidden = value !== 'graph';
  byId('document-tree-mode').setAttribute('aria-pressed', String(value === 'tree'));
  byId('document-graph-mode').setAttribute('aria-pressed', String(value === 'graph'));
  if (value === 'graph') {
    if (state.savedView) displayGraph(state.savedView); else await showGraph();
  } else await loadTree();
}

/** Окно таблицы использует относительные координаты; исходные координаты ячеек сохранены. */
async function showTable() {
  if (!state.selected) return;
  const generation = state.generation, selection = state.selection;
  const rowCount = Number(byId('document-row-count').value), columnCount = Number(byId('document-column-count').value);
  if (rowCount * columnCount > 1000) throw new Error('Окно ограничено 1000 ячейками.');
  const result = await query('table', { nodeId: state.selected.id, documentId: state.selected.documentId,
    rowStart: Number(byId('document-row').value) - 1, columnStart: Number(byId('document-column').value) - 1,
    rowCount, columnCount });
  if (generation !== state.generation || selection !== state.selection) return;
  const target = byId('document-table'); target.replaceChildren();
  const head = element('tr');
  for (const title of ['Строка источника', 'Колонка источника', 'Значение', 'Примечание']) head.append(element('th', title));
  const header = element('thead'); header.append(head); target.append(header);
  const body = element('tbody');
  for (const cell of result.cells || []) {
    const attributes = cell.attributes || {}, row = element('tr');
    const emptyStatuses = { empty: '(пусто)', missing: '(отсутствует)', unavailable: '(недоступно)' };
    const value = cell.text || emptyStatuses[attributes.valueStatus] || (attributes.value === null ? 'null' : String(attributes.value ?? ''));
    const notes = [attributes.formula ? `Формула: ${attributes.formula}` : '', attributes.cachedValueStatus ? `Кеш: ${attributes.cachedValueStatus}; свежесть ${attributes.cacheFreshness || 'unknown'}` : '', attributes.rowSpan > 1 ? `Строк: ${attributes.rowSpan}` : '', attributes.columnSpan > 1 ? `Колонок: ${attributes.columnSpan}` : ''].filter(Boolean).join(' · ');
    for (const text of [String((attributes.row ?? 0) + 1), String((attributes.column ?? 0) + 1), value, notes]) row.append(element('td', text));
    body.append(row);
  }
  target.append(body);
  byId('document-table-info').textContent = `${result.cells?.length || 0} сохранённых ячеек. Пустые позиции не создаются. Координаты окна относительны таблице; координаты источника указаны с 1.`;
}

/** Читает отдельную bounded часть текста; запросы не накапливают весь большой документ в DOM. */
async function content(next = false) {
  if (!state.selected) return;
  const generation = state.generation, selection = state.selection;
  const result = await query('content', { nodeId: state.selected.id, documentId: state.selected.documentId,
    offset: next ? state.contentOffset : 0, limit: 4000 });
  if (generation !== state.generation || selection !== state.selection) return;
  byId('document-preview').textContent = result.text;
  state.contentOffset = result.nextOffset;
  byId('document-content-more').hidden = result.nextOffset === null;
  byId('document-content-info').textContent = `Символы ${result.totalCharacters ? result.offset + 1 : 0}–${result.offset + [...result.text].length} из ${result.totalCharacters}. Единица: Unicode codepoint.`;
}

/** Discovery сохраняет выбранный scope отдельно от открытой карты. */
async function discover(event) {
  event.preventDefault();
  const projectId = byId('document-project').value.trim(), root = byId('document-root').value.trim();
  if (!projectId && !root) throw new Error('Укажите проект или абсолютный каталог.');
  state.discoveryScope = { ...(projectId ? { projectId } : {}), ...(root ? { root } : {}),
    query: byId('document-file-filter').value.trim() };
  state.sourceRoot = root || null; state.sourceProject = projectId || null;
  state.discoveryGeneration++;
  await loadFiles();
}

/** Следующая страница сохраняет уже отмеченные checkbox и закреплённый discovery scope. */
async function loadFiles(cursor = null) {
  if (!state.discoveryScope) return;
  const generation = state.discoveryGeneration;
  const result = await api('/api/document-files', { ...state.discoveryScope, limit: 50, ...(cursor ? { cursor } : {}) });
  if (generation !== state.discoveryGeneration) return;
  const target = byId('document-files');
  if (!cursor) target.replaceChildren();
  for (const file of result.files || []) {
    const label = element('label', '', 'document-file'), checkbox = element('input');
    checkbox.type = 'checkbox'; checkbox.value = file.path;
    label.append(checkbox, element('span', `${file.path} · ${(file.sizeBytes / 1024).toFixed(1)} КиБ`)); target.append(label);
  }
  state.fileCursor = result.nextCursor;
  byId('document-files-more').hidden = !state.fileCursor;
  byId('document-build').hidden = false;
  byId('document-message').textContent = `${target.children.length} из ${result.total ?? target.children.length} файлов. Выберите до 100 файлов.`;
}

/** Только submit build и refresh имеют право отправлять ensure. */
async function build(event) {
  event.preventDefault();
  const files = [...byId('document-files').querySelectorAll('input:checked')].map(input => input.value);
  if (!files.length || files.length > 100) throw new Error('Выберите от 1 до 100 файлов.');
  const strategy = byId('document-strategy').value;
  const result = await api('/api/document-maps/ensure', { files, title: byId('document-name').value,
    ...(state.sourceRoot ? { root: state.sourceRoot } : {}), ...(state.sourceProject ? { projectId: state.sourceProject } : {}),
    ...(strategy ? { strategy } : {}) });
  state.map = result.map.id; state.snapshot = null; state.selected = null;
  resetInspector(); scene.reset(); byId('document-tree').replaceChildren();
  byId('document-tools').hidden = true; byId('document-save-view').hidden = true;
  byId('document-title').textContent = result.map.title || 'Документы'; address();
  await catalog(); await monitorJob(result.job.id, result.map.id);
}

/** Одно наблюдение ограничено 30 секундами; дальнейшая проверка запускается кнопкой. */
async function monitorJob(jobId, mapId) {
  const pollGeneration = ++state.pollGeneration, started = Date.now();
  state.job = { id: jobId, mapId };
  byId('document-cancel').hidden = false; byId('document-check-job').hidden = true;
  while (pollGeneration === state.pollGeneration) {
    const job = await api(`/api/map-jobs/${encodeURIComponent(jobId)}?mapId=${encodeURIComponent(mapId)}`);
    if (pollGeneration !== state.pollGeneration) return;
    byId('document-progress').textContent = `Импорт: ${job.status}${job.error ? ' · ' + job.error : ''}`;
    if (!['queued', 'running'].includes(job.status)) {
      byId('document-cancel').hidden = true;
      await catalog();
      if (job.status === 'completed') await loadMap(mapId, job.snapshotId);
      return;
    }
    if (Date.now() - started > 30000) {
      byId('document-check-job').hidden = false;
      byId('document-progress').textContent += ' · Импорт продолжается. Проверка доступна кнопкой.';
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 750));
  }
}

/** Сохранённый вид привязан к точному snapshot; это не запуск повторного импорта. */
async function saveView() {
  const result = await api(`/api/document-maps/${encodeURIComponent(state.map)}/views`, {
    snapshotId: state.snapshot, title: state.selected?.label || state.manifest.title || 'Документы',
    ...(state.document ? { documentId: state.document } : {}) });
  const url = new URL(location.href); url.searchParams.set('view', result.id || result.viewId);
  history.replaceState({}, '', url); byId('document-message').textContent = 'Вид сохранён. Ссылка в адресной строке закрепляет снимок.';
}

/** Восстанавливает сохранённый view или обычный map/snapshot URL. */
async function restore() {
  const restoreRequest = ++state.restoreRequest;
  const params = new URLSearchParams(location.search), map = params.get('map');
  if (!map) {
    if (state.map) {
      state.generation++; state.selection++; state.pollGeneration++;
      Object.assign(state, { map: null, snapshot: null, selected: null, document: null, parent: null, savedView: null });
      resetInspector(); scene.reset(); byId('document-tree').replaceChildren();
      byId('document-title').textContent = 'Выберите карту документов';
      for (const id of ['document-tools', 'document-refresh', 'document-save-view', 'document-cancel', 'document-more']) byId(id).hidden = true;
      byId('document-status').textContent = ''; byId('document-progress').textContent = '';
    }
    return;
  }
  let view = null;
  let snapshot = params.get('snapshot');
  if (params.get('view')) {
    view = await api(`/api/document-maps/${encodeURIComponent(map)}/views/${encodeURIComponent(params.get('view'))}`);
    snapshot = view.snapshotId;
  }
  if (restoreRequest !== state.restoreRequest) return;
  await loadMap(map, snapshot, params.get('node'), false, view);
}

/** Обработчики UI не интерпретируют строки документа и всегда перехватывают ошибки. */
function bind() {
  for (const [id, callback] of [['document-discover', discover], ['document-build', build],
    ['document-search', async event => { event.preventDefault(); state.selection++; state.search = byId('document-search-text').value.trim(); state.parent = null; state.document = null; await mode('tree'); }],
    ['document-table-form', async event => { event.preventDefault(); await showTable(); }]]) {
    byId(id).addEventListener('submit', event => perform(() => callback(event)));
  }
  const actions = {
    'document-catalog-more': () => catalog(state.catalogCursor), 'document-files-more': () => loadFiles(state.fileCursor),
    'document-more': () => loadTree(state.cursor), 'document-tree-mode': () => mode('tree'), 'document-graph-mode': () => mode('graph'),
    'document-roots': async () => { state.selection++; state.parent = null; state.document = null; state.search = ''; state.selected = null; resetInspector(); showManifest(state.manifest); address(); await mode(state.mode); },
    'document-parent': () => selectNode(state.selected.parentId, state.selected.documentId),
    'document-content': () => content(), 'document-content-more': () => content(true), 'document-save-view': saveView,
    'document-refresh': async () => { const result = await api('/api/document-maps/ensure', { mapId: state.map }); await monitorJob(result.job.id, state.map); },
    'document-cancel': async () => { if (!state.job) return; const result = await api(`/api/map-jobs/${encodeURIComponent(state.job.id)}/cancel`, { mapId: state.job.mapId }); byId('document-progress').textContent = result.status === 'completed' ? 'Импорт уже завершён.' : 'Отмена принята. Опубликованный снимок сохранён.'; byId('document-cancel').hidden = true; },
    'document-check-job': () => state.job && monitorJob(state.job.id, state.job.mapId),
    'document-select-all': () => { for (const input of byId('document-files').querySelectorAll('input')) input.checked = true; },
    'document-clear-files': () => { for (const input of byId('document-files').querySelectorAll('input')) input.checked = false; }
  };
  for (const [id, action] of Object.entries(actions)) byId(id).addEventListener('click', () => perform(action));
  window.addEventListener('popstate', () => perform(restore));
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f' && state.snapshot) {
      event.preventDefault(); byId('document-search-text').focus();
    }
  });
}

/** Capability discovery не импортирует parsers; отсутствующий профиль остаётся видимым. */
async function start() {
  bind();
  const params = new URLSearchParams(location.search);
  byId('document-project').value = params.get('repo') || params.get('projectId') || '';
  byId('document-root').value = params.get('root') || '';
  const capabilities = await api('/api/document-capabilities');
  for (const provider of capabilities.providers || []) {
    const option = element('option', `${provider.id}${provider.status !== 'available' ? ' · недоступен' : ''}`);
    option.value = provider.strategy || provider.id; option.disabled = provider.status !== 'available';
    byId('document-strategy').append(option);
  }
  await catalog(); await restore();
}
await perform(start);
