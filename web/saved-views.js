import { el } from './dom.js';

/** Форма создаёт самостоятельный артефакт, не меняя фильтры или выбор карты. */
export class SavedViewsPanel {
  constructor(root, { getContext, fetcher = (...args) => fetch(...args) }) {
    this.root = root;
    this.getContext = getContext;
    this.fetcher = fetcher;
    this.revision = 0;
    this.listRevision = 0;
    this.context = '';
    this.field('create').addEventListener('click', () => this.create());
    this.field('refresh').addEventListener('click', () => this.refresh());
  }
  /** Поля изолированы в панели снимков. */
  field(name) { return this.root.querySelector(`[data-saved="${name}"]`); }
  /** Смена выбора отменяет показ старого ответа; созданный сервером снимок остаётся в списке. */
  sync() {
    const context = JSON.stringify(this.getContext());
    if (context === this.context) return;
    this.context = context;
    this.revision++;
    this.controller?.abort();
    const selected = this.getContext();
    this.field('create').disabled = !selected.projectId || !selected.snapshotId;
    this.field('status').textContent = selected.nodeId ? `Основа: ${selected.label || selected.nodeId}. Поиск добавляет узлы к выбранному.` : 'Выберите узел или задайте слова для поиска в снимке репозитория.';
  }
  /** JSON API; текущий исходный код не передаётся. */
  async request(url, body, signal) {
    const options = body === undefined ? { signal } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal };
    const response = await this.fetcher(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  /** Сохраняет ограниченную область графа и явно отделённую авторскую заметку. */
  async create() {
    this.sync();
    const context = this.getContext();
    if (!context.projectId || !context.snapshotId) return;
    const search = this.field('query').value.trim();
    if (!context.nodeId && !search) { this.field('status').textContent = 'Выберите узел или введите слова для поиска.'; return; }
    const note = this.field('note').value.trim();
    if (note && !context.nodeId) { this.field('status').textContent = 'Для пояснения выберите конкретный узел; поиск без пояснения доступен отдельно.'; return; }
    const body = {
      projectId: context.projectId, snapshotId: context.snapshotId,
      title: this.field('title').value.trim() || context.label || 'Снимок кода',
      query: search, search, nodeIds: context.nodeId ? [context.nodeId] : [],
      excludeKinds: this.field('exclude').value.split(/[\s,]+/).filter(Boolean),
      excludeNodeIds: [...new Set(this.field('exclude-nodes').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean))],
      depth: Math.max(0, Math.min(3, Number(this.field('depth').value) || 0)),
      maxNodes: Math.max(1, Math.min(120, Number(this.field('max-nodes').value) || 40)),
      annotations: note ? [{ id: 'note-1', title: 'Пояснение автора', text: note, nodeIds: context.nodeId ? [context.nodeId] : [] }] : [],
    };
    if (body.excludeNodeIds.length > 120) { this.field('status').textContent = 'Допустимо исключить до 120 узлов по ID.'; return; }
    const revision = ++this.revision;
    this.controller?.abort();
    this.controller = new AbortController();
    this.field('create').disabled = true;
    this.field('status').textContent = 'Сохраняем неизменяемый снимок…';
    try {
      const data = await this.request('/api/views', body, this.controller.signal);
      if (revision !== this.revision || this.context !== JSON.stringify(this.getContext())) return;
      const view = data.view || data;
      const link = el('a', 'Открыть сохранённый снимок');
      link.href = `/view.html?id=${encodeURIComponent(view.id)}`;
      link.target = '_blank';
      link.rel = 'noopener';
      this.field('status').replaceChildren(link);
      await this.refresh();
    } catch (error) {
      if (revision === this.revision) this.field('status').textContent = error.message;
    } finally { if (revision === this.revision) this.field('create').disabled = false; }
  }
  /** Список показывает все сохранённые артефакты; ссылки остаются локальными. */
  async refresh() {
    const revision = ++this.listRevision;
    try {
      const data = await this.request('/api/views');
      if (revision !== this.listRevision) return;
      const container = this.field('list');
      container.replaceChildren();
      for (const view of data.views || []) {
        const link = el('a', view.title || view.id);
        link.href = `/view.html?id=${encodeURIComponent(view.id)}`;
        link.target = '_blank';
        link.rel = 'noopener';
        container.append(link);
      }
      if (!container.childNodes.length) container.append(el('p', 'Сохранённых снимков пока нет.', 'small muted'));
      if (data.truncated) container.append(el('p', 'Показаны последние 100 сохранённых снимков.', 'small muted'));
    } catch (error) { if (revision === this.listRevision) this.field('list').textContent = error.message; }
  }
}
