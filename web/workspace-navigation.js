import { $, el } from './dom.js';
import { isSystemMode } from './view-modes.js';
import { navigationSnapshot, pathDetailToken, inlineDetailToken, resolveNavigation } from './navigation-history.js';
import { requestJson } from './bounded-request.js';
import { PATH_NODE_LIMIT } from './execution-path-limits.js';

/** Восстанавливает адресуемый workspace; owns порядок перехода и отмену, но не хранение history или графа. */
export function createWorkspaceNavigation({ state, view, viewers, search, actions, request = requestJson }) {
  const { wikiViewer, pathViewer, factsPopup, documentationViewer, systemView, mapBlocks, methodWorkbench, projectOverview } = viewers;
  const { searchNavigation, navigationSearch, semanticSearch } = search;
  const { updateDisplayProvider, updateDiscovery, openProject, draw, filteredNodes, displayedAggregate,
    renderCodeProjects, renderSelection, loadIngress, paintPathGraph, controls, status, closeTransientStudy } = actions;
  /** Адрес карты отделён от исходника и popup; browser state содержит только небольшие настройки просмотра. */
  function captureNavigation() {
    const system = isSystemMode($('view-mode').value);
    return navigationSnapshot({
      route: {
        overview: projectOverview?.isOpen() ? 'open' : 'closed',
        task: state.discoveryTask?.kind==='impact'?'impact':null,
        repo: state.project?.id, algorithm: state.algorithm, view: $('view-mode').value, display: $('display-provider').value,
        level: state.architecture.level, project: state.architecture.projectId, file: state.architecture.file,
        page: system ? systemView.page : state.architecture.page,
        node: system ? systemView.selectedNodeId : state.selected,
        context: state.expandedId, group: state.community,
        isolate: state.isolatedRoot, block: state.selectedBlock, inline: inlineDetailToken(state.inlineExpanded),
        isolateBlock: state.isolatedBlock?.id,
        path: state.pathOnGraph?.rootNodeId, entry: state.pathOnGraph ? pathViewer.entry?.id : null,
        detail: state.pathOnGraph ? pathDetailToken(view.pathExpandedIds, view.pathCollapsedIds) : null,
        reading: state.methodPresentation === 'methods' ? 'methods' : state.pathOnGraph && state.methodPresentation === 'documentation' ? 'documentation' : null,
        methodFocus: methodWorkbench?.snapshot()?.focusId || state.readerState?.focusId || null,
        scale: $('system-scale').value,
        wiki: wikiViewer.documentId, wikiNode: wikiViewer.nodeId, wikiOffset: wikiViewer.offset,
      },
      snapshotId: state.graph.metadata?.snapshotId, camera: view.camera,
      reader: methodWorkbench?.snapshot() || state.readerState,
      methodPlacements: view.pathMethodPlacements, inlineMethodPlacements: state.inlineMethodPlacements,
      inlineExpanded: state.inlineExpanded, isolatedBlock: state.isolatedBlock,
      pathExpanded: state.pathOnGraph ? view.pathExpandedIds : [], pathCollapsed: state.pathOnGraph ? view.pathCollapsedIds : [], focused: state.focused,
      searchContext: { mode: $('discovery-mode').value, codeQuery: $('navigation-search').value, semanticQuery: $('semantic-query').value, entryQuery: $('ingress-catalog-search').value, kind: $('semantic-kind').value, scope: $('navigation-search-scope').value, codeResultsOpen: Boolean($('navigation-search').value) && !$('navigation-search-results').hidden },
      filters: {
        search: $('search').value, kind: $('kind').value, relation: $('relation').value,
        codeProjectIds: state.codeProjectIds, preset: $('architecture-preset').value,
        projectBlocks: $('project-blocks').checked, algorithm: state.algorithm,
        systemSearch: $('system-search').value, systems: $('system-systems').checked,
        packages: $('system-packages').checked, systemKind: $('solution-kind').value,
      },
    });
  }

  /** Применяет только известные controls; значения внешнего URL не создают новые options в интерфейсе. */
  function restoreNavigationControls(snapshot) {
    const { route, filters } = snapshot;
    state.discoveryTask=route.task==='impact'&&state.project&&state.graph.metadata?.snapshotId?{kind:'impact',projectId:state.project.id,snapshotId:state.graph.metadata.snapshotId}:null;
    projectOverview?.setOpen(route.overview==='open');
    $('view-mode').value = route.view;
    state.methodPresentation = route.reading === 'methods' ? 'methods' : route.reading === 'documentation' ? 'documentation' : 'diagram';
    $('display-provider').value = route.display;
    updateDisplayProvider();
    $('system-scale').value = route.scale;
    $('architecture-preset').value = filters.preset;
    $('project-blocks').checked = filters.projectBlocks;
    $('search').value = filters.search;
    for (const id of ['kind', 'relation']) {
      $(id).value = [...$(id).options].some(option => option.value === filters[id]) ? filters[id] : '';
    }
    $('system-search').value = filters.systemSearch;
    $('system-systems').checked = filters.systems;
    $('system-packages').checked = filters.packages;
    $('solution-kind').value = filters.systemKind;
    const search = snapshot.searchContext;
    if ($('semantic-query').value !== search.semanticQuery || $('semantic-kind').value !== search.kind) semanticSearch.invalidateSearch();
    $('discovery-mode').value = search.mode;
    $('navigation-search').value = search.codeQuery;
    $('semantic-query').value = search.semanticQuery;
    $('ingress-catalog-search').value = search.entryQuery;
    $('semantic-kind').value = search.kind;
    $('navigation-search-scope').value = search.scope;
    $('navigation-search-results').hidden = !search.codeResultsOpen;
    updateDiscovery();
  }

  /** Back/reload сохраняет текущую карту до принятия target и проверяет поколение каждого await.
   * @param {object} requested Снимок адресуемой навигации, уже нормализованный history.
   * @param {object} options Сигнал отмены и isCurrent одного восстановления.
   * @returns {Promise<void>} Восстанавливает controls/readers только для принятого актуального scope.
   */
  async function restoreNavigation(requested, { signal, isCurrent }) {
    const initial = resolveNavigation(requested, { projects: state.projects });
    const repo = initial.snapshot.route.repo;
    const notices = initial.messages;
    const desired = requested.filters.algorithm;
    const algorithm = state.algorithms.some(item => item.id === desired) ? desired : state.algorithm;
    const needsGraph = !isSystemMode(requested.route.view) && !state.graph.nodes.length && Boolean(state.project?.snapshotId);
    if (state.project?.id !== repo || state.loading || needsGraph || algorithm !== state.algorithm) {
      const accepted = await openProject(repo, false, {signal, isCurrent, algorithm, viewMode: requested.route.view});
      if (accepted === false || !isCurrent()) return;
    }
    if (!isCurrent()) return;
    // Keep current readers and controls usable until the requested scope is committed.
    closeTransientStudy();
    methodWorkbench?.leave();
    searchNavigation.cancel();
    wikiViewer.close(false);
    pathViewer.close();
    state.pathOnGraph = null;
    factsPopup.close(false);
    restoreNavigationControls(requested);
    state.algorithm = algorithm;
    $('algorithm').value = algorithm;
    if (!repo) {
      draw(true);
      if (notices.length) status(notices.join(' '), true);
      return;
    }
    const checked = resolveNavigation(requested, { projects: state.projects, nodes: state.graph.nodes, snapshotId: state.graph.metadata?.snapshotId });
    const snapshot = checked.snapshot;
    const route = snapshot.route;
    notices.push(...checked.messages);
    restoreNavigationControls(snapshot);
    navigationSearch.refresh();
    state.codeProjectIds = snapshot.filters.codeProjectIds;
    renderCodeProjects();
    state.architecture = { level: route.level, projectId: route.project, file: route.file, page: route.page };
    state.community = route.group;
    state.selected = route.view === 'system' ? null : route.node;
    state.expandedId = route.context;
    state.focused = snapshot.focused;
    state.isolatedRoot = route.isolate;
    state.isolatedBlock = snapshot.isolatedBlock;
    state.inlineExpanded = new Set(snapshot.inlineExpanded);
    view.pathPlacementScope = null; view.pathRestorePlacements = new Map(snapshot.methodPlacements.map(item => [item.id, item]));
    state.inlineRestorePlacements = new Map(snapshot.inlineMethodPlacements.map(item => [item.id, item]));
    state.selectedBlock = route.block;
    if (route.view === 'groups' && state.community && !displayedAggregate(filteredNodes(false)).communities.some(group => String(group.id) === state.community)) {
      state.community = null;
      notices.push('Группа из адреса не найдена. Открыт обзор групп.');
    }
    if (state.selected && !filteredNodes(false).some(node => node.id === state.selected) && !route.path) {
      state.selected = null;
      notices.push('Выбранный символ больше не входит в сохранённый фильтр. Открыта доступная карта.');
    }
    documentationViewer.select(null);
    $('answer').replaceChildren();
    if (!state.selected) $('selection').replaceChildren(el('h2', route.view === 'system' ? 'Системы' : 'Код'), el('p', 'Выберите элемент карты для просмотра исходника и связей.', 'muted'));
    if (isSystemMode(route.view)) {
      systemView.selectedNodeId = null;
      systemView.selectedEdge = null;
      systemView.page = route.page;
      if (systemView.status !== 'ready') await systemView.load();
      if (!isCurrent()) return;
      systemView.page = route.page;
      if (state.isolatedRoot && !systemView.displayData?.nodes.some(node => node.id === state.isolatedRoot)) state.isolatedRoot = null;
      // Back из кода восстанавливает весь shell Systems, включая controls и breadcrumbs.
      draw(false);
      if (route.node && systemView.projection?.nodes.some(node => node.id === route.node)) systemView.select(route.node);
      else if (route.node) notices.push('Системный объект из адреса не найден. Открыт обзор систем.');
    } else if (route.path) {
      const restored = await restorePathNavigation(snapshot, { signal, isCurrent, notices });
      if (!isCurrent()) return;
      if (!restored) {
        snapshot.camera = null;
        if (state.selected) { renderSelection(); view.setSelected(state.selected); }
      }
    } else {
      draw(true);
      if (state.selected) { renderSelection(); view.setSelected(state.selected); }
      else if (state.selectedBlock) mapBlocks.restoreSelection();
    }
    if (!isCurrent()) return;
    if (snapshot.camera) { view.camera = { ...snapshot.camera }; view.transform(); }
    await methodWorkbench?.restore(snapshot, { isCurrent });
    if (!isCurrent()) return;
    if (route.wiki) await wikiViewer.openDocument(route.wiki, { nodeId: route.wikiNode, offset: route.wikiOffset, notify: false, signal });
    else if (route.wikiNode) await wikiViewer.openNode(route.wikiNode, { notify: false, signal });
    if (!isCurrent()) return;
    controls();
    if (notices.length) status([...new Set(notices)].join(' '), true);
  }

  /** Путь строится заново по текущему снимку; в history не переносится его граф или текст исходника. */
  async function restorePathNavigation(snapshot, { signal, isCurrent, notices }) {
    const route = snapshot.route;
    try {
      if (route.entry && !state.ingress.data) await loadIngress();
      if (!isCurrent()) return;
      pathViewer.entry = (state.ingress.data?.entries || []).find(entry => entry.id === route.entry) || null;
      if (route.entry && !pathViewer.entry) notices.push('Вход из адреса не найден. Путь показан от метода.');
      const data = await request(`/api/projects/${encodeURIComponent(route.repo)}/execution-path`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
        body: JSON.stringify({ nodeId: route.path, snapshotId: state.graph.metadata?.snapshotId, maxDepth: 30, maxNodes: PATH_NODE_LIMIT }),
      });
      if (!isCurrent()) return;
      if (data.rootNodeId !== route.path || data.snapshotId !== state.graph.metadata?.snapshotId) throw new Error('Путь относится к другому снимку. Открыта карта кода.');
      // Сообщение о последнем раскрытии временное; Back восстанавливает только фактическую схему и выбор.
      view.pathReveal = null;
      state.pathOnGraph = data;
      state.selected ||= route.path;
      paintPathGraph(data);
      const available = new Set((data.steps || []).map(step => step.nodeId));
      view.pathExpandedIds = new Set(snapshot.pathExpanded.filter(id => available.has(id) || id.startsWith(route.path + '::inline-call:')));
      view.pathCollapsedIds = new Set(snapshot.pathCollapsed.filter(id => available.has(id)));
      paintPathGraph(data);
      renderSelection();
      return true;
    } catch (error) {
      if (!isCurrent() || error.name === 'AbortError') return;
      state.pathOnGraph = null;
      draw(true);
      status(error.message, true);
      return false;
    }
  }

  return { capture: captureNavigation, restore: restoreNavigation };
}
