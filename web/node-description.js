import {requestJson} from './bounded-request.js';
import { el } from './dom.js';
import { branchLabels, renderControlFlow, renderDocumentation } from './path-annotations.js';
import { renderParameterDetails } from './parameter-details.js';
import { renderAttributeFacts } from './attribute-details.js';
import { renderComplexitySummary } from './complexity-summary.js';
import { renderInfrastructureDetails } from './infrastructure-details.js';
import { renderAstExplorer } from './ast-explorer.js';
import { renderNodeSemanticStatus } from './node-semantic-status.js';
/** Короткое независимое описание символа; запрос не запускает анализ целого пути. */
export class NodeDescriptionViewer {
  /** Инъекция fetch и исходника позволяет проверить snapshot-изоляцию без браузера. */
  constructor(root, getContext, { fetcher = (...args) => fetch(...args), onSource = () => {}, scrollOnRender = true, titlePrefix = 'Описание узла', loadingText = 'Читаем сигнатуру, документацию и факты локального графа…' } = {}) {
    this.root = root;
    this.getContext = getContext;
    this.fetcher = fetcher;
    this.onSource = onSource;
    this.scrollOnRender = scrollOnRender;
    this.titlePrefix = titlePrefix;
    this.loadingText = loadingText;
    this.revision = 0;
    this.opened = false;
    this.find = name => root.querySelector(`[data-description="${name}"]`);
    this.find('close').addEventListener('click', () => this.close());
    this.find('retry').addEventListener('click', () => this.open());
  }
  /** Описание принадлежит одному символу конкретного снимка. */
  contextKey() { const context = this.getContext(); return JSON.stringify([context.projectId, context.snapshotId, context.nodeId]); }
  /** Отменяет описание, когда пользователь перешёл к другому символу или индексу. */
  sync() { if (this.opened && this.key !== this.contextKey()) this.close(); }
  /** Скрывает панель и отвергает ответы, не поддержавшие AbortSignal. */
  close() { this.controller?.abort(); this.revision++; this.opened = false; this.root.hidden = true; }
  /**
   * Загружает описание выбранного узла, сохраняя контекст и отмену перехода.
   *
   * Общий JSON-клиент учитывает чтение тела и конечный срок ожидания.
   * Устаревший ответ не отображается; ошибка оставляет доступный повтор.
   */
  async open() {
    const context = this.getContext();
    if (!context.projectId || !context.snapshotId || !context.nodeId) return;
    this.controller?.abort();
    this.controller = new AbortController();
    const revision = ++this.revision;
    this.key = this.contextKey();
    this.opened = true;
    this.root.hidden = false;
    this.find('title').textContent = `${this.titlePrefix} · ${context.label || context.nodeId}`;
    this.find('status').textContent = this.loadingText;
    this.find('body').replaceChildren();
    this.find('retry').hidden = true;
    try {
      const data = await requestJson(`/api/projects/${encodeURIComponent(context.projectId)}/node-description`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nodeId: context.nodeId, snapshotId: context.snapshotId }), signal: this.controller.signal }, {fetcher: this.fetcher});
      if (revision !== this.revision || !this.opened || this.key !== this.contextKey()) return;
      if (data.nodeId !== context.nodeId || data.snapshotId !== context.snapshotId) throw new Error('Описание относится к другому узлу или снимку. Обновите карту.');
      this.render(data);
    } catch (error) {
      if (error.name === 'AbortError' || revision !== this.revision || !this.opened || this.key !== this.contextKey()) return;
      this.find('status').textContent = error.message;
      this.find('retry').hidden = false;
    }
  }
  /** Разделяет факты анализатора и исходные комментарии; смысл метода не выдумывается по его имени. */
  render(data) {
    this.find('status').textContent = 'Факты собраны программно из локального графа и текущих исходников. Комментарии ниже — текст разработчиков, не сгенерированное объяснение.';
    const body = this.find('body');
    body.replaceChildren();
    const context = { ...this.getContext() };
    const requestKey = this.contextKey();
    const lazyOptions = { fetcher: this.fetcher, signal: this.controller?.signal,
      isCurrent: () => this.opened && this.key === requestKey && this.contextKey() === requestKey };
    if (data.semanticEngines) body.append(renderNodeSemanticStatus(context, data.semanticEngines, lazyOptions));
    if (data.astExploration?.supported) body.append(renderAstExplorer(context, lazyOptions));
    const project = typeof data.project === 'object' ? data.project?.label || data.project?.name : data.project;
    body.append(el('h3', 'Факты о символе'), el('p', `${data.label || data.nodeId} · ${data.kind || 'символ'} · ${project || 'Без проекта'}`), el('p', `${data.file || 'Исходник не сопоставлен'}:${data.line || '?'}${data.endLine ? `–${data.endLine}` : ''}`, 'path-location'));
    if (data.signature) body.append(el('pre', String(data.signature), 'node-signature'));
    if (data.returnType) body.append(el('p', `Тип результата: ${data.returnType}`));
    if (data.complexity) body.append(renderComplexitySummary(data.complexity));
    const resources = renderInfrastructureDetails(data, this.onSource);
    if (resources) body.append(resources);
    const attributes = el('details', undefined, 'node-attribute-summary');
    attributes.append(el('summary', 'Атрибуты объявления и контекста'), renderAttributeFacts(data, this.onSource));
    body.append(attributes);
    body.append(renderParameterDetails(data, this.onSource));
    if (data.file) {
      const source = el('button', 'Исходник символа');
      source.addEventListener('click', () => this.onSource(data.nodeId));
      body.append(source);
    }
    if (data.factualSummary) body.append(el('p', String(data.factualSummary)));
    const documentation = renderDocumentation(data.documentation);
    documentation.open = true;
    body.append(documentation);
    if (data.typeContext) {
      body.append(el('p', `Содержащий тип: ${data.typeContext.name || '?'} · ${data.typeContext.kind || ''}`), renderDocumentation(data.typeContext.documentation, 'Документация содержащего типа'));
    }
    body.append(renderControlFlow(data.controlFlow));
    const calls = el('details', undefined, 'node-call-facts');
    calls.append(el('summary', 'Входящие и исходящие связи снимка'));
    for (const [direction, label] of [['incoming', 'Входящие'], ['outgoing', 'Исходящие']]) {
      const edges = data.calls?.[direction] || [];
      const total = data.counts?.[direction] ?? edges.length;
      calls.append(el('strong', `${label}: показано ${Math.min(edges.length, 50)} из ${total}`));
      for (const edge of edges.slice(0, 50)) {
        const confidence = edge.confidence === 'candidate' ? 'возможная цель' : edge.confidence || 'достоверность не указана';
        calls.append(el('p', `${edge.sourceLabel || edge.source || '?'} → ${edge.targetLabel || edge.target || '?'} · ${edge.relation || 'связь'} · ${confidence}`));
        if (direction === 'outgoing' && edge.branchContextKnown) {
          const conditions = branchLabels(data.controlFlow, edge.branchIds);
          if (conditions.length) calls.append(el('p', `Условия: ${conditions.join(' → ')}`, 'path-event-conditions'));
        }
      }
      if (total > Math.min(edges.length, 50)) calls.append(el('p', 'Список ограничен; общее число включает связи за пределами списка.', 'small muted'));
    }
    body.append(calls);
    const truncated = Object.entries(data.truncation || {}).filter(([, value]) => value).map(([key]) => ({ incoming: 'входящие связи', outgoing: 'исходящие связи', documentation: 'документация', controlFlow: 'ветвления', declaration: 'объявление', files: 'файлы', bytes: 'объём исходника', events: 'события' }[key] || key));
    if (truncated.length) body.append(el('p', `Ограничены: ${truncated.join(', ')}.`, 'small muted'));
    if (data.analyzerVersion) body.append(el('p', `Локальный анализатор: ${data.analyzerVersion} · текущие исходники, связи снимка.`, 'small muted'));
    if (data.warning) body.append(el('p', String(data.warning), 'small muted'));
    if (this.scrollOnRender) this.root.scrollIntoView?.({ behavior: 'instant', block: 'start' });
  }
}
