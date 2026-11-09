/**
 * Offline Atlas guide and fingerprint-bound mod wiki, composed with the host shell.
 */
import {el} from './dom.js';
import {requestJson} from './bounded-request.js';
import {projectWikiHref, renderProjectWiki, wikiHeadingId} from './project-wiki-content.js';
import {ProjectWikiNavigation} from './project-wiki-navigation.js';

const UI = Object.freeze({
  guideLoading: 'Загружаем встроенное руководство…',
  guideInvalid: 'Неверный пакет wiki. Обновите ресурсы Atlas.',
  catalogLoading: 'Загружаем страницы модов…',
  catalogReady: 'Страницы модов распределены по разделам Atlas.',
  catalogEmpty: 'Установленные моды пока не добавили wiki.',
  catalogError: 'Wiki модов сейчас недоступны. Встроенное руководство можно читать ниже.',
  catalogInvalid: 'Каталог wiki модов не соответствует встроенному руководству. Обновите ресурсы и повторите.',
  pageLoading: 'Читаем документацию мода…',
  pageInvalid: 'Ответ относится к другой странице или версии мода. Обновите каталог и повторите.',
  pageChanged: 'Версия мода изменилась. Обновите каталог, затем откройте страницу ещё раз.',
  pageMissing: 'Документ не найден. Выберите другую страницу или вернитесь к разделу Atlas.',
  pageUnavailable: 'Не удалось прочитать страницу мода. Можно повторить запрос или открыть встроенное руководство.',
});
const LOCALE = Object.freeze({locale: 'ru', availableLocales: ['ru'], fallback: 'ru'});
const examples = {
  Architecture: [['Приложение: create_app', 'atlas_server_create_app'], ['Хранилище: ProjectStore._build', 'atlas_store_projectstore_build'], ['MCP: create_mcp', 'atlas_agent_mcp_create_mcp']],
  'Walkthrough-Map': [['Публикация: ProjectStore._build', 'atlas_store_projectstore_build'], ['Адаптер: build_graph', 'atlas_integrations_graphify_adapter_build_graph']],
  'Walkthrough-Agents': [['Инструменты: create_mcp', 'atlas_agent_mcp_create_mcp'], ['Документ: WikiService.document', 'atlas_wiki_service_wikiservice_document']],
  'Walkthrough-Tests': [['Контракт: test_dataset_page.py', 'atlas_test_dataset_page']],
};
const nodes = Object.fromEntries(['state', 'retry', 'article', 'pages', 'examples', 'next', 'filter',
  'breadcrumbs', 'mod-state', 'mod-retry', 'catalog-retry', 'catalog-load', 'contents', 'count'].map(name => [name, document.getElementById('wiki-' + name)]));
let bundle;
let catalog;
let catalogState = 'loading';
let revision = 0;
let disposed = false;
let pageController;
let catalogController;
let guideController;
let catalogPending = false;
let renderedKey;
const documents = new Map();
const positions = new Map();
const interfaceController = new AbortController();
const listenerOptions = {signal: interfaceController.signal};
const navigation = new ProjectWikiNavigation({root: nodes.pages, search: nodes.filter,
  contents: nodes.contents, count: nodes.count, makeLink: pageLink, getRoute: route});

/**
 * Decode only owned route fields; catalogue identities validate their meaning later.
 */
function route() {
  const query = new URLSearchParams(location.search);
  return {sectionId: query.get('page') || 'Home', modId: query.get('mod'), pageId: query.get('modPage')};
}

/**
 * Keep reading positions separate from document text and provider response caches.
 */
function routeKey(value = route()) {
  return [value.sectionId, value.modId || '', value.pageId || ''].join('\u0000');
}

/**
 * Bound catalogue data before creating links; Markdown is never part of this response.
 * @param {object} data API catalog to validate against the loaded guide sections.
 * @returns {object} The unchanged catalog; malformed/unknown identities throw an Error.
 */
