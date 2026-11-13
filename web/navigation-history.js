import { normalizeViewMode } from './view-modes.js';
import { workspaceDisplayProvider } from './display-providers.js';
import { codeProjectId } from './filters.js';
import { normalizeProgressivePlacements } from './progressive-placement.js';
import { normalizeReaderState } from './method-reader-model.js';

const ROUTE_KEYS = ['repo', 'view', 'level', 'project', 'file', 'page', 'node', 'context', 'group', 'path', 'entry', 'detail', 'isolate', 'display', 'scale', 'wiki', 'wikiNode', 'wikiOffset', 'block', 'inline', 'isolateBlock', 'algorithm', 'reading', 'methodFocus', 'overview', 'task'];
const MAX_PATH_METHODS = 600;
const MAX_INLINE_BLOCKS = 320;

/** URL хранит только адрес карты; произвольные объекты и чрезмерно длинные ID не принимаются. */
function identifier(value, limit = 2000) { return typeof value === 'string' && value.length <= limit && value ? value : null; }

/** Проверяет внешний маршрут; страницы нумеруются с нуля, неизвестные режимы открывают карту кода. */
export function normalizeRoute(value = {}) {
  if (!value || typeof value !== 'object') value = {};
  const route = {
    task: value.task==='impact'?'impact':null,
    overview: ['open','closed'].includes(value.overview) ? value.overview : null,
    repo: identifier(value.repo), algorithm: identifier(value.algorithm), view: normalizeViewMode(value.view),
    level: ['files', 'symbols'].includes(value.level) ? value.level : 'projects',
    project: identifier(value.project), file: identifier(value.file),
    page: Number.isSafeInteger(Number(value.page)) && Number(value.page) >= 0 ? Math.min(Number(value.page), 100000) : 0,
    node: identifier(value.node), context: identifier(value.context), group: identifier(value.group),
    block: identifier(value.block), isolateBlock: identifier(value.isolateBlock),
    inline: typeof value.inline === 'string' && /^[a-f0-9]{8}$/.test(value.inline) ? value.inline : null,
    isolate: identifier(value.isolate), path: identifier(value.path), entry: identifier(value.entry), display: workspaceDisplayProvider(normalizeViewMode(value.view), value.display).id,
    detail: typeof value.detail === 'string' && /^[a-f0-9]{8}$/.test(value.detail) ? value.detail : null,
    reading: ['documentation', 'methods'].includes(value.reading) ? value.reading : null,
    methodFocus: identifier(value.methodFocus),
    scale: value.scale === 'projects' ? 'projects' : 'repositories',
    wiki: typeof value.wiki === 'string' && value.wiki.length <= 256 ? identifier(value.wiki) : null,
    wikiNode: identifier(value.wikiNode),
    wikiOffset: Number.isSafeInteger(Number(value.wikiOffset)) && Number(value.wikiOffset) > 0 ? Math.min(Number(value.wikiOffset), 131072) : 0,
  };
  if (!route.project) route.level = 'projects';
  if (route.level === 'projects') { route.project = null; route.file = null; }
  if (route.level === 'files') route.file = null;
  if (route.view !== 'groups') route.group = null;
  if (route.view === 'system') {
    route.level = 'projects'; route.project = null; route.file = null;
    route.context = null; route.path = null; route.block = null; route.inline = null; route.isolateBlock = null;
    route.reading = null; route.methodFocus = null; route.methodFocus = null;
  }
  if (!route.path) { route.entry = null; route.detail = null; if (route.reading !== 'methods') route.reading = null; }
  if (route.reading !== 'methods') route.methodFocus = null;
  if (!route.wiki) route.wikiOffset = 0;
  if (!route.repo) {
    route.task = null;
    route.level = 'projects'; route.project = null; route.file = null; route.node = null;
    route.context = null; route.group = null; route.block = null; route.inline = null; route.isolateBlock = null;
    route.isolate = null; route.path = null; route.entry = null; route.detail = null; route.page = 0;
    route.reading = null;
    route.wiki = null; route.wikiNode = null; route.wikiOffset = 0; route.block = null; route.inline = null; route.isolateBlock = null;
  }
  return route;
}

/** Читает только известные query-поля; остальные параметры страницы остаются нетронутыми. */
export function readRoute(href) {
  const url = new URL(href);
  const value = {};
  for (const key of ROUTE_KEYS) value[key] = url.searchParams.get(key);
  return normalizeRoute(value);
}

