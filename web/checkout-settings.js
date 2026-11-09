import {requestJson} from './bounded-request.js';

const MESSAGES = {
  ru: {
    refresh: 'Обновить список', loading: 'Читаем временные карты…',
    empty: 'Временных checkout-карт нет.', temporary: 'Временная', unknown: 'Неизвестно',
    remove: 'Удалить', removing: 'Удаление…', removed: 'Временная карта удалена. Исходники checkout сохранены.',
    draining: 'Удаление ожидает завершения работы. Обновите список для проверки.',
    readError: 'Не удалось обновить список. Прежние строки сохранены.',
    deleteError: 'Результат удаления неизвестен. Обновите список для проверки.',
    invalid: 'Некорректный ответ списка временных карт.', snapshot: 'Снимок',
    label: 'Карта', type: 'Тип', checkout: 'Checkout / снимок', path: 'Временная папка',
    expires: 'Действует до', state: 'Состояние', actions: 'Действия',
    ready: 'Готова', building: 'Построение', active: 'Доступна', failed: 'Ошибка построения',
    expired: 'Срок истёк', deleting: 'Удаление · ожидается завершение работы',
    cleanupFailed: 'Ошибка удаления · можно повторить', deleted: 'Удалена',
  },
  en: {
    refresh: 'Refresh list', loading: 'Loading temporary maps…',
    empty: 'No temporary checkout maps.', temporary: 'Temporary', unknown: 'Unknown',
    remove: 'Delete', removing: 'Deleting…', removed: 'Temporary map deleted. Checkout source files are preserved.',
    draining: 'Deletion is waiting for active work to finish. Refresh the list to check.',
    readError: 'Could not refresh the list. Previous rows are retained.',
    deleteError: 'Deletion outcome is unknown. Refresh the list to check.',
    invalid: 'Invalid temporary-map catalog response.', snapshot: 'Snapshot',
    label: 'Map', type: 'Type', checkout: 'Checkout / snapshot', path: 'Temporary folder',
    expires: 'Expires at', state: 'State', actions: 'Actions',
    ready: 'Ready', building: 'Building', active: 'Available', failed: 'Build failed',
    expired: 'Expired', deleting: 'Deleting · waiting for active work',
    cleanupFailed: 'Cleanup failed · retry available', deleted: 'Deleted',
  },
};
const SESSION_ID = /^[a-f0-9]{32}$/;
const STATE_KEYS = {ready: 'ready', completed: 'ready', building: 'building', pending: 'building', queued: 'building',
  running: 'building', active: 'active', failed: 'failed', expired: 'expired', deleting: 'deleting',
  cleanup_failed: 'cleanupFailed', 'cleanup-failed': 'cleanupFailed', cleanupfailed: 'cleanupFailed', deleted: 'deleted'};

/**
 * Resolve explicit host languages; neither user preferences nor server text is rewritten.
 * @param {object} context Declared locale/availableLocales/fallbackLocale, matching host settings.
 * @returns {object} Owned plain messages and Intl date formatter in the enabled locale.
 */
function labels({locale = 'ru', availableLocales = ['ru', 'en'], fallbackLocale = 'ru'} = {}) {
  const enabled = Array.isArray(availableLocales) ? availableLocales.filter(value => Object.hasOwn(MESSAGES, value)) : [];
  const fallback = enabled.includes(fallbackLocale) ? fallbackLocale : enabled[0] || 'ru';
  const resolved = enabled.includes(locale) ? locale : fallback;
  const messages = MESSAGES[resolved];
  const formatter = new Intl.DateTimeFormat(resolved, {dateStyle: 'medium', timeStyle: 'short'});
  return {locale: resolved, text: key => messages[key] || MESSAGES[fallback][key] || key,
    date: value => typeof value === 'string' && Number.isFinite(Date.parse(value)) ? formatter.format(new Date(value)) : '—'};
}

/**
 * Render isolated temporary checkout maps in the host's native settings table.
 * @param {HTMLElement} root Section owning data-checkout-list/status/refresh placeholders.
 * @param {object} options Explicit localeContext and optional native-fetch-compatible transport.
 * GET/list never renews a lease. Only the clicked session ID is deleted; no polling or automatic retries.
 */