function validateCatalog(data) {
  const knownSections = new Set(bundle.pages.map(page => page.id));
  if (data.apiVersion !== 1 || !Array.isArray(data.mods) || data.mods.length > 256) throw new Error(UI.catalogInvalid);
  const ids = new Set();
  for (const mod of data.mods) {
    if (typeof mod.id !== 'string' || !mod.id || mod.id.length > 128 || ids.has(mod.id)
        || typeof mod.name !== 'string' || typeof mod.fingerprint !== 'string' || !mod.fingerprint
        || !knownSections.has(mod.section) || !Array.isArray(mod.pages) || mod.pages.length > 16) throw new Error(UI.catalogInvalid);
    ids.add(mod.id);
    const pages = new Set();
    for (const page of mod.pages) {
      if (typeof page.id !== 'string' || !page.id || pages.has(page.id) || typeof page.title !== 'string'
          || typeof page.path !== 'string' || !page.path.toLowerCase().endsWith('.md') || page.path.startsWith('/')
          || /[\\\u0000-\u001f]/.test(page.path) || page.path.split('/').includes('..')) throw new Error(UI.catalogInvalid);
      pages.add(page.id);
    }
  }
  return data;
}

/**
 * Fence document identity against the exact catalogue entry, including source path.
 * @param {object} data API document response, including Markdown text and source identity.
 * @param {object} mod Exact catalog mod, including its fingerprint and origin.
 * @param {object} page Exact catalog page, including its relative source path.
 * @returns {object} The unchanged document; mismatch or oversized text throws status409.
 */
function validateDocument(data, mod, page) {
  if (data.modId !== mod.id || data.pageId !== page.id || data.section !== mod.section
      || data.fingerprint !== mod.fingerprint || data.source?.path !== page.path || data.source?.origin !== mod.origin
      || data.format !== 'markdown' || typeof data.text !== 'string'
      || new TextEncoder().encode(data.text).length > 64 * 1024) {
    const error = new Error(UI.pageInvalid); error.status = 409; throw error;
  }
  return data;
}

/**
 * Resolve an Atlas code example without executing the analyzed repository.
 * @param {HTMLButtonElement} button Trigger disabled during the lookup.
 * @param {string} nodeId Exact expected symbol ID in the single code-atlas snapshot.
 * @param {HTMLElement} status Destination for lookup/recovery messages.
 * @param {number} expectedRevision Reader revision fencing stale lookup responses.
 * @returns {Promise<void>} Navigates on a verified match; failures become status text.
 */
async function openExample(button, nodeId, status, expectedRevision) {
  button.disabled = true;
  status.textContent = 'Проверяем символ в текущей карте Atlas…';
  try {
    const {projects = []} = await requestJson('/api/projects', {}, {timeoutMs: 10000});
    const matches = projects.filter(project => project.name === 'code-atlas' && project.snapshotId);
    if (matches.length !== 1) throw new Error('Добавьте репозиторий code-atlas в «Код» или выберите карту вручную.');
    const project = matches[0];
    const result = await requestJson(`/api/projects/${encodeURIComponent(project.id)}/search-nodes`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({snapshotId: project.snapshotId, query: nodeId, limit: 120, excludeKinds: ['field']}),
    }, {timeoutMs: 10000});
    if (expectedRevision !== revision || disposed) return;
    if (result.snapshotId !== project.snapshotId || !result.nodes?.some(node => node.id === nodeId)) throw new Error('Символ отсутствует в текущем снимке. Обновите карту или найдите файл по имени.');
    location.assign('/?' + new URLSearchParams({repo: project.id, view: 'architecture', level: 'projects', node: nodeId,
      path: nodeId, display: 'flow', scale: 'repositories', algorithm: 'natural'}));
  } catch (error) { if (expectedRevision === revision && !disposed) status.textContent = error.message; }
  finally { button.disabled = false; }
}

/**
 * Keep map examples optional so static author text never waits for API availability.
 */
function showExamples(pageId) {
  nodes.examples.replaceChildren();
  const targets = examples[pageId] || [];
  nodes.examples.hidden = !targets.length;
  if (!targets.length) return;
  const status = el('p', 'Переходы к символам самой карты Atlas. Открытие не запускает код проекта.');
  status.setAttribute('role', 'status'); nodes.examples.append(el('h2', 'Проверить на карте Atlas'), status);
  const expectedRevision = revision;
  for (const [label, nodeId] of targets) {
    const button = el('button', label); button.type = 'button'; button.title = nodeId;
    button.addEventListener('click', () => openExample(button, nodeId, status, expectedRevision), listenerOptions);
    nodes.examples.append(button);
  }
}