/** Отличает первый запуск без адреса карты от явного маршрута без выбранного репозитория. */
export function hasNavigationRoute(href) {
  const query = new URL(href).searchParams;
  return ROUTE_KEYS.some(key => query.has(key));
}

/** Записывает детерминированный маршрут, сохраняя чужие query-параметры и hash. */
export function writeRoute(route, href) {
  const url = new URL(href);
  const normalized = normalizeRoute(route);
  for (const key of ROUTE_KEYS) url.searchParams.delete(key);
  for (const key of ROUTE_KEYS) {
    const value = normalized[key];
    if (value != null && value !== '' && (!['page', 'wikiOffset'].includes(key) || value !== 0)) url.searchParams.set(key, String(value));
  }
  return url.href;
}

/** Ограничивает коллекцию ID без копирования исходника, событий или графа в browser history. */
function ids(value, limit = 240) {
  if (!Array.isArray(value) && !(value instanceof Set)) return [];
  const result = [];
  for (const item of value) {
    const id = identifier(item, 12000);
    if (id && !result.includes(id)) result.push(id);
    if (result.length >= limit) break;
  }
  return result.sort();
}

/** Короткий fingerprint меняет адрес раскрытия; исходные ID остаются только в history.state. */
export function pathDetailToken(expanded, collapsed) {
  const detail = [ids(expanded, MAX_PATH_METHODS), ids(collapsed, MAX_PATH_METHODS)];
  if (!detail[0].length && !detail[1].length) return null;
  let hash = 2166136261;
  for (const character of JSON.stringify(detail)) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
  return (hash >>> 0).toString(16).padStart(8, '0');
}

/** Inline fingerprint и browser payload используют один и тот же предел. */
export function inlineDetailToken(expanded) { return pathDetailToken(ids(expanded, MAX_INLINE_BLOCKS), []); }

/** Разрешён только компактный typed адрес, без membership, исходника и произвольного payload. */
function blockAddress(value) {
  if (!value || !['project', 'file', 'community'].includes(value.kind) || !identifier(value.id)) return null;
  return { id: identifier(value.id), kind: value.kind, projectId: identifier(value.projectId), file: identifier(value.file),
    groupId: identifier(value.groupId), projectScoped: value.projectScoped === true,
    label: typeof value.label === 'string' ? value.label.slice(0, 200) : '' };
}

/** Keep only validated navigation coordinates for a bounded saved-map page. */
function mapPartAddress(value) {
  if (!value || typeof value !== 'object') return null;
  const offset = number => Number.isSafeInteger(number) && number >= 0 && number <= 100000 ? number : 0;
  return { projectScope: identifier(value.projectScope), groupId: identifier(value.groupId),
    groups: value.groups === true, offset: offset(value.offset), projectOffset: offset(value.projectOffset), groupOffset: offset(value.groupOffset) };
}