export class CheckoutSettings {
  constructor(root, {localeContext, fetcher = (...args) => fetch(...args)} = {}) {
    this.root = root; this.document = root.ownerDocument; this.fetcher = fetcher;
    this.labels = labels(localeContext); this.rows = new Map(); this.sessions = new Map();
    this.busy = new Set(); this.pending = new Set(); this.deleted = new Set(); this.transports = new Set();
    this.revision = 0; this.disposed = false;
    this.list = root.querySelector('[data-checkout-list]');
    this.status = root.querySelector('[data-checkout-status]');
    this.refreshButton = root.querySelector('[data-checkout-refresh]');
    if (!this.list || !this.status || !this.refreshButton) throw new TypeError('Checkout settings placeholders are missing');
    root.lang = this.labels.locale;
    this.refreshButton.textContent = this.labels.text('refresh');
    this.status.setAttribute('role', 'status');
    this.onRefresh = () => this.refresh();
    this.onClick = event => {
      const button = event.target.closest?.('[data-checkout-delete]');
      if (button && this.root.contains(button)) this.remove(button.dataset.checkoutDelete);
    };
    this.refreshButton.addEventListener('click', this.onRefresh);
    root.addEventListener('click', this.onClick);
  }

  /**
   * Start one metadata read; repeated starts supersede the earlier GET without touching TTL.
   * @returns {Promise<void>} An accepted catalog or a visible read failure; prior rows survive errors.
   */
  start() { return this.refresh(); }

