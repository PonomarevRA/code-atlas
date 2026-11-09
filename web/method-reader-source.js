/** Bounded description queue: explicit reading outranks queued previews in one pinned snapshot. */
export class MethodReaderSource {
  constructor({ fetcher = (...args) => fetch(...args), maxEntries = 32, concurrency = 2 } = {}) {
    this.fetcher = fetcher; this.maxEntries = Math.min(32, Math.max(1, maxEntries)); this.concurrency = Math.min(2, Math.max(1, concurrency));
    this.cache = new Map(); this.requests = new Map(); this.queue = []; this.active = 0; this.generation = 0;
  }
  /** A new repository or revision cancels requests before sharing any cached source facts. */
  setContext(context) {
    const key = JSON.stringify([context?.projectId, context?.snapshotId]);
    if (key === this.key) return;
    this.cancel(true); this.context = { ...context }; this.key = key;
  }
  /** Reuse compatible path facts; only raw IDs with the current snapshot are admitted. */
  seed(id, data) {
    if (data?.nodeId !== id || data.snapshotId !== this.context?.snapshotId) return false;
    this.store(id, data); return true;
  }
  /** The cache owns only bounded descriptions, not the complete graph or browser history. */
  store(id, data) {
    this.cache.delete(id); this.cache.set(id, data);
    while (this.cache.size > this.maxEntries) this.cache.delete(this.cache.keys().next().value);
  }
  /** Queue one raw method; retries replace cached failures and selections move pending work to the front. */
  load(id, { priority = false, force = false } = {}) {
    if (!this.context?.projectId || !this.context?.snapshotId || !id) return Promise.reject(new Error('Контекст метода недоступен.'));
    if (!force && this.cache.has(id)) {
      const data = this.cache.get(id); this.store(id, data); return Promise.resolve(data);
    }
    const existing = this.requests.get(id);
    if (existing) {
      if (priority && this.queue.includes(existing)) this.queue = [existing, ...this.queue.filter(entry => entry !== existing)];
      return existing.promise;
    }
    const entry = { id, generation: this.generation, context: { ...this.context }, controller: new AbortController() };
    entry.promise = new Promise((resolve, reject) => { entry.resolve = resolve; entry.reject = reject; });
    this.requests.set(id, entry);
    if (priority) this.queue.unshift(entry); else this.queue.push(entry);
    this.pump(); return entry.promise;
  }
  /** Active aborted fetches still count until settled, preventing cross-context concurrency spikes. */
  pump() {
    while (this.active < this.concurrency && this.queue.length) {
      const entry = this.queue.shift(); this.active++;
      this.fetch(entry).then(entry.resolve, entry.reject).finally(() => {
        this.active--; if (this.requests.get(entry.id) === entry) this.requests.delete(entry.id); this.pump();
      });
    }
  }
  /** Validate identity even when the transport ignores AbortSignal or returns an old response. */
  async fetch(entry) {
    const response = await this.fetcher(`/api/projects/${encodeURIComponent(entry.context.projectId)}/node-description`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: entry.controller.signal,
      body: JSON.stringify({ nodeId: entry.id, snapshotId: entry.context.snapshotId }),
    });
    const data = await response.json();
    if (entry.generation !== this.generation || entry.controller.signal.aborted) throw new DOMException('Контекст изменился.', 'AbortError');
    if (!response.ok) throw new Error(data.error || `Ошибка описания: ${response.status}`);
    if (data.nodeId !== entry.id || data.snapshotId !== entry.context.snapshotId) throw new Error('Описание относится к другому методу или снимку.');
    this.store(entry.id, data); return data;
  }
  /** Closing cancels pending work; successful descriptions may remain reusable in the same snapshot. */
  cancel(clear = false) {
    this.generation++;
    const cancelled = new DOMException('Чтение отменено.', 'AbortError');
    for (const entry of this.requests.values()) { entry.controller.abort(); entry.reject(cancelled); }
    this.queue = []; this.requests.clear();
    if (clear) this.cache.clear();
  }
}
