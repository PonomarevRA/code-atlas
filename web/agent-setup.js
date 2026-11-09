/** Форматирует конфигурацию без интерпретации текста или выполнения команд. */
export function agentConfigJson(config) { return JSON.stringify(config, null, 2); }
/** Разрешает только локальные маршруты скачивания пакета Code Atlas. */
export function agentDownloadPath(value) {
  if (typeof value !== 'string' || !value.startsWith('/api/agent-setup/')) return null;
  try {
    const url = new URL(value, 'http://localhost');
    if (url.origin !== 'http://localhost' || !['/api/agent-setup/config', '/api/agent-setup/skill', '/api/agent-setup/skill.zip'].includes(url.pathname)) return null;
    return `${url.pathname}${url.search}`;
  } catch { return null; }
}
/** Показывает инструкции и готовые файлы, но не устанавливает настройки редактора. */
export class AgentSetupPanel {
  constructor(root, { getContext, fetcher = (...args) => fetch(...args), clipboard = globalThis.navigator?.clipboard }) {
    this.root = root;
    this.getContext = getContext;
    this.fetcher = fetcher;
    this.clipboard = clipboard;
    this.revision = 0;
    this.key = '';
    this.loadedKey = null;
    root.addEventListener('toggle', () => {
      if (root.open) { this.sync(); if (this.loadedKey !== this.key && !this.loading) this.load(); }
    });
    this.field('retry').addEventListener('click', () => this.load());
    this.field('copy').addEventListener('click', () => this.copy());
    this.field('copy-codex')?.addEventListener('click', () => this.copy('codex-config'));
    this.sync();
  }
  /** Находит поле только внутри панели подключения. */
  field(name) { return this.root.querySelector(`[data-agent="${name}"]`); }
  /** Смена индекса сразу убирает устаревшие JSON и ссылки, даже если панель закрыта. */
  sync() {
    const key = JSON.stringify(this.getContext());
    if (key === this.key) return;
    this.key = key;
    this.revision++;
    this.controller?.abort();
    this.loading = false;
    this.field('retry').disabled = false;
    this.field('configuration')?.setAttribute('aria-busy', 'false');
    this.loadedKey = null;
    this.field('config').value = '';
    const codex = this.field('codex-config');
    if (codex) codex.value = '';
    this.field('copy').disabled = true;
    const copyCodex = this.field('copy-codex');
    if (copyCodex) copyCodex.disabled = true;
    this.field('warning').textContent = '';
    for (const name of ['config', 'skill', 'zip', 'codex']) {
      const link = this.field(`download-${name}`);
      if (!link) continue;
      link.hidden = true;
      link.removeAttribute('href');
    }
    const context = this.getContext();
    this.field('context').textContent = context.projectId ? 'Настройка для выбранного репозитория.' : 'Репозиторий не выбран: общая конфигурация всех индексов.';
    this.field('status').textContent = 'Раскройте панель, чтобы получить актуальные файлы подключения.';
    if (this.root.open) this.load();
  }
  /** Получает конфигурацию с реальным портом сервера и выбранной областью MCP. */
  async load() {
    this.controller?.abort();
    this.controller = new AbortController();
    const revision = ++this.revision;
    const context = this.getContext();
    const key = JSON.stringify(context);
    this.loading = true;
    this.loadedKey = null;
    this.field('config').value = '';
    const codex = this.field('codex-config');
    if (codex) codex.value = '';
    this.field('copy').disabled = true;
    const copyCodex = this.field('copy-codex');
    if (copyCodex) copyCodex.disabled = true;
    for (const name of ['config', 'skill', 'zip', 'codex']) {
      const link = this.field(`download-${name}`);
      if (!link) continue;
      link.hidden = true;
      link.removeAttribute('href');
    }
    this.field('retry').disabled = true;
    const configuration = this.field('configuration');
    configuration?.setAttribute('aria-busy', 'true');
    this.field('status').textContent = 'Готовим конфигурацию MCP…';
    try {
      const query = context.projectId ? `?projectId=${encodeURIComponent(context.projectId)}` : '';
      const response = await this.fetcher(`/api/agent-setup${query}`, { signal: this.controller.signal });
      const data = await response.json();
      if (revision !== this.revision || key !== JSON.stringify(this.getContext())) return;
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      if ((data.project?.id || null) !== (context.projectId || null)) throw new Error('Область конфигурации изменилась. Повторите загрузку.');
      if (!data.mcpConfig?.mcpServers || typeof data.mcpConfig.mcpServers !== 'object') throw new Error('Сервер не вернул конфигурацию MCP. Повторите загрузку.');
      this.loadedKey = key;
      this.field('config').value = agentConfigJson(data.mcpConfig);
      if (codex) codex.value = data.codexConfig || '';
      this.field('copy').disabled = false;
      if (copyCodex) copyCodex.disabled = !codex?.value;
      this.field('context').textContent = data.project ? `${data.project.name} · ${data.project.path} · статус: ${data.project.status}` : 'Все сохранённые репозитории. Для ограничения одним проектом используйте конфигурацию его индекса.';
      this.field('warning').textContent = data.warning || '';
      for (const name of ['config', 'skill', 'zip', 'codex']) {
        const path = agentDownloadPath(data.downloads?.[name]);
        const link = this.field(`download-${name}`);
        if (!link) continue;
        link.hidden = !path;
        if (path) link.href = path;
      }
      this.field('status').textContent = `Конфигурация готова · ${data.serverUrl || 'локальный сервер'}. Подключение проверьте в клиенте.`;
    } catch (error) {
      if (revision === this.revision && key === JSON.stringify(this.getContext())) this.field('status').textContent = error.message;
    } finally {
      if (revision === this.revision) {
        this.loading = false;
        this.field('retry').disabled = false;
        configuration?.setAttribute('aria-busy', 'false');
      }
    }
  }
  /** Копирует конфигурацию выбранного клиента; старый результат не меняет статус нового проекта. */
  async copy(field = 'config') {
    const input = this.field(field);
    const value = input?.value;
    if (!value) return;
    const isCodex = field === 'codex-config';
    const format = isCodex ? 'TOML' : 'JSON';
    const revision = this.revision;
    try {
      if (!this.clipboard?.writeText) throw new Error('Буфер обмена недоступен');
      await this.clipboard.writeText(value);
      if (revision !== this.revision) return;
      this.field('status').textContent = isCodex
        ? 'TOML скопирован. Добавьте секцию в настройки MCP Codex.'
        : 'JSON скопирован. Объедините запись mcpServers с существующим .cursor/mcp.json.';
    } catch {
      if (revision !== this.revision) return;
      input.focus();
      input.select();
      this.field('status').textContent = `Скопируйте выделенный ${format} вручную или скачайте файл.`;
    }
  }
}
