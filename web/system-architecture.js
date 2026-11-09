import {requestJson} from './bounded-request.js';
import {ArchitectureSessions, ARCHITECTURE_LIMITS, boundArchitectureProjection, boundCanonicalArchitecture} from './architecture-session.js';
import {architectureBlocks, architectureBlockId} from './architecture-blocks.js';
import {stableArchitectureGeometry, architecturePositions} from './architecture-layout.js';
import { isolateCallChain } from './call-isolation.js';
import { isSystemMode, systemScale } from './view-modes.js';
import { solutionArchitecture } from './solution-architecture.js';
import { $, el } from './dom.js';
export const SYSTEM_KIND_LABELS = { project: 'Проект', 'external-project': 'Внешний проект', http: 'HTTP', database: 'БД', kafka: 'Kafka', rabbit: 'RabbitMQ', package: 'Пакет', 'code-scope': 'Область кода' };
/**
 * Проецирует только проекты и внешние зависимости; фильтры символов здесь не переосмысливаются.
 */
export function systemProjection(data, { projectIds = null, primaryRepositoryId = null, systems = true, packages = false, query = '', page = 0, pageSize = 160 } = {}) {
  const owners = projectIds === null ? null : new Set(projectIds.map(String));
  const projects = new Set();
  const eligible = new Map();
  const terms = query.toLowerCase().trim().split(/\s+/u).filter(Boolean);
  for (const node of data.nodes || []) {
    if (node.kind === 'project' || node.kind === 'code-scope') {
      const primary = !node.repositoryId || !primaryRepositoryId || node.repositoryId === primaryRepositoryId;
      if (primary && owners && !owners.has(String(node.originalCodeProjectId || node.codeProjectId || '__unassigned__'))) continue;
      projects.add(node.id);
    } else {
      const hiddenPackage = node.kind === 'package' && !packages;
      const externalSystem = node.kind !== 'package' && node.kind !== 'external-project';
      if (hiddenPackage || (externalSystem && !systems)) continue;
    }
    eligible.set(node.id, node);
  }
  const linked = new Set(projects);
  for (const edge of data.edges || []) {
    if (edge.source === edge.target || !eligible.has(edge.source) || !eligible.has(edge.target)) continue;
    if (projects.has(edge.source) || projects.has(edge.target)) { linked.add(edge.source); linked.add(edge.target); }
  }
  // A logical child is one level below an eligible owner's resource block.
  for (const edge of data.edges || []) {
    if (edge.relation === 'logical-member' && linked.has(edge.source)
        && eligible.get(edge.source)?.resourceBlock && eligible.has(edge.target)) linked.add(edge.target);
  }
  const matches = new Set();
  for (const node of eligible.values()) {
    if (!linked.has(node.id)) continue;
    const childNames = node.resourceBlock ? (node.children || []).slice(0, 240).map(child => child.label).join(' ') : '';
    const text = `${node.label} ${node.host || ''} ${node.logicalName || ''} ${childNames}`.toLowerCase();
    if (terms.every(term => text.includes(term))) matches.add(node.id);
  }
  // Поиск сохраняет непосредственные зависимости найденного объекта, а не оставляет изолированную точку.
  const included = new Set(matches);
  if (terms.length) for (const edge of data.edges || []) {
    if (!linked.has(edge.source) || !linked.has(edge.target)) continue;
    if (matches.has(edge.source) || matches.has(edge.target)) { included.add(edge.source); included.add(edge.target); }
  }
  if (terms.length) for (const edge of data.edges || []) {
    if (projects.has(edge.source) && included.has(edge.target) && eligible.get(edge.target)?.resourceBlock) included.add(edge.source);
    if (projects.has(edge.target) && included.has(edge.source) && eligible.get(edge.source)?.resourceBlock) included.add(edge.target);
  }
  const nodes = [];
  for (const node of eligible.values()) if (included.has(node.id)) nodes.push(node);
  nodes.sort((a, b) => Number(b.kind === 'project') - Number(a.kind === 'project') || a.label.localeCompare(b.label) || a.id.localeCompare(b.id));
  const allIds = new Set(nodes.map(node => node.id));
  const allEdges = (data.edges || []).filter(edge => edge.source !== edge.target && allIds.has(edge.source) && allIds.has(edge.target));
  const size = Math.max(1, Math.min(240, Math.trunc(pageSize) || 160));
  const pageCount = Math.max(1, Math.ceil(nodes.length / size));
  const currentPage = Math.max(0, Math.min(pageCount - 1, Math.trunc(page) || 0));
  const visible = nodes.slice(currentPage * size, (currentPage + 1) * size);
  const visibleIds = new Set(visible.map(node => node.id));
  const edges = allEdges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target));
  return { nodes: visible, allNodes: nodes, edges, allEdges, matchedNodes: matches.size, totalNodes: nodes.length, totalEdges: allEdges.length, page: currentPage, pageCount };
}
/**
 * Управляет отдельным системным индексом; синтетические id никогда не выбираются как символы.
 */
