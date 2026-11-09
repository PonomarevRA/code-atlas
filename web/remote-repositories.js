import { $, el } from './dom.js';
import { monitorJob } from './job-monitor.js';
/** Фоновое получение Git не блокирует просмотр карты и не переключает выбор после действий пользователя. */
export class RemoteRepositoryPanel {
  constructor({ state, controls, refreshProjects, openProject, fetcher = (...args) => fetch(...args), storage, wait }) {
    Object.assign(this, { state, controls, refreshProjects, openProject, fetcher, wait });
    try { this.storage = storage || globalThis.localStorage; } catch { this.storage = null; }
    $('source-mode').addEventListener('change', () => this.renderMode());
    $('remote-form').addEventListener('submit', event => { event.preventDefault(); this.submit(); });
    this.renderMode();
  }
  /** Выбор источника не меняет открытый репозиторий и его фильтры. */
  renderMode() {
    const remote = $('source-mode').value === 'remote';
    $('remote-form').hidden = !remote;
    $('project-form').hidden = remote;
  }
  /** Единый доступ к API; сообщения сервера уже не содержат stderr Git. */
  async request(path, body) {
    const options = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const response = await this.fetcher(path, options);
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.error || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  /** Создаёт только одну задачу; pending POST тоже блокирует повторную отправку. */
  async submit() {
    if (this.job || this.state.submitting || this.state.job) return;
    const revision = this.state.revision;
    this.state.submitting = true;
    this.controls();
    $('remote-submit').disabled = true;
    $('remote-status').textContent = 'Начинаем получение Git…';
    try {
      const result = await this.request('/api/remote-repositories', { url: $('remote-url').value.trim(), ref: $('remote-ref').value.trim() });
      this.job = result.job;
      try { this.storage?.setItem('code-atlas-remote-job', this.job.id); } catch { /* Задача продолжится без восстановления после reload. */ }
      await this.monitor(revision);
    } catch (error) { $('remote-status').textContent = error.message; }
    finally { this.finish(); }
  }
  /** Возвращает возможность действий только после завершения monitoring, не при единичной сетевой ошибке. */
  finish() {
    this.job = null;
    this.state.submitting = false;
    $('remote-submit').disabled = false;
    this.controls();
    try { this.storage?.removeItem('code-atlas-remote-job'); } catch { /* Приватный режим может запретить запись. */ }
  }
  /** Восстанавливает известную задачу после reload; новый clone не запускается. */
  async resume() {
    let id;
    try { id = this.storage?.getItem('code-atlas-remote-job'); } catch { return false; }
    if (!id) return false;
    this.state.submitting = true;
    this.controls();
    $('remote-submit').disabled = true;
    try {
      this.job = { id, status: 'queued' };
      await this.monitor(this.state.revision);
    } catch (error) { $('remote-status').textContent = error.message; }
    finally { this.finish(); }
    return true;
  }
  /** Polling сохраняет busy при разрыве связи; завершение не перехватывает выбранный пользователем проект. */
  async monitor(originalRevision) {
    await monitorJob(this.job, {
      read: async id => {
        try { return await this.request(`/api/remote-repositories/jobs/${encodeURIComponent(id)}`); }
        catch (error) {
          if (error.status === 404) return { id, status: 'failed', error: 'Задача больше не доступна, возможно сервер был перезапущен. Проверьте список индексов и локальные копии перед повторным получением.' };
          throw error;
        }
      },
      isCurrent: id => this.job?.id === id,
      onState: async current => {
        this.job = current;
        const messages = { queued: 'Ожидаем Git…', cloning: 'Получаем репозиторий и подготавливаем безопасную локальную копию…', indexing: 'Репозиторий получен. Строим индекс…', completed: 'Локальная копия и индекс готовы.', failed: current.error || 'Не удалось получить репозиторий.' };
        $('remote-status').textContent = messages[current.status] || current.status;
        if (current.coverage) $('remote-status').append(el('p', `Файлов: ${current.coverage.files}. Пропущено ссылок/специальных записей: ${current.coverage.skippedEntries}; подмодулей: ${current.coverage.skippedSubmodules || 0}.`, 'small muted'));
        if (current.status === 'failed' && current.path) $('remote-status').append(el('p', `Локальная копия сохранена: ${current.path}. Её можно открыть как локальную папку.`, 'small muted'));
        if (current.status === 'completed') {
          await this.refreshProjects();
          if (this.state.revision === originalRevision) await this.openProject(current.projectId);
        }
      },
      onTransientError: (error, delay) => { $('remote-status').textContent = `Задача продолжается. Сервер недоступен, повтор через ${Math.ceil(delay / 1000)} с.`; },
      ...(this.wait ? { wait: this.wait } : {}),
    });
  }
}
