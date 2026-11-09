import { el } from './dom.js';
import { MethodReaderSource } from './method-reader-source.js';
import { normalizeReaderState, readerDependencyIndex, readerPreview, readerPurpose, readerNodeLabel } from './method-reader-model.js';
import { renderReaderSteps } from './method-reader-steps.js';
import { complexityText } from './complexity-badge.js';
import { readingBreadcrumbs, readingCallContext, qualifiedMethodLabel } from './reading-navigation.js';
import { renderReadingProvenance } from './reading-provenance.js';
import { renderReaderContract } from './reading-explanations.js';

const DEPENDENCIES = { calls: 'Вызывает', reads: 'Читает', writes: 'Записывает', initializes_from: 'Инициализация',
  inherits: 'Наследует', inherits_from: 'Наследует', extends: 'Наследует', implements: 'Реализует', dispatches_to: 'Возможная реализация' };

/** Bounded implementation comparison and source reading occupy the existing map workspace. */
export class MethodWorkbench {
  constructor(root, { getContext, getNode, getGraph, getDescription = () => null, onSource, onClose,
    onReturnToCall, onReturnToEntry, onRefreshMap, onChange = () => {}, fetcher }) {
    Object.assign(this, { root, getContext, getNode, getGraph, getDescription, onSource, onClose,
      onReturnToCall, onReturnToEntry, onRefreshMap, onChange });
    this.source = new MethodReaderSource({ fetcher }); this.records = new Map(); this.generation = 0; this.active = false;
    this.comparisons = new Map();
    this.find = name => root.querySelector(`[data-reader="${name}"]`);
    this.find('diagram').addEventListener('click', () => this.close());
    this.find('back').addEventListener('click', () => this.back());
    root.addEventListener('scroll', () => { if (this.active && !this.restoring) this.onChange(this.snapshot(), { replace: true }); });
  }
  /** Bind one snapshot, build its adjacency once, and preserve the actual call occurrence. */
  open(call, { autoload = true, notify = true } = {}) {
    this.context = { ...this.getContext() }; this.source.setContext(this.context);
    this.generation++; this.records.clear(); this.trail = []; this.active = true; this.root.hidden = false;
    const targetIds = [...new Set([...(call.implementationItems || []).map(item => item.id), ...(call.targetIds || [])])].slice(0, 50);
    this.origin = { originMode: call.originMode || 'diagram', occurrenceId: call.occurrenceId,
      ownerNodeId: call.ownerNodeId, line: call.line, targetIds,
      candidateCount: call.candidateCount || targetIds.length, candidateCountExact: call.candidateCountExact !== false };
    this.callSite = { ...this.origin }; this.expression = call.expression || '';
    this.refreshGraph();
    this.catalog = (call.implementationItems?.length ? call.implementationItems : targetIds.map(id => ({ id,
      ...this.catalogItem(id) }))).slice(0, 50);
    this.total = call.candidateCount || this.catalog.length; this.exact = call.candidateCountExact !== false;
    this.listLimited = Boolean(call.implementationListLimited || this.total > this.catalog.length);
    this.saveComparison();
    this.mode = this.catalog.length > 1 || this.total > 1 ? 'compare' : 'method';
    this.focusId = this.mode === 'method' ? this.catalog[0]?.id || targetIds[0] || call.ownerNodeId : null;
    this.root.scrollTop = 0; this.render(); if (notify) this.onChange(this.snapshot());
    if (!autoload) return Promise.resolve();
    if (this.mode === 'method') return this.ensure(this.focusId, true);
    const project = this.catalog.filter(item => (item.scope || 'source') === 'source');
    for (const item of project.slice(0, 6)) this.ensure(item.id);
    return Promise.resolve();
  }
  /** Loaded full-snapshot edges stay separate from source-order facts and are never reconstructed from positions. */
  refreshGraph() {
    const graph = this.getGraph();
    if (graph !== this.graph) { this.graph = graph; this.dependencies = readerDependencyIndex(graph); }
  }
  /** Candidate categories follow captured paths/dispatch calls; runtime registration is still unknown. */
  catalogItem(id) {
    const node = this.getNode(id), file = node?.file || '';
    const tests = file.split(/[\\/]/).some(part => ['test', 'tests'].includes(part.toLowerCase()));
    const forwards = (this.dependencies?.get(id) || []).some(edge => edge.relation === 'calls'
      && (this.dependencies.get(edge.target) || []).some(dispatch => dispatch.relation === 'dispatches_to' && dispatch.target === id));
    return { id, label: readerNodeLabel(node, id), line: node?.line, scope: tests ? 'tests' : forwards ? 'delegate' : 'source' };
  }
  /** Keep a small live inventory cache; history stores only IDs and known counts. */
  saveComparison() {
    if (!this.callSite?.occurrenceId) return;
    const key = JSON.stringify([this.context.projectId, this.context.snapshotId, this.callSite.occurrenceId]);
    this.comparisons.set(key, { items: this.catalog, total: this.total, exact: this.exact, expression: this.expression });
    while (this.comparisons.size > 17) this.comparisons.delete(this.comparisons.keys().next().value);
  }
  /** Restore a previous comparison after drilling without substituting the nested call's candidates. */
  restoreComparison() {
    const key = JSON.stringify([this.context.projectId, this.context.snapshotId, this.callSite?.occurrenceId]);
    const cached = this.comparisons.get(key);
    this.catalog = cached?.items || (this.callSite.targetIds || []).map(id => this.catalogItem(id));
    this.total = cached?.total || this.callSite.candidateCount || this.catalog.length;
    this.exact = cached?.exact ?? this.callSite.candidateCountExact !== false;
    this.listLimited = this.total > this.catalog.length; this.expression = cached?.expression || '';
  }
  /** Direct selections take queue priority, including candidates absent from the path's returned steps. */
  async ensure(id, priority = false, force = false) {
    if (!id || !this.active) return;
    const generation = this.generation;
    const compatible = this.getDescription(id);
    if (compatible && !force) this.source.seed(id, { ...compatible, snapshotId: compatible.snapshotId || this.context.snapshotId });
    const previous = this.records.get(id);
    if (previous?.status === 'ready' && !force && this.source.cache.has(id)) return this.source.cache.get(id);
    this.records.set(id, { status: 'loading' }); this.render();
    try {
      const data = await this.source.load(id, { priority, force });
      if (!this.active || generation !== this.generation) return;
      this.records.set(id, { status: 'ready' }); this.render(); return data;
    } catch (error) {
      if (!this.active || generation !== this.generation || error.name === 'AbortError') return;
      this.records.set(id, { status: 'error', error: error.message }); this.render();
    }
  }
  /** Reading changes only the local field; fixed origin and global diagram selection remain untouched. */
  async read(id, callSite = this.callSite) {
    if (!this.active) return;
    this.saveComparison();
    this.trail.push({ mode: this.mode, focusId: this.focusId, callSite: { ...this.callSite },
      expression: this.expression, scrollTop: this.root.scrollTop });
    if (this.trail.length > 16) this.trail.shift();
    if (callSite !== this.callSite) this.expression = callSite.expression || '';
    this.mode = 'method'; this.focusId = id; this.callSite = { ...callSite }; this.root.scrollTop = 0;
    this.render(); this.find('body').querySelector('h2')?.focus?.({ preventScroll: true });
    this.onChange(this.snapshot()); return this.ensure(id, true);
  }
  /** Return to the concrete caller occurrence or the initial comparison, preserving its vertical reading position. */
  back() {
    if (!this.active) return;
    const frame = this.trail.pop();
    if (!frame) { this.close(); return; }
    this.mode = frame.mode; this.focusId = frame.focusId; this.callSite = frame.callSite;
    if (this.mode === 'compare') this.restoreComparison();
    else this.expression = frame.expression || this.callExpression(this.callSite);
    this.render(); this.find('body').querySelector('h2')?.focus?.({ preventScroll: true });
    this.root.scrollTop = frame.scrollTop; this.onChange(this.snapshot());
    if (this.mode === 'method' && !this.source.cache.has(this.focusId)) this.ensure(this.focusId, true);
  }
  /** Recover source text from a bounded live cache; history continues to store addresses only. */
  callExpression(site) {
    const key = JSON.stringify([this.context.projectId, this.context.snapshotId, site?.occurrenceId]);
    return this.comparisons.get(key)?.expression || '';
  }
  /** Jump to a prior local reading frame while retaining that frame's own call occurrence and scroll. */
  backTo(index) {
    if (!Number.isSafeInteger(index) || index < 0 || index >= this.trail.length) return;
    this.trail = this.trail.slice(0, index + 1); this.back();
  }
  /** Explicit return lets the workspace restore the retained diagram and its concrete caller or entry. */
  returnTo(kind) {
    if (!this.active) return;
    const origin = { ...this.origin, entryNodeId: this.context.entryNodeId || this.origin.ownerNodeId };
    const callback = kind === 'entry' ? this.onReturnToEntry : this.onReturnToCall;
    this.invalidate();
    if (callback) callback(origin); else this.onClose(origin);
  }
  /** Snapshot addresses and scroll only; API responses and source expressions are intentionally excluded. */
  snapshot() {
    if (!this.active) return null;
    return normalizeReaderState({ ...this.context, mode: this.mode, focusId: this.focusId, origin: this.origin,
      callSite: this.callSite, trail: this.trail, scrollTop: this.root.scrollTop });
  }
  /** Restore only a matching revision; candidate inventory is recovered from the current graph, not history source blobs. */
  async restore(payload) {
    const saved = normalizeReaderState(payload), context = this.getContext();
    if (!saved || saved.projectId !== context.projectId || saved.snapshotId !== context.snapshotId) throw new Error('Состояние читателя относится к другому снимку.');
    this.restoring = true;
    const key = JSON.stringify([context.projectId, context.snapshotId, saved.origin.occurrenceId]);
    const cached = this.comparisons.get(key);
    const opening = this.open({ ...saved.origin, implementationItems: cached?.items, expression: cached?.expression,
      candidateCount: cached?.total ?? saved.origin.candidateCount, candidateCountExact: cached?.exact ?? saved.origin.candidateCountExact },
      { autoload: false, notify: false });
    const generation = this.generation;
    try {
      await opening;
      if (!this.active || generation !== this.generation) return false;
      this.trail = saved.trail; this.mode = saved.mode; this.callSite = saved.callSite; this.focusId = saved.focusId;
      if (this.mode === 'compare') this.restoreComparison(); else this.expression = this.callExpression(this.callSite);
      this.render(); this.root.scrollTop = saved.scrollTop;
      if (this.mode === 'method') await this.ensure(this.focusId, true);
      else for (const item of this.catalog.filter(item => item.scope === 'source').slice(0, 6)) this.ensure(item.id);
      return this.active && generation === this.generation;
    } finally {
      if (generation === this.generation) this.restoring = false;
    }
  }
  /** Snapshot changes invalidate the reader instead of silently showing an earlier repository's facts. */
  sync() {
    const current = this.getContext();
    if (this.active && (current.projectId !== this.context.projectId || current.snapshotId !== this.context.snapshotId)) this.invalidate();
  }
  /** Close cancels requests; caller-owned view state was never mutated and can simply be revealed again. */
  close() { const origin = this.origin; this.invalidate(); this.onClose(origin); }
  /** Internal invalidation does not create a user navigation event or reroot a diagram. */
  invalidate() {
    this.active = false; this.root.hidden = true; this.generation++; this.source.cancel(); this.records.clear();
  }
  /** Short independent dependency facts do not claim runtime order or absence of hidden external dependencies. */
  dependencyStrip(id) {
    const section = el('section', undefined, 'reader-dependencies');
    const edges = this.dependencies.get(id) || [];
    section.append(el('small', `Зависимости снимка · ${edges.length} связей (не порядок исполнения)`));
    for (const edge of edges.slice(0, 12)) {
      const target = this.getNode(edge.target), button = el('button', `${DEPENDENCIES[edge.relation]} · ${readerNodeLabel(target, edge.target)}`);
      button.type = 'button'; button.setAttribute('data-reader-action', 'dependency'); button.setAttribute('data-node-id', edge.target);
      button.title = `${edge.relation} · ${edge.source_file || ''}:${edge.sourceLine || '?'}`;
      button.addEventListener('click', () => {
        if (['calls', 'dispatches_to'].includes(edge.relation)) this.read(edge.target, { ownerNodeId: id, line: edge.sourceLine, occurrenceId: `${id}:${edge.sourceLine || ''}:${edge.target}` });
        else this.onSource(edge.target, target?.line);
      }); section.append(button);
    }
    if (!edges.length) section.append(el('p', 'Сопоставленных исходящих связей в снимке нет; это не доказывает отсутствие зависимостей.', 'small muted'));
    if (edges.length > 12) section.append(el('small', `Показано 12 из ${edges.length} связей.`));
    return section;
  }
  /** Actions separate reading from opening actual source, preserving line-specific navigation. */
  button(text, action, id, callback, explanation = text) {
    const button = el('button', text); button.type = 'button'; button.setAttribute('data-reader-action', action);
    button.title = explanation; button.setAttribute('aria-label', explanation);
    if (id) button.setAttribute('data-node-id', id); button.addEventListener('click', callback); return button;
  }
  /** A description card distinguishes preview truncation, unsupported bodies and source-analysis limits. */
  details(id, container, preview) {
    const record = this.records.get(id), data = this.source.cache.get(id), node = this.getNode(id);
    const purpose = readerPurpose(node, data);
    if (purpose) container.append(el('p', purpose, 'reader-purpose'));
    const metric = data?.complexity || node?.complexity;
    if (metric) {
      const badge = el('span', metric.score == null ? '?' : `${metric.status === 'partial' ? '≥' : ''}${metric.score}`, 'reader-complexity');
      badge.title = complexityText(metric); badge.setAttribute('aria-label', complexityText(metric));
      badge.style.background = metric.score == null ? '#7b8784' : metric.score >= 10 ? '#b13e43' : metric.score >= 5 ? '#986718' : '#327f70'; container.append(badge);
    }
    container.append(this.dependencyStrip(id));
    if (record?.status === 'error') container.append(el('p', record.error, 'reader-notice'), this.button('Повторить', 'retry', id, () => this.ensure(id, true, true)));
    else if (!data) container.append(el('p', record?.status === 'loading' ? 'Читаем описание метода…' : 'Подробности ещё не загружены.', 'small muted'));
    if (!data) return;
    renderReadingProvenance(container, data, { onContinue: () => {
      const reading = container.querySelector(preview ? '.reader-preview' : '.reader-method-body');
      reading?.setAttribute('tabindex', '-1'); reading?.focus?.({ preventScroll: true });
      reading?.scrollIntoView?.({ block: 'nearest' });
    }, onRefresh: this.onRefreshMap });
    renderReaderContract(container, data, { includePurpose: !purpose });
    const flow = data.methodFlow;
    if (flow?.status === 'parsed') {
      const body = el('div', undefined, preview ? 'reader-preview' : 'reader-method-body');
      const shown = readerPreview(data);
      renderReaderSteps(body, preview ? shown.steps : flow.steps, data, { preview,
        onSource: this.onSource, onCall: (event, step) => this.followCall(event, step, data) }); container.append(body);
      if (preview && shown.limited) container.append(el('p', `Обзор: 4 из ${shown.total} исходных шагов. Все доступные шаги — «Читать метод».`, 'reader-notice'));
      if (flow.truncated || flow.unsupported?.length) container.append(el('p', 'Анализ тела ограничен; часть конструкций или переходов не представлена.', 'reader-notice'));
      if (!flow.steps?.length) container.append(el('p', 'Сопоставленные шаги не извлечены. Проверьте исходник метода.', 'small muted'));
    } else container.append(el('p', 'Детальный порядок действий недоступен. Показаны комментарии и зависимости снимка.', 'reader-notice'));
    if (data.complexity?.excludedDeferredBodies) container.append(el('p', `Вложенные функции / lambda: ${data.complexity.excludedDeferredBodies}. Их выполнение здесь не разворачивается.`, 'small muted'));
    container.append(el('small', `Тело: ${data.sourceKind || 'working-tree'} · hash ${data.sourceHash?.slice(0, 12) || 'не установлен'} · связи: снимок ${this.context.snapshotId}`, 'reader-provenance'));
  }
  /** Calls may have multiple possible targets; their names are compared before any target is treated as selected. */
  followCall(event, step, description) {
    const targets = [...new Set([...(event.targetNodeIds || []), ...(event.implementationTargetIds || [])])];
    const candidates = [];
    for (const id of targets) {
      const implementations = (this.dependencies.get(id) || []).filter(edge => edge.relation === 'dispatches_to').map(edge => edge.target);
      candidates.push(...(implementations.length ? implementations : [id]));
    }
    const ids = [...new Set(candidates)];
    const site = { ownerNodeId: description.nodeId, line: event.line || step.line,
      occurrenceId: `${description.nodeId}:${event.line}:${event.column}:${event.endLine}:${event.endColumn}`, targetIds: ids.slice(0, 50),
      candidateCount: ids.length, candidateCountExact: true, expression: event.text };
    if (ids.length === 1) return this.read(ids[0], site);
    if (!ids.length) return;
    this.saveComparison();
    this.trail.push({ mode: this.mode, focusId: this.focusId, callSite: this.callSite,
      expression: this.expression, scrollTop: this.root.scrollTop });
    if (this.trail.length > 16) this.trail.shift();
    this.callSite = site; this.mode = 'compare'; this.focusId = null;
    this.catalog = ids.slice(0, 50).map(id => this.catalogItem(id));
    this.total = ids.length; this.listLimited = ids.length > 50; this.expression = event.text;
    this.saveComparison();
    this.render(); this.root.scrollTop = 0; this.onChange(this.snapshot());
    for (const item of this.catalog.filter(item => item.scope === 'source').slice(0, 6)) this.ensure(item.id);
  }
  /** Rebuild only the bounded reader DOM, retaining its own scroll and never touching graph geometry. */
  render() {
    if (!this.active) return;
    const offset = this.root.scrollTop, body = this.find('body');
    const focused = typeof document !== 'undefined' ? document.activeElement : null;
    const action = body.contains?.(focused) ? focused?.getAttribute('data-reader-action') : null;
    const focusId = focused?.getAttribute?.('data-node-id');
    const headingFocused = body.contains?.(focused) && focused?.tagName === 'H2';
    body.replaceChildren();
    const breadcrumbs = this.find('breadcrumbs'); breadcrumbs.replaceChildren();
    this.renderNavigation(breadcrumbs, body);
    this.find('back').disabled = !this.trail.length;
    if (this.mode === 'method') {
      const node = this.getNode(this.focusId), data = this.source.cache.get(this.focusId);
      body.append(el('h2', qualifiedMethodLabel(node, data, this.getNode, this.focusId)));
      body.append(this.button('Открыть код ↗', 'source', this.focusId, () => this.onSource(this.focusId, data?.line || node?.line)));
      body.append(this.button('Обновить описание', 'retry', this.focusId, () => this.ensure(this.focusId, true, true)));
      this.details(this.focusId, body, false);
    } else {
      body.append(el('h2', `Возможные реализации · ${this.exact ? '' : 'не менее '}${this.total}`));
      body.append(el('p', 'Сравните назначения и действия. Фактический выбор DI и число экземпляров не установлены.', 'small muted'));
      const labels = { source: 'В проекте', delegate: 'Переадресуют вызов', tests: 'В тестовых исходниках' };
      for (const scope of ['source', 'delegate', 'tests']) {
        const members = this.catalog.filter(item => (item.scope || 'source') === scope);
        if (!members.length) continue;
        const group = el('section', undefined, 'reader-group'); group.append(el('h3', `${labels[scope]} · ${members.length}`));
        const index = el('div', undefined, 'reader-candidate-index');
        for (const item of members) index.append(this.button(item.label, 'read', item.id, () => this.read(item.id)));
        group.append(index); body.append(group);
      }
      // Put the complete name inventory before previews so long bodies cannot hide alternatives.
      for (const scope of ['source', 'delegate', 'tests']) {
        const members = this.catalog.filter(item => (item.scope || 'source') === scope);
        if (!members.length) continue;
        const group = el('section', undefined, 'reader-group');
        group.append(el('h3', `${labels[scope]} · подробности`));
        for (const item of members) {
          const card = el('article', undefined, 'reader-candidate'); card.setAttribute('data-reader-candidate', item.id);
          card.append(el('h4', item.label), this.button('Прочитать объяснение', 'read', item.id, () => this.read(item.id)),
            this.button('Открыть код ↗', 'source', item.id, () => this.onSource(item.id, item.line)));
          this.details(item.id, card, true);
          if (!this.source.cache.has(item.id) && this.records.get(item.id)?.status !== 'loading') card.append(this.button('Показать подробности', 'retry', item.id, () => this.ensure(item.id, true)));
          group.append(card);
        }
        body.append(group);
      }
      if (this.listLimited) body.append(el('p', `Каталог ограничен: ${this.catalog.length} из ${this.total} имён.`, 'reader-notice'));
      body.append(el('p', 'Автоматический обзор — до 6 реализаций проекта; остальные доступны по кнопке. Обзор содержит до 4 шагов каждого метода.', 'small muted'));
    }
    body.querySelector('h2')?.setAttribute('tabindex', '-1');
    this.root.scrollTop = offset;
    if (headingFocused) body.querySelector('h2')?.focus?.({ preventScroll: true });
    if (action) {
      const replacement = [...body.querySelectorAll('[data-reader-action]')].find(button =>
        button.getAttribute('data-reader-action') === action && button.getAttribute('data-node-id') === focusId);
      replacement?.focus?.({ preventScroll: true });
    }
  }
  /** Qualified breadcrumbs and call addresses keep repeated invocations distinguishable after a return. */
  renderNavigation(breadcrumbs, body) {
    const description = id => this.source.cache.get(id) || this.getDescription(id);
    const items = readingBreadcrumbs({ origin: this.origin, callSite: this.callSite, trail: this.trail,
      focusId: this.focusId, entryNodeId: this.context.entryNodeId }, this.getNode, description);
    for (const [index, item] of items.entries()) {
      if (index) breadcrumbs.append(el('span', ' → '));
      const site = item.site && readingCallContext(item.site, this.getNode, description);
      const explanation = site?.title || item.label;
      if (item.kind === 'current') breadcrumbs.append(el('strong', item.label));
      else breadcrumbs.append(this.button(item.label, 'breadcrumb', String(index), () => {
        if (item.kind === 'frame') this.backTo(item.index);
        else this.returnTo(item.kind === 'entry' ? 'entry' : 'call');
      }, explanation));
    }
    const site = readingCallContext(this.callSite, this.getNode, description);
    const context = el('p', `Вызов: ${site.label}${this.expression ? ' · ' + this.expression : ''}`, 'reader-call-context');
    context.title = site.title; context.setAttribute('data-occurrence-id', site.occurrenceId);
    body.append(context);
    const actions = el('nav', undefined, 'reader-return-actions');
    actions.setAttribute('aria-label', 'Возврат к карте');
    actions.append(this.button('К вызову', 'return-call', this.origin.occurrenceId, () => this.returnTo('call'),
      readingCallContext(this.origin, this.getNode, description).title));
    actions.append(this.button('К входу', 'return-entry', this.context.entryNodeId || this.origin.ownerNodeId,
      () => this.returnTo('entry'), 'Вернуться к входному методу маршрута'));
    body.append(actions);
  }
}
