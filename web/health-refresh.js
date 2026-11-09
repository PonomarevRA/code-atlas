import {beginLoading} from './loading-state.js';
import { el } from './dom.js';
import { requestJson } from './bounded-request.js';

/** Job completion is different from score completeness: unknown components remain visible. */
export function healthRefreshText(job) {
  const labels = { queued: 'Здоровье карты: задача в очереди', running: 'Здоровье карты: идёт пересчёт',
    failed: 'Пересчёт здоровья не завершён', interrupted: 'Пересчёт прерван при остановке Atlas',
    unavailable: 'Производная оценка недоступна', 'not-requested': 'Фоновый пересчёт ещё не запускался',
    superseded: 'Для пересчёта нужна новая версия карты' };
  if (job?.status === 'completed') return `Здоровье карты обновлено · H определено: ${job.assessedCount ?? '?'} из ${job.methodCount ?? '?'} · неизвестно: ${job.unknownCount ?? '?'}${job.notApplicableCount ? ` · без тела: ${job.notApplicableCount}` : ''}`;
  return labels[job?.status] || 'Статус здоровья карты недоступен';
}

/** Replace only derived fields on existing records; raw identities, layout and source facts stay owned by their readers. */
export function applyHealthUpdates(records, incoming) {
  for (const record of records) {
    if (!record) continue;
    const facts = incoming.get(record.nodeId || record.id);
    if (!facts) continue;
    for (const key of ['health', 'documentationHealth']) if (facts[key]) record[key] = { ...record[key], ...facts[key] };
  }
}

/** Poll one pinned revision; discard stale responses and keep the last map usable on failures. */
export class HealthRefreshController {
  /** Bind an isolated status surface and injectable request/timer adapters for lifecycle verification. */
  constructor(root, { request = requestJson, onReady = () => {}, schedule = (fn, delay) => setTimeout(fn, delay), cancel = id => clearTimeout(id) } = {}) {
    this.root = root; this.request = request; this.onReady = onReady; this.schedule = schedule; this.cancel = cancel;
    this.revision = 0; this.message = el('p', '', 'small muted'); this.button = el('button', 'Пересчитать здоровье карты');
    this.message.setAttribute('role', 'status'); this.message.setAttribute('aria-live', 'polite');
    root.append(this.message, this.button); root.hidden = true;
    this.button.addEventListener('click', () => this.refresh());
  }
  /** Cancel a former project/revision and observe only explicit active jobs. */
  async watch(scope) {
    this.stop(); this.scope = scope; this.root.hidden = !scope?.projectId || !scope?.snapshotId;
    if (this.root.hidden) return;
    this.sawActive = false;
    return this.read(this.revision);
  }
  /** Abort network/timers on scope changes and page exit. */
  stop() { this.revision++; this.cancel(this.timer); this.abort?.abort(); this.loadingRelease?.(); this.loadingRelease = null; }
  /** Fixed-origin relative URLs carry a snapshot guard; no source text is sent. */
  path() { return `/api/projects/${encodeURIComponent(this.scope.projectId)}/code-health/refresh`; }
  /**
   * Show the observed job and keep one background token while queued/running.
   *
   * job is a pinned server observation. Terminal observations release the token;
   * textContent and the existing retry button retain the page's error semantics.
   */
  render(job) {
    const active = ['queued', 'running'].includes(job.status);
    if (active && !this.loadingRelease) this.loadingRelease = beginLoading({ru:'Пересчёт здоровья карты…', en:'Updating map health…'});
    if (!active) {this.loadingRelease?.(); this.loadingRelease = null;}
    this.message.textContent = healthRefreshText(job);
    this.button.disabled = ['queued', 'running'].includes(job.status);
  }
  /** Re-read until terminal state; one callback refreshes displayed facts after an observed active job. */
  async read(revision) {
    this.abort = new AbortController();
    try {
      const job = await this.request(`${this.path()}?snapshotId=${encodeURIComponent(this.scope.snapshotId)}`, { signal: this.abort.signal }, { timeoutMs: 15000 });
      if (revision !== this.revision) return;
      this.render(job);
      if (['queued', 'running'].includes(job.status)) {
        this.sawActive = true;
        this.timer = this.schedule(() => this.read(revision), 2000);
      } else if (job.status === 'completed' && this.sawActive) {
        this.sawActive = false;
        await this.onReady(this.scope, job);
      }
    } catch (error) {
      if (revision !== this.revision || error.name === 'AbortError') return;
      this.loadingRelease?.(); this.loadingRelease = null;
      this.message.textContent = `Статус здоровья: ${error.message}`;
      this.button.disabled = false;
    }
  }
  /** Explicit retries schedule captured-fact analysis, independently of rebuilding the source map. */
  async refresh() {
    if (!this.scope || this.button.disabled) return;
    const revision = this.revision, scope = this.scope;
    this.button.disabled = true;
    try {
      await this.request(this.path(), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ snapshotId: scope.snapshotId }) }, { timeoutMs: 15000 });
      if (revision !== this.revision) return;
      this.sawActive = true;
      await this.read(revision);
    } catch (error) {
      if (revision !== this.revision) return;
      this.message.textContent = error.message; this.button.disabled = false;
    }
  }
}