/** Компактная запись сохраняет адрес, раскрытия, фильтры и камеру; неизвестные поля отбрасываются. */
export function navigationSnapshot(value = {}) {
  if (!value || typeof value !== 'object') value = {};
  const camera = value.camera;
  const validCamera = camera && [camera.x, camera.y, camera.scale].every(Number.isFinite) && camera.scale >= 0.001 && camera.scale <= 100;
  const filters = value.filters || {};
  const search = value.searchContext || {};
  return {
    codeAtlasNavigation: 1, route: normalizeRoute(value.route), snapshotId: identifier(value.snapshotId),
    ...(value.mapPart ? {mapPart:mapPartAddress(value.mapPart)} : {}),
    camera: validCamera ? { x: camera.x, y: camera.y, scale: camera.scale } : null,
    reader: value.route?.reading === 'methods' ? normalizeReaderState(value.reader) : null,
    methodPlacements: normalizeProgressivePlacements(value.methodPlacements),
    inlineMethodPlacements: normalizeProgressivePlacements(value.inlineMethodPlacements),
    inlineExpanded: ids(value.inlineExpanded, MAX_INLINE_BLOCKS), isolatedBlock: blockAddress(value.isolatedBlock),
    pathExpanded: ids(value.pathExpanded, MAX_PATH_METHODS), pathCollapsed: ids(value.pathCollapsed, MAX_PATH_METHODS), focused: value.focused === true,
    searchContext: {
      mode: ['meaning', 'entries'].includes(search.mode) ? search.mode : 'code',
      codeQuery: typeof search.codeQuery === 'string' ? search.codeQuery.slice(0, 1000) : '',
      semanticQuery: typeof search.semanticQuery === 'string' ? search.semanticQuery.slice(0, 1000) : '',
      entryQuery: typeof search.entryQuery === 'string' ? search.entryQuery.slice(0, 1000) : '',
      kind: ['comment', 'documentation'].includes(search.kind) ? search.kind : 'all',
      scope: search.scope === 'current' ? 'current' : 'all',
      codeResultsOpen: search.codeResultsOpen === true,
    },
    filters: {
      search: typeof filters.search === 'string' ? filters.search.slice(0, 2000) : '',
      kind: identifier(filters.kind) || '', relation: identifier(filters.relation) || '',
      codeProjectIds: filters.codeProjectIds == null ? null : ids(filters.codeProjectIds, 1000),
      preset: filters.preset === 'all' ? 'all' : 'dependencies', projectBlocks: filters.projectBlocks !== false,
      algorithm: identifier(filters.algorithm) || identifier(value.route?.algorithm), systemSearch: typeof filters.systemSearch === 'string' ? filters.systemSearch.slice(0, 2000) : '',
      systems: filters.systems !== false, packages: filters.packages === true,
      systemKind: ['http', 'database', 'kafka', 'rabbit', 'external-project'].includes(filters.systemKind) ? filters.systemKind : '',
    },
  };
}

/** Проверяет адрес после загрузки каталога/графа; отсутствующие цели возвращают ближайшую доступную карту. */
export function resolveNavigation(value, { projects = [], nodes = null, snapshotId = null } = {}) {
  const snapshot = navigationSnapshot(value);
  const route = snapshot.route;
  const messages = [];
  if (route.repo && !projects.some(project => project.id === route.repo)) {
    messages.push('Репозиторий из адреса не найден в локальном каталоге. Выберите доступный проект.');
    route.repo = null;
    snapshot.route = normalizeRoute(route);
    return { snapshot, messages };
  }
  const stale = Boolean(snapshot.snapshotId && snapshotId && snapshot.snapshotId !== snapshotId);
  if (stale) {
    snapshot.pathExpanded = []; snapshot.pathCollapsed = []; snapshot.camera = null;
    snapshot.inlineExpanded = []; snapshot.isolatedBlock = null;
    route.context = null; route.group = null; route.block = null; route.inline = null; route.isolateBlock = null;
    route.wiki = null; route.wikiOffset = 0;
    snapshot.methodPlacements = []; snapshot.inlineMethodPlacements = [];
    snapshot.reader = null; route.reading = null; route.methodFocus = null;
    messages.push('Снимок изменился: прежние раскрытия и камера сброшены, адрес проверен по текущей карте.');
  }
  if (route.inline !== inlineDetailToken(snapshot.inlineExpanded)) {
    snapshot.inlineExpanded = []; route.inline = null;
    messages.push('Вложенные блоки доступны в истории этой вкладки. Показана основная карта.');
  }
  if (!route.isolateBlock || snapshot.isolatedBlock?.id !== route.isolateBlock) {
    snapshot.isolatedBlock = null; route.isolateBlock = null;
  }
  if (route.view === 'system' || !route.repo) {
    snapshot.inlineExpanded = []; snapshot.isolatedBlock = null;
  }
  if (route.detail && !snapshot.pathExpanded.length && !snapshot.pathCollapsed.length) {
    route.detail = null;
    messages.push('Раскрытия из адреса доступны в истории этой вкладки. Показан корень пути.');
  }
  if (route.path && (snapshot.pathExpanded.length || snapshot.pathCollapsed.length)
      && route.detail !== pathDetailToken(snapshot.pathExpanded, snapshot.pathCollapsed)) {
    snapshot.pathExpanded = []; snapshot.pathCollapsed = []; route.detail = null;
    messages.push('Сохранённые раскрытия не соответствуют адресу. Показан корень пути.');
  }
  if (route.view !== 'system' && Array.isArray(nodes)) {
    const rawIds = new Set(nodes.map(node => node.id));
    const projectIds = new Set(nodes.map(codeProjectId));
    if (route.project && !projectIds.has(route.project)) {
      route.level = 'projects'; route.project = null; route.file = null; route.page = 0;
      messages.push('Внутренний проект из адреса не найден. Открыт обзор проектов.');
    }
    if (route.level === 'symbols' && !nodes.some(node => codeProjectId(node) === route.project && (node.file || null) === route.file)) {
      route.level = 'files'; route.file = null; route.page = 0;
      messages.push('Файл из адреса не найден. Открыты файлы проекта.');
    }
    for (const key of ['node', 'context', 'path', 'isolate', 'wikiNode', 'methodFocus']) {
      if (route[key] && !rawIds.has(route[key])) {
        route[key] = null;
        messages.push('Символ из адреса отсутствует в текущем снимке. Открыта доступная карта.');
      }
    }
    if (!route.path) { snapshot.pathExpanded = []; snapshot.pathCollapsed = []; route.entry = null; }
    else {
      snapshot.pathExpanded = snapshot.pathExpanded.filter(id => rawIds.has(id) || id.startsWith(route.path + '::inline-call:'));
      snapshot.pathCollapsed = snapshot.pathCollapsed.filter(id => rawIds.has(id));
    }
    snapshot.filters.codeProjectIds = snapshot.filters.codeProjectIds?.filter(id => projectIds.has(id)) ?? null;
  }
  if (snapshot.reader && (snapshot.reader.projectId !== route.repo || snapshotId && snapshot.reader.snapshotId !== snapshotId
      || snapshot.reader.focusId !== route.methodFocus)) {
    snapshot.reader = null;
    messages.push('Сохранённое чтение не соответствует адресу: метод будет открыт по текущей карте.');
  }
  snapshot.route = normalizeRoute(route);
  return { snapshot, messages };
}

