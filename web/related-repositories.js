import { $, el } from './dom.js';
import { monitorJob } from './job-monitor.js';
const preferenceKey = id => `code-atlas-related-repositories:${id}`;
/** Читает независимый набор каждого основного репозитория; неизвестные сохранённые id не теряются. */
export function readRelatedIds(storage, primaryId) {
  try {
    const value = JSON.parse(storage?.getItem(preferenceKey(primaryId)) || '[]');
    if (!Array.isArray(value)) return [];
    return [...new Set(value.filter(id => typeof id === 'string' && id !== primaryId))].slice(0, 8);
  } catch { return []; }
}
/** Управляет только связанными индексами; фоновые задания не переключают основной репозиторий. */
export class RelatedRepositories {
  /** Fetch/ожидание/хранилище внедряются для независимой проверки гонок задания. */
  constructor({ state, onChange, controls, fetcher = (...args) => fetch(...args), storage, wait }) {
    this.state = state;
    this.onChange = onChange;
    this.controls = controls;
    this.fetcher = fetcher;
    this.wait = wait;
    try { this.storage = storage || globalThis.localStorage; } catch { this.storage = null; }
    this.memory = new Map();
    this.revision = 0;
    this.busy = false;
    $('related-refresh').addEventListener('click', () => this.refresh());
    $('related-attach').addEventListener('click', () => this.attach($('related-available').value));
    $('related-index-form').addEventListener('submit', event => { event.preventDefault(); this.indexAndAttach(); });
  }
  /** Возвращает текущий набор основного репозитория без общей глобальной настройки. */
  getSelected(primaryId = this.state.project?.id) {
    if (!primaryId) return [];
    if (!this.memory.has(primaryId)) this.memory.set(primaryId, readRelatedIds(this.storage, primaryId));
    return [...this.memory.get(primaryId)];
  }
  /** Записывает только конкретный основной репозиторий; localStorage не обязателен для работы. */
  save(primaryId, ids) {
    const selected = [...new Set(ids)].filter(id => id !== primaryId).slice(0, 8);
    this.memory.set(primaryId, selected);
    try { this.storage?.setItem(preferenceKey(primaryId), JSON.stringify(selected)); } catch { /* Текущий сеанс сохраняет выбор в памяти. */ }
    if (primaryId === this.state.project?.id) this.onChange();
    this.render();
  }
  /** Добавляет сохранённый индекс; повторы и девятый дополнительный индекс отвергаются явно. */
  attach(id, primaryId = this.state.project?.id) {
    if (!id || !primaryId || id === primaryId) return false;
    const selected = this.getSelected(primaryId);
    if (selected.includes(id)) return true;
    if (selected.length >= 8) { this.message('Можно добавить не больше 8 связанных репозиториев.'); return false; }
    this.save(primaryId, [...selected, id]);
    return true;
  }
  /** Переключение основного индекса не отменяет уже запущенную серверную индексацию. */
  resetForPrimary() { this.render(); }
  /** Показывает статус операции отдельно от статуса построения системной карты. */
  message(text) { $('related-status').textContent = text; }
  /** Обновляет список сохранённых индексов, не меняя выбранный основной проект. */
  async refresh() {
    const revision = ++this.revision;
    try {
      const response = await this.fetcher('/api/projects');
      const data = await response.json();
      if (revision !== this.revision) return;
      if (!response.ok) throw new Error(data.error || 'Не удалось прочитать сохранённые индексы.');
      this.state.projects = data.projects;
      const selector = $('projects');
      selector.replaceChildren(new Option('Выберите проект', ''));
      for (const project of data.projects) selector.append(new Option(project.name || project.path, project.id));
      selector.value = this.state.project?.id || '';
      this.render();
    } catch (error) { if (revision === this.revision) this.message(error.message); }
  }
  /** Рисует доступные индексы и удаляемые связи; фильтр .csproj здесь не меняется. */
  render() {
    const primaryId = this.state.project?.id;
    const selected = this.getSelected(primaryId);
    const names = new Map((this.state.projects || []).map(project => [project.id, project.name || project.path]));
    const summary = $('related-summary');
    if (summary) {
      summary.textContent = `Связанные репозитории (${selected.length})`;
      summary.title = selected.map(id => names.get(id) || id).join(', ');
    }
    const available = $('related-available');
    const previous = available.value;
    available.replaceChildren(new Option('Выберите сохранённый индекс', ''));
    for (const project of this.state.projects || []) {
      if (project.id === primaryId || selected.includes(project.id) || project.status !== 'completed') continue;
      available.append(new Option(project.name || project.path, project.id));
    }
    if ([...available.options].some(option => option.value === previous)) available.value = previous;
    $('related-chips').replaceChildren();
    for (const id of selected) {
      const chip = el('button', `${names.get(id) || id} ×`, 'related-chip');
      chip.setAttribute('aria-label', `Убрать связанный репозиторий ${names.get(id) || id}`);
      chip.addEventListener('click', () => this.save(primaryId, this.getSelected(primaryId).filter(value => value !== id)));
      $('related-chips').append(chip);
    }
    $('related-attach').disabled = !primaryId || selected.length >= 8;
    $('related-index').disabled = !primaryId || this.busy || Boolean(this.state.job) || this.state.submitting || selected.length >= 8;
  }
  /** Индексирует путь и после успеха добавляет результат именно к исходному основному репозиторию. */
  async indexAndAttach() {
    const primaryId = this.state.project?.id;
    const path = $('related-path').value.trim();
    if (!primaryId || !path || this.busy || this.state.job || this.state.submitting) return;
    if (this.getSelected(primaryId).length >= 8) { this.message('Сначала уберите один из 8 связанных репозиториев.'); return; }
    this.busy = true;
    this.state.submitting = true;
    this.controls();
    this.render();
    this.message('Запускаем индексацию связанного репозитория…');
    try {
      const response = await this.fetcher('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Не удалось запустить индексацию.');
      const job = data.job;
      this.state.job = job;
      this.state.submitting = false;
      this.controls();
      await monitorJob(job, {
        read: async id => {
          const response = await this.fetcher(`/api/jobs/${encodeURIComponent(id)}`);
          const current = await response.json();
          if (!response.ok) throw new Error(current.error || 'Не удалось получить состояние индексации.');
          return current;
        },
        isCurrent: id => this.state.job?.id === id,
        onState: async current => {
          this.state.job = current;
          this.message(current.status === 'completed' ? 'Индекс готов. Добавляем связанный репозиторий…' : current.status === 'failed' ? `Индексация не удалась: ${current.error || 'неизвестная ошибка'}` : 'Индексируем связанный репозиторий… основной остаётся открытым.');
          if (current.status === 'completed') {
            await this.refresh();
            const attached = this.attach(data.project.id, primaryId);
            this.message(!attached && data.project.id !== primaryId ? 'Индекс готов, но лимит 8 связей уже занят. Уберите связь и добавьте индекс вручную.' : data.project.id === primaryId ? 'Основной репозиторий переиндексирован. Обновите его карту.' : `Индекс готов и добавлен к ${this.state.projects.find(project => project.id === primaryId)?.name || primaryId}.`);
          }
        },
        onTransientError: (error, delay) => this.message(`Индексация продолжается; связь с сервером недоступна. Проверим через ${Math.ceil(delay / 1000)} с: ${error.message}`),
        ...(this.wait ? { wait: this.wait } : {}),
      });
      if (this.state.job?.id === job.id) this.state.job = null;
    } catch (error) { this.message(error.message); }
    finally { this.state.submitting = false; this.busy = false; this.controls(); this.render(); }
  }
}
