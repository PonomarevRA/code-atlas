import {beginLoading} from './loading-state.js';
import { el } from './dom.js';
import { requestJson } from './bounded-request.js';

/** Локальный поиск документов по смыслу: запрос явный, ответы закреплены за снимком. */
export class SemanticSearchPanel {
  /** Поле запроса и submit могут находиться в toolbar и ссылаться на форму панели по ID. */
  constructor(root, { getScope, onSelect, onOpenDocument, fetcher = (...args) => fetch(...args), wait = ms => new Promise(resolve => setTimeout(resolve, ms)) }) {
    this.root = root;
    this.getScope = getScope;
    this.onSelect = onSelect;
    this.onOpenDocument = onOpenDocument;
    this.fetcher = fetcher;
    this.wait = wait;
    this.fields = {};
    const externalIds = { query: 'semantic-query', search: 'semantic-search-submit' };
    const owner = root.ownerDocument || globalThis.document;
    for (const name of ['form', 'query', 'kind', 'search', 'build', 'status', 'results']) {
      this.fields[name] = root.querySelector(`[data-semantic="${name}"]`)
        || owner?.getElementById?.(externalIds[name]);
    }
    this.revision = 0;
    this.searchRevision = 0;
    this.key = '';
    this.ready = false;
    this.modelAvailable = false;
    this.building = false;
    this.fields.form.addEventListener('submit', event => { event.preventDefault(); this.search(); });
    this.fields.build.addEventListener('click', () => this.build());
    this.fields.query.addEventListener('input', () => this.cancelSearch());
    this.fields.kind.addEventListener('change', () => this.cancelSearch());
  }

  /** Смена проекта/ревизии отменяет запросы и опрос предыдущей задачи. */
  sync() {
    const scope = this.getScope();
    const key = scope.projectId && scope.snapshotId ? `${scope.projectId}:${scope.snapshotId}` : '';
    if (key === this.key) return;
    this.key = key;
    this.revision++;
    clearTimeout(this.deferredTimer);
    this.controller?.abort();
    this.searchController?.abort();
    this.building = false;
    this.ready = false;
    this.modelAvailable = false;
    this.fields.results.replaceChildren();
    this.controls();
    if (key) this.loadStatus();
    else this.fields.status.textContent = 'Откройте карту проекта.';
  }

  /** Редактирование ввода отменяет только поиск, не фоновую индексацию на сервере. */
  cancelSearch() { this.invalidateSearch(); }

  /** Back/Forward отменяет устаревшие hits без сброса query/kind и без нового запроса к модели. */
  invalidateSearch({ clearResults = true } = {}) {
    this.searchRevision += 1;
    this.searchController?.abort();
    if (clearResults) this.fields.results.replaceChildren();
    this.controls();
  }