/** Камера не создаёт отдельный переход; раскрытие реализации и выбор символа создают. */
function semanticKey(snapshot) {
  const { camera, snapshotId, ...navigation } = snapshot;
  return JSON.stringify(navigation);
}

/** Read only bounded Atlas history positions; older entries simply start a new known trail. */
function historyCursor(value) {
  if (!value || typeof value.id !== 'string' || value.id.length > 100 || !value.id) return null;
  for (const position of [value.index, value.end]) {
    if (!Number.isSafeInteger(position) || position < 0 || position > 1000000) return null;
  }
  if (value.index > value.end) return null;
  return { id: value.id, index: value.index, end: value.end };
}

/** Identify a fresh trail without saving browsing URLs or graph contents outside history.state. */
function freshHistoryCursor() { return { id: `atlas-${Date.now()}-${Math.random().toString(36).slice(2)}`, index: 0, end: 0 }; }

/** Select/checkbox input не является набором текста: их change должен создать настоящий переход. */
export function navigationInteraction(event) {
  const target = event.target;
  if (!target) return null;
  if (target.closest?.('[data-navigation-ignore]')) return null;
  const textField = target.tagName === 'TEXTAREA' || (target.tagName === 'INPUT' && ['text', 'search', ''].includes(target.type || ''));
  if (event.type === 'input') return textField ? 'replace' : null;
  if (event.type === 'change') return textField ? 'replace' : 'push';
  if (event.type === 'keydown') {
    if (!['Enter', ' '].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey) return null;
    if ((target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') && target.id !== 'navigation-search') return null;
  }
  return target.closest?.('button, select, input, .graph-node, .community-node, .flow-card, .path-card, .path-reference') ? 'push' : null;
}

/** Browser Back/Forward отменяет предыдущую асинхронную реставрацию и никогда не создаёт push. */
export class NavigationHistory {
  /** browser/getSnapshot/restore внедряются для проверки настоящего протокола history без DOM карты. */
  constructor({ browser = window, getSnapshot, restore, onError = () => {}, onChange = () => {} }) {
    this.browser = browser;
    this.getSnapshot = getSnapshot;
    this.restore = restore;
    this.onError = onError;
    this.onChange = onChange;
    this.cursor = historyCursor(browser.history.state?.codeAtlasHistory) || freshHistoryCursor();
    this.revision = 0;
    this.ready = false;
    this.notify();
    browser.addEventListener('popstate', event => {
      const cursor = historyCursor(event.state?.codeAtlasHistory);
      if (cursor?.id === this.cursor.id) this.cursor.index = cursor.index;
      else this.cursor = cursor || freshHistoryCursor();
      const state = { ...(event.state?.codeAtlasNavigation === 1 ? event.state : {}), route: readRoute(browser.location.href) };
      return this.restoreEntry(state);
    });
  }

  /** Publish known navigation bounds; both buttons are disabled until a guarded restore finishes. */
  notify() {
    const available = this.ready && !this.restoring;
    this.onChange({ canBack: available && this.cursor.index > 0, canForward: available && this.cursor.index < this.cursor.end });
  }

  /**
   * Request one native history step, retaining the live camera before popstate restores the destination.
   * @param {number} delta Only -1 (Back) and 1 (Forward) are accepted.
   * @returns {boolean} Whether an available step was requested; restoration completes through popstate.
   */
  go(delta) {
    if (delta !== -1 && delta !== 1) return false;
    if (!this.ready || this.restoring) return false;
    if (delta === -1 ? this.cursor.index === 0 : this.cursor.index >= this.cursor.end) return false;
    this.checkpoint();
    this.restoring = true;
    this.notify();
    this.browser.history.go(delta);
    return true;
  }

  /** Сохраняет прежнюю камеру перед действием; новый пользовательский переход отменяет незавершённый Back. */
  checkpoint({ cancelRestore = false } = {}) {
    if (!this.ready) return;
    if (this.restoring && !cancelRestore) return;
    this.controller?.abort();
    this.revision++;
    this.restoring = false;
    // select/change уже содержит новое DOM value. Прежний смысл берём из записи, камеру — из живой карты.
    const live = navigationSnapshot(this.getSnapshot());
    this.current = navigationSnapshot({ ...(this.current || live), camera: live.camera });
    this.browser.history.replaceState({ ...this.current, codeAtlasHistory: { ...this.cursor } }, '', writeRoute(this.current.route, this.browser.location.href));
    this.notify();
  }

  /** Пишет переход только при изменении смысла карты; typing и движение камеры заменяют текущую запись. */
  commit({ replace = false } = {}) {
    if (!this.ready || this.restoring) return;
    const next = navigationSnapshot(this.getSnapshot());
    const changed = !this.current || semanticKey(this.current) !== semanticKey(next);
    const method = !replace && !this.pendingReplace && changed ? 'pushState' : 'replaceState';
    if (method === 'pushState') {
      this.cursor.index++;
      this.cursor.end = this.cursor.index;
    }
    this.browser.history[method]({ ...next, codeAtlasHistory: { ...this.cursor } }, '', writeRoute(next.route, this.browser.location.href));
    this.current = next;
    this.notify();
  }

  /** Завершает DOM-действие после всех listeners; промежуточные draw при typing также используют replace. */
  scheduleCommit({ replace = false } = {}) {
    clearTimeout(this.commitTimer);
    this.pendingReplace = replace;
    this.commitTimer = setTimeout(() => {
      this.commit({ replace });
      this.pendingReplace = false;
    }, 0);
  }

  /** Сразу сохраняет последнюю камеру: native Back может произойти до следующего таймера. */
  scheduleCamera() {
    this.commit({ replace: true });
  }

  /** Первый запуск предпочитает URL; сохранённый репозиторий применяется только к пустому начальному адресу. */
  async start(fallbackRoute = {}) {
    const saved = this.browser.history.state;
    const route = hasNavigationRoute(this.browser.location.href) ? readRoute(this.browser.location.href) : fallbackRoute;
    await this.restoreEntry(saved?.codeAtlasNavigation === 1 ? { ...saved, route } : { route });
  }

  /** Даже fetch, проигнорировавший AbortSignal, не может записать устаревший маршрут после нового popstate. */
  async restoreEntry(value, { replace = true } = {}) {
    this.controller?.abort();
    const controller = this.controller = new AbortController();
    const revision = ++this.revision;
    const isCurrent = () => revision === this.revision && !controller.signal.aborted;
    this.restoring = true;
    this.notify();
    try {
      await this.restore(navigationSnapshot(value), { signal: controller.signal, isCurrent });
      if (!isCurrent()) return;
      this.ready = true;
      this.restoring = false;
      this.commit({ replace });
    } catch (error) {
      if (!isCurrent()) return;
      this.ready = true;
      this.restoring = false;
      this.onError(error);
      this.commit({ replace: true });
    }
  }
}
