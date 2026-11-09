import { el } from './dom.js';

/** Строит ограниченные ключевые слова; не читает и не отправляет исходник. */
export function documentationQuery(label, project = '') {
  return `${label || ''} ${project || ''}`.replace(/([a-zа-я0-9])([A-ZА-Я])/g, '$1 $2').replace(/([A-ZА-Я])([A-ZА-Я][a-zа-я])/g, '$1 $2').replace(/[_\s]+/g, ' ').trim().slice(0, 500);
}
/** Разрешает только обычные web-ссылки без исполнения схем из ответа инструмента. */
export function safeDocumentationUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

/** Независимый контроллер документации: настройка не требуется для работы карты. */
export class DocumentationViewer {
  constructor(root, { getScope, fetcher = (...args) => fetch(...args) }) {
    this.root = root;
    this.getScope = getScope;
    this.fetcher = fetcher;
    this.version = null;
    this.revision = 0;
    this.configurationRevision = 0;
    this.node = null;
    this.tools = new Map();
    this.scope = JSON.stringify(getScope());
    this.field('auto').checked = false;
    this.field('discover').addEventListener('click', () => this.discover());
    this.field('save').addEventListener('click', () => this.save());
    this.field('disconnect').addEventListener('click', () => this.disconnect());
    this.field('search').addEventListener('click', () => this.search());
    this.field('query').addEventListener('input', () => { this.cancel(); this.schedule(); });
    this.field('auto').addEventListener('change', () => { this.cancel(); this.schedule(); });
    for (const name of ['url', 'tool', 'query-argument', 'arguments']) {
      this.field(name).addEventListener('input', () => {
        this.configurationRevision++;
        this.version = null;
        this.cancel();
        const selectedTool = this.tools.get(this.field('tool').value);
        const detail = selectedTool ? [selectedTool.description, selectedTool.warning].filter(Boolean).join(' · ') : '';
        this.field('config-status').textContent = `Настройки изменены. Сохраните подключение перед поиском. ${detail}`;
      });
    }
  }
  /** Имена полей локальны панели, поэтому контроллер не зависит от глобального DOM. */
  field(name) { return this.root.querySelector(`[data-docs="${name}"]`); }
  /** Прерывает запрос и инвалидирует даже transport, который игнорирует AbortSignal. */
  cancel() {
    clearTimeout(this.timer);
    this.controller?.abort();
    this.revision++;
    this.field('search').disabled = false;
    this.field('results').replaceChildren();
    this.field('status').textContent = '';
  }
  /** Смена репозитория/снимка очищает привязку и старый результат. */
  sync() {
    const scope = JSON.stringify(this.getScope());
    if (scope === this.scope) return;
    this.scope = scope;
    this.node = null;
    this.cancel();
    this.field('query').value = '';
  }
  /** Поддерживает как raw, так и агрегированные узлы: используется только имя. */
  select(node) {
    this.sync();
    this.cancel();
    this.node = node || null;
    this.field('query').value = node ? documentationQuery(node.label) : '';
    this.schedule();
  }
  /** Автопоиск разрешён только явным checkbox; ввод сглаживается задержкой. */
  schedule() {
    if (!this.field('auto').checked || this.version === null || !this.node) return;
    this.timer = setTimeout(() => this.search(), 350);
  }
  /** Выполняет JSON API и переводит ошибки в пользовательский текст. */
  async request(path, body, signal) {
    const options = body === undefined ? { signal } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal };
    const response = await this.fetcher(`/api/documentation-mcp/${path}`, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  /** Восстанавливает настройки, но никогда не запускает инструмент автоматически. */
  async loadConfig() {
    const revision = this.configurationRevision;
    try {
      const data = await this.request('config');
      if (revision !== this.configurationRevision) return;
      const config = data.config;
      if (!config) {
        this.version = null;
        this.cancel();
        this.field('url').value = '';
        this.field('arguments').value = '{}';
        this.field('query-argument').value = 'query';
        const placeholder = el('option', 'Выберите инструмент явно');
        placeholder.value = '';
        this.field('tool').replaceChildren(placeholder);
        this.field('tool').value = '';
        this.field('config-status').textContent = 'MCP документации не настроен.';
        return;
      }
      this.field('url').value = config.url || '';
      this.field('query-argument').value = config.queryArgument || 'query';
      this.field('arguments').value = JSON.stringify(config.arguments || {}, null, 2);
      const placeholder = el('option', 'Выберите инструмент явно');
      placeholder.value = '';
      this.field('tool').replaceChildren(placeholder);
      if (config.tool) {
        const option = el('option', config.tool);
        option.value = config.tool;
        this.field('tool').append(option);
        this.field('tool').value = config.tool;
      }
      this.version = data.configVersion ?? null;
      this.field('config-status').textContent = 'Сохранённое подключение восстановлено.';
    } catch (error) { if (revision === this.configurationRevision) this.field('config-status').textContent = error.message; }
  }
  /** Discovery не выбирает инструмент и не вызывает tools/call. */
  async discover() {
    const revision = ++this.configurationRevision;
    this.version = null;
    this.cancel();
    this.field('config-status').textContent = 'Получаем список инструментов…';
    try {
      const data = await this.request('tools', { url: this.field('url').value.trim() });
      if (revision !== this.configurationRevision) return;
      const placeholder = el('option', 'Выберите инструмент явно');
      placeholder.value = '';
      this.field('tool').replaceChildren(placeholder);
      this.tools.clear();
      for (const tool of data.tools || []) {
        this.tools.set(tool.name, tool);
        const option = el('option', tool.name);
        option.value = tool.name;
        option.title = [tool.description, tool.warning].filter(Boolean).join(' · ');
        this.field('tool').append(option);
      }
      this.field('tool').value = '';
      this.field('config-status').textContent = `Найдено инструментов: ${(data.tools || []).length}${data.truncated ? ' · список ограничен' : ''}. Выберите инструмент чтения и имя аргумента.`;
    } catch (error) { if (revision === this.configurationRevision) this.field('config-status').textContent = error.message; }
  }
  /** Сохраняет только явно выбранный инструмент и валидный JSON объекта аргументов. */
  async save() {
    const revision = ++this.configurationRevision;
    this.version = null;
    this.cancel();
    try {
      const tool = this.field('tool').value;
      if (!tool) throw new Error('Выберите инструмент поиска.');
      const args = JSON.parse(this.field('arguments').value || '{}');
      if (!args || Array.isArray(args) || typeof args !== 'object') throw new Error('Постоянные аргументы должны быть JSON-объектом.');
      const data = await this.request('config', { url: this.field('url').value.trim(), tool, queryArgument: this.field('query-argument').value.trim(), arguments: args });
      if (revision !== this.configurationRevision) return;
      this.version = data.configVersion ?? null;
      this.field('config-status').textContent = 'Подключение сохранено. Можно искать документацию.';
    } catch (error) { if (revision === this.configurationRevision) this.field('config-status').textContent = error.message; }
  }
  /** Отключает серверную конфигурацию и отменяет результаты прежнего подключения. */
  async disconnect() {
    const revision = ++this.configurationRevision;
    this.version = null;
    this.cancel();
    this.field('auto').checked = false;
    try {
      await this.request('disconnect', {});
      if (revision !== this.configurationRevision) return;
      this.field('url').value = '';
      this.field('arguments').value = '{}';
      this.field('query-argument').value = 'query';
      const placeholder = el('option', 'Выберите инструмент явно');
      placeholder.value = '';
      this.field('tool').replaceChildren(placeholder);
      this.field('tool').value = '';
      this.tools.clear();
      this.field('config-status').textContent = 'MCP отключён. Конфигурация удалена.';
    } catch (error) {
      if (revision === this.configurationRevision) this.field('config-status').textContent = `Не удалось отключить MCP: ${error.message}`;
    }
  }
  /** Ключ поиска включает выбор, снимок, запрос и версию подключения. */
  key() { return JSON.stringify([this.getScope(), this.node?.id, this.field('query').value, this.version]); }
  /** Выполняет выбранный поиск; устаревшие ответы не попадают в новый инспектор. */
  async search() {
    this.sync();
    this.cancel();
    const query = this.field('query').value.trim().slice(0, 500);
    if (this.version === null) { this.field('status').textContent = 'Сначала настройте и сохраните подключение MCP.'; return; }
    if (!query) { this.field('status').textContent = 'Введите ключевые слова.'; return; }
    const revision = this.revision;
    const key = this.key();
    this.controller = new AbortController();
    this.field('search').disabled = true;
    this.field('status').textContent = 'Ищем документацию…';
    try {
      const data = await this.request('search', { query, configVersion: this.version }, this.controller.signal);
      if (revision !== this.revision || key !== this.key()) return;
      if (data.configVersion !== undefined && data.configVersion !== this.version) {
        this.field('status').textContent = 'Подключение изменилось. Сохраните настройки и повторите поиск.';
        return;
      }
      this.render(data);
    } catch (error) {
      if (revision === this.revision && key === this.key()) this.field('status').textContent = error.message;
    } finally { if (revision === this.revision) this.field('search').disabled = false; }
  }
  /** Выводит текст и проверенные ссылки; разметка MCP не интерпретируется. */
  render(data) {
    const container = this.field('results');
    container.replaceChildren();
    const documents = data.documents || [];
    for (const document of documents.slice(0, 100)) {
      container.append(el('strong', document.title || 'Документ'));
      container.append(el('pre', document.text || '', 'documentation-text'));
      const url = safeDocumentationUrl(document.url);
      if (url) {
        const link = el('a', 'Открыть источник');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        container.append(link);
      }
    }
    if (data.warning) container.append(el('p', data.warning, 'small muted'));
    const count = Math.min(documents.length, 100);
    this.field('status').textContent = `Документов: ${count}${data.truncated || documents.length > 100 ? ' · ответ ограничен' : ''}${count ? '' : ' · результатов нет'}.`;
  }
}