/**
 * Create one named route link without interpolating provider prose as HTML.
 */
function pageLink(label, pageId, mod, page) {
  const link = el('a', label);
  link.href = projectWikiHref(pageId, {modId: mod?.id, modPageId: page?.id, context: location.search});
  link.dataset.search = [label, mod?.name || '', page?.path || ''].join(' ').toLocaleLowerCase(LOCALE.locale);
  const selected = route();
  if (selected.sectionId === pageId && (mod ? selected.modId === mod.id && selected.pageId === page.id : !selected.modId)) link.setAttribute('aria-current', 'page');
  return link;
}

/**
 * Render task-grouped contents without coupling navigation state to API or article loading.
 */
function showNavigation() {
  navigation.render(bundle.pages, catalog?.mods || []);
}

/**
 * Search navigation without replacing the article, selected identity or reading position.
 */
function filterPages() {
  navigation.filter({expandContents: true});
}

/**
 * Keep section ancestry visible independently of disclosure/search state.
 */
function showBreadcrumbs(section, mod, page) {
  nodes.breadcrumbs.replaceChildren();
  const items = [[pageLink('Wiki Atlas', 'Home'), false]];
  if (section.id !== 'Home' || mod) items.push([pageLink(section.title, section.id), !mod]);
  if (mod) items.push([el('span', mod.name), false], [el('span', page.title), true]);
  if (!mod && section.id === 'Home') items[0][1] = true;
  for (const [item, current] of items) {
    if (nodes.breadcrumbs.childNodes.length) { const separator = el('span', '›'); separator.setAttribute('aria-hidden', 'true'); nodes.breadcrumbs.append(separator); }
    if (current) { const text = el('span', item.textContent); text.setAttribute('aria-current', 'page'); nodes.breadcrumbs.append(text); }
    else nodes.breadcrumbs.append(item);
  }
}

/**
 * Cache only a bounded set of exact fingerprint-bound document responses.
 */
function rememberDocument(key, value) {
  documents.delete(key); documents.set(key, value);
  while (documents.size > 32) documents.delete(documents.keys().next().value);
}

/**
 * Restore an explicit anchor or prior history position without stealing search focus.
 */
function finishNavigation({focus = false, restore = false} = {}) {
  nodes.article.setAttribute('aria-busy', 'false');
  renderedKey = routeKey();
  if (focus) nodes.article.focus({preventScroll: true});
  requestAnimationFrame(() => {
    if (disposed || renderedKey !== routeKey()) return;
    let fragment;
    try { fragment = decodeURIComponent(location.hash.slice(1)); } catch { fragment = ''; }
    if (fragment) {
      const heading = document.getElementById(fragment) || document.getElementById(wikiHeadingId(fragment));
      if (heading && nodes.article.contains(heading)) { heading.scrollIntoView(); return; }
    }
    const saved = history.state?.wikiReader;
    let top = 0;
    if (restore) top = saved?.key === renderedKey ? saved.scrollY : positions.get(renderedKey) || 0;
    window.scrollTo({top, behavior: 'instant'});
  });
}

/**
 * Offer recovery while keeping the entire bundled guide reachable in navigation.
 */
function showPageError(error, section, options) {
  const changed = error.status === 409;
  let message = UI.pageUnavailable;
  if (changed) message = UI.pageChanged;
  else if (error.status === 404) message = UI.pageMissing;
  nodes.state.textContent = message;
  nodes.article.append(el('p', error.message), pageLink('Открыть раздел Atlas: ' + section.title, section.id));
  nodes['mod-retry'].hidden = changed;
  nodes['catalog-retry'].hidden = !changed;
  finishNavigation(options);
}

/**
 * Render one selected static or mod page; stale/aborted responses cannot replace a later route.
 * @param {object} options Optional focus and restore flags; both default to false.
 * @returns {Promise<void>} Updates article/navigation state; provider errors offer recovery.
 */
