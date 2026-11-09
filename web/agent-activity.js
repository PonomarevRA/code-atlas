import { AgentActivityModel, activityLabel, activityTarget, activityOperations } from './agent-activity-model.js';
import { AgentActivityStream } from './agent-activity-stream.js';
import { AgentActivityMarkers, agentColor } from './agent-activity-markers.js';

const SETTINGS = 'code-atlas-agent-activity-v1';

/** Мини-лента наблюдаемых операций. Оба потребителя изначально выключены. */
export class AgentActivity {
  /** Подключает view и текущий getContext; onNavigate открывает выбранный raw ID обычной навигацией. */
  constructor({ view, getContext, onNavigate }) {
    Object.assign(this, { view, getContext, onNavigate });
    this.model = new AgentActivityModel();
    this.mode = 'hidden';
    this.markersEnabled = false;
    this.lastSize = 'compact';
    this.follow = true;
    this.filtersOpen = false;
    this.renderedRows = new Map();
    this.markers = new AgentActivityMarkers(view, getContext);
    this.mount();
    this.restoreSettings();
    // A tray command is explicit user intent; ordinary page loads retain the default hidden layer.
    if (new URLSearchParams(window.location?.search || '').get('agentActivity') === '1') this.mode = 'compact';
    this.stream = new AgentActivityStream({ model: this.model,
      onChange: packet => {
        this.lastPacket = packet;
        if (packet.reset) this.resetHistory('Журнал обновился. Показаны текущие операции.');
        this.scheduleRender();
      },
      onStatus: (text, state) => {
        const omitted = this.lastPacket?.omittedCount;
        const message = state === 'ready' && omitted
          ? `${text}. Более ранние записи доступны в истории.`
          : text;
        if (this.status.textContent !== message) this.status.textContent = message;
        this.streamState = state;
        this.scheduleRender();
      } });
    this.sync();
    window.addEventListener('pagehide', () => { this.stream.stop(); this.historyAbort?.abort(); });
    window.addEventListener('resize', () => { this.positionPanel(); this.markers.update(); });
  }
  /** Создаёт только статический каркас; все данные событий вставляются через textContent. */
  mount() {
    this.root = document.createElement('aside');
    this.root.className = 'agent-activity';
    this.root.setAttribute('aria-label', 'Журнал операций агентов');
    this.root.innerHTML = `<section id="agent-activity-panel" class="agent-activity-panel" hidden aria-labelledby="agent-activity-title">
      <header><div class="agent-activity-window-controls"><button type="button" data-activity="close" class="is-close" aria-label="Закрыть ленту" title="Закрыть"><span aria-hidden="true">×</span></button><button type="button" data-activity="minimize" class="is-minimize" aria-label="Свернуть ленту" title="Свернуть"><span aria-hidden="true">−</span></button><button type="button" data-activity="expand" class="is-expand" aria-label="Расширить ленту" aria-pressed="false" title="Расширить / вернуть размер"><span aria-hidden="true">↗</span></button></div><strong id="agent-activity-title" tabindex="-1">Операции MCP</strong><button type="button" data-activity="filters" class="agent-activity-icon" aria-label="Фильтры истории" title="Фильтры истории" aria-controls="agent-activity-filters" aria-expanded="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 17h16M9 4v6M15 14v6"/></svg></button></header>
      <p class="agent-activity-scope" data-activity="scope"></p>
      <p class="agent-activity-status" data-activity="status" role="status"></p>
      <div class="agent-activity-modes" role="group" aria-label="Режим журнала"><button type="button" data-activity="live" aria-pressed="true">Текущие</button><button type="button" data-activity="history" aria-pressed="false">История</button></div>
      <div id="agent-activity-filters" data-activity="filter-options" class="agent-activity-filter-options" hidden>
        <form data-activity="history-form" class="agent-activity-filters" aria-label="Фильтры истории"><label>Операция<select data-activity="operation"><option value="">Все</option><option>search</option><option>read</option><option>describe</option><option>neighbors</option><option>status</option><option>other</option></select></label><label>Сессия<select data-activity="session"><option value="">Все</option></select></label><label>Поиск в истории<input data-activity="query" type="search" maxlength="512" placeholder="Что искали"></label><button type="submit" data-activity="apply">Найти в истории</button></form>
        <details class="agent-activity-maintenance"><summary>Управление журналом</summary><p>Очистка затрагивает записи всех проектов Atlas.</p><button type="button" data-activity="clear">Очистить журнал всех проектов</button></details>
      </div>
      <p data-activity="history-status" class="agent-activity-notice" role="status" hidden></p>
      <ol data-activity="events" class="agent-activity-events" tabindex="0" aria-label="Наблюдаемые операции MCP"></ol>
      <footer><button type="button" data-activity="older">Ранее</button><span data-activity="count"></span></footer></section>
      <div class="agent-activity-launcher"><button type="button" data-activity="toggle" class="agent-activity-icon" aria-label="Лента агентов" title="Лента агентов" aria-controls="agent-activity-panel" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-6 3V6a2 2 0 0 1 2-2Z"/><path d="M7 9h10M7 13h7"/></svg></button><button type="button" data-activity="markers" class="agent-activity-marker-toggle" aria-label="Показывать работу агентов на карте" aria-pressed="false" title="Показывать работу агентов на карте"><span aria-hidden="true"></span></button></div>`;
    document.body.append(this.root);
    this.panel = this.part('panel') || this.root.querySelector('section');
    this.list = this.part('events');
    this.status = this.part('status');
    this.root.addEventListener('click', event => this.action(event.target.closest('button')?.dataset.activity));
    this.panel.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault();
      this.action('close');
    });
    const historyForm = this.part('history-form');
    historyForm.addEventListener('submit', event => {
      event.preventDefault();
      void this.loadHistory();
    });
    historyForm.addEventListener('input', () => {
      if (!this.historyAbort && !this.historyPage) return;
      this.cancelHistory();
      this.setHistoryStatus('Фильтры изменились. Нажмите «Найти в истории».');
      this.scheduleRender();
    });
    this.list.addEventListener('scroll', () => { this.follow = this.list.scrollHeight - this.list.scrollTop - this.list.clientHeight < 40; });
  }
  /** Находит элемент только внутри feature, не затрагивая навигацию карты. */
  part(name) { return this.root.querySelector(`[data-activity="${name}"]`); }
  /** Сохраняет только небольшие настройки представления, никогда тексты или курсоры событий. */
  saveSettings() {
    try { localStorage.setItem(SETTINGS, JSON.stringify({ mode: this.mode, markersEnabled: this.markersEnabled, lastSize: this.lastSize })); }
    catch { /* Storage может быть запрещён в приватном WebView. */ }
  }
  /** Первый запуск остаётся выключенным; повреждённая настройка не включает consumers. */
  restoreSettings() {
    try {
      const saved = JSON.parse(localStorage.getItem(SETTINGS) || 'null');
      if (!saved) return;
      if (['compact', 'expanded', 'minimized', 'hidden'].includes(saved.mode)) this.mode = saved.mode;
      this.markersEnabled = saved.markersEnabled === true;
      this.lastSize = saved.lastSize === 'expanded' ? 'expanded' : 'compact';
    } catch { /* Defaults достаточны при недоступной настройке. */ }
  }
  /** Меняет вид панели и независимый слой; запись журнала управляется сервисом. */
  action(action) {
    if (action === 'toggle') this.mode = this.isOpen() ? 'hidden' : this.lastSize;
    if (action === 'close') this.mode = 'hidden';
    if (action === 'minimize') this.mode = 'minimized';
    if (action === 'markers') this.markersEnabled = !this.markersEnabled;
    if (action === 'filters') this.filtersOpen = !this.filtersOpen;
    if (action === 'expand') { this.mode = this.mode === 'expanded' ? 'compact' : 'expanded'; this.lastSize = this.mode; }
    if (action === 'history') { this.loadHistory(); return; }
    if (action === 'older') {
      const cursor = this.historyPage?.events[0]?.sequence;
      if (this.historyPage?.hasMore && cursor) this.loadHistory(cursor);
      return;
    }
    if (action === 'live') { this.resetHistory(); this.scheduleRender(); return; }
    if (action === 'clear') { this.clearHistory(); return; }
    if (!action) return;
    this.saveSettings();
    this.sync();
    if (action === 'toggle' && this.isOpen()) this.panel.querySelector('strong').focus({ preventScroll: true });
    if (action === 'close' || action === 'minimize') this.part('toggle').focus({ preventScroll: true });
  }
  isOpen() { return this.mode === 'compact' || this.mode === 'expanded'; }
  /** Резервирует реальную высоту нижних controls, в том числе при переносе кнопок на мобильном. */
  positionPanel() {
    const controls = document.querySelector?.('#canvas-navigation-controls');
    const rect = controls && !controls.closest('[hidden]') ? controls.getBoundingClientRect() : null;
    if (!rect?.width || !rect.height) {
      this.root.style.removeProperty('--agent-max-height');
      this.root.style.bottom = '';
      return;
    }
    this.root.style.bottom = `${Math.max(28, window.innerHeight - rect.top + 12)}px`;
    this.root.style.setProperty('--agent-max-height', `${Math.max(140, rect.top - 100)}px`);
  }
  /** Подписывается один раз для всех consumers; смена проекта сбрасывает чужую историю. */
  sync() {
    const context = this.getContext();
    if (this.projectId !== context.projectId) {
      this.projectId = context.projectId;
      this.lastPacket = null;
      this.renderedRows.clear();
      const listHadFocus = this.list.contains(document.activeElement);
      this.list.replaceChildren();
      if (listHadFocus) this.list.focus({ preventScroll: true });
      this.sessionSignature = null;
      this.resetHistory();
      this.part('operation').value = '';
      this.part('session').value = '';
      this.part('query').value = '';
    }
    const open = this.isOpen();
    this.part('scope').textContent = context.projectId ? `Проект: ${context.projectName || context.projectId}` : 'Проект не выбран';
    if (!context.projectId) this.status.textContent = 'Выберите проект, чтобы наблюдать его вызовы MCP.';
    this.panel.hidden = !open;
    this.root.classList.toggle('is-expanded', this.mode === 'expanded');
    this.part('toggle').setAttribute('aria-pressed', String(open));
    this.part('toggle').hidden = open;
    this.root.classList.toggle('has-open-panel', open);
    this.part('markers').setAttribute('aria-pressed', String(this.markersEnabled));
    this.part('expand').setAttribute('aria-pressed', String(this.mode === 'expanded'));
    this.part('filters').setAttribute('aria-expanded', String(this.filtersOpen));
    this.part('filter-options').hidden = !this.filtersOpen;
    this.positionPanel();
    this.stream.configure(open || this.markersEnabled, context.projectId);
    if (!open && this.historyAbort) this.resetHistory();
    this.scheduleRender();
  }
  /** Ограничивает обновления ленты одним кадром на пакет; ACK уже обработан reducer. */
  scheduleRender() {
    if (this.renderPending) return;
    this.renderPending = requestAnimationFrame(() => { this.renderPending = null; this.render(); });
  }
  /** Обновляет последнюю активность без автоматического перемещения камеры или фокуса. */
  render() {
    this.markers.update(this.markersEnabled, [...this.model.agents.values()]);
    if (!this.isOpen()) return;
    const oldTop = this.list.scrollTop;
    const focusedEvent = document.activeElement?.dataset.activitySequence;
    const events = this.historyPage?.events || this.model.events;
    if (this.filtersOpen) this.sessionOptions(events);
    const count = this.renderRows(events);
    const history = Boolean(this.historyPage);
    this.part('history').setAttribute('aria-pressed', String(history));
    this.part('live').setAttribute('aria-pressed', String(!history));
    const historyUnavailable = !this.projectId || Boolean(this.historyAbort) || this.clearing === true;
    this.part('history').setAttribute('aria-disabled', String(historyUnavailable));
    this.part('apply').setAttribute('aria-disabled', String(historyUnavailable));
    const canPage = this.historyPage?.hasMore && this.historyPage.events.length &&
      !historyUnavailable && this.historyPage.params === this.historyParams().toString();
    this.part('older').setAttribute('aria-disabled', String(!canPage));
    this.part('count').textContent = `Операций: ${count}`;
    this.list.scrollTop = this.follow && !this.historyPage && !focusedEvent ? this.list.scrollHeight : oldTop;
    if (focusedEvent && !this.list.contains(document.activeElement)) {
      const buttons = this.list.querySelectorAll('button[data-activity-sequence]:not(:disabled)');
      const focused = Array.from(buttons).find(button => button.dataset.activitySequence === focusedEvent);
      (focused || this.list).focus({ preventScroll: true });
    }
  }
  /** Повторно использует строки текущего списка, пока событие и доступность его узла не изменились. */
  renderRows(events) {
    const context = this.getContext();
    const visible = this.view.nodeElements;
    const next = new Map();
    for (const { event } of activityOperations(events)) {
      const target = activityTarget(event, context, visible);
      const previous = this.renderedRows.get(event);
      const row = previous?.status === target.status ? previous.row : this.eventRow(event, target);
      next.set(event, { row, status: target.status });
    }

    // Сначала удаляем устаревшие строки, чтобы сдвиг ограниченного окна не перемещал остальные.
    for (const [event, previous] of this.renderedRows) {
      if (next.get(event)?.row !== previous.row) previous.row.remove();
    }
    this.renderedRows = next;
    if (!next.size) {
      const message = this.emptyMessage(Boolean(this.historyPage?.filtered));
      const empty = this.emptyRow ||= document.createElement('li');
      if (empty.textContent !== message) empty.textContent = message;
      if (!empty.parentNode) this.list.append(empty);
      return 0;
    }

    if (this.list.firstElementChild === this.emptyRow) this.emptyRow.remove();
    let index = 0;
    for (const { row } of next.values()) {
      const current = this.list.children[index++] || null;
      if (current !== row) this.list.insertBefore(row, current);
    }
    return next.size;
  }
  /** Объясняет пустую ленту с учётом области, подписки и активных фильтров. */
  emptyMessage(filtered) {
    if (!this.projectId) return 'Журнал появится после выбора проекта.';
    if (this.historyPage || this.streamState === 'ready') {
      return filtered ? 'Нет операций по выбранным фильтрам.' : 'В этом окне журнала пока нет операций.';
    }
    return this.streamState === 'offline'
      ? 'Нет сохранённых записей для показа.'
      : 'Ожидаем первые записи от журнала Atlas…';
  }
  /** Показывает короткие имена сессий, но передаёт полный ID с одинаковой exact семантикой live/history. */
  sessionOptions(events) {
    const select = this.part('session');
    const selected = select.value;
    const sessions = new Map();
    for (const event of [...this.model.events, ...events]) {
      if (event.sessionId) sessions.set(event.sessionId, event.clientKind || 'unknown');
      while (sessions.size > 100) sessions.delete(sessions.keys().next().value);
    }
    if (selected && !sessions.has(selected)) sessions.set(selected, 'сессия');
    const signature = JSON.stringify([...sessions]);
    if (signature === this.sessionSignature) return;
    this.sessionSignature = signature;
    const all = document.createElement('option'); all.value = ''; all.textContent = 'Все';
    select.replaceChildren(all);
    for (const [id, client] of sessions) {
      const option = document.createElement('option'); option.value = id; option.textContent = `${client} · ${id.slice(0, 8)}`;
      select.append(option);
    }
    select.value = selected;
  }
  /** Представляет одно событие безопасным текстом; переход возможен лишь к текущему snapshot. */
  eventRow(event, target = activityTarget(event, this.getContext(), this.view.nodeElements)) {
    const row = document.createElement('li');
    row.style.setProperty('--agent-color', agentColor(event.sessionId || 'unknown'));
    const heading = document.createElement('small');
    const date = new Date(event.occurredAt || event.recordedAt);
    heading.textContent = `${Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString()} · ${event.clientKind || 'unknown'} · сессия ${(event.sessionId || 'unknown').slice(0, 8)}${event.request?.tool ? ` · ${String(event.request.tool).slice(0, 100)}` : ''}`;
    const text = document.createElement('p');
    const requestSummary = event.request?.summary || activityLabel({ ...event, phase: 'started' });
    text.textContent = `Запрос: ${String(requestSummary).slice(0, 600)}`;
    row.append(heading, text);
    if (event.phase === 'finished' && event.response) this.appendResponse(row, event.response, event.outcome);
    else {
      const response = document.createElement('p');
      response.textContent = event.phase === 'finished' ? `Ответ: ${activityLabel(event)} · сводка ответа не сохранена` : 'Ответ: пока не наблюдался';
      row.append(response);
    }
    if (event.operation === 'search' && event.query?.mode !== 'preview') {
      const privacy = document.createElement('small'); privacy.textContent = 'Текст запроса не сохраняется'; row.append(privacy);
    }
    if (event.target?.nodeId) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.activitySequence = String(event.sequence);
      button.textContent = target.status === 'stale' ? 'Другая версия карты' : event.target.label || 'Открыть узел';
      button.disabled = target.status === 'stale' || target.status === 'other-project' || target.status === 'unavailable';
      if (target.status === 'outside') button.title = 'Узел вне текущего вида';
      button.addEventListener('click', () => this.onNavigate(event.target.nodeId));
      row.append(button);
    }
    return row;
  }
  /** Показывает фактическую bounded сводку ответа; найденные результаты не становятся положением агента. */
  appendResponse(row, response, outcome) {
    const text = document.createElement('p');
    let label = 'Ответ';
    if (outcome === 'failed') label += ' · ошибка';
    if (outcome === 'cancelled') label += ' · отменено';
    text.textContent = `${label}: ${String(response.summary || 'Сводка недоступна').slice(0, 600)}${response.truncated ? ' · показана часть ответа' : ''}`;
    row.append(text);
    if (!Array.isArray(response.items) || !response.items.length) {
      const preview = document.createElement('small'); preview.textContent = 'Фрагменты ответа не сохранены'; row.append(preview);
      return;
    }
    for (const item of response.items.slice(0, 3)) {
      const detail = document.createElement('small');
      detail.className = 'agent-activity-response-item';
      detail.textContent = `${String(item.label || '').slice(0, 200)}${item.file ? ` · ${String(item.file).slice(0, 300)}` : ''}`;
      row.append(detail);
    }
  }
  /** Фиксирует область и поля формы в одном запросе к истории. */
  historyParams() {
    const params = new URLSearchParams({ projectId: this.projectId || '', limit: '100' });
    for (const [key, part] of [['operation', 'operation'], ['sessionId', 'session'], ['q', 'query']]) {
      const value = this.part(part).value.trim();
      if (value) params.set(key, value);
    }
    return params;
  }
  /** Выводит результат команды отдельно от статуса живой подписки. */
  setHistoryStatus(text) {
    const status = this.part('history-status');
    status.textContent = text;
    status.hidden = !text;
  }
  /** Отзывает текущий запрос, даже если транспорт игнорирует AbortSignal. */
  cancelHistory() {
    this.historyAbort?.abort();
    this.historyAbort = null;
    this.list.setAttribute('aria-busy', 'false');
  }
  /** Возвращает живую ленту при смене области, журнала или режима. */
  resetHistory(message = '') {
    this.cancelHistory();
    this.historyPage = null;
    this.follow = true;
    this.setHistoryStatus(message);
  }
  /** Загружает ограниченную страницу, оставляя видимые записи до успешного ответа. */
  async loadHistory(beforeSequence, { signal } = {}) {
    if (signal?.aborted || !this.projectId || this.historyAbort || this.clearing) return;
    const params = this.historyParams();
    const applied = params.toString();
    if (beforeSequence && this.historyPage?.params !== applied) return;
    const abort = new AbortController();
    this.historyAbort = abort;
    const cancel = () => {
      if (this.historyAbort !== abort) return;
      this.cancelHistory();
      this.setHistoryStatus('Загрузка истории отменена.');
      this.scheduleRender();
    };
    signal?.addEventListener('abort', cancel, { once: true });
    if (beforeSequence) params.set('beforeSequence', beforeSequence);
    this.list.setAttribute('aria-busy', 'true');
    this.setHistoryStatus('Загружаем историю…');
    this.scheduleRender();
    try {
      const response = await fetch(`/api/agent-activity/history?${params}`, { signal: abort.signal });
      if (!response.ok) throw new Error('history');
      const page = await response.json();
      if (this.historyAbort !== abort) return;
      this.historyPage = { ...page, params: applied, filtered: ['operation', 'sessionId', 'q'].some(key => params.has(key)), events: (page.events || []).slice(-100) };
      this.follow = false;
      this.list.scrollTop = 0;
      this.setHistoryStatus('История загружена.');
      return true;
    } catch {
      if (this.historyAbort === abort) this.setHistoryStatus('История недоступна. Повторите запрос.');
      return false;
    } finally {
      signal?.removeEventListener('abort', cancel);
      if (this.historyAbort === abort) {
        this.cancelHistory();
        this.scheduleRender();
      }
    }
  }
  /** Очистка явно подтверждается, поскольку удаляет журнал всех проектов сервиса. */
  async clearHistory() {
    if (this.clearing || !window.confirm('Очистить журнал операций всех проектов Atlas? Это не удаляет код или карты.')) return;
    this.clearing = true;
    const button = this.part('clear');
    button.setAttribute('aria-disabled', 'true');
    this.cancelHistory();
    this.setHistoryStatus('Очищаем журнал…');
    try {
      const response = await fetch('/api/agent-activity/clear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      if (!response.ok) throw new Error('clear');
      this.model.reset();
      this.resetHistory('Журнал всех проектов очищен.');
    } catch {
      this.setHistoryStatus('Не удалось очистить журнал.');
    } finally {
      this.clearing = false;
      button.removeAttribute('aria-disabled');
      this.scheduleRender();
    }
  }
}