  /**
   * Create plain text nodes only; paths and labels are never interpreted as HTML or URLs.
   */
  element(tag, text, className) {
    const node = this.document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  /**
   * Bound the complete response wait and retain abort ownership for component disposal.
   * @param {string} path Fixed API path plus a validated opaque session ID.
   * @param {object} options Native fetch options; DELETE is never replayed automatically.
   */
  async request(path, options = {}) {
    const controller = new AbortController();
    this.transports.add(controller);
    const abort = () => controller.abort();
    options.signal?.addEventListener('abort', abort, {once: true});
    if (options.signal?.aborted) controller.abort();
    try {
      return await requestJson(path, {...options, signal: controller.signal, cache: 'no-store'},
        {fetcher: this.fetcher, timeoutMs: 30_000, activity: false});
    } finally {
      this.transports.delete(controller);
      options.signal?.removeEventListener('abort', abort);
    }
  }

  /**
   * Validate a whole catalog before replacing evidence; malformed data is never a false empty list.
   */
  catalog(data) {
    if (!data || !Array.isArray(data.sessions) || data.sessions.length > 1000) throw new Error(this.labels.text('invalid'));
    const sessions = new Map();
    for (const session of data.sessions) {
      if (!session || typeof session.sessionId !== 'string' || !SESSION_ID.test(session.sessionId)
          || session.temporary !== true || sessions.has(session.sessionId)) {
        throw new Error(this.labels.text('invalid'));
      }
      if (!this.deleted.has(session.sessionId)) sessions.set(session.sessionId, session);
    }
    return sessions;
  }

  /**
   * Accept only the newest GET; deleting invalidates every already-started read as well.
   * @returns {Promise<void>} Errors remain visible alongside the last accepted rows.
   */
  async refresh() {
    if (this.disposed) return;
    this.listController?.abort();
    const controller = new AbortController(); this.listController = controller;
    const revision = ++this.revision;
    this.refreshButton.disabled = true;
    this.status.textContent = this.labels.text('loading');
    let focusRemoved = false;
    try {
      const data = await this.request('/api/checkout-sessions', {signal: controller.signal});
      if (this.disposed || revision !== this.revision) return;
      this.sessions = this.catalog(data);
      // An authoritative cleanup failure permits an explicit retry; a draining
      // receipt remains disabled until a later list proves another state.
      for (const id of this.pending) {
        const state = this.sessions.get(id)?.state;
        if (!this.sessions.has(id) || state && state !== 'deleting') this.pending.delete(id);
      }
      focusRemoved = this.render();
      this.status.textContent = this.pending.size ? this.labels.text('draining') : '';
    } catch (error) {
      if (!this.disposed && revision === this.revision && error.name !== 'AbortError') {
        this.status.textContent = `${this.labels.text('readError')} ${error.message || ''}`.trim();
      }
    } finally {
      if (!this.disposed && revision === this.revision) {
        this.refreshButton.disabled = false;
        if (focusRemoved) this.refreshButton.focus({preventScroll: true});
      }
    }
  }

  /**
   * Keep keyed rows/buttons so refresh does not remove a surviving keyboard focus target.
   * @returns {boolean} Whether the currently focused row disappeared and needs a host fallback.
   */
  render() {
    let focusRemoved = false;
    for (const [id, item] of this.rows) {
      if (!this.sessions.has(id)) {
        focusRemoved ||= item.row.contains(this.document.activeElement);
        focusRemoved ||= this.focusCandidate === id && this.document.activeElement === this.document.body;
        item.row.remove(); this.rows.delete(id);
      }
    }
    for (const [id, session] of this.sessions) {
      let item = this.rows.get(id);
      if (!item) {
        const row = this.element('tr'); row.dataset.checkoutSession = id;
        const cells = Array.from({length: 7}, () => this.element('td'));
        const button = this.element('button'); button.type = 'button'; button.dataset.checkoutDelete = id;
        cells[6].append(button); row.append(...cells); this.list.append(row);
        item = {row, cells, button}; this.rows.set(id, item);
      }
      const label = typeof session.label === 'string' && session.label ? session.label : id;
      item.cells[0].textContent = label;
      item.cells[1].textContent = this.labels.text('temporary');
      const checkout = typeof session.checkout === 'string' ? session.checkout : session.checkout?.path;
      item.cells[2].textContent = typeof checkout === 'string' ? checkout : this.labels.text('unknown');
      item.cells[2].className = 'path';
      const snapshot = session.project?.snapshotId;
      if (typeof snapshot === 'string') item.cells[2].append(this.element('small', `${this.labels.text('snapshot')}: ${snapshot}`));
      item.cells[3].textContent = typeof session.path === 'string' ? session.path : this.labels.text('unknown');
      item.cells[3].className = 'path';
      item.cells[4].textContent = this.labels.date(session.expiresAt);
      // An active lifecycle permits reads, but does not imply that extraction
      // finished successfully. Show its actual build status until retirement.
      let state = session.state;
      if (this.pending.has(id)) state = 'deleting';
      else if (state === 'active' && typeof session.project?.status === 'string') state = session.project.status;
      item.cells[5].textContent = Object.hasOwn(STATE_KEYS, state) ? this.labels.text(STATE_KEYS[state]) : `${this.labels.text('unknown')}: ${state || '—'}`;
      if (typeof session.cleanupError === 'string' && session.cleanupError) item.cells[5].append(this.element('small', session.cleanupError));
      item.button.textContent = this.labels.text(this.busy.has(id) ? 'removing' : 'remove');
      item.button.setAttribute('aria-label', `${this.labels.text('remove')}: ${label} · ${this.labels.text('temporary')}`);
      item.button.disabled = this.busy.has(id) || this.pending.has(id) || state === 'deleting' || state === 'deleted';
      const keys = ['label', 'type', 'checkout', 'path', 'expires', 'state', 'actions'];
      for (let i = 0; i < keys.length; i++) item.cells[i].setAttribute('data-label', this.labels.text(keys[i]));
    }
    if (!this.rows.size) {
      this.emptyRow ||= this.element('tr');
      const cell = this.element('td', this.labels.text('empty')); cell.colSpan = 7;
      this.emptyRow.replaceChildren(cell); this.list.append(this.emptyRow);
    } else this.emptyRow?.remove();
    return focusRemoved;
  }

  /**
   * Delete only one accepted temporary session and reconcile with a fresh authoritative list.
   * @param {string} id Exact current server-issued session ID; arbitrary paths are never sent.
   * @returns {Promise<void>} No duplicate concurrent DELETE; ambiguous failures keep the row and expose an error.
   */
  async remove(id) {
    if (this.disposed || !this.sessions.has(id) || this.busy.has(id) || this.pending.has(id)
        || this.deleted.has(id)
        || ['deleting', 'deleted'].includes(this.sessions.get(id).state)) return;
    if (this.rows.get(id)?.button === this.document.activeElement) this.focusCandidate = id;
    this.busy.add(id);
    this.listController?.abort(); ++this.revision;
    this.refreshButton.disabled = false;
    this.render();
    let notice;
    try {
      const result = await this.request(`/api/checkout-sessions/${encodeURIComponent(id)}`, {method: 'DELETE'});
      if (this.disposed) return;
      const state = result?.state || result?.status || result?.session?.state;
      if (state === 'deleting') {
        this.pending.add(id); notice = this.labels.text('draining');
      } else if (state === 'deleted' || result?.deleted === true || result?.removed === true) {
        this.deleted.add(id); notice = this.labels.text('removed');
        this.sessions.set(id, {...this.sessions.get(id), state: 'deleted'});
      }
      // Even an acknowledged deletion is not used as a replacement catalog.
      await this.refresh();
      if (!this.disposed && notice && !this.status.textContent) this.status.textContent = notice;
    } catch (error) {
      if (!this.disposed && error.name !== 'AbortError') {
        this.status.textContent = `${this.labels.text('deleteError')} ${error.message || ''}`.trim();
      }
    } finally {
      this.busy.delete(id);
      if (!this.disposed) this.render();
    }
  }

  /**
   * Abort owned transports/listeners and fence ignored aborts; no polling timers are created.
   */
  dispose() {
    if (this.disposed) return;
    this.disposed = true; ++this.revision;
    this.listController?.abort();
    for (const controller of this.transports) controller.abort();
    this.transports.clear();
    this.refreshButton.removeEventListener('click', this.onRefresh);
    this.root.removeEventListener('click', this.onClick);
  }
}