export class SystemArchitectureView {
  /**
   * Общий GraphView используется только для отображения, переход в код выполняется явно.
   */
  constructor({ state, view, controls, openProjectCode, getRelatedIds = () => [], onDocumentation = () => {}, onInspect = () => {}, fetcher = (...args) => fetch(...args) }) {
    this.onInspect = onInspect;
    this.state = state;
    this.view = view;
    this.controls = controls;
    this.openProjectCode = openProjectCode;
    this.onDocumentation = onDocumentation;
    this.getRelatedIds = getRelatedIds;
    this.fetcher = fetcher;
    this.sessions = new ArchitectureSessions();
    this.revision = 0;
    this.page = 0;
    this.status = 'idle';
    for (const id of ['system-systems', 'system-packages', 'solution-kind']) $(id).addEventListener('change', () => { this.page = 0; this.render(true); });
    $('system-scale').addEventListener('change', () => {
      this.page = 0;
      this.selectedNodeId = null;
      this.selectedEdge = null;
      this.onDocumentation(null);
      $('selection').replaceChildren(el('h2', 'Архитектура'), el('p', 'Выберите репозиторий, проект или внешнюю систему.'));
      this.render(true);
    });
    $('system-search').addEventListener('input', () => { this.page = 0; this.render(true); });
    $('system-previous').addEventListener('click', () => { this.page--; this.render(true); });
    $('system-next').addEventListener('click', () => { this.page++; this.render(true); });
    $('system-retry').addEventListener('click', () => this.load());
    $('architecture-pin')?.addEventListener('click', () => {
      if (this.session?.togglePin(this.selectedNodeId)) this.render(false);
      this.updateExplorationControls();
    });
    $('architecture-reset')?.addEventListener('click', () => this.resetExploration());
    view.onSystemEdge = edge => this.selectEdge(edge);
  }
  /**
   * Ключ из committed project/snapshot и точного related scope, а не текущего символа.
   */
  contextKey() { return JSON.stringify([this.state.project?.id, this.state.graph.metadata?.snapshotId, [...this.getRelatedIds()].sort()]); }
  /**
   * Сохраняет камеру последней архитектурной шкалы до переключения в код.
   */
  suspend() {
    const camera = this.view.camera;
    if (this.renderedSession && camera && [camera.x, camera.y, camera.scale].every(Number.isFinite)) {
      this.renderedSession.cameras.set(this.renderedLayout, {...camera});
    }
    this.renderedSession = null; this.renderedLayout = null;
  }
  /**
   * Только committed raw ID добавляется один раз; в режиме кода сеть не запускается.
   */
  observeRawNode(id) {
    if (!this.state.project || !this.state.graph.metadata?.snapshotId || !this.state.nodes?.has(id)) return false;
    const session = this.sessions.get(this.contextKey());
    const changed = session.visit(id);
    if (changed && isSystemMode($('view-mode').value)) void this.load();
    return changed;
  }
  /**
   * Отменяет транспорт, сохраняя bounded сессию при возвращении в тот же scope.
   */
  reset() {
    this.suspend(); this.onDocumentation(null); this.controller?.abort(); this.revision++;
    this.key = this.contextKey(); this.session = this.sessions.get(this.key); this.data = this.session.data;
    this.projection = null; this.displayData = null; this.selectedNodeId = null; this.selectedEdge = null;
    this.status = this.data ? 'ready' : 'idle'; this.message = this.data?.warning || ''; this.page = 0;
    $('system-search').value = '';
  }
  /**
   * Изменение related scope не переиспользует прежние закрепления и proof.
   */
  invalidate() {
    const query = $('system-search').value;
    this.sessions.invalidate(this.contextKey()); this.reset(); $('system-search').value = query;
    this.render(false);
  }
  /**
   * Явный сброс очищает посещения/закрепления и возвращает начальные владельцы.
   */
  resetExploration() {
    this.session ||= this.sessions.get(this.contextKey()); this.session.clearExploration();
    this.selectedNodeId = null; this.selectedEdge = null; this.page = 0;
    void this.load();
  }
  /**
   * Подписи и состояния native controls не меняют фокус пользователя.
   */
  updateExplorationControls() {
    const session = this.session;
    const pin = $('architecture-pin');
    if (pin) {
      const active = Boolean(session?.pinned.has(this.selectedNodeId));
      pin.disabled = !session?.canonicalIds().has(this.selectedNodeId);
      pin.setAttribute('aria-pressed', String(active)); pin.textContent = active ? 'Открепить узел' : 'Закрепить узел';
    }
    const status = $('architecture-exploration-status');
    if (status) {
      const limited = this.projection?.omittedNodes || this.projection?.omittedEdges || this.canonicalRevealed?.sceneLimits?.omittedNodes || this.canonicalRevealed?.sceneLimits?.omittedEdges;
      status.textContent = `Посещено ${session?.visited.size || 0}/64 · закреплено ${session?.pinned.size || 0}/24 · область до 240 узлов/1500 связей${limited ? ' · достигнут лимит проекции' : ''}${session?.limitMessage ? ' · ' + session.limitMessage : ''}`;
    }
    const button = $('architecture-export'); if (button) button.disabled = !this.data || this.data.architectureVersion !== 2 || !this.projection?.allNodes.length;
  }
  /**
   * POST всегда даёт canonical federated ID даже при пустом related/visited наборе.
   * @returns {Promise<void>} Принимает только актуальный snapshot/scope/generation; старый ответ игнорируется.
   * Отменяет предыдущий запрос; ошибка отображается рядом с сохранённой картой без удаления committed data.
   */
  async load() {
    const project = this.state.project, snapshotId = this.state.graph.metadata?.snapshotId;
    if (!project || !snapshotId) return;
    this.controller?.abort(); this.controller = new AbortController();
    const revision = ++this.revision, key = this.contextKey();
    const session = this.sessions.get(key), visitRevision = session.visitRevision;
    this.key = key; this.session = session; this.data = session.data;
    this.status = 'loading'; this.message = 'Дополняем архитектуру; открытая карта остаётся доступной…'; this.render(false);
    try {
      const data = await requestJson(`/api/projects/${encodeURIComponent(project.id)}/system-architecture`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, signal: this.controller.signal,
        body: JSON.stringify({snapshotId, relatedProjectIds: [...this.getRelatedIds()], visitedNodeIds: [...session.visited]}),
      }, {fetcher: this.fetcher, timeoutMs: 120000});
      if (revision !== this.revision || key !== this.contextKey()) return;
      if (data.snapshotId !== snapshotId || !Array.isArray(data.nodes) || !Array.isArray(data.edges)) throw new Error('Снимок архитектуры изменился. Обновите карту.');
      session.accept(data, visitRevision); this.data = session.data; this.status = 'ready';
      this.message = data.architectureVersion === 2 ? data.warning || 'Статические основания; runtime не наблюдался.'
        : 'Сервер не поддерживает накопление посещений. Нужна актуальная версия API; показан базовый индекс.';
    } catch (error) {
      if (error.name === 'AbortError' || revision !== this.revision || key !== this.contextKey()) return;
      this.status = 'error'; this.message = error.status === 422 ? 'Сервер не поддерживает новый контракт архитектуры. Обновите приложение и повторите запрос.' : error.message;
    }
    if (isSystemMode($('view-mode').value)) this.render(false);
  }
  /**
   * Рисует только синтетические узлы и точные межузловые факты с явными лимитами.
   * @param {boolean} fit Совместимый аргумент прежних вызовов; камера восстанавливается по сессии/layoutKey.
   * @returns {void} Передаёт renderer семантические кластеры и прежние same-layout позиции для pins.
   */
  render(fit = false) {
    if (!isSystemMode($('view-mode').value)) return;
    const focusedGraphId = document.activeElement?.closest?.('[data-node-id]')?.getAttribute('data-node-id');
    const focusedResultId = document.activeElement?.getAttribute?.('data-architecture-result');
    if (this.key !== this.contextKey() && this.status !== 'idle') this.reset();
    this.session ||= this.sessions.get(this.contextKey());
    if ((this.status === 'idle' || this.status === 'ready' && this.session.loadedRevision !== this.session.visitRevision) && this.state.graph.metadata?.snapshotId) { void this.load(); return; }
    this.updateExplorationControls();
    $('system-status').hidden=false;
    $('system-status').textContent = this.message || 'Выберите проиндексированный репозиторий.';
    if (this.data) {
      const coverage = this.data.coverage || {};
      const limitations = el('details');
      limitations.append(el('summary', 'Основания и ограничения'), el('p', this.message));
      limitations.append(el('p', 'Начните с репозиториев; для зависимостей между библиотеками выберите проекты .csproj. Оба масштаба используют одни факты и свидетельства.'));
      limitations.append(el('p', 'Фильтр .csproj применяется только к основному репозиторию; проекты связанных репозиториев сохраняются. Фильтры по символам, связям и словам сохранены для карты кода. Здесь нет методов и внутренних связей проектов. Внешние узлы имеют собственный цвет.'));
      for (const [key, label] of [['skippedFiles', 'Пропущено файлов'], ['parseErrors', 'Ошибки разбора'], ['ambiguousFiles', 'Неоднозначные файлы'], ['ambiguousConfigDirectories', 'Неоднозначные каталоги конфигурации'], ['unresolvedReferences', 'Не разрешены ссылки']]) {
        if (coverage[key]) limitations.append(el('p', `${label}: ${coverage[key]}`));
      }
      const repositories = new Map((this.data.repositories || []).map(repository => [repository.id, repository.name]));
      for (const item of coverage.perRepository || []) {
        const name = repositories.get(item.repositoryId) || item.repositoryId;
        const partial = item.limited || item.discoveryLimited ? ' · частичное покрытие' : '';
        limitations.append(el('p', `${name}: прочитано ${item.readFiles ?? '—'}, пропущено ${item.skippedFiles ?? '—'}, ошибок разбора ${item.parseErrors ?? '—'}${partial}`));
      }
      const limited = coverage.limited || coverage.discoveryLimited;
      const summary = el('p', `Файлов ${coverage.readFiles ?? '—'} из ${coverage.candidateFiles ?? '—'} · пропущено ${coverage.skippedFiles ?? '—'}${coverage.parseErrors ? ` · ошибок разбора ${coverage.parseErrors}` : ''}${coverage.ambiguousFiles ? ` · неоднозначных ${coverage.ambiguousFiles}` : ''}${limited ? ' · достигнут лимит, карта частичная' : ''}`);
      $('system-coverage').replaceChildren(summary,limitations);
      const statusItems = [];
      if (this.status === 'loading' || this.status === 'error') statusItems.unshift(el('p', this.message, this.status === 'error' ? 'error-text' : ''));
      $('system-status').replaceChildren(...statusItems);
      $('system-status').hidden=!statusItems.length;
    }
    $('system-retry').hidden = this.status !== 'error';
    $('back-overview').hidden = true;
    $('expansion-toolbar').hidden = true;
    $('empty').hidden = true;
    const solution = systemScale($('view-mode').value, $('system-scale').value) === 'repositories';
    $('system-packages').disabled = solution;
    $('system-package-control').hidden = solution;
    $('solution-kind-control').hidden = !solution;
    $('system-heading').textContent = solution ? 'Архитектура · репозитории и внешние зависимости' : 'Архитектура · проекты .csproj и внешние зависимости';
    $('view-title').textContent = solution ? 'Архитектура · репозитории' : 'Архитектура · проекты .csproj';
    $('reset').textContent = 'Вместить архитектуру';
    $('view-instruction').textContent = 'Выберите проект, систему или стрелку для фактов и свидетельств';
    $('results').replaceChildren();
    $('more').hidden = true;
    if (!this.data) {
      this.view.renderOverview([], [], null);
      $('node-count').textContent = '—';
      $('graph-stats').textContent = this.status === 'loading' ? 'Индекс загружается…' : 'Индекс недоступен';
      $('visible-count').textContent = '';
      $('results-count').textContent = '';
      $('system-previous').disabled = true;
      $('system-next').disabled = true;
      return;
    }
    const canonical = this.session.graph();
    const grouped = architectureBlocks(canonical, this.session.expandedBlocks, this.session.pinned);
    this.blocks = grouped.blocks; this.canonicalRevealed = canonical;
    let displayData = grouped.graph;
    if (solution) {
      // Сначала применяем фильтр подпроектов, затем сворачиваем все страницы, а не только видимые узлы.
      const scoped = systemProjection(displayData, { projectIds: this.state.isolatedRoot ? null : this.state.codeProjectIds, primaryRepositoryId: this.state.project?.id, packages: false });
      const kind = this.state.isolatedRoot ? '' : $('solution-kind').value;
      const scopedNodes = scoped.allNodes.filter(node => !kind || node.kind === 'project' || node.kind === kind);
      displayData = solutionArchitecture({ ...displayData, nodes: scopedNodes, edges: scoped.allEdges }, this.state.project);
    }
    this.displayData = displayData;
    let projection = systemProjection(displayData, { projectIds: solution ? null : this.state.codeProjectIds, primaryRepositoryId: this.state.project?.id, systems: $('system-systems').checked, packages: !solution && $('system-packages').checked, query: $('system-search').value, page: this.page });
    // Изоляция использует все исходящие зависимости, включая следующие страницы и скрытые поиском узлы.
    if (this.state.isolatedRoot) {
      const chain = isolateCallChain(displayData, this.state.isolatedRoot, { relations: null });
      if (chain.missingRoot) this.state.isolatedRoot = null;
      else {
        projection = { ...projection, nodes: chain.nodes, edges: chain.edges, allNodes: chain.nodes, allEdges: chain.edges,
          totalNodes: chain.totalNodes, totalEdges: chain.totalEdges, page: 0, pageCount: 1, isolationTruncated: chain.truncated };
      }
    }
    projection = boundArchitectureProjection(projection, this.session.pinned);
    this.projection = projection;
    const selectionScope = JSON.stringify([$('view-mode').value, $('system-scale').value, $('solution-kind').value, this.state.codeProjectIds, $('system-systems').checked, $('system-packages').checked, $('system-search').value, this.state.isolatedRoot]);
    const changedScope = this.selectionScope && this.selectionScope !== selectionScope;
    this.selectionScope = selectionScope;
    const nodeHidden = this.selectedNodeId && !projection.allNodes.some(node => node.id === this.selectedNodeId);
    const edgeHidden = this.selectedEdge && !projection.allEdges.some(edge => edge.source === this.selectedEdge.source && edge.target === this.selectedEdge.target && edge.relation === this.selectedEdge.relation);
    if (nodeHidden || edgeHidden || (changedScope && !this.state.isolatedRoot && (this.selectedNodeId || this.selectedEdge))) {
      this.selectedNodeId = null;
      this.selectedEdge = null;
      this.onDocumentation(null);
      $('selection').replaceChildren(el('h2', 'Область выбора изменилась'), el('p', 'Выберите проект, систему или связь в текущем фильтре.', 'muted'));
    }
    this.page = projection.page;
    const nodes = projection.nodes.map(node => ({ ...node, system: true, kindLabel: node.solution ? 'Репозиторий' : SYSTEM_KIND_LABELS[node.kind] || node.kind, metricLabel: { project: 'PRJ', 'external-project': 'EXT', package: 'PKG', database: 'DB', http: 'HTTP', kafka: 'Kafka', rabbit: 'AMQP' }[node.kind] || node.kind, architecture: true, architectureLevel: 'projects', displayLabel: node.label }));
    const edges = projection.edges.slice(0, 1500).map(edge => ({ ...edge, system: true }));
    this.view.colorProjects = this.state.graph.codeProjects || [];
    const layoutKey = `${solution ? 'repositories' : 'projects'}:${this.view.displayProvider || 'topology'}`;
    const sameLayout = this.renderedSession === this.session && this.renderedLayout === layoutKey;
    const camera = sameLayout ? this.view.camera && {...this.view.camera} : this.session.cameras.get(layoutKey);
    if (!sameLayout) this.suspend();
    const registry = this.session.layout(layoutKey);
    const previousPositions = sameLayout ? this.view.positions : null;
    this.view.renderOverview(nodes, edges, null, null, null, {positionHook: geometry => stableArchitectureGeometry(geometry, registry,
      {nodes, pinnedIds: this.session.pinned, previousPositions})});
    if (camera) { this.view.camera = {...camera}; this.view.transform?.(); }
    else this.view.fit();
    this.renderedSession = this.session; this.renderedLayout = layoutKey;
    this.updateExplorationControls();
    $('node-count').textContent = String(projection.totalNodes);
    $('graph-stats').textContent = `${projection.totalNodes} проектов/внешних узлов · ${projection.totalEdges} направленных связей · ${this.data.repositories?.length || 1} репозиториев`;
    $('visible-count').textContent = `${nodes.length} из ${projection.totalNodes} узлов · ${edges.length} из ${projection.totalEdges} связей · страница ${projection.page + 1}/${projection.pageCount}${$('system-search').value.trim() ? ` · поиск: ${projection.matchedNodes} совпадений и их прямые зависимости` : ''}`;
    $('results-count').textContent = 'Проекты и внешние зависимости';
    $('system-previous').disabled = this.page === 0;
    $('system-next').disabled = this.page + 1 >= projection.pageCount;
    for (const node of projection.nodes) {
      const button = el('button', undefined, 'result');
      button.setAttribute('data-architecture-result', node.id);
      button.append(el('strong', node.label), el('small', `${node.solution ? 'Репозиторий' : SYSTEM_KIND_LABELS[node.kind] || node.kind} · ${node.repositoryName || (node.repositoryNames || []).join(', ') || this.state.project?.name || ''}`));
      button.addEventListener('click', () => this.select(node.id));
      $('results').append(button);
    }
    // Restore only the replaced control; unrelated native focus is never stolen.
    if (focusedGraphId) this.view.communityElements?.get(String(focusedGraphId))?.group?.focus?.({preventScroll:true});
    else if (focusedResultId) {
      const replacement = [...$('results').querySelectorAll('[data-architecture-result]')].find(item => item.getAttribute('data-architecture-result') === focusedResultId);
      replacement?.focus?.({preventScroll:true});
    }
  }
  /**
   * Очищает выбор raw-символа прежде чем показывать синтетический объект.
   */
  beginSelection() {
    this.onInspect?.();
    this.state.selected = null;
    this.state.expandedId = null;
    this.state.answerRevision++;
    $('answer').replaceChildren();
    this.controls();
  }
  /**
   * Показывает направление, тип, вес и исходные основания одной системной связи.
   */
  evidence(edge) {
    const details = el('details', undefined, 'system-evidence');
    const names = new Map((this.displayData?.nodes || this.data?.nodes || []).map(node => [node.id, node.label]));
    details.append(el('summary', `${names.get(edge.source) || edge.source} → ${names.get(edge.target) || edge.target} · ${edge.relation} · вес ${edge.weight}`));
    if (edge.participants?.length) details.append(el('p', `Участвуют проекты: ${edge.participants.map(item => item.label).join(', ')}`, 'small muted'));
    for (const evidence of (edge.evidence || []).slice(0, 50)) details.append(el('p', `${evidence.repositoryName ? `${evidence.repositoryName} · ` : ''}${evidence.file || 'Без файла'}:${evidence.line || '?'} · ${evidence.type || ''} · ${evidence.confidence || ''}${evidence.configVariant ? ` · ${evidence.configVariant}` : ''}`, 'path-location'));
    if (edge.evidenceTruncated) details.append(el('p', 'Сервер ограничил список отдельных свидетельств. Вес связи включает все учтённые факты, включая повторяющиеся.', 'small muted'));
    if ((edge.evidence?.length || 0) > 50) details.append(el('p', `Показаны 50 из ${edge.evidence.length} свидетельств.`, 'small muted'));
    return details;
  }
  /**
   * Инспектор системного узла содержит только факты; переход в код проекта — явное действие.
   */
  select(id) {
    const node = (this.displayData || this.data)?.nodes.find(item => item.id === id);
    if (!node) return;
    this.selectedNodeId = id;
    if (node.resourceBlock) {
      if (!this.session.expandedBlocks.delete(id)) this.session.expandedBlocks.add(id);
      this.render(false);
    } else {
      const owners = this.data.nodes.filter(item => item.id === id || node.solution && item.repositoryId === node.repositoryId).map(item => item.id);
      if (this.session.revealOwners(owners)) this.render(false);
    }
    this.beginSelection(); this.updateExplorationControls();
    this.onDocumentation(node);
    this.selectedEdge = null;
    const panel = $('selection');
    panel.replaceChildren(el('h2', node.label), el('span', node.solution ? 'Репозиторий' : SYSTEM_KIND_LABELS[node.kind] || node.kind, 'pill'));
    const repositories = node.repositoryName || (node.repositoryNames || []).join(', ') || this.state.project?.name;
    if (repositories) panel.append(el('p', `Репозиторий: ${repositories}`));
    if (node.identityScope === 'endpoint-host') panel.append(el('p', 'Узел объединён по адресу endpoint. Совпадение адреса не доказывает, что это один сервис или одна база данных: пути и другие параметры не входят в идентичность.', 'small muted'));
    if (node.logicalNames?.length) panel.append(el('p', `Имена в конфигурациях: ${node.logicalNames.join(', ')}`));
    if (node.resourceBlock) {
      panel.append(el('p', 'Логическая группа владельца. Связь с конкретным сервером/брокером не установлена; конфигурации остаются альтернативами.', 'small muted'));
      const toggle = el('button', this.session.expandedBlocks.has(id) ? 'Свернуть ресурсы' : 'Раскрыть ресурсы');
      toggle.type = 'button'; toggle.setAttribute('data-architecture-toggle', id); toggle.setAttribute('aria-expanded', String(this.session.expandedBlocks.has(id)));
      toggle.addEventListener('click', () => {
        this.select(id);
        $('selection').querySelector('[data-architecture-toggle]')?.focus?.({preventScroll:true});
      }); panel.append(toggle);
      for (const child of (node.children || []).slice(0, 64)) {
        const item = el('details'); item.append(el('summary', `${child.resourceKind}: ${child.label}`));
        item.append(el('p', `Статус имени: ${child.status || 'unknown'} · runtime неизвестен`, 'small muted'));
        for (const edge of this.data.edges.filter(edge => edge.source === child.id || edge.target === child.id)) item.append(this.evidence(edge));
        panel.append(item);
      }
      if ((node.children?.length || 0) > 64) panel.append(el('p', 'В инспекторе показаны 64 ресурса. Карта и экспорт имеют явный лимит 240 узлов.', 'small muted'));
    }
    if (node.host) panel.append(el('p', `Хост: ${node.host}`));
    if (node.logicalName) panel.append(el('p', `Имя конфигурации: ${node.logicalName}`));
    if (node.kind === 'project' && node.codeProjectId) {
      const button = el('button', 'Открыть код проекта');
      button.addEventListener('click', async () => {
        const context = this.contextKey();
        const selectedId = this.selectedNodeId;
        button.disabled = true;
        try {
          await this.openProjectCode(node.originalCodeProjectId || node.codeProjectId, node.repositoryId || this.state.project?.id);
        } catch (error) {
          if (context === this.contextKey() && selectedId === this.selectedNodeId) {
            panel.append(el('p', `Не удалось открыть код проекта: ${error.message}`, 'error-text'));
          }
        } finally { button.disabled = false; }
      });
      panel.append(button);
    }
    if (node.memberProjects?.length) {
      const members = el('details');
      members.append(el('summary', `Проекты репозитория: ${node.memberProjects.length}`));
      for (const member of node.memberProjects) {
        const button = el('button', member.label, 'connection');
        button.addEventListener('click', async () => {
          const context = this.contextKey();
          try { await this.openProjectCode(member.id, member.repositoryId); }
          catch { if (context === this.contextKey() && this.selectedNodeId === id) panel.append(el('p', 'Не удалось открыть проект. Повторите переход.', 'error-text')); }
        });
        members.append(button);
      }
      panel.append(members);
    }
    const edges = (this.projection?.allEdges || []).filter(edge => edge.source === id || edge.target === id);
    panel.append(el('p', `${edges.length} направленных связей в области фильтра. Внутренние методы и самосвязи скрыты по определению этого вида.`, 'small muted'));
    for (const edge of edges.slice(0, 100)) panel.append(this.evidence(edge));
    if (edges.length > 100) panel.append(el('p', 'В инспекторе показаны первые 100 связей.', 'small muted'));
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  /**
   * Сериализатор получает canonical факты всех страниц, а не camera/DOM crop.
   * @returns {{graph:object,options:object}} Отфильтрованные факты, eligible owner closure, pins/геометрия
   * и display metadata. Закрытые группы сохраняют ресурсы; усечение помечается coverage и sceneLimits.
   * @throws {Error} Экспорт недоступен без готового ответа architectureVersion:2.
   */
  exportModel() {
    if (!this.projection || !this.data || this.data.architectureVersion !== 2) throw new Error('Архитектура ещё не готова к экспорту.');
    const display = this.projection.allNodes, displayed = new Set(display.map(node => node.id));
    const repositories = new Map(display.filter(node => node.solution).map(node => [node.repositoryId, node.memberProjects || []]));
    const nodes = this.canonicalRevealed.nodes.filter(node => displayed.has(node.id)
      || node.resourceKind && displayed.has(architectureBlockId(node.ownerId, node.kind))
      || node.kind === 'project' && (repositories.get(node.repositoryId) || []).some(member => member.id === (node.originalCodeProjectId || node.codeProjectId)));
    const ownerFilter = this.state.codeProjectIds === null ? null : new Set(this.state.codeProjectIds.map(String));
    const included = new Set(nodes.map(node => node.id));
    for (const node of [...nodes]) if (node.ownerId && !included.has(node.ownerId)) {
      const owner = this.canonicalRevealed.nodes.find(item => item.id === node.ownerId);
      const primary = !owner?.repositoryId || owner.repositoryId === this.state.project.id;
      if (owner && (!primary || !ownerFilter || ownerFilter.has(String(owner.originalCodeProjectId || owner.codeProjectId || '__unassigned__')))) {
        nodes.push(owner); included.add(owner.id);
      }
    }
    const ids = new Set(nodes.map(node => node.id));
    const edges = this.canonicalRevealed.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target));
    const layout = this.session.layout(this.renderedLayout);
    const bounded = boundCanonicalArchitecture({...this.data, nodes, edges}, this.session.pinned);
    this.exportOmissions = bounded.sceneLimits;
    return {graph: {...bounded, projection: {scale: $('system-scale').value, displayNodeIds: [...displayed]}},
      options: {projectId: this.state.project.id, snapshotId: this.data.snapshotId,
        pinnedIds: [...this.session.pinned].filter(id => displayed.has(id) || ids.has(id)),
        positions: architecturePositions(layout, new Set([...displayed, ...ids]), this.view.positions)}};
  }
  /**
   * Стрелка имеет собственный инспектор свидетельств, а не действие исходника/MCP.
   */
  selectEdge(edge) {
    this.onDocumentation(null);
    this.selectedNodeId = null;
    this.beginSelection();
    this.selectedEdge = edge;
    const panel = $('selection');
    const evidence = this.evidence(edge);
    evidence.open = true;
    panel.replaceChildren(el('h2', 'Архитектурная связь'), evidence);
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}