async function showCurrent(options = {}) {
  if (!bundle || disposed) return;
  pageController?.abort(); pageController = new AbortController();
  const expectedRevision = ++revision;
  const selected = route();
  const section = bundle.pages.find(page => page.id === selected.sectionId);
  nodes['mod-retry'].hidden = true; nodes['catalog-retry'].hidden = true;
  nodes.next.replaceChildren(); showExamples(null); showNavigation();
  if (!section) {
    nodes.article.replaceChildren(el('h1', 'Раздел не найден'), el('p', 'Выберите страницу в оглавлении.'));
    nodes.breadcrumbs.replaceChildren(pageLink('Wiki Atlas', 'Home'));
    document.title = 'Раздел не найден · Wiki Atlas';
    nodes.state.textContent = 'Неизвестная страница wiki.'; finishNavigation(options); return;
  }
  if (!selected.modId) {
    showBreadcrumbs(section);
    document.title = `${section.title} · Wiki Atlas`;
    const reviewed = section.reviewedAt ? `страница сверена ${section.reviewedAt}` : `пакет обновлён ${bundle.reviewedAt}`;
    nodes.state.textContent = `Авторское руководство · ${reviewed} · ${section.source} · доступно без API`;
    renderProjectWiki(nodes.article, section.text, {pages: new Set(bundle.pages.map(page => page.id)), sectionId: section.id, context: location.search});
    showExamples(section.id);
    const index = bundle.pages.indexOf(section);
    for (const [position, label] of [[index - 1, '←'], [index + 1, 'Далее →']]) {
      const neighbor = bundle.pages[position];
      if (neighbor) nodes.next.append(pageLink(`${label} ${neighbor.title}`, neighbor.id));
    }
    finishNavigation(options); return;
  }
  const mod = catalog?.mods.find(item => item.id === selected.modId && item.section === section.id);
  const page = mod?.pages.find(item => item.id === selected.pageId);
  nodes.article.replaceChildren(el('h1', page?.title || 'Документация мода'));
  nodes.article.setAttribute('aria-busy', 'true');
  if (!mod || !page) {
    document.title = `Документация мода · ${section.title} · Wiki Atlas`;
    showBreadcrumbs(section);
    const error = new Error(catalogState === 'loading' ? UI.catalogLoading : catalogState === 'error' ? UI.catalogError : UI.pageMissing);
    if (catalogState === 'ready') error.status = 404;
    if (catalogState === 'loading') { nodes.state.textContent = UI.catalogLoading; return; }
    showPageError(error, section, options); nodes['mod-retry'].hidden = true; return;
  }
  showBreadcrumbs(section, mod, page);
  document.title = `${page.title} · ${mod.name} · Wiki Atlas`;
  nodes.state.textContent = UI.pageLoading;
  const key = [mod.id, page.id, mod.fingerprint].join('\u0000');
  try {
    let data = documents.get(key);
    if (!data) {
      const path = `/api/mods/${encodeURIComponent(mod.id)}/wiki/${encodeURIComponent(page.id)}`;
      data = await requestJson(path + '?' + new URLSearchParams({fingerprint: mod.fingerprint}), {signal: pageController.signal}, {timeoutMs: 10000});
      data = validateDocument(data, mod, page);
    }
    if (expectedRevision !== revision || disposed) return;
    rememberDocument(key, data);
    renderProjectWiki(nodes.article, data.text, {mod, currentPath: page.path, pageId: page.id, sectionId: section.id,
      pages: new Set(bundle.pages.map(item => item.id)), context: location.search});
    const statusLabels = {auto: 'автовыбор по файлам', changed: 'версия изменилась',
      'missing-dependency': 'недоступна зависимость', disabled: 'выключен', enabled: 'включён'};
    const activation = statusLabels[mod.status] || (mod.enabled ? 'включён' : 'выключен');
    nodes.state.textContent = `${mod.name} · ${mod.version} · ${activation} · ${data.source.path}`;
    const index = mod.pages.indexOf(page);
    for (const [position, label] of [[index - 1, '←'], [index + 1, 'Далее →']]) {
      const neighbor = mod.pages[position];
      if (neighbor) nodes.next.append(pageLink(`${label} ${neighbor.title}`, section.id, mod, neighbor));
    }
    finishNavigation(options);
  } catch (error) {
    if (expectedRevision === revision && !disposed && error.name !== 'AbortError') showPageError(error, section, options);
  }
}

/**
 * Persist only the current scroll position, leaving unrelated host history state intact.
 */