  /** Преобразует same-origin HTTP-ошибку в текст; данные не интерпретируются как HTML. */
  async request(path, body, signal) {
    const options = { signal };
    if (body !== undefined) Object.assign(options, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return requestJson(`/api${path}`, options, { fetcher: (...args) => this.fetcher(...args) });
  }

  /**
   * Согласует кнопки и один токен фоновой индексации с текущим состоянием.
   *
   * building удерживает токен между проверками; сброс при смене scope либо
   * завершении освобождает его. Обычный GET статуса не создаёт новый токен.
   */
  controls() {
    if (this.building && !this.loadingRelease) this.loadingRelease = beginLoading({ru:'Индексация документации…', en:'Indexing documentation…'});
    if (!this.building) {this.loadingRelease?.(); this.loadingRelease = null;}
    this.fields.search.disabled = !this.key || !this.ready;
    this.fields.build.disabled = !this.key || !this.modelAvailable || this.building;
    this.fields.build.textContent = this.building ? 'Индексация…' : this.ready ? 'Обновить снимок документов' : 'Построить индекс документов';
  }

  /** Загружает метаданные, не запускает модель и продолжает опрос уже активной задачи. */
  async loadStatus() {
    clearTimeout(this.deferredTimer);
    const scope = this.getScope(), revision = this.revision;
    const statusRevision = this.statusRevision = (this.statusRevision || 0) + 1;
    this.controller?.abort();
    this.controller = new AbortController();
    try {
      const data = await this.request(`/projects/${encodeURIComponent(scope.projectId)}/documentation-index?snapshotId=${encodeURIComponent(scope.snapshotId)}`, undefined, this.controller.signal);
      if (revision !== this.revision || statusRevision !== this.statusRevision) return;
      if (data.snapshotId !== scope.snapshotId) throw new Error('Снимок индекса изменился. Откройте карту заново.');
      this.modelAvailable = Boolean(data.modelAvailable);
      this.ready = Boolean(data.index) && this.modelAvailable;
      const count = data.index?.chunkCount ?? data.index?.chunks ?? 0;
      this.fields.status.textContent = this.ready ? `Локальный LanceDB · ${count} фрагментов · снимок ${scope.snapshotId.slice(0, 8)}${data.index?.truncated ? ' · корпус ограничен' : ''}` : data.message || 'Индекс ещё не построен. Комментарии и документация останутся на компьютере.';
      if (data.deferred) {
        this.fields.status.textContent += ' Обновление документов ожидает завершения предыдущей задачи.';
        this.building = true;
        this.controls();
        clearTimeout(this.deferredTimer);
        this.deferredTimer = setTimeout(() => { if (revision === this.revision) this.loadStatus(); }, 2000);
        return;
      }
      this.building = false;
      if (data.job && ['queued', 'indexing'].includes(data.job.status)) {
        this.building = true;
        this.controls();
        await this.watch(data.job, revision, this.controller.signal);
      } else {
        this.controls();
      }
    } catch (error) {
      if (revision === this.revision && statusRevision === this.statusRevision && error.name !== 'AbortError') {
        this.building = false;
        this.controls();
        this.fields.status.textContent = `${error.message} Повторите обновление индекса, когда сервер доступен.`;
      }
    }
  }

  /** Запускает обновление отдельно от карты; при ошибке прежний индекс остаётся доступен. */
  async build() {
    const scope = this.getScope(), revision = this.revision;
    if (!this.key || !this.modelAvailable || this.building) return;
    this.statusRevision = (this.statusRevision || 0) + 1;
    this.building = true;
    this.controller?.abort();
    this.controller = new AbortController();
    this.controls();
    try {
      const result = await this.request(`/projects/${encodeURIComponent(scope.projectId)}/documentation-index`, { snapshotId: scope.snapshotId, force: true }, this.controller.signal);
      if (revision !== this.revision) return;
      await this.watch(result.job, revision, this.controller.signal);
    } catch (error) {
      if (revision !== this.revision || error.name === 'AbortError') return;
      this.building = false;
      this.controls();
      this.fields.status.textContent = error.message;
    }
  }

  /** Сетевой сбой не завершает серверный job; опрос повторяется с ограниченным интервалом. */
  async watch(job, revision, signal) {
    while (revision === this.revision && !signal.aborted) {
      try {
        const current = await this.request(`/documentation-index/jobs/${encodeURIComponent(job.id)}`, undefined, signal);
        if (revision !== this.revision) return;
        this.fields.status.textContent = current.phase === 'capture' ? 'Снимаем комментарии и документацию…' : 'Сохраняем локальные векторы…';
        if (current.status === 'completed' || current.status === 'failed') {
          this.building = false;
          this.controls();
          if (current.status === 'failed') this.fields.status.textContent = current.error || 'Индексация не завершена. Повторите её.';
          else await this.loadStatus();
          return;
        }
      } catch (error) {
        if (error.name === 'AbortError' || revision !== this.revision) return;
        if (error.status === 404) {
          this.building = false;
          this.controls();
          await this.loadStatus();
          if (revision === this.revision) this.fields.status.textContent += ' Предыдущая задача отсутствует после перезапуска; при необходимости обновите индекс.';
          return;
        }
        this.fields.status.textContent = 'Не удалось получить прогресс. Повторяем опрос; серверная задача продолжает работу.';
      }
      await this.wait(2000);
    }
  }

  /** Возвращает до восьми выдержек; новый ввод и другой снимок отменяют старый ответ. */
  async search() {
    const scope = this.getScope(), revision = this.revision;
    const query = this.fields.query.value.trim(), kind = this.fields.kind.value || 'all';
    if (!this.key || !this.ready || !query) return;
    this.cancelSearch();
    const searchRevision = this.searchRevision;
    this.searchController = new AbortController();
    this.fields.search.disabled = true;
    this.fields.status.textContent = 'Ищем по локальным векторам… Первый поиск загружает модель и может занять больше времени.';
    try {
      const data = await this.request(`/projects/${encodeURIComponent(scope.projectId)}/semantic-search`, { snapshotId: scope.snapshotId, query, kind, limit: 8 }, this.searchController.signal);
      if (revision !== this.revision || searchRevision !== this.searchRevision) return;
      if (data.snapshotId !== scope.snapshotId) throw new Error('Снимок поиска изменился. Откройте карту заново.');
      this.render(data);
      this.fields.status.textContent = `${data.hits.length} совпадений по смыслу · меньшее расстояние — ближе, это не вероятность. ${data.index?.truncated ? 'Корпус ограничен.' : ''}`;
    } catch (error) {
      if (revision === this.revision && searchRevision === this.searchRevision && error.name !== 'AbortError') this.fields.status.textContent = error.message;
    } finally {
      if (revision === this.revision && searchRevision === this.searchRevision) this.controls();
    }
  }

  /** Показывает источник и прямые связи с кодом; названия файлов не становятся HTML. */
  render(data) {
    this.fields.results.replaceChildren();
    const nodes = new Map((data.codeNodes || []).map(node => [node.id, node]));
    const scope = { ...this.getScope() };
    const revision = this.revision, searchRevision = this.searchRevision;
    const query = this.fields.query.value.trim(), kind = this.fields.kind.value || 'all';
    /** Сохранённая DOM-карточка не открывает совпавший raw-ID другого снимка или другого запроса. */
    const current = () => {
      const active = this.getScope();
      return revision === this.revision && searchRevision === this.searchRevision
        && active.projectId === scope.projectId && active.snapshotId === scope.snapshotId
        && this.fields.query.value.trim() === query && (this.fields.kind.value || 'all') === kind;
    };
    for (const hit of data.hits) {
      const card = el('article', undefined, 'semantic-hit');
      card.append(el('strong', `${hit.kind === 'comment' ? 'Комментарий' : 'Документация'} · расстояние ${Number(hit.distance).toFixed(3)}`));
      card.append(el('small', `${hit.file}:${hit.line}${hit.endLine > hit.line ? `–${hit.endLine}` : ''} · ${hit.sourceKind === 'graph-snapshot' ? 'из карты' : 'снято из рабочей копии'}`));
      card.append(el('p', hit.text));
      card.title = `Захват: ${data.index?.capturedAt || data.index?.indexedAt || 'не указан'} · SHA-256: ${hit.sourceHash || 'не указан'} · связь: ${hit.association || 'не установлена'}`;
      let codeActions = 0;
      for (const id of hit.nodeIds || []) {
        const node = nodes.get(id);
        if (!node) continue;
        const button = el('button', `Открыть код во всём репозитории: ${node.label || id}`, 'text-button');
        button.addEventListener('click', () => { if (current()) this.onSelect(id, { ...scope }); });
        card.append(button);
        codeActions += 1;
      }
      if (typeof hit.documentId === 'string' && hit.documentId && typeof this.onOpenDocument === 'function') {
        const button = el('button', 'Открыть полный документ', 'text-button');
        button.addEventListener('click', () => { if (current()) this.onOpenDocument(hit.documentId, { ...scope, line: hit.line, endLine: hit.endLine }); });
        card.append(button);
      }
      if (!codeActions) card.append(el('small', (hit.nodeIds || []).length ? 'Связанные узлы отсутствуют в данных результата. Документ можно читать без перехода к коду.' : 'Прямая связь с узлом карты не установлена. Документ можно читать без перехода к коду.', 'muted'));
      this.fields.results.append(card);
    }
  }
}