function savePosition() {
  if (!renderedKey || renderedKey !== routeKey()) return;
  positions.delete(renderedKey);
  positions.set(renderedKey, window.scrollY);
  while (positions.size > 64) positions.delete(positions.keys().next().value);
  history.replaceState({...history.state, wikiReader: {key: renderedKey, scrollY: window.scrollY}}, '', location.href);
}

/**
 * Internal document links use real URLs and browser history, preserving modified-click behavior.
 */
function navigate(event) {
  const link = event.target.closest?.('a');
  if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target === '_blank' || link.hasAttribute('download')) return;
  const url = new URL(link.href, location.href);
  if (url.origin !== location.origin || url.pathname !== '/wiki.html') return;
  event.preventDefault(); savePosition();
  history.pushState({wikiReader: {key: null, scrollY: 0}}, '', url.href);
  showCurrent({focus: true});
  navigation.afterNavigate();
}

/**
 * Catalogue loading is independent: an API failure never removes bundled author pages.
 */
async function loadCatalog() {
  if (!bundle || catalogPending || disposed) return;
  savePosition();
  catalogPending = true; catalogState = 'loading';
  nodes['mod-state'].textContent = UI.catalogLoading;
  nodes['catalog-load'].setAttribute('aria-disabled', 'true');
  nodes['catalog-load'].setAttribute('aria-busy', 'true');
  catalogController?.abort(); catalogController = new AbortController();
  try {
    const data = await requestJson('/api/mods/wiki', {signal: catalogController.signal}, {timeoutMs: 10000});
    if (disposed) return;
    catalog = validateCatalog(data); catalogState = 'ready';
    nodes['mod-state'].textContent = catalog.mods.length ? UI.catalogReady + (catalog.truncated ? ' Показана часть каталога.' : '') : UI.catalogEmpty;
    nodes['catalog-load'].textContent = 'Обновить wiki модов';
    showNavigation();
    if (route().modId) await showCurrent({restore: true});
  } catch (error) {
    if (disposed || error.name === 'AbortError') return;
    catalogState = 'error'; nodes['mod-state'].textContent = UI.catalogError;
    nodes['catalog-load'].textContent = 'Повторить загрузку wiki модов';
    if (route().modId) await showCurrent({restore: true});
  } finally {
    catalogPending = false;
    nodes['catalog-load'].setAttribute('aria-disabled', 'false'); nodes['catalog-load'].setAttribute('aria-busy', 'false');
  }
}

/**
 * Load only the bundled static package before consulting optional mod APIs.
 */
async function loadGuide() {
  nodes.retry.hidden = true; nodes.state.textContent = UI.guideLoading;
  guideController?.abort(); guideController = new AbortController();
  try {
    const data = await requestJson('/project-wiki.json', {signal: guideController.signal}, {timeoutMs: 10000});
    if (disposed) return;
    if (data.version !== 1 || !Array.isArray(data.pages) || !data.pages.length) throw new Error(UI.guideInvalid);
    bundle = data; await showCurrent(); loadCatalog();
  } catch (error) {
    if (disposed || error.name === 'AbortError') return;
    nodes.state.textContent = error.message; nodes.retry.hidden = false;
  }
}

/**
 * Release active requests and owned navigation listeners when the page leaves the document.
 * @returns {void} Further route updates are ignored; no provider data is persisted.
 */
export function disposeProjectWiki() {
  disposed = true; revision++;
  pageController?.abort(); catalogController?.abort(); guideController?.abort();
  interfaceController.abort(); navigation.dispose(); documents.clear(); positions.clear();
}
const onPopState = () => showCurrent({restore: true});
nodes.retry.addEventListener('click', loadGuide, listenerOptions);
nodes['mod-retry'].addEventListener('click', () => showCurrent({focus: true}), listenerOptions);
nodes['catalog-retry'].addEventListener('click', loadCatalog, listenerOptions);
nodes['catalog-load'].addEventListener('click', loadCatalog, listenerOptions);
nodes.filter.addEventListener('input', filterPages, listenerOptions);
document.addEventListener('click', navigate, listenerOptions); window.addEventListener('popstate', onPopState, listenerOptions);
window.addEventListener('pagehide', event => { if (!event.persisted) disposeProjectWiki(); }, listenerOptions);
await loadGuide();
