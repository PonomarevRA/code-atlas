import { createDesktopAutomation, rememberDesktopMapScope } from './desktop-automation.js';
import { canvasViewportRect, ensureCanvasVisible } from './canvas-viewport.js';
import { renderMapEmptyState } from './map-empty-state.js';
import { createProjectOverview, projectScopeLabel } from './project-overview.js';
import { analysisDiagnosticsState, analysisIssueCard } from './analysis-diagnostics.js';
import { restoreProgressiveFocus } from './progressive-navigation.js';
import { loadMods, modErrors } from './mod-runtime.js';
import { projectDataMaps } from './project-data-maps.js';
import { createMapBlocks, blockMembers } from './map-blocks.js';
import { createGroupOverview } from './group-overview-controller.js';
import { SemanticSearchPanel } from './semantic-search.js';
import { WorkspacePanels } from './workspace-panels.js';
import { ActivitySidebar } from './activity-sidebar.js';
import { AgentActivity } from './agent-activity.js';
import { mountWorkspaceLayout, bindProjectPicker } from './workspace-layout.js';
import { MapFindbar } from './map-findbar.js';
import { createMapFindScene } from './map-find-scene.js';
import { MapChrome } from './map-chrome.js';
import { bindGraphExport, bindArchitectureExport } from './graph-export.js';
import { DiscoveryShell } from './discovery-shell.js';
import { WikiViewer } from './wiki-view.js';
import { createSearchNavigation } from './search-navigation.js';
import { isolateCallChain, isolateCallRoots, isolateExecutionPath } from './call-isolation.js';
import { RemoteRepositoryPanel } from './remote-repositories.js';
import { AgentSetupPanel } from './agent-setup.js';
import { createCodeNavigation } from './code-navigation-controller.js';
import { NavigationHistory, navigationInteraction } from './navigation-history.js';
import { bindBrowserMapLink, readBrowserMapURL } from './browser-map-link.js';
import { createWorkspaceNavigation } from './workspace-navigation.js';
import { isSystemMode, presentViewMode } from './view-modes.js';
import { SavedViewsPanel } from './saved-views.js';
import { DocumentationViewer } from './documentation-view.js';
import { RelatedRepositories } from './related-repositories.js';
import { SystemArchitectureView } from './system-architecture.js';
import { NodeDescriptionViewer } from './node-description.js';
import { NodeAttributesViewer } from './node-attributes.js';
import { NodeFactsPopup } from './node-facts-popup.js';
import { ImplementationList } from './implementation-list.js';
import { createMethodWorkbench } from './method-workbench-controller.js';
import { CODE_ROLES } from './code-semantics.js';
import { ExecutionPathViewer } from './execution-path.js';
import { PathToolbar } from './path-toolbar.js';
import { MethodDocumentation } from './method-documentation.js';
import { projectExecutionPath, pathReadingTitle } from './path-graph-projection.js';
import { expansionNeighborhood } from './expansion.js';
import { DISPLAY_PROVIDERS, displayProvider, workspaceDisplayProvider } from './display-providers.js';
import { createNavigationSearch } from './navigation-search.js';
import { monitorJob } from './job-monitor.js';
import { createGitController } from './git-controller.js';
import { GraphModel } from './graph-model.js';
import { $, el } from './dom.js';
import { createNodeResult, setResultSelected, activateResultCard } from './result-list.js';
import { renderInspector } from './inspector.js';
import { renderNodeHealth } from './node-health-panel.js';
import { HealthRefreshController, applyHealthUpdates } from './health-refresh.js';
import { createIngressController } from './ingress-controller.js';
import { renderTestClassOverview, testsForContainer } from './test-class-overview.js';
import { requestJson } from './bounded-request.js';
import { beginLoading } from './loading-state.js';
import { mountLoadingStatus } from './loading-status.js';
import { projectColor } from './colors.js';
import { SourceViewer } from './source-view.js';
import { GraphView } from './graph.js';
import { bindNodePathGesture, uniqueNodeEntry } from './node-path-gesture.js';
import { projectCommunityId } from './project-blocks.js';
import { RegexSearch } from './regex-search.js';
import { markChangedNodes, markChangedCommunities } from './changes.js';
import { codeProjectId } from './filters.js';
mountLoadingStatus(document);
const activityLayout = mountWorkspaceLayout(document);
const state = { projects: [], project: null, graph: { nodes: [], edges: [] }, nodes: new Map(), community: null, selected: null, focused: false, listLimit: 100, revision: 0, answerRevision: 0, job: null, submitting: false, loading: false, algorithm: 'graphistry', algorithms: [], codeProjectIds: null, comparison: null, compareRevision: 0, comparing: false, regexIds: [], regexScope: null };
state.pendingMapLoad = null;
state.requestedProjectId = null;
state.loadedAlgorithm = state.algorithm;
let mapLoadRevision = 0;
let jobLoadingRelease = null;
let jobLoadingRevision = 0;
window.addEventListener('pagehide', event => {
  if (event.persisted) return;
  state.pendingMapLoad?.controller.abort();
  jobLoadingRevision++;
  jobLoadingRelease?.();
});
state.expandedId = null;
state.isolatedRoot = null;
state.architecture = { level: 'projects', projectId: null, file: null, page: 0 };
state.architectureMembership = new Map();
state.architectureGroups = [];
state.ingress = { data: null, page: 0, revision: 0, controller: null, message: '' };
const model = new GraphModel();
let navigationHistory = null;
let mapChrome;
let activitySidebar;
let methodWorkbench;
const healthRefresh = new HealthRefreshController($('health-refresh'), { onReady: refreshDisplayedHealth });
window.addEventListener('pagehide', () => healthRefresh.stop());
state.readerState = null;
const workspacePanels = new WorkspacePanels({ defaultOpen:{left:true,right:false}, workspace: document.querySelector('.workspace'), panels: { left: $('workspace-left'), right: $('workspace-right') }, toggles: { left: $('panel-left-toggle'), right: $('panel-right-toggle') }, bindToggles: false,
  onLayout: info => { mapChrome?.syncPanels(info); activitySidebar?.syncLayout(); } });
const discoveryShell = new DiscoveryShell($('workspace-discovery'), $('discovery-toggle'));
mapChrome = new MapChrome({ body: document.body, toolbar: document.querySelector('.workspace-controls'),
  search: $('workspace-search-controls'), controls: $('map-chrome-controls'), discovery: discoveryShell,
  panels: workspacePanels, toggles: { search: $('search-controls-toggle'), controls: $('map-controls-toggle'), focus: $('map-focus-toggle'), canvas: $('canvas-controls-toggle') },
  context: $('map-compact-context'), canvas: document.querySelector('.canvas-wrap'), canvasControls: $('canvas-navigation-controls'), activitySearch: true, compact: true, bottomInset: 22,
  onLayout: () => activitySidebar?.syncLayout() });
activitySidebar = new ActivitySidebar({ root: activityLayout.rail, roots: [activityLayout.rail, activityLayout.rightRail], blocks: activityLayout.blocks, panels: workspacePanels,
  onActivate: (id, options) => {
    if (id === 'search') { mapChrome.setOpen('search', true, { focus: options.remember, remember: options.remember }); discoveryShell.openForMode(); }
    mapChrome.updateHeight();
  } });
const releaseProjectPicker = bindProjectPicker(activityLayout.projectPicker, () => activitySidebar.reveal('repository'), $('projects'));
window.addEventListener('pagehide', releaseProjectPicker, {once:true});
const wikiViewer = new WikiViewer($('wiki-viewer'), {
  getScope: () => ({ projectId: state.project?.id, snapshotId: state.graph.metadata?.snapshotId }),
  getNode: id => state.nodes.get(id),
  onNavigate: () => navigationHistory?.commit(), onCode: id => selectSemanticNode(id),
  onBuild: () => { $('discovery-mode').value = 'meaning'; updateDiscovery(true); $('semantic-search-panel').querySelector('.semantic-index-controls').open = true; },
});
$('node-wiki-open').addEventListener('click', () => { navigationHistory?.checkpoint(); showStudy('wiki'); wikiViewer.openNode(state.selected); });
const agentSetupPanel = new AgentSetupPanel($('agent-setup-panel'), {
  getContext: () => ({ projectId: state.project?.id, snapshotId: state.graph.metadata?.snapshotId }),
});
const savedViewsPanel = new SavedViewsPanel($('saved-views-panel'), {
  getContext: () => ({ projectId: state.project?.id, snapshotId: state.graph.metadata?.snapshotId, nodeId: state.selected, label: state.nodes.get(state.selected)?.label }),
});
savedViewsPanel.refresh();
const documentationViewer = new DocumentationViewer($('documentation-view'), {
  getScope: () => ({ projectId: state.project?.id, snapshotId: state.graph.metadata?.snapshotId }),
});
documentationViewer.loadConfig();
let sourceOverride = null;
const sourceViewer = new SourceViewer($('source-viewer'), () => sourceOverride || ({ projectId: state.project?.id, snapshotId: state.graph.metadata?.snapshotId, nodeId: state.selected, label: state.nodes.get(state.selected)?.label }));
$('source-open').addEventListener('click', () => { sourceOverride = null; showStudy('source'); sourceViewer.open(); });
/** Открывает исходник raw-узла независимо от фильтра, сохраняя выбранный контекст карты. */
function openSourceNode(nodeId, line) {
  const node = state.nodes.get(nodeId);
  if (!node || !node.file) return;
  const focusLine = Number.isInteger(line) && line > 0 ? line : undefined;
  sourceOverride = { projectId: state.project.id, snapshotId: state.graph.metadata.snapshotId, nodeId, label: node.label, selectionId: state.selected, line: focusLine };
  showStudy('source');
  sourceViewer.open();
}
/** Описывает текущий выбранный символ без предположений о принадлежности синтетических узлов. */
function selectedContext() {
  return { projectId: state.project?.id, snapshotId: state.graph.metadata?.snapshotId, nodeId: state.selected, label: state.nodes.get(state.selected)?.label };
}
/** Один хоп входящих вызовов уже загруженного графа кода, в порядке строки исходника. */
function directCallers(nodeId) {
  if (!nodeId) return [];
  const callers = [];
  for (const edge of model.incident(nodeId, state.graph.nodes)) {
    if (edge.target !== nodeId) continue;
    if (edge.relation !== 'calls' && edge.relation !== 'dispatches_to') continue;
    const node = state.nodes.get(edge.source);
    if (!node || String(node.id).startsWith('system-')) continue;
    callers.push(node);
  }
  callers.sort((left, right) => (left.line ?? 1e9) - (right.line ?? 1e9) || String(left.label || left.id).localeCompare(String(right.label || right.id)));
  return callers;
}
const pathViewer = new ExecutionPathViewer($('execution-path'), selectedContext, {
  onSource: openSourceNode,
  getCallers: nodeId => directCallers(nodeId),
  getEntries: nodeId => entriesForNode(nodeId),
  onResult: data => { state.pathOnGraph = data; paintPathGraph(data); navigationHistory?.commit(); },
  onError: message => status(message, true),
});
pathViewer.canvasOnly = true;
const descriptionViewer = new NodeDescriptionViewer($('node-description'), selectedContext, { onSource: openSourceNode });
const methodDocumentation = new MethodDocumentation($('method-documentation'), {
  onSelect: id => selectNode(id, false), onSource: openSourceNode,
  onDiagram: () => setMethodPresentation('diagram'),
  onRefreshMap: () => $('rebuild').click(),
});
const attributesViewer = new NodeAttributesViewer($('node-attributes'), selectedContext, { onSource: openSourceNode });
const factsPopup = new NodeFactsPopup($('node-facts-popup'), { onSource: (id, line) => { factsPopup.close(false); openSourceNode(id, line); } });
const implementationList = new ImplementationList($('implementation-list-popup'), { onSource: openSourceNode });
/** Открывает одну грань изучения и закрывает остальные, чтобы путь и исходник не спорили. */
function showStudy(kind) {
  if (kind !== 'wiki') wikiViewer.close(false);
  if (kind !== 'description') descriptionViewer.close();
  if (kind !== 'attributes') attributesViewer.close();
  if (kind !== 'path') pathViewer.close();
  if (kind !== 'source') sourceViewer.close();
  $('node-description-open').classList.toggle('primary', kind === 'description');
  $('node-attributes-open').classList.toggle('primary', kind === 'attributes');
  $('execution-path-open').classList.toggle('primary', kind === 'path');
  $('source-open').classList.toggle('primary', kind === 'source');
  $('node-wiki-open').classList.toggle('primary', kind === 'wiki');
}
/** Переход истории закрывает временные панели и освобождает modal focus; wiki восстанавливается из URL отдельно. */
function closeTransientStudy() {
  sourceOverride = null;
  showStudy(null);
  factsPopup.close(false);
}
/** Запускает короткое описание отдельно от обхода возможных вызовов. */
function describeNode(nodeId = state.selected) {
  if (nodeId !== state.selected) selectNode(nodeId);
  if (state.selected !== nodeId) return;
  showStudy('description');
  descriptionViewer.open();
}
$('node-description-open').addEventListener('click', () => describeNode());
/** Атрибуты читаются отдельно; выбор, раскрытые реализации и камера остаются на карте. */
$('node-attributes-open').addEventListener('click', () => { showStudy('attributes'); attributesViewer.open(); });
/** Анализирует выбранный raw-символ и рисует путь в окне графа теми же узлами. */
function analyzePath(nodeId = state.selected, entry = null, presentation = 'diagram') {
  if (showTestClassOverview(nodeId)) return;
  navigationHistory?.checkpoint();
  if (nodeId !== state.selected) selectNode(nodeId);
  if (state.selected !== nodeId) return;
  methodWorkbench?.leave();
  state.methodPresentation = presentation;
  $('display-provider').value = 'flow';
  updateDisplayProvider();
  if (state.isolatedRoot) state.isolatedRoot = nodeId;
  pathViewer.entry = entry;
  pathViewer.canvasOnly = true;
  showStudy('path');
  discoveryShell.setOpen(false);
  pathViewer.open();
  status(entry ? 'Строим путь от входа на карте…' : 'Строим путь выполнения на карте…');
}
/** Рисует раскрываемые блоки пути; исходные линии и уверенность сохраняются для подписей связей. */
function paintPathGraph(data) {
  if (showTestClassOverview(data.rootNodeId)) return;
  $('expansion-toolbar').hidden = true;
  $('architecture-navigation').hidden = true;
  $('ingress-panel').hidden = true;
  $('back-overview').hidden = false;
  $('back-overview').textContent = '← Карта кода';
  $('view-title').textContent = pathReadingTitle(data, pathViewer.entry);
  const entry = pathViewer.entry;
  const entryFlow = entry && $('display-provider').value === 'flow';
  let instruction = 'Раскрывайте реализации прямо на схеме. Вызовы читаются слева направо; условия и циклы можно скрыть переключателями.';
  if (entryFlow) instruction = 'Путь от входа показан Flow-картой. Раскрывайте вызовы и условия: уже видимые карточки сохраняют свои места. Выбор подсвечивает входящие и исходящие вызовы.';
  else if ($('display-provider').value === 'flow') instruction = 'Читайте путь слева направо. Раскрывайте вызовы на схеме; условия if/switch и циклы включаются отдельно. Выбор подсвечивает входящие и исходящие связи.';
  $('view-instruction').textContent = instruction;
  const analysisData = data;
  if (state.isolatedRoot) data = isolatedPathData(data);
  const { nodes, edges } = projectExecutionPath(data, state.nodes, entry);
  view.pathImplementationCatalog = data.implementationCatalog || {};
  if (!nodes.length) {
    status('Узлы пути не найдены на текущей карте.', true);
    return;
  }
  view.pathRootId = data.rootNodeId;
  view.pathSnapshotId = data.snapshotId;
  view.pathProjectId = state.project?.id;
  view.onPathSource = (id, line) => openSourceNode(id, line);
  view.onPathRelationsChange = info => pathToolbar.renderRelations(info);
  view.onPathScopeChange = info => {
    const noun = data.rootKind === 'class' || data.steps?.some(step => step.kind === 'field') ? 'блоков' : 'методов';
    pathToolbar.render(info, data);
    renderChainResults(view.pathScope.nodes.filter(node => !String(node.id).startsWith('path-entry:')), 'методов схемы');
    updateIsolationToolbar();
    $('visible-count').textContent = `${info.visible} из ${info.total} ${noun} · «Раскрыть» добавляет реализацию на эту схему · перетаскивание и колесо`;
    $('node-count').textContent = `${info.visible} / ${info.total} ${noun}`;
    $('graph-stats').textContent = `Схема кода · ${info.visible} из ${info.total} ${noun} · ${view.pathFlatFlow ? 'чтение слева направо' : 'условия и циклы внутри блоков'}`;
    queueMicrotask(() => navigationHistory?.commit());
  };
  const newAnalysis = view.pathAnalysisData !== analysisData;
  if (newAnalysis) discoveryShell.setOpen(false);
  if (newAnalysis) { view.pathReveal = null; view.readableFocusTrail = []; view.onReadableFocusChange(null); }
  view.pathAnalysisData = analysisData;
  view.clearExpansion(true);
  view.renderPathCards(nodes, edges, state.selected || data.rootNodeId);
  syncMethodPresentation();
  updateDisplayProvider();
  updateIsolationToolbar();
  if (newAnalysis) {
    view.fitPathInitial();
    // После запуска пути рабочее поле и его управление должны быть видны вместе.
    $('path-toolbar').scrollIntoView({ behavior: 'instant', block: 'start' });
  }
  status(`Путь содержит ${data.steps?.length || 0} методов${entry ? ` от ${entry.label || entry.route}` : ''}. Раскрывайте реализации на схеме.`);
}
$('execution-path-open').addEventListener('click', () => analyzePath());
$('method-documentation-open').addEventListener('click', () => {
  if (state.pathOnGraph?.steps.some(step => step.nodeId === state.selected)) {
    showStudy(null);
    setMethodPresentation('documentation');
  } else analyzePath(state.selected, null, 'documentation');
});
/** Меняет способ чтения текущего анализа, сохраняя выбранный метод, раскрытие и камеру. */
function setMethodPresentation(mode) {
  navigationHistory?.checkpoint();
  if (mode === 'methods') {
    const id = state.selected || state.pathOnGraph?.rootNodeId;
    if (id) methodWorkbench.open({ ...state.nodes.get(id), id, targetIds: [id] });
    return;
  }
  methodWorkbench?.leave();
  state.methodPresentation = mode === 'documentation' ? 'documentation' : 'diagram';
  syncMethodPresentation();
  navigationHistory?.commit();
}
/** Документация занимает рабочее поле; инструменты масштаба принадлежат только схеме. */
function syncMethodPresentation() {
  methodWorkbench?.checkContext();
  const reading = Boolean(methodWorkbench?.reader.active && state.methodPresentation === 'methods');
  const active = Boolean(state.pathOnGraph && state.methodPresentation === 'documentation');
  $('graph').hidden = active || reading;
  document.querySelector('.canvas-wrap').classList.toggle('method-workbench-active', reading);
  $('path-toolbar').classList.toggle('method-workbench-mode', reading);
  document.querySelector('.canvas-wrap').classList.toggle('method-documentation-active', active);
  $('path-toolbar').classList.toggle('method-documentation-mode', active);
  $('map-keyboard-help').hidden = active || reading;
  if (state.pathOnGraph) {
    $('view-title').textContent = active ? 'Документация метода' : pathReadingTitle(state.pathOnGraph, pathViewer.entry);
    $('view-instruction').textContent = active
      ? 'Читайте комментарии, условия и действия. Вызов открывает документацию следующего метода.'
      : 'Читайте путь слева направо. Раскрывайте вызовы и условия на схеме.';
  }
  const selector = $('path-toolbar').querySelector('[data-path-toolbar="presentation"]');
  selector.value = reading ? 'methods' : active ? 'documentation' : 'diagram';
  methodDocumentation.display(state.pathOnGraph, state.selected, active);
  // Inspector focus can scroll the document past the reading surface; preserve its internal position.
  if (active || reading) ensureCanvasVisible($(active ? 'method-documentation' : 'method-workbench'));
}
const view = new GraphView($('graph'), selectNode, id => {
  if (isSystemMode($('view-mode').value)) systemView.select(id);
  else mapBlocks.select(id);
});
const agentActivity = new AgentActivity({ view,
  getContext: () => ({ projectId: state.project?.id, projectName: state.project?.name, snapshotId: state.graph.metadata?.snapshotId }),
  onNavigate: id => { if (state.nodes.has(id)) selectSemanticNode(id); },
});
view.onViewportChange = () => agentActivity.markers.update();
// Reuse path navigation and history; synthetic groups and ambiguous ingress handlers stay selectable.
const nodePathGesture = bindNodePathGesture(view.svg, {
  onNode: id => {
    if (!state.nodes.has(id)) return;
    analyzePath(id, uniqueNodeEntry(state.ingress.data?.entries || [], id));
  },
  onEntry: id => {
    const entry = state.ingress.data?.entries?.find(item => item.id === id);
    const handlers = entry?.handlerNodeIds || [];
    if (handlers.length === 1 && state.nodes.has(handlers[0])) analyzePath(handlers[0], entry);
  },
});
const mapFind = new MapFindbar({ root: $('map-findbar'), trigger: activityLayout.mapFindOpen, svg: view.svg,
  adapter: createMapFindScene(view, { getScope: () => JSON.stringify([state.project?.id, state.graph.metadata?.snapshotId]),
    getViewport: () => ({ x: 0, y: 64, width: view.svg.clientWidth, height: Math.max(0, view.svg.clientHeight - 64) }) }),
  isAvailable: () => Boolean(state.project && !state.loading),
  onOpen: () => {
    $('workspace-menu').open = false;
    // Narrow drawers cover the Find bar; reveal the map without rewriting the saved drawer preference.
    if (workspacePanels.mobile) for (const side of ['left','right']) workspacePanels.setOpen(side, false, { remember: false });
  } });
view.onSceneChange = () => { mapFind.refresh(); agentActivity.sync(); };
view.getCanvasViewportRect = () => canvasViewportRect(view.svg);
view.onReadableFocusChange = frame => { $('canvas-focus-return').hidden = !frame; };
$('canvas-focus-return').addEventListener('click', () => {
  const frame = view.readableFocusTrail?.at(-1);
  if (frame) restoreProgressiveFocus(view, frame.occurrenceId);
});
view.onPathSource = (id, line) => openSourceNode(id, line);
view.modIsolate = id => {selectNode(id);isolateSelectedNode();};
view.onSequenceSelect = (id, occurrenceId) => selectNode(id, true, true, occurrenceId);
view.getSourceNode = id => state.nodes.get(id);
methodWorkbench = createMethodWorkbench({ root: $('method-workbench'), state, view, onSource: openSourceNode,
  checkpoint: () => navigationHistory?.checkpoint(), commit: options => navigationHistory?.commit(options),
  sync: syncMethodPresentation, status, onRefreshMap: () => $('rebuild').click() });
view.onReadCall = (node, opener) => methodWorkbench.open(node, opener);
view.onImplementationList = (node, opener) => methodWorkbench.open(node, opener);
const mapBlocks = createMapBlocks({ state, view, filteredNodes, communityId: displayedCommunityId,
  openGroupProject: projectId => groupNavigation.open(projectId),
  selectNode, navigate: navigateArchitecture, openCommunity, analyzePath, isolate: isolateSelectedNode,
  readMethod: node => methodWorkbench.open(node),
  draw, controls, checkpoint: () => navigationHistory?.checkpoint(), commit: () => navigationHistory?.commit(),
  reveal: () => activitySidebar.reveal('details') });
const groupNavigation = createGroupOverview({ state, view, resetBlocks: () => mapBlocks.reset(), selectBlock: mapBlocks.select, draw, controls,
  onLayout: () => mapChrome.updateHeight(),
  checkpoint: () => navigationHistory?.checkpoint(), commit: () => navigationHistory?.commit() });
view.onCameraChange = () => navigationHistory?.scheduleCamera();
view.onDescribeNode = (nodeId, opener) => {
  const node = state.nodes.get(nodeId);
  if (node) factsPopup.open({ projectId: state.project?.id, snapshotId: state.graph.metadata?.snapshotId, nodeId, label: node.label }, opener);
};
for (const role of CODE_ROLES) {
  const item = el('span', role.label, 'semantic-legend-item');
  item.style.setProperty('--role-color', role.color);
  item.title = role.description;
  $('semantic-legend').append(item);
}
// Путь читается горизонтально; резерв всей цепочки сохраняет места при раскрытии.
view.pathReading = true;
view.pathControlFilters = { conditions: true, loops: true };
const pathToolbar = new PathToolbar($('path-toolbar'), {
  presentation: setMethodPresentation,
  filterControls: filters => view.setPathControlFilters(filters),
  expand: () => view.expandAllPathNodes(),
  collapse: () => { view.collapseAllPathNodes(); view.fitPathInitial(); },
  focus: () => view.focusPathNode(state.selected || state.pathOnGraph?.rootNodeId),
  fit: () => view.fit(),
  follow: id => view.followPathNode(id),
});
const relatedRepositories = new RelatedRepositories({ state, controls, onChange: () => systemView.invalidate() });
const systemView = new SystemArchitectureView({ state, view, controls, getRelatedIds: () => relatedRepositories.getSelected(), onInspect: () => activitySidebar.reveal('details'), onDocumentation: node => documentationViewer.select(node), openProjectCode: async (projectId, repositoryId = state.project?.id) => {
  if (repositoryId !== state.project?.id) {
    const originalRevision = state.revision;
    await refreshProjects();
    if (state.revision !== originalRevision) return;
    $('view-mode').value = 'architecture';
    const loading = openProject(repositoryId);
    const expectedRevision = state.revision;
    await loading;
    if (state.revision !== expectedRevision || state.project?.id !== repositoryId || !state.graph.metadata?.snapshotId) return;
  }
  $('view-mode').value = 'architecture';
  navigateArchitecture({ level: 'files', projectId, file: null, page: 0 });
} });
for (const provider of DISPLAY_PROVIDERS) {
  const option = document.createElement('option');
  option.value = provider.id;
  option.textContent = provider.label;
  $('display-provider').append(option);
}
try { $('display-provider').value = displayProvider(localStorage.getItem('code-atlas-display-provider')).id; } catch { /* Топология остаётся значением по умолчанию. */ }
/** Применяет только визуальные возможности провайдера; предпочтения карты сохраняются. */
function updateDisplayProvider({ restorePreference = false } = {}) {
  const code = $('view-mode').value === 'architecture';
  if (restorePreference && !code) {
    try { $('display-provider').value = displayProvider(localStorage.getItem('code-atlas-display-provider')).id; }
    catch { /* Keep the current display when storage is unavailable. */ }
  }
  const provider = workspaceDisplayProvider($('view-mode').value, $('display-provider').value);
  $('display-provider').value = provider.id;
  $('display-provider').disabled = code;
  for (const option of $('display-provider').options) {
    option.hidden = code && option.value !== 'flow';
    option.textContent = code && option.value === 'flow' ? 'Карточки кода'
      : displayProvider(option.value).label;
  }
  view.codeReading = code;
  view.setDisplayProvider(provider.id);
  $('project-blocks').disabled = isSystemMode($('view-mode').value) || state.algorithm === 'natural' || !provider.projectBlocks;
  $('bundle-edges').disabled = !provider.bundles;
  const path = Boolean(state.pathOnGraph);
  $('display-provider-note').hidden = !path && provider.id !== 'flow';
  let note = 'Обзор групп: проект → группы → символы. До 6 карточек на странице. Зависимости появляются при выборе или наведении; это не порядок выполнения.';
  if (code) note = 'Код: проект → папки и файлы → символы. В HTML видна вложенность. «Раскрыть здесь» показывает детали. Для метода «Схема» открывает путь. «Вместить» показывает текущую страницу целиком.';
  if (path && pathViewer.entry && provider.id === 'flow') note = 'Методы и условия на общей Flow-карте. Раскрытие сохраняет позиции карточек. Пунктир — возможная реализация; выбор подсвечивает входящие и исходящие вызовы.';
  else if (path) note = 'Реализация: методы и условия из исходника. Раскрытие добавляет тела прямо на карту. Пунктир — возможная реализация; выбор подсвечивает входящие и исходящие вызовы.';
  $('display-provider-note').textContent = note;
  $('architecture-preset').disabled = path;
}
updateDisplayProvider();
$('display-provider').addEventListener('change', () => {
  updateDisplayProvider();
  try { localStorage.setItem('code-atlas-display-provider', $('display-provider').value); } catch { /* Приватный режим может запрещать запись. */ }
  draw(Boolean(!state.pathOnGraph && !state.isolatedRoot));
});
try { $('bundle-edges').checked = localStorage.getItem('code-atlas-bundle-edges') !== 'false'; } catch { /* Настройка доступна и без хранилища. */ }
view.setBundled($('bundle-edges').checked);
$('bundle-edges').addEventListener('change', () => {
  view.setBundled($('bundle-edges').checked);
  try { localStorage.setItem('code-atlas-bundle-edges', String($('bundle-edges').checked)); } catch { /* Приватный режим может запрещать запись. */ }
});
const regexSearch = new RegexSearch(ids => { state.regexIds = ids; updateRegexHighlight(); }, (message, error = false) => { $('regex-status').textContent = message; $('regex-status').classList.toggle('error-text', error); });
const ingressController = createIngressController({ state, view, draw, controls, filteredNodes, selectNode, analyzePath, describeNode, onReady: () => {
  if (state.pathOnGraph && state.selected === state.pathOnGraph.rootNodeId && showTestClassOverview(state.pathOnGraph.rootNodeId)) return;
  if (state.selected) renderSelection();
  if (pathViewer.opened && pathViewer.latestData) pathViewer.renderBoard(pathViewer.latestData);
}, navigateNode: selectSemanticNode, onSource: openSourceNode, revealDetails: () => activitySidebar.reveal('details') });
const loadIngress = ingressController.load;
const renderIngress = ingressController.render;
const gitController = createGitController({ state, api, applyFilters, controls });
const clearComparison = gitController.clear;
const navigationSearch = createNavigationSearch({ model,
  getNodes: scope => scope === 'all' ? state.graph.nodes : filteredNodes(false),
  getScopeKey: scope => JSON.stringify([state.project?.id, state.graph.metadata?.snapshotId, state.graph.nodes.length, scope, scope === 'current' ? [state.codeProjectIds, $('search').value, $('kind').value, $('relation').value, state.isolatedRoot, state.isolatedBlock?.id, state.comparison?.changedNodeIds, $('only-changed').checked] : null]),
  getProjects: () => state.graph.codeProjects || [],
  onSelect: (id, { scope }) => scope === 'current' ? selectNode(id) : selectSemanticNode(id),
  onVisibilityChange: () => { discoveryShell.setOpen(false); navigationHistory?.commit({ replace: true }); },
});
const semanticSearch = new SemanticSearchPanel($('semantic-search-panel'), {
  getScope: () => ({ projectId: state.project?.id, snapshotId: state.graph.metadata?.snapshotId }),
  onSelect: selectSemanticNode,
  onOpenDocument: (id, scope) => {
    if (scope.projectId !== state.project?.id || scope.snapshotId !== state.graph.metadata?.snapshotId) return;
    navigationHistory?.checkpoint(); wikiViewer.openDocument(id, { nodeId: null });
  },
});
const searchNavigation = createSearchNavigation({
  getScope: () => ({ projectId: state.project?.id, snapshotId: state.graph.metadata?.snapshotId }),
  getNode: id => state.nodes.get(id), ensureGraph: ensureSearchGraph,
  navigate: navigateSearchNode, onError: message => status(message, true),
});
/** Поиск остаётся в одном месте, запрос каждого способа сохраняется при переключении. */
function updateDiscovery(load = false) {
  if (load) activitySidebar.reveal('search');
  const mode = $('discovery-mode').value;
  $('navigation-search-panel').hidden = mode !== 'code' || !state.project;
  $('semantic-search-panel').hidden = mode !== 'meaning' || !state.project;
  $('ingress-catalog-launcher').hidden = mode !== 'entries' || !state.project;
  if (mode !== 'entries' || !state.project) ingressController.catalog.close();
  discoveryShell.syncMode(mode);
  if (load) discoveryShell.openForMode();
  if (load && mode === 'entries' && state.project) ensureSearchGraph(selectedContext()).then(loadIngress).catch(error => status(error.message, true));
}
$('discovery-mode').addEventListener('change', () => { updateDiscovery(true); if ($('discovery-mode').value === 'entries' && state.project) ingressController.catalog.open(); });
updateDiscovery();
/** «Системы» загружают полный граф только когда пользователю понадобился поиск символов. */
$('navigation-search').addEventListener('input', () => {
  if (!state.graph.nodes.length && state.project && !state.loading) ensureSearchGraph(selectedContext()).then(() => navigationSearch.refresh()).catch(error => status(error.message, true));
});
/** Обзор использует тот же выбранный проект; отдельная запись/каталог/чат не создаются. */
const projectOverview = createProjectOverview($('project-overview'), {
  onAction: startProjectTask,
  onState: data => {state.overviewMapState=data;syncProjectOverview();},
  onLayout: () => {
    $('project-overview-open').setAttribute('aria-expanded',String(!$('project-overview').hidden));
    queueMicrotask(()=>mapChrome?.updateHeight());
  },
});
$('project-impact-show').addEventListener('click', showImpactLinks);
$('project-task-clear').addEventListener('click', () => {navigationHistory?.checkpoint();state.discoveryTask=null;syncProjectOverview();navigationHistory?.commit();});
/** Сохраняет состояние обзора в истории и возвращает фокус к кнопке карты. */
function toggleProjectOverview(open) {
  navigationHistory?.checkpoint();
  projectOverview.setOpen(open);
  $(open?'project-overview-title':'project-overview-open').focus({preventScroll:true});
  navigationHistory?.commit();
}
$('project-overview-open').addEventListener('click', () => toggleProjectOverview(!projectOverview.isOpen()));
$('project-overview-close').addEventListener('click', () => toggleProjectOverview(false));
$('project-overview').addEventListener('keydown', event => {
  if(event.key==='Escape'&&!event.defaultPrevented)toggleProjectOverview(false);
});
window.addEventListener('pagehide', event => { if (!event.persisted) projectOverview.dispose(); });

/**
 * Показывает точный контекст карты и отдельно обновляет обзор, не перестраивая геометрию.
 * @returns {void} Поиск/источники остаются привязаны к committed project/snapshot.
 */
function syncProjectOverview() {
  projectOverview.sync({project:state.project,snapshotId:state.graph.metadata?.snapshotId});
  $('project-overview-open').hidden=!state.project;
  const projectLabel=(state.graph.codeProjects||[]).find(item=>String(item.id)===state.architecture.projectId)?.label;
  const scopeLabel=projectScopeLabel({project:state.project,snapshotId:state.graph.metadata?.snapshotId,
    mode:$('view-mode').value,file:state.architecture.file,projectLabel,nodeLabel:state.nodes.get(state.selected)?.label});
  $('project-scope-label').textContent=state.project?scopeLabel.split(' › ').slice(1).join(' › '):scopeLabel;
  $('project-scope-label').title=scopeLabel;
  const task=state.discoveryTask;
  if(task&&(task.projectId!==state.project?.id||task.snapshotId!==state.graph.metadata?.snapshotId))state.discoveryTask=null;
  const selected=state.nodes.get(state.selected);
  const impactReady=Boolean(selected&&!['analysis-error','analysis_error'].includes(selected.kind));
  $('project-task-context').hidden=!state.discoveryTask;
  $('project-impact-show').disabled=!impactReady;
  $('project-task-hint').textContent=impactReady?'Оценка влияния · '+(selected.label||selected.id)+'. Откройте входящие и исходящие статические связи; отсутствие связи не доказывает отсутствие влияния.'
    :'Оценка влияния · шаг 1 из 2: выберите область, файл и символ на карте либо найдите его в поиске. Шаг 2: «Связи выбранного узла». Фильтры карты ограничивают показанный контекст.';
  const banner=$('project-load-state');
  const overviewState=state.overviewMapState;
  const matches=Boolean(overviewState && state.project && overviewState.projectId===state.project.id && (overviewState.snapshotId||null)===(state.graph.metadata?.snapshotId||null));
  const failedWithSnapshot=Boolean(state.graph.metadata?.snapshotId) && (matches ? overviewState.state==='build_failed_with_snapshot' : state.project?.status==='failed');
  const refreshing=matches && overviewState.state==='refreshing_with_snapshot';
  const diagnostics=analysisDiagnosticsState(matches?overviewState.diagnostics:state.graph.metadata?.analysisDiagnostics);
  banner.hidden=!state.mapLoadError && !state.pendingMapLoad && !failedWithSnapshot && !refreshing && !diagnostics.partial;
  if(state.mapLoadError)banner.textContent='Не удалось открыть запрошенную карту: '+state.mapLoadError+(state.graph.metadata?.snapshotId?' Открытая карта и её контекст сохранены.':'');
  else if(state.pendingMapLoad)banner.textContent='Открываем «'+(state.pendingMapLoad.project?.name||'проект')+'»…'+(state.graph.metadata?.snapshotId?' Можно продолжать работу с открытой картой.':'');
  else if(failedWithSnapshot)banner.textContent='Последнее обновление не удалось. Показан последний доступный снимок; его можно исследовать. Обновить карту можно явной командой слева.';
  else if(refreshing)banner.textContent='Карта обновляется. Последний доступный снимок остаётся интерактивным.';
  else banner.textContent='';
  if(diagnostics.partial)banner.textContent+=(banner.textContent?' ':'')+'Карта частична'+(diagnostics.errorCount>0?' · проблем: '+diagnostics.errorCount:'')+'. Подробности в обзоре.';
}

/**
 * Открывает существующий инспектор и окружение выбранного raw-узла, не вычисляя новый «impact graph».
 * @returns {void} Сохраняет текущую задачу/снимок; без выбранного символа сообщает следующий шаг.
 */
function showImpactLinks() {
  const task=state.discoveryTask,node=state.nodes.get(state.selected);
  if(!task||task.projectId!==state.project?.id||task.snapshotId!==state.graph.metadata?.snapshotId)return;
  if(!node||['analysis-error','analysis_error'].includes(node.kind)){status('Сначала выберите файл и символ для просмотра связей.');return;}
  navigationHistory?.checkpoint();
  activitySidebar.reveal('details');renderSelection();
  if($('view-mode').value==='architecture' ? state.expandedId!==state.selected : !state.focused)showNodeLinks();
  status('Показаны входящие и исходящие связи выбранного узла в текущем фильтре. Это статические факты; полное влияние изменения не доказано.');
  syncProjectOverview();navigationHistory?.commit();
}

/**
 * Направляет задачу к существующим средствам Atlas и проверяет снимок после каждого await.
 * @param {string} action Известное действие обзора: understand/entrypoints/impact/area/issue/suggestion/rebuild/reload.
 * @param {object} payload Сохранённая область или ориентир; строки не являются URL или исполняемыми командами.
 * @param {object} overview DTO того же project/snapshot; чужая или устаревшая панель не меняет текущую карту.
 * @returns {Promise<void>} Ошибки чтения остаются видимыми; сборка возможна только через явный rebuild.
 */
async function startProjectTask(action,payload,overview) {
  const context=selectedContext();
  if(overview.projectId!==context.projectId || (overview.snapshotId||null)!==(context.snapshotId||null))return;
  if(action==='reload'){
    const revision=state.revision;
    navigationHistory?.checkpoint();
    try{await refreshProjects();if(revision===state.revision&&context.projectId===state.project?.id)await openProject(context.projectId,true);}
    catch(error){status(error.message,true);}
    return;
  }
  if(action==='rebuild') {
    if(state.job||state.submitting){status('Построение уже выполняется. Дождитесь его завершения.');return;}
    $('rebuild').click(); return;
  }
  if(!context.snapshotId)return;
  navigationHistory?.checkpoint();
  if(action==='impact'){
    state.discoveryTask={kind:'impact',projectId:context.projectId,snapshotId:context.snapshotId};
    syncProjectOverview();
    if(state.selected&&state.nodes.has(state.selected)){projectOverview.setOpen(false);showImpactLinks();return;}
    projectOverview.setOpen(false);$('discovery-mode').value='code';updateDiscovery(true);$('navigation-search').focus({preventScroll:true});
    status('Оценка влияния: сначала выберите файл или символ. Затем откройте «Связи выбранного узла»; запрос поиска сам по себе не является анализом влияния.');
    navigationHistory?.commit();return;
  }
  if(action==='area' || action==='issue' || (action==='suggestion' && payload?.action==='graph')) {
    try {
      await ensureSearchGraph(context);
      if(context.projectId!==state.project?.id||context.snapshotId!==state.graph.metadata?.snapshotId)return;
      const ids=action==='area'?payload.nodeIds:[payload.nodeId];
      const node=ids?.map(id=>state.nodes.get(id)).find(Boolean);
      if(!node){status('Ориентир не вошёл в текущий граф. Откройте поиск по всему репозиторию.',true);return;}
      projectOverview.setOpen(false);$('view-mode').value='architecture';updateDisplayProvider();
      if(action==='area'){
        wikiViewer.close(false);
        clearFilters();
        clearComparison(false);
        state.isolatedRoot=null;state.isolatedBlock=null;state.pathOnGraph=null;
        state.inlineExpanded=new Set();
        pathViewer.close();
        $('search').value=payload.path && payload.path!=='.' ? payload.path : '';
        navigateArchitecture({level:'files',projectId:codeProjectId(node),file:null,page:0});
        status('Область «'+payload.title+'» открыта по сохранённому пути. Фильтр карты можно сбросить; Назад вернёт прежний контекст.');
      }
      else await selectSemanticNode(node.id);
    } catch(error){status(error.message,true);}
  } else {
    const entries=action==='entrypoints'||payload?.action==='entrypoints';
    const meaning=action==='understand'||payload?.action==='documentation';
    $('discovery-mode').value=entries?'entries':meaning?'meaning':'code';
    if(meaning){$('semantic-kind').value=action==='understand'||payload?.action==='documentation'?'documentation':'all';semanticSearch.invalidateSearch();}
    const query=typeof payload?.query==='string'?payload.query.slice(0,1000):'';
    if(query)$(meaning?'semantic-query':entries?'ingress-catalog-search':'navigation-search').value=query;
    updateDiscovery(true);projectOverview.setOpen(false);
    if(entries)ingressController.catalog.open();
    else $(meaning?'semantic-query':'navigation-search').focus({preventScroll:true});
    status(entries?'Выберите подтверждённую точку входа. Каталог охватывает весь репозиторий.'
      :meaning?'Опишите задачу своими словами. Поиск использует сохранённые документы; при отсутствии индекса он сообщит об этом.':'Поиск по всему репозиторию; фильтр карты не ограничивает этот запрос.');
  }
  navigationHistory?.commit();
}
const codeNavigation = createCodeNavigation({ state, view, model, filteredNodes, renderIngress, renderContextExpansion, refreshRegexSearch, selectNode, selectBlock: mapBlocks.select, resetBlocks: mapBlocks.reset, draw, controls, onDocumentation: node => documentationViewer.select(node) });
const remoteRepository = new RemoteRepositoryPanel({ state, controls, refreshProjects, openProject });
/** Отправляет только same-origin API-запросы и преобразует ошибки в понятный текст. */
async function api(path, body, {signal} = {}) {
  const options = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  // A first grouping may exceed the normal read deadline; it still has a finite wait.
  const timeoutMs = path.includes('/graph?') ? 120000 : 30000;
  return requestJson(`/api${path}`, {...options, signal}, {timeoutMs});
}
/** Обновляет доступное для скринридера состояние операции. */
function status(message, error = false) { $('status').textContent = message; $('status').classList.toggle('error', error); }
/** Только вызываемый символ задаёт цепочку покрытия; файлы и атрибуты открывают весь проект. */
function coverageCallable(node) {
  if (!node || ['attribute', 'attribute_scope', 'file', 'project', 'note'].includes(node.kind)) return false;
  return node._callable === true || ['method', 'constructor', 'local-function'].includes(node.kind)
    || (node.kind === 'symbol' && /\([^)]*\)/.test(String(node.label || '')));
}
/** Синхронизирует доступность действий с выбранным проектом и активной индексацией. */
function controls() {
  syncProjectOverview();
  const systemMode = isSystemMode($('view-mode').value);
  renderMapEmptyState($('empty'), {hasGraph:Boolean(state.graph.nodes.length), loading:state.loading || Boolean(state.job), project:state.project, error:state.mapLoadError,
    systemContext:systemMode && Boolean(state.graph.metadata?.snapshotId)});
  queueMicrotask(() => mapFind.refresh());
  const hasRawSelection = !systemMode && Boolean(state.selected);
  const hasCodeSelection = !systemMode && Boolean(state.selected || state.selectedBlock);
  $('isolate-node').hidden = !(systemMode ? systemView.selectedNodeId : hasCodeSelection);
  document.querySelector('.selected-node-actions').hidden = !hasRawSelection;
  const coverageNode = state.nodes.get(state.selected);
  renderNodeHealth($('node-health-panel'), hasRawSelection ? coverageNode : null, state.project?.id);
  $('node-actions-context').textContent = hasRawSelection ? (coverageNode?.label || state.selected) : 'Выберите карточку на карте или в списке слева.';
  const coverageScope = coverageCallable(coverageNode);
  $('coverage-open').disabled = state.loading || !state.project || !state.graph.metadata?.snapshotId;
  $('coverage-open').title = coverageScope ? 'Условия и тесты выбранного метода и его цепочки вызовов' : 'Условия и тесты всего репозитория';
  $('discovery-mode').disabled = state.loading || !state.project || !state.graph.metadata?.snapshotId;
  $('navigation-search').disabled = state.loading || !state.project || !state.graph.metadata?.snapshotId;
  $('navigation-search-scope').disabled = state.loading || !state.project || !state.graph.metadata?.snapshotId;
  agentSetupPanel.sync();
  savedViewsPanel.sync();
  documentationViewer.sync();
  relatedRepositories.render();
  if (sourceOverride && (sourceOverride.projectId !== state.project?.id || sourceOverride.snapshotId !== state.graph.metadata?.snapshotId || sourceOverride.selectionId !== state.selected)) sourceOverride = null;
  pathViewer.sync();
  descriptionViewer.sync();
  attributesViewer.sync();
  $('node-description-open').disabled = !state.selected || state.loading;
  $('node-attributes-open').disabled = !state.selected || state.loading;
  $('node-wiki-open').disabled = !state.nodes.has(state.selected) || state.loading;
  $('execution-path-open').disabled = !state.selected || state.loading;
  $('method-documentation-open').disabled = !coverageScope || state.loading;
  syncMethodPresentation();
  if (!state.selected && state.expandedId) {
    state.expandedId = null;
    view.clearExpansion();
    $('expansion-toolbar').hidden = true;
    updateRegexHighlight();
  }
  $('expand-node').disabled = !state.selected;
  $('open-node-file').disabled = !state.selected;
  $('source-open').disabled = !state.selected || state.loading;
  sourceViewer.sync();
  $('build').disabled = Boolean(state.job) || state.submitting;
  $('remote-submit').disabled = Boolean(state.job) || state.submitting;
  $('rebuild').disabled = !state.project || Boolean(state.job) || state.submitting;
  $('ask').disabled = state.loading || !state.project || !state.graph.nodes.length;
  $('algorithm').disabled = !state.project || isSystemMode($('view-mode').value);
  $('project-blocks').disabled = isSystemMode($('view-mode').value) || state.algorithm === 'natural' || !displayProvider($('display-provider').value).projectBlocks;
  $('graph').setAttribute('aria-busy', state.loading);
  $('loading-graph').hidden = !state.loading;
  for (const input of $('code-projects').querySelectorAll('input')) input.disabled = state.loading;
  $('all-code-projects').disabled = state.loading;
  $('no-code-projects').disabled = state.loading;
  $('describe').disabled = !state.selected;
  $('focus').disabled = !state.selected;
  $('isolate-node').disabled = state.loading || !(isSystemMode($('view-mode').value) ? systemView.selectedNodeId : state.selected || state.selectedBlock);
  $('isolate-node').textContent = state.isolatedRoot ? 'Изолировать от выбранного узла' : 'Изолировать цепочку';
  $('compare-submit').disabled = !state.project || state.loading || state.comparing || Boolean(state.job);
  $('compare-clear').disabled = !state.comparison && !state.comparing;
  $('only-changed').disabled = !state.comparison;
}
/** Открывает отдельную страницу анализа; checkpoint позволяет браузеру вернуть камеру карты. */
$('coverage-open').addEventListener('click', () => {
  if (!state.project || state.loading || !state.graph.metadata?.snapshotId) return;
  navigationHistory?.checkpoint();
  const url = new URL('/coverage.html', location.href);
  url.searchParams.set('repo', state.project.id);
  url.searchParams.set('snapshot', state.graph.metadata.snapshotId);
  const node = state.nodes.get(state.selected);
  if (coverageCallable(node)) url.searchParams.set('node', node.id);
  location.assign(url.href);
});
/** Читает каталог проектов; не меняет выбор пользователя. */
async function refreshProjects() {
  const data = await api('/projects');
  state.projects = data.projects;
  $('projects').replaceChildren(new Option('Выберите проект', ''));
  for (const project of data.projects) $('projects').append(new Option(project.name || project.path, project.id));
  $('projects').value = state.project?.id || '';
  return data;
}
/** Связывает принятую карту кода с картами данных выбранного проекта. */
const updateProjectDataMaps = projectDataMaps(document.getElementById('project-data-map'));

/** Обновляет панели после синхронной публикации принятого scope.
 * @param {object|null} project Принятый проект либо пустой выбор.
 * @param {boolean} preserveFilters Сохранять ли фильтры текущего представления.
 * @returns {void} Закрывает прежние readers и обновляет подписи без сетевого ожидания.
 */
function commitProjectContext(project, preserveFilters) {
  healthRefresh.watch(null);
  navigationHistory?.checkpoint();
  wikiViewer.close(false);
  mapBlocks.reset();
  systemView.reset();
  state.isolatedRoot = null;
  state.expandedId = null;
  state.pathOnGraph = null;
  state.ingress.controller?.abort();
  state.ingress.revision++;
  state.ingress.data = null;
  state.ingress.page = 0;
  state.ingress.message = '';
  state.ingress.status = 'idle';
  ingressController.catalog.reset();
  if (!preserveFilters) $('navigation-search').value = '';
  if (!preserveFilters) state.architecture = { level: 'projects', projectId: null, file: null, page: 0 };
  regexSearch.cancel(); state.regexIds = []; state.regexScope = null;
  agentActivity.sync();
  updateProjectDataMaps(project);
  relatedRepositories.resetForPrimary();
  state.selected = null;
  state.community = null;
  state.focused = false;
  if (!preserveFilters) { clearFilters(); clearComparison(false); }
  $('communities').replaceChildren(new Option('Обзор всех групп', ''));
  $('answer').replaceChildren(el('p', 'Запросы MCP относятся ко всему репозиторию; фильтры ограничивают карту.', 'small muted'));
  $('selection').replaceChildren(el('h2', 'Взгляд изнутри'), el('p', 'Выберите узел, чтобы увидеть путь к исходнику и связи.', 'muted'));
  $('project-name').textContent = project?.name || 'Карта проекта';
  if (project) {
    $('repository-add').open = false;
    try { localStorage.setItem('code-atlas-project', project.id); } catch { /* Хранение выбора необязательно. */ }
    $('project-path').value = project.path;
  }
}

/**
 * Публикует проект, граф и индекс вместе, прежде чем вызвать зависимые панели.
 * @param {object} request Актуальная заявка с project, algorithm, viewMode и systemOnly.
 * @param {object} graph Проверенный граф с массивами nodes/edges; metadata.snapshotId отсутствует у выбранной регистрации без снимка.
 * @param {boolean} preserveFilters Сохранять ли существующие фильтры.
 * @returns {void} Заменяет committed scope и рисует принятый граф синхронно.
 */
function commitMapLoad(request, graph, preserveFilters) {
  const nodeIndex = new Map(graph.nodes.map(node => [node.id, node]));
  // Publish the complete raw scope before callbacks can observe the new project.
  state.revision++;
  state.answerRevision++;
  state.project = request.project;
  state.graph = graph;
  state.nodes = nodeIndex;
  state.selected = null;
  state.algorithm = request.algorithm;
  state.loadedAlgorithm = request.algorithm;
  model.setGraph(graph);
  commitProjectContext(request.project, preserveFilters);
  $('view-mode').value = request.viewMode;
  state.loading = false;
  state.mapLoadError = null;
  if (state.comparison && state.comparison.snapshotId !== graph.metadata?.snapshotId) clearComparison(false);
  if (request.project && graph.metadata?.snapshotId && !request.systemOnly) {
    healthRefresh.watch({ projectId: request.project.id, snapshotId: graph.metadata?.snapshotId });
    loadIngress();
    for (const field of ['kind', 'relation']) {
      const previous = $(field).value;
      $(field).replaceChildren(new Option(field === 'kind' ? 'Все типы' : 'Все связи', ''));
      populateFilter(field, field === 'kind' ? graph.nodes : graph.edges, field);
      if ([...$(field).options].some(option => option.value === previous)) $(field).value = previous;
    }
  }
  renderCodeProjects();
  const definition = state.algorithms.find(item => item.id === request.algorithm);
  $('grouping-info').textContent = definition?.description || graph.metadata?.groupingAlgorithm || request.algorithm;
  status(!graph.metadata?.snapshotId && request.project ? 'Проект выбран. Доступного снимка пока нет; состояние показано в обзоре.' : request.systemOnly ? 'Системная карта открыта по snapshotId без загрузки полного графа кода.'
    : `Карта загружена: ${graph.nodes.length.toLocaleString('ru-RU')} узлов · ${definition?.label || request.algorithm}.`);
  draw(true);
}

/**
 * Загружает target отдельно от committed scope.
 * @param {string|null} id ID проекта из каталога либо пустой выбор.
 * @param {boolean} preserveFilters Сохранять ли фильтры после принятия графа.
 * @param {object} options signal/isCurrent принадлежат history; algorithm/viewMode задают target.
 * @returns {Promise<boolean>} true после commit карты либо регистрации без снимка; false при отмене, ошибке чтения или устаревшем ответе.
 * Старый проект, выбранный узел и его snapshot доступны до атомарного commit актуального ответа.
 */
async function openProject(id, preserveFilters = false, options = {}) {
  state.pendingMapLoad?.controller.abort();
  const controller = new AbortController();
  let project = state.projects.find(item => item.id === id) || null;
  const request = { revision: ++mapLoadRevision, project, controller,
    algorithm: options.algorithm || state.algorithm, viewMode: options.viewMode || $('view-mode').value };
  request.systemOnly = Boolean(project?.snapshotId && project.snapshotAvailable !== false && isSystemMode(request.viewMode));
  const abort = () => controller.abort();
  options.signal?.addEventListener('abort', abort, {once:true});
  if (options.signal?.aborted) abort();
  state.pendingMapLoad = request;
  const hasCommittedMap = Boolean(state.project && state.graph.metadata?.snapshotId);
  // Loading gates only the empty first view. Existing controls keep the committed scope.
  state.loading = Boolean(project) && !hasCommittedMap;
  if (hasCommittedMap) state.algorithm = state.loadedAlgorithm;
  state.mapLoadError = null;
  controls();
  status(project ? `Загружаем карту «${project.name || project.id}»…${hasCommittedMap ? ' Открытая карта остаётся доступной.' : ''}` : 'Закрываем карту…');
  const current = () => state.pendingMapLoad === request && request.revision === mapLoadRevision
    && !controller.signal.aborted && (!options.isCurrent || options.isCurrent());
  // A new project's first snapshot may still be building; remember intent without changing raw scope.
  if (current()) state.requestedProjectId = project?.id || null;
  try {
    if (request.systemOnly) {
      // A cached catalog hint can outlive an externally removed snapshot. Probe current metadata on user open.
      const catalog = await api('/projects', undefined, {signal:controller.signal});
      if (!current()) return false;
      const latest = catalog.projects.find(item=>item.id===id);
      if (!latest) throw new Error('Запись карты больше недоступна. Обновите список проектов.');
      project = latest;
      request.project = project;
      request.systemOnly = Boolean(project?.snapshotId && project.snapshotAvailable !== false);
      state.requestedProjectId = project?.id || null;
    }
    let graph = {nodes: [], edges: [], metadata: {}};
    if (options.expectedSnapshotId != null && project?.snapshotId !== options.expectedSnapshotId) {
      controller.abort();
      return false;
    }
    if (project && !project.snapshotId) {
      // A registered project without a published snapshot remains selected; opening never starts a rebuild.
      commitMapLoad(request, graph, preserveFilters);
      navigationHistory?.commit();
      return true;
    }
    if (request.systemOnly) graph = {nodes: [], edges: [], metadata: {snapshotId: project.snapshotId}, codeProjects: []};
    else if (project) graph = await api(`/projects/${encodeURIComponent(project.id)}/graph?algorithm=${encodeURIComponent(request.algorithm)}&projection=map`, undefined, {signal: controller.signal});
    if (!current()) return false;
    if (!graph || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)
        || graph.nodes.some(node => !node || typeof node.id !== 'string')
        || graph.edges.some(edge => !edge || typeof edge.source !== 'string' || typeof edge.target !== 'string')
        || (project && typeof graph.metadata?.snapshotId !== 'string')) {
      throw new Error('Сервер вернул неполную карту. Повторите загрузку.');
    }
    if (options.expectedSnapshotId != null && graph.metadata?.snapshotId !== options.expectedSnapshotId) {
      controller.abort();
      return false;
    }
    commitMapLoad(request, graph, preserveFilters);
    navigationHistory?.commit();
    return true;
  } catch (error) {
    if (!current()) return false;
    if (['SNAPSHOT_REBUILD_STARTED','SNAPSHOT_REBUILD_RUNNING'].includes(error.code) && error.jobId) {
      status(error.message);
      const recovery = {recovery:true,recoveryViewMode:request.viewMode,recoveryAlgorithm:request.algorithm};
      if (state.job?.id === error.jobId) Object.assign(state.job,recovery);
      else {
        // The existing watcher keeps a committed map usable and reopens only the user's still-selected project.
        void watchJob({id:error.jobId,projectId:project.id,status:'queued',...recovery}).catch(failure=>status(failure.message,true));
      }
      return false;
    }
    state.mapLoadError = error.message;
    status(`${error.message}${hasCommittedMap ? ' Открытая карта сохранена; можно продолжить работу.' : ''}`, true);
    if (!hasCommittedMap) draw(true);
    return false;
  } finally {
    options.signal?.removeEventListener('abort', abort);
    if (state.pendingMapLoad === request) {
      state.pendingMapLoad = null;
      if (controller.signal.aborted) {
        state.requestedProjectId = state.project?.id || null;
        status(hasCommittedMap ? 'Загрузка отменена. Открытая карта остаётся доступной.'
          : 'Загрузка карты отменена. Выберите проект, чтобы продолжить.');
      }
      state.loading = false;
      $('projects').value = state.project?.id || '';
      $('algorithm').value = state.algorithm;
      controls();
    }
  }
}
/** Строит checklist внутренних проектов; null сохраняет режим «все», пустой список — «ни одного». */
function renderCodeProjects() {
  const projects = [...(state.graph.codeProjects || [])];
  const known = new Set(projects.map(project => String(project.id)));
  for (const node of state.graph.nodes) {
    const id = codeProjectId(node);
    if (!known.has(id)) { projects.push({ id, label: id === '__unassigned__' ? 'Без проекта' : id }); known.add(id); }
  }
  if (state.codeProjectIds !== null) state.codeProjectIds = state.codeProjectIds.filter(id => known.has(id));
  $('code-projects').replaceChildren();
  for (const project of projects) {
    const id = String(project.id);
    const label = el('label', undefined, 'project-option');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.value = id;
    checkbox.checked = state.codeProjectIds === null || state.codeProjectIds.includes(id);
    checkbox.addEventListener('change', () => {
      state.codeProjectIds = [...$('code-projects').querySelectorAll('input:checked')].map(input => input.value);
      applyFilters();
    });
    const palette = projectColor(project.label || id, { unassigned: id === '__unassigned__' });
    const swatch = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    swatch.setAttribute('class', 'project-swatch'); swatch.setAttribute('viewBox', '0 0 12 12'); swatch.setAttribute('aria-hidden', 'true');
    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('cx', 6); circle.setAttribute('cy', 6); circle.setAttribute('r', 5); circle.setAttribute('fill', palette.stroke); swatch.append(circle);
    label.append(checkbox, swatch, el('span', `${project.label || id}${project.nodeCount === undefined ? '' : ` · ${project.nodeCount}`}`));
    label.title = project.path || project.label || id;
    $('code-projects').append(label);
  }
}
/** Добавляет уникальные непустые значения фильтра в отсортированном виде. */
function populateFilter(id, items, field) {
  const values = new Set();
  for (const item of items) if (item[field]) values.add(item[field]);
  for (const value of [...values].sort()) $(id).append(new Option(value, value));
}
/** Читает фильтры интерфейса и применяет их ко всему репозиторию. */
function filteredNodes(scopedNeighbors = true) {
  if ((state.isolatedRoot || state.isolatedBlock) && !isSystemMode($('view-mode').value)) return isolationScope().nodes;
  return model.filter({ keywords: $('search').value, kind: $('kind').value, relation: $('relation').value, codeProjectIds: state.codeProjectIds, neighborOf: scopedNeighbors && state.focused ? state.selected : null, changedNodeIds: $('only-changed').checked ? state.comparison?.changedNodeIds || [] : null });
}
/** Пересчитывает карту после фильтра; скрытый выбор не возвращается в SVG принудительно. */
function applyFilters() {
  state.expandedId = null;
  state.inlineExpanded.clear(); state.selectedBlock = null;
  state.architecture.page = 0;
  state.ingress.page = 0;
  state.listLimit = 100;
  const matches = filteredNodes(false);
  if (state.selected && !matches.some(node => node.id === state.selected)) {
    state.selected = null; state.focused = false; state.answerRevision++;
    documentationViewer.select(null);
    $('selection').replaceChildren(el('h2', 'Выбор вне фильтра'), el('p', 'Выберите доступный узел или сбросьте фильтры.', 'muted'));
    $('answer').replaceChildren();
  }
  if (state.selected) renderSelection();
  draw(true);
  controls();
}
/** Выбирает обычную группу либо её точный фрагмент внутри проекта. */
function displayedCommunityId(node) {
  return $('project-blocks').checked ? projectCommunityId(node) : String(node.community ?? 'ungrouped');
}
/** Агрегирует текущий фильтр, не меняя исходное членство узлов в сообществах. */
function displayedAggregate(nodes) { return model.aggregate(nodes, $('relation').value, $('project-blocks').checked); }
/** Открывает группу без изменения фильтров пользователя. */
function openCommunity(id) {
  navigationHistory?.checkpoint();
  state.expandedId = null;
  mapBlocks.reset();
  state.community = id || null;
  state.focused = false;
  state.selected = null;
  state.answerRevision++;
  $('answer').replaceChildren();
  const filtered = displayedAggregate(filteredNodes(false));
  const group = filtered.communities.find(item => String(item.id) === id);
  documentationViewer.select(group);
  $('selection').replaceChildren(el('h2', group?.label || 'Обзор групп'), el('p', group ? `${group.nodeCount} узлов · ${group.internalEdges} внутренних связей · ${group.externalEdges} внешних связей в фильтре. Выберите узел для деталей.` : 'Размер круга отражает число узлов. Наведите курсор на группу, чтобы увидеть её связи.', 'muted'));
  controls();
  draw(true);
}
/** Показывает агрегацию всех совпадений или ограниченные детали выбранной группы. */
function draw(fit = false) {
  syncProjectOverview();
  groupNavigation.hide();
  view.groupingProvider = state.algorithm;
  queueMicrotask(() => navigationHistory?.commit());
  factsPopup.sync(state.project?.id, state.graph.metadata?.snapshotId);
  implementationList.sync(state.project?.id, state.graph.metadata?.snapshotId);
  semanticSearch.sync();
  if (!state.pathOnGraph) pathToolbar.hide();
  syncMethodPresentation();
  updateIsolationToolbar();
  $('focus').textContent = state.focused ? 'Показаны связи узла' : 'Связи узла';
  const architecture = $('view-mode').value === 'architecture';
  const system = isSystemMode($('view-mode').value);
  $('system-options').hidden = !system;
  const hasGraph = Boolean(state.graph.nodes?.length);
  $('semantic-legend-panel').hidden = !hasGraph || system;
  renderMapEmptyState($('empty'), {hasGraph, loading:state.loading, project:state.project, error:state.mapLoadError,
    systemContext:system && Boolean(state.graph.metadata?.snapshotId)});
  document.querySelector('.graph-options').hidden = !hasGraph;
  document.querySelector('.view-navigation').hidden = !hasGraph;
  $('architecture-navigation').hidden = !architecture || !hasGraph;
  $('back-overview').textContent = architecture ? (state.architecture.level === 'symbols' ? '← Файлы проекта' : '← Все проекты') : '← Все группы';
  $('ingress-panel').hidden = !architecture || !$('show-ingress').checked;
  $('view-instruction').textContent = 'Нажмите блок для выбора · → войти внутрь · + раскрыть внутренние блоки здесь';
  $('reset').textContent = architecture ? 'Обзор проектов' : 'Обзор групп';
  $('architecture-preset').disabled = !architecture;
  $('communities').disabled = architecture || system || Boolean(state.isolatedRoot);
  presentViewMode($('view-mode').value);
  if (!hasGraph) {
    document.querySelector('.graph-options').hidden = true;
    document.querySelector('.view-navigation').hidden = true;
  }
  updateDiscovery();
  if (system) { systemView.render(fit); updateIsolationToolbar(); return; }
  systemView.suspend();
  if (state.pathOnGraph) {
    $('back-overview').hidden = false;
    $('back-overview').textContent = '← Карта кода';
    $('view-title').textContent = pathReadingTitle(state.pathOnGraph, pathViewer.entry);
    $('view-instruction').textContent = 'Раскрывайте реализации прямо на схеме. Внутри метода видны условия и циклы; вызывающий блок остаётся рядом.';
    paintPathGraph(state.pathOnGraph);
    return;
  }
  if (state.isolatedRoot || state.isolatedBlock) { drawIsolatedCalls(fit); return; }
  if (view.pathAnalysisData) {
    view.pathAnalysisData = null; view.readableFocusTrail = []; view.onReadableFocusChange(null);
    status('Показана карта кода. Найдите вход или метод, чтобы изучить его путь.');
  }
  ingressController.catalog.render();
  navigationSearch.refresh();
  if (architecture) { drawArchitecture(fit); return; }
  view.colorProjects = state.graph.codeProjects || [];
  const groupScope = $('display-provider').value === 'flow' && state.architecture.level !== 'projects' ? state.architecture.projectId : null;
  const allMatches = filteredNodes();
  const matches = groupScope === null ? allMatches : allMatches.filter(node => codeProjectId(node) === groupScope);
  const aggregate = displayedAggregate(matches);
  const changedIds = state.comparison?.changedNodeIds || [];
  const communities = markChangedCommunities(aggregate.communities, aggregate.displayNodes, changedIds);
  if (state.community && !communities.some(group => String(group.id) === state.community)) state.community = null;
  $('communities').replaceChildren(new Option('Обзор всех групп', ''));
  for (const group of communities) $('communities').append(new Option(`${group.label} · ${group.nodeCount} узлов в фильтре`, String(group.id)));
  const groupNoun = $('project-blocks').checked ? 'фрагментов групп' : 'групп';
  const isOverview = !state.community && !state.focused;
  if (isOverview && !state.expandedId && $('display-provider').value === 'flow') {
    groupNavigation.render(allMatches, { fragments: $('project-blocks').checked, relation: $('relation').value, fit });
    $('communities').value = '';
    refreshRegexSearch(matches);
    return;
  }
  let candidates = matches;
  if (state.community && !state.focused) candidates = matches.filter(node => displayedCommunityId(node) === state.community);
  let caption;
  if (isOverview) {
    const matchingGroups = new Set(matches.map(displayedCommunityId));
    const groups = communities.filter(group => matchingGroups.has(String(group.id)));
    const ordered = [...groups].sort((a, b) => b.nodeCount - a.nodeCount);
    const visible = ordered.slice(0, 320);
    const ids = new Set(visible.map(group => String(group.id)));
    const edges = [];
    for (const edge of aggregate.communityEdges) if (ids.has(String(edge.source)) && ids.has(String(edge.target))) edges.push(edge);
    edges.sort((a, b) => b.weight - a.weight);
    view.renderOverview(visible, edges.slice(0, 1500), null, $('project-blocks').checked ? state.graph.codeProjects || [] : null, { nodes: matches, edges: aggregate.edges, scope: 'весь текущий фильтр до ограничения SVG' });
    caption = `Обзор: ${visible.length} из ${communities.length} ${groupNoun} · ${Math.min(edges.length, 1500)} из ${edges.length} межгрупповых связей · наведение выделяет связи`;
    $('view-title').textContent = $('project-blocks').checked ? 'Проекты и фрагменты групп' : 'Группы кода';
    if (state.graph.metadata?.projectDiscovery) $('view-instruction').textContent = 'Фрагменты — пересечения групп и проектов/разделов по папкам. Для изучения начните с каталога тестов или вида «Код». Число фрагментов не равно числу модулей сборки.';
  } else {
    const visible = candidates.slice(0, 240);
    const chosen = candidates.find(node => node.id === state.selected);
    if (chosen && !visible.includes(chosen)) { if (visible.length === 240) visible.pop(); visible.push(chosen); }
    const ids = new Set(visible.map(node => node.id));
    const directEdges = [], otherEdges = [], projectEdges = [];
    let availableEdges = 0;
    for (const edge of aggregate.edges) {
      if ($('relation').value && edge.relation !== $('relation').value) continue;
      if (!ids.has(edge.source) || !ids.has(edge.target)) continue;
      availableEdges++;
      projectEdges.push(edge);
      const destination = edge.source === state.selected || edge.target === state.selected ? directEdges : otherEdges;
      if (destination.length < 1200) destination.push(edge);
    }
    // Прямые связи выбранного узла имеют приоритет при ограничении SVG.
    const edges = directEdges.concat(otherEdges).slice(0, 1200);
    view.render(markChangedNodes(visible, changedIds), edges, state.selected, communities, $('project-blocks').checked ? state.graph.codeProjects || [] : null, { nodes: visible, edges: projectEdges, scope: 'между показанными узлами до ограничения рёбер SVG' });
    const community = communities.find(group => String(group.id) === state.community);
    $('view-title').textContent = state.focused ? 'Соседи в текущем фильтре' : community?.label || 'Детали группы';
    caption = `${state.focused ? 'Соседи' : 'Группа'}: ${visible.length} из ${candidates.length} узлов · ${edges.length} из ${availableEdges} связей между показанными узлами · лимит 240 узлов`;
  }
  if (fit && state.expandedId) view.fit();
  renderContextExpansion();
  if (fit) { if (state.expandedId) view.expansion?.focus(); else view.fit(); }
  $('communities').value = state.community || '';
  $('back-overview').hidden = isOverview;
  if (groupScope !== null) $('back-overview').textContent = '← Группы проекта';
  renderMapEmptyState($('empty'), {hasGraph:Boolean(state.graph.nodes.length), loading:state.loading, project:state.project, error:state.mapLoadError});
  $('node-count').textContent = `${matches.length.toLocaleString('ru-RU')} / ${state.graph.nodes.length.toLocaleString('ru-RU')}`;
  $('graph-stats').textContent = `В фильтре: ${communities.length} ${groupNoun} / ${matches.length.toLocaleString('ru-RU')} узлов / ${aggregate.edges.length.toLocaleString('ru-RU')} связей`;
  const changedCount = communities.reduce((count, group) => count + group.changedNodeCount, 0);
  if (view.inlineSceneActive && state.inlineScene) {
    const scene = state.inlineScene;
    caption = `${scene.items.length} карточек · ${matches.length} символов в фильтре · ${scene.edges.length} из ${scene.counts.totalProjectedEdges} связей между видимыми владельцами${scene.truncated ? ' · достигнут предел раскрытия' : ''}`;
  }
  $('visible-count').textContent = caption + (state.comparison ? ` · ${changedCount} узлов изменённых файлов в фильтре` : '');
  // Поиск глобальный даже в деталях: результат из другой группы сразу раскрывает её.
  const list = $('search').value.trim() ? matches : candidates;
  $('results-count').textContent = `${list.length.toLocaleString('ru-RU')} совпадений · список ${Math.min(list.length, state.listLimit)}`;
  $('results').replaceChildren();
  for (const node of list.slice(0, state.listLimit)) {
    const button = createNodeResult(node, state.selected, id => {
      state.focused = false;
      activateResultCard(view, id, selectNode);
    }, `${node.kind || 'узел'} · ${node.file || 'путь не указан'}`);
    $('results').append(button);
  }
  $('more').hidden = list.length <= state.listLimit;
  $('focus').textContent = state.focused ? 'Показаны связи узла' : 'Связи узла';
  refreshRegexSearch(matches);
}
/** Переходит по архитектуре локально, не меняя checkbox-фильтры проектов. */
function navigateArchitecture(next, nodeId = null) { navigationHistory?.checkpoint(); state.isolatedRoot = null; state.pathOnGraph = null; codeNavigation.navigate(next, nodeId); }
/** Передаёт раскрытие агрегата контроллеру навигации, не выбирая synthetic ID как символ. */
function openArchitectureGroup(id) { codeNavigation.open(id); }
/** Отображает текущую страницу кода с сохранением фильтров и подсветок. */
function drawArchitecture(fit) { codeNavigation.render(fit); }
$('view-mode').addEventListener('change', () => {
  mapBlocks.reset();
  updateDisplayProvider({ restorePreference: true });
  const system = isSystemMode($('view-mode').value);
  if (state.isolatedRoot && system === model.nodes.has(state.isolatedRoot)) state.isolatedRoot = null;
  state.expandedId = null;
  state.pathOnGraph = null;
  pathViewer.close();
  state.focused = false;
  if (isSystemMode($('view-mode').value)) { documentationViewer.select(null); relatedRepositories.refresh(); state.selected = null; state.answerRevision++; $('answer').replaceChildren(); $('selection').replaceChildren(el('h2', 'Системная архитектура'), el('p', 'Выберите проект, систему или стрелку для фактов конфигурации.', 'muted')); }
  else if (state.project && !(state.graph.nodes || []).length) {
    openProject(state.project.id, true);
    return;
  }
  draw(true);
  controls();
});
/** Перезапускает поиск только при изменении текущего набора данных или шаблона. */
function refreshRegexSearch(nodes = filteredNodes()) {
  const pattern = $('regex-search').value;
  if (nodes !== state.regexScope?.nodes || pattern !== state.regexScope?.pattern) {
    state.regexScope = { nodes, pattern };
    regexSearch.search(nodes, $('regex-search').value);
  } else updateRegexHighlight();
}
/** Подсвечивает совпадения поверх существующего SVG без draw/fit и изменения выбранного узла. */
function updateRegexHighlight() {
  const counts = new Map();
  for (const id of state.regexIds) {
    const node = state.nodes.get(id);
    if (!node) continue;
    if (view.expansion?.child?.nodeElements.has(id)) continue;
    const community = $('view-mode').value === 'architecture' ? state.architectureMembership.get(id) : displayedCommunityId(node);
    if (community === undefined) continue;
    counts.set(community, (counts.get(community) || 0) + 1);
  }
  const visible = view.setSearchMatches(state.regexIds, counts);
  $('regex-count').textContent = $('regex-search').value ? `Совпадений в фильтре: ${state.regexIds.length} · представлены на карте: ${visible}` : '';
}
$('regex-search').addEventListener('input', () => refreshRegexSearch());
$('project-blocks').addEventListener('change', () => {
  try { localStorage.setItem('code-atlas-project-blocks', String($('project-blocks').checked)); } catch { /* Режим доступен без localStorage. */ }
  openCommunity(null);
});
$('show-labels').addEventListener('change', () => {
  view.setShowLabels($('show-labels').checked);
  try { localStorage.setItem('code-atlas-show-labels', String($('show-labels').checked)); } catch { /* Настройка работает и без хранилища. */ }
});
/** Сохраняет карту до асинхронного перехода к raw-ID из поиска или wiki. */
function selectSemanticNode(id, { signal } = {}) {
  navigationHistory?.checkpoint();
  return searchNavigation.select(id, { signal });
}
/** Лениво загружает граф для поиска, не меняя системную карту и её выбранные элементы. */
async function ensureSearchGraph(scope) {
  if (state.graph.nodes.length) return;
  const revision = state.revision, algorithm = state.algorithm;
  const key = JSON.stringify([scope.projectId, scope.snapshotId, revision, algorithm]);
  if (state.searchGraphLoad?.key === key) return state.searchGraphLoad.promise;
  const promise = loadSearchGraph(scope, revision, algorithm);
  state.searchGraphLoad = { key, promise };
  try { await promise; } finally { if (state.searchGraphLoad?.key === key) state.searchGraphLoad = null; }
}
/** Одна загрузка принадлежит одному поколению карты, результат другого проекта отбрасывается. */
async function loadSearchGraph(scope, revision, algorithm) {
  const graph = await api(`/projects/${encodeURIComponent(scope.projectId)}/graph?algorithm=${encodeURIComponent(algorithm)}&projection=map`);
  if (revision !== state.revision || scope.projectId !== state.project?.id || scope.snapshotId !== state.graph.metadata?.snapshotId) return;
  if (graph.metadata?.snapshotId !== scope.snapshotId) throw new Error('Снимок изменился. Обновите карту перед переходом к коду.');
  state.graph = graph; state.nodes = new Map(graph.nodes.map(node => [node.id, node])); model.setGraph(graph);
  for (const field of ['kind', 'relation']) populateFilter(field, field === 'kind' ? graph.nodes : graph.edges, field);
  renderCodeProjects(); loadIngress(); navigationSearch.refresh();
}
/** Открывает найденный код во всём репозитории; Back возвращает прежние фильтры и камеру. */
function navigateSearchNode(node) {
  wikiViewer.close(false);
  navigationHistory?.checkpoint();
  clearFilters();
  clearComparison(false);
  state.isolatedRoot = null;
  state.pathOnGraph = null;
  $('view-mode').value = 'architecture';
  updateDisplayProvider();
  codeNavigation.navigate({ level: 'symbols', projectId: codeProjectId(node), file: node.file || null, page: 0 }, node.id);
  selectNode(node.id);
  status('Открыт код во всём репозитории. «Назад» в браузере вернёт прежний вид и фильтры.');
}
/** Выбирает символ фильтра или уже показанной схемы, сохраняя раскладку и область изоляции. */
function selectNode(id, revealInspector = true, fromSequence = false, occurrenceId = null) {
  if (String(id).startsWith('path-entry:')) return;
  const node = model.nodes.get(id);
  if (!node) return;
  const visibleSequenceSymbol = fromSequence && view.sequenceInspectionIds?.has(id);
  if (fromSequence && !visibleSequenceSymbol) return;
  const scope = isolationScope();
  if (scope && !scope.allNodeIds.has(id)) return;
  if (!scope && !state.pathOnGraph && !visibleSequenceSymbol && !model.nodeIds(filteredNodes(false)).has(id)) return;
  navigationHistory?.checkpoint();
  state.selectedBlock = null;
  for (const item of view.communityElements.values()) { item.group.classList.toggle('selected', false); item.group.setAttribute('aria-pressed', 'false'); }
  state.selected = id;
  systemView.observeRawNode(id);
  if (revealInspector) {
    const projectId = state.project?.id;
    nodePathGesture.revealSelection(() => {
      if (state.selected === id && state.project?.id === projectId) activitySidebar.revealSelection();
    });
  }
  documentationViewer.select(node);
  state.answerRevision++;
  $('answer').replaceChildren();
  renderSelection(); controls(); view.setSelected(id, fromSequence ? occurrenceId : null);
  for (const button of $('results').querySelectorAll('.result')) setResultSelected(button, button.dataset.nodeId === id);
  navigationHistory?.commit();
}
/** Разворачивает ограниченное окружение рядом с обзором, сохраняя агрегацию и фильтры. */
function renderContextExpansion() {
  $('expansion-toolbar').hidden = !state.expandedId;
  if (!state.expandedId) return;
  const relations = $('view-mode').value === 'architecture' && $('architecture-preset').value === 'dependencies'
    ? ['calls', 'references', 'imports', 'inherits', 'implements', 'dispatches_to'] : null;
  const payload = expansionNeighborhood(state.graph, state.expandedId, { nodes: filteredNodes(false), relation: $('relation').value, relations });
  if (!payload) { state.expandedId = null; $('expansion-toolbar').hidden = true; return; }
  payload.nodes = markChangedNodes(payload.nodes, state.comparison?.changedNodeIds || []);
  view.renderExpansion(payload);
  $('expansion-status').textContent = `Окружение: ${payload.nodes.length - 1} из ${payload.totalNeighbors} соседей · ${payload.edges.length} из ${payload.totalEdges} связей между показанными символами. В углу — сохранённая карта, в основной области — исходные символы; счётчики карты не суммируются с окружением.`;
}
$('fit-expansion').addEventListener('click', () => view.expansion?.fitLocal());
$('focus-expansion').addEventListener('click', () => view.expansion?.focus());
$('collapse-expansion').addEventListener('click', () => {
  state.expandedId = null;
  view.clearExpansion();
  $('expansion-toolbar').hidden = true;
  updateRegexHighlight();
});
$('expand-node').addEventListener('click', () => {
  if (!state.selected) return;
  state.expandedId = state.selected;
  state.focused = false;
  state.community = null;
  state.architecture = { level: 'projects', projectId: null, file: null, page: 0 };
  draw(true);
});
/** Явный переход к файлу остаётся отдельным от выбора символа в общей карте. */
$('open-node-file').addEventListener('click', () => {
  const node = state.nodes.get(state.selected);
  if (!node) return;
  state.expandedId = null;
  $('view-mode').value = 'architecture';
  state.architecture = { level: 'symbols', projectId: codeProjectId(node), file: node.file || null, page: 0 };
  state.architecture.page = codeNavigation.pageForNode(state.architecture, node.id);
  draw(true);
});
/** Captured class ownership supplies the overview; no class body is presented as a test run. */
function renderSelectedTestClass(nodeId) {
  const node = state.nodes.get(nodeId);
  if (!node) return false;
  const rendered = renderTestClassOverview($('selection'), node, state.ingress.data?.entries || [], {
    onTest: test => ingressController.select({ ...test, detailsAvailable: true }),
    onPath: async (id, test) => { await selectSemanticNode(id); analyzePath(id, test); },
    onSource: openSourceNode,
  });
  if (rendered) return true;
  if (!(node.testContainerFacts?.declaredTestCount > 0)) return false;
  const key = JSON.stringify([state.project?.id, state.graph.metadata?.snapshotId, nodeId]);
  const panel = $('selection');
  panel.replaceChildren(el('h2', node.label || 'Тестовый класс'));
  const existing = state.testClassLoad;
  if (existing?.key === key && existing.error) {
    panel.append(el('p', `Тесты класса недоступны: ${existing.error}`, 'small muted'));
    const retry = el('button', 'Повторить загрузку тестов');
    retry.addEventListener('click', () => { state.testClassLoad = null; renderSelection(); }); panel.append(retry);
  } else {
    panel.append(el('p', 'Читаем объявления тестов этого класса…', 'small'));
    if (existing?.key !== key) loadTestClassFacts(node, key);
  }
  return true;
}
/** Hydrate one captured class DTO; stale replies never replace another selection. */
async function loadTestClassFacts(node, key) {
  const projectId = state.project?.id, snapshotId = state.graph.metadata?.snapshotId;
  const pending = { key };
  state.testClassLoad = pending;
  try {
    const payload = await requestJson(`/api/projects/${encodeURIComponent(projectId)}/graph-node`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nodeId: node.id, snapshotId }),
    });
    if (state.testClassLoad !== pending || state.project?.id !== projectId || state.graph.metadata?.snapshotId !== snapshotId) return;
    const facts = payload.node?.testContainerFacts;
    if (!facts?.tests?.length) throw new Error('Объявления не вошли в ответ. Обновите карту или откройте каталог тестов.');
    node.testContainerFacts = facts;
    if (state.selected === node.id) renderSelection();
  } catch (error) {
    if (state.testClassLoad !== pending || state.project?.id !== projectId || state.graph.metadata?.snapshotId !== snapshotId) return;
    pending.error = error.message;
    if (state.selected === node.id) renderSelection();
  }
}
/** Return from a class execution URL to its code map and explicit test starting points. */
function showTestClassOverview(nodeId) {
  const node = state.nodes.get(nodeId);
  if (!node || (!testsForContainer(nodeId, state.ingress.data?.entries || [], node.testContainerFacts).length && !(node.testContainerFacts?.declaredTestCount > 0))) return false;
  state.selected = nodeId;
  if (state.pathOnGraph) { state.pathOnGraph = null; pathViewer.close(); draw(true); }
  renderSelectedTestClass(nodeId);
  controls();
  activitySidebar.reveal('details');
  status('Тестовый класс: выберите тест, его данные или схему действий.');
  return true;
}
/** Показывает связи выбранного узла только внутри текущего индуцированного фильтра. */
function renderSelection() {
  if (renderSelectedTestClass(state.selected)) return;
  renderInspector(state, model, filteredNodes(false), $('relation').value, selectNode, {
    entries: entriesForNode(state.selected),
    onEntryPath: entry => analyzePath(state.selected, entry),
  });
  const issue=state.graph.metadata?.analysisDiagnostics?.issues?.find(item=>item.nodeId===state.selected);
  if(issue)$('selection').append(analysisIssueCard(issue,{projectId:state.project?.id}));
}

/** Update derived scores after a background job without replacing topology, selection or camera. */
async function refreshDisplayedHealth(scope) {
  const revision = state.revision, algorithm = state.algorithm;
  const graph = await api(`/projects/${encodeURIComponent(scope.projectId)}/graph?algorithm=${encodeURIComponent(algorithm)}&projection=map`);
  if (revision !== state.revision || scope.projectId !== state.project?.id || scope.snapshotId !== state.graph.metadata?.snapshotId
      || graph.metadata?.snapshotId !== scope.snapshotId) return;
  const facts = new Map(graph.nodes.map(node => [node.id, node]));
  applyHealthUpdates(state.nodes.values(), facts);
  state.graph.metadata.codeHealth = graph.metadata.codeHealth;
  applyHealthUpdates(state.pathOnGraph?.steps || [], facts);
  for (const entry of view.pathMethodFacts?.entries.values() || []) applyHealthUpdates([entry.data], facts);
  applyHealthUpdates(methodWorkbench?.reader.source.cache.values() || [], facts);
  const camera = { ...view.camera };
  renderSelection(); controls(); draw(false);
  view.camera = camera; view.transform();
}
/** Входы, чей обработчик — выбранный символ. */
function entriesForNode(nodeId) {
  if (!nodeId) return [];
  return (state.ingress.data?.entries || []).filter(entry => (entry.handlerNodeIds || []).includes(nodeId));
}
/** Запрашивает MCP и не показывает устаревший ответ после смены проекта или узла. */
async function requestAnswer(type, body) {
  if (!state.project) return;
  const revision = ++state.answerRevision;
  const projectId = state.project.id;
  $('answer').replaceChildren(el('p', 'Запрос к локальному MCP…', 'muted'));
  try {
    const answer = await api(`/projects/${encodeURIComponent(projectId)}/${type}`, { ...body, snapshotId: state.graph.metadata?.snapshotId });
    if (revision !== state.answerRevision) return;
    const provider = el('span', String(answer.provider || 'Graphify MCP'), 'pill');
    const output = el('pre', String(answer.text || 'MCP не вернул текст.'));
    $('answer').replaceChildren(provider, output);
    if (answer.tools?.length) $('answer').append(el('p', `Инструменты: ${answer.tools.join(', ')}`, 'small muted'));
  } catch (error) { if (revision === state.answerRevision) $('answer').replaceChildren(el('p', error.message, 'error-text')); }
}
/**
 * Следит за одной серверной задачей; ошибка сети не выдаётся за ошибку индексации.
 * @param {object} job Реальная задача с id/projectId и опциональным intent восстановления выбранной карты.
 * @returns {Promise<void>} Завершается при terminal-состоянии или замене; finally освобождает loader.
 * Неудача обновляет регистрацию, сохраняя её последний пригодный snapshot и committed граф.
 */
async function watchJob(job) {
  jobLoadingRelease?.();
  const revision = ++jobLoadingRevision;
  const release = beginLoading('Построение карты…');
  jobLoadingRelease = release;
  try {
    state.job = job;
    controls();
    status('Graphify строит карту. Можно исследовать сохранённые проекты.');
    await monitorJob(job, {
      read: id => api(`/jobs/${encodeURIComponent(id)}`),
      isCurrent: id => state.job?.id === id && revision === jobLoadingRevision,
      onTransientError: (error, delay) => status(`Связь с задачей потеряна: ${error.message}. Задача остаётся активной; повторная проверка через ${delay / 1000} с.`, true),
      onState: async current => {
        release.update?.(current.status === 'completed' ? 'Открываем построенную карту…' : 'Построение карты…');
        if (current.status === 'failed') {
          state.job = null;
          const failed=state.projects?.find(item=>item.id===current.projectId);
          if(failed){failed.status='failed';failed.error=current.error;}
          if(state.project?.id===current.projectId){state.project={...state.project,status:'failed',error:current.error};}

          status(current.error || 'Не удалось построить карту.', true);
        } else if (current.status === 'completed') {
          state.job = null;
          try {
            await refreshProjects();
            const openCompleted = job.recovery ? state.requestedProjectId === current.projectId
              : !state.project || state.project.id === current.projectId || state.requestedProjectId === current.projectId;
            if (openCompleted) await openProject(current.projectId,false,job.recovery
              ? {viewMode:job.recoveryViewMode,algorithm:job.recoveryAlgorithm} : {});
            else status('Индексация завершена. Проект доступен в списке.');
          } catch (error) { status(`Индекс построен, но список не обновлён: ${error.message}`, true); }
        } else status('Graphify строит карту…');
        controls();
      },
    });
    controls();
  } finally {
    release();
    if (revision === jobLoadingRevision) jobLoadingRelease = null;
  }
}
$('project-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (state.job || state.submitting) return;
  state.submitting = true;
  controls();
  try {
    const result = await api('/projects', { path: $('project-path').value.trim() });
    await refreshProjects();
    await openProject(result.project.id);
    state.submitting = false;
    await watchJob(result.job);
  } catch (error) { status(error.message, true); }
  finally { state.submitting = false; controls(); }
});
$('projects').addEventListener('change', () => openProject($('projects').value));
$('rebuild').addEventListener('click', async () => {
  if (!state.project || state.job || state.submitting) return;
  clearComparison();
  state.submitting = true;
  controls();
  try {
    const result = await api(`/projects/${encodeURIComponent(state.project.id)}/rebuild`, {});
    state.submitting = false;
    await watchJob(result.job);
  } catch (error) { status(error.message, true); }
  finally { state.submitting = false; controls(); }
});
for (const id of ['search', 'kind', 'relation']) $(id).addEventListener(id === 'search' ? 'input' : 'change', applyFilters);
$('algorithm').addEventListener('change', () => {
  navigationHistory.checkpoint();
  const snapshot = workspaceNavigation.capture();
  snapshot.filters.algorithm = $('algorithm').value;
  snapshot.route.algorithm = $('algorithm').value;
  snapshot.camera = null;
  navigationHistory.restoreEntry(snapshot, { replace: false });
});
$('all-code-projects').addEventListener('click', () => { state.codeProjectIds = null; renderCodeProjects(); applyFilters(); });
$('no-code-projects').addEventListener('click', () => { state.codeProjectIds = []; renderCodeProjects(); applyFilters(); });
$('clear-filters').addEventListener('click', () => { clearFilters(); state.community = null; state.focused = false; renderCodeProjects(); applyFilters(); });
$('more').addEventListener('click', () => { state.listLimit += 100; draw(); });
/** Сбрасывает фильтры только по явному действию пользователя или при смене репозитория. */
function clearFilters() {
  $('only-changed').checked = false;
  state.codeProjectIds = null;
  $('search').value = '';
  $('kind').value = '';
  $('relation').value = '';
  state.listLimit = 100;
}
/** Цепочка строится по всему снимку: фильтры не разрывают промежуточные вызовы. */
function isolationScope() {
  if (state.isolatedBlock) {
    const members = blockMembers(state.isolatedBlock, state.graph.nodes, displayedCommunityId);
    return isolateCallRoots(state.graph, members.map(node => node.id));
  }
  if (!state.isolatedRoot) return null;
  const system = isSystemMode($('view-mode').value);
  return isolateCallChain(system ? systemView.displayData || { nodes: [], edges: [] } : state.graph,
    state.isolatedRoot, { relations: system ? null : ['calls', 'dispatches_to'] });
}
/** Показывает pinned-корень и пределы SVG отдельно от полноты сохранённых фактов. */
function updateIsolationToolbar() {
  const scope = isolationScope();
  $('isolation-toolbar').hidden = !scope || scope.missingRoot;
  if (!scope || scope.missingRoot) return;
  const root = scope.nodes.find(node => node.id === state.isolatedRoot);
  $('isolation-title').textContent = `Изоляция · ${state.isolatedBlock?.label || root?.label || state.isolatedRoot}`;
  $('isolation-count').textContent = state.pathOnGraph
    ? `Область: ${scope.totalNodes} блоков / ${scope.totalEdges} связей · на схеме ${view.pathScope?.visible || 0} методов · раскрывайте тела на месте`
    : `${scope.nodes.length} из ${scope.totalNodes} блоков · ${scope.edges.length} из ${scope.totalEdges} связей${scope.truncated ? ' · достигнут лимит отображения' : ''} · исходящие связи всего снимка`;
}
/** При активной реализации сохраняет исходные if/switch/циклы и меняет только корень и вызовы. */
function isolatedPathData(data) {
  const scope = isolationScope();
  return state.isolatedBlock ? data : isolateExecutionPath(data, scope);
}
/** Изолированная карта показывает raw-блоки с исходящими вызовами в обоих провайдерах. */
function drawIsolatedCalls(fit) {
  const scope = isolationScope();
  if (!scope || scope.missingRoot) { state.isolatedRoot = null; state.isolatedBlock = null; draw(fit); return; }
  view.colorProjects = state.graph.codeProjects || [];
  view.render(scope.nodes, scope.edges, state.selected, state.graph.communities || [], null);
  if (fit) view.fit();
  $('architecture-navigation').hidden = true;
  $('expansion-toolbar').hidden = true;
  $('ingress-panel').hidden = true;
  $('back-overview').hidden = false;
  $('back-overview').textContent = '← Снять изоляцию';
  $('view-title').textContent = 'Изолированная цепочка вызовов';
  $('view-instruction').textContent = 'Только выбранный блок и вызываемые им реализации по цепочке. Входящие связи и ссылки на типы скрыты.';
  $('visible-count').textContent = `${scope.nodes.length} из ${scope.totalNodes} блоков · ${scope.edges.length} из ${scope.totalEdges} вызовов${scope.truncated ? ' · карта ограничена' : ''}`;
  $('graph-stats').textContent = 'calls / возможные реализации · весь сохранённый снимок';
  $('node-count').textContent = `${scope.totalNodes} / ${state.graph.nodes.length}`;
  renderChainResults(scope.nodes, 'блоков цепочки');
  updateIsolationToolbar();
  navigationSearch.refresh();
}
/** Список следует фактически показанным raw-блокам, а не прежней странице проектов. */
function renderChainResults(nodes, noun) {
  $('results-count').textContent = `${nodes.length} ${noun}`;
  $('more').hidden = true;
  $('results').replaceChildren();
  for (const node of nodes) {
    const button = createNodeResult(node, state.selected, id => activateResultCard(view, id, selectNode));
    $('results').append(button);
  }
}
/** Устанавливает фиксированный корень; выбор дочернего блока не меняет эту область. */
function isolateSelectedNode() {
  const system = isSystemMode($('view-mode').value);
  const block = system ? null : mapBlocks.address();
  const root = system ? systemView.selectedNodeId : state.selected;
  if (!root && !block) return;
  navigationHistory?.checkpoint();
  state.isolatedRoot = root;
  state.isolatedBlock = block ? { ...block, label: mapBlocks.selected()?.label } : null;
  state.inlineExpanded.clear();
  state.expandedId = null;
  state.focused = false;
  if (state.pathOnGraph && !state.pathOnGraph.steps.some(step => step.nodeId === root)) state.pathOnGraph = null;
  if (state.pathOnGraph) pathViewer.entry = null;
  draw(true); controls();
  navigationHistory?.commit();
}
/** Возвращает сохранённые фильтры и уровень карты без создания отдельной страницы истории. */
function clearIsolation() {
  navigationHistory?.checkpoint();
  state.isolatedRoot = null;
  state.isolatedBlock = null;
  draw(true); controls();
  navigationHistory?.commit();
}
$('isolate-node').addEventListener('click', isolateSelectedNode);
$('isolation-clear').addEventListener('click', clearIsolation);
/** Показывает соседей выбранного символа в текущем режиме «Код», не открывая группы. */
function showNodeLinks() {
  if (!state.selected) return;
  if ($('view-mode').value !== 'architecture') {
    state.focused = !state.focused;
    state.expandedId = null;
    draw(true);
    return;
  }
  state.focused = false;
  if (state.expandedId === state.selected) {
    state.expandedId = null;
    view.clearExpansion();
    $('expansion-toolbar').hidden = true;
    draw(true);
    return;
  }
  state.expandedId = state.selected;
  draw(true);
}
$('focus').addEventListener('click', showNodeLinks);
$('empty-focus-path').addEventListener('click', () => {
  if ($('empty').dataset.action === 'rebuild' && state.project) { $('rebuild').click(); return; }
  if ($('empty').dataset.action === 'retry' && state.project) {
    openProject(state.project.id, true);
    return;
  }
  activitySidebar.reveal('repository');
  if ($('empty').dataset.action === 'choose') { $('projects').focus(); return; }
  $('repository-add').open = true;
  $('project-path').focus();
  $('project-form').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
});
$('reset').addEventListener('click', () => {
  if (state.isolatedRoot || state.isolatedBlock) { clearIsolation(); return; }
  if (state.pathOnGraph) { state.pathOnGraph = null; pathViewer.close(); draw(true); return; }
  if ($('view-mode').value === 'groups' && $('display-provider').value === 'flow') { groupNavigation.open(); return; }
  isSystemMode($('view-mode').value) ? systemView.render(true) : $('view-mode').value === 'architecture' ? navigateArchitecture({ level: 'projects', projectId: null, file: null, page: 0 }) : openCommunity(null);
});
$('back-overview').addEventListener('click', () => {
  if (state.isolatedRoot || state.isolatedBlock) { clearIsolation(); return; }
  if (state.pathOnGraph) { state.pathOnGraph = null; pathViewer.close(); draw(true); return; }
  if ($('view-mode').value === 'groups' && $('display-provider').value === 'flow' && !state.community) { groupNavigation.open(); return; }
  $('view-mode').value === 'architecture' ? navigateArchitecture({ ...state.architecture, level: state.architecture.level === 'symbols' ? 'files' : 'projects', file: null, page: 0 }) : openCommunity(null);
});
$('communities').addEventListener('change', () => openCommunity($('communities').value));
$('fit').addEventListener('click', () => view.fit());
$('zoom-in').addEventListener('click', () => view.zoom(1.25));
bindGraphExport({ button: $('export-map-image'), status: $('map-export-status'), svg: $('graph'),
  name: () => `${$('project-name').textContent || 'code-atlas'}${view.pathSceneActive ? '-path' : ''}`,
  format: () => $('map-export-format').value });
bindArchitectureExport({button: $('architecture-export'), status: $('architecture-export-status'),
  format: () => $('architecture-export-format').value, getModel: () => systemView.exportModel()});
$('zoom-out').addEventListener('click', () => view.zoom(.8));
$('describe').addEventListener('click', () => {
  $('answer').replaceChildren(el('p', 'Описание кода открывается кнопкой «Описание» у выбранного узла. Запрос Graphify MCP с этой панели не отправляется. Если ответ агентского MCP обрезан, смотрите тот же узел здесь.', 'small muted'));
});
$('query-form').addEventListener('submit', event => { event.preventDefault(); const question = $('question').value.trim(); if (question) requestAnswer('query', { question }); });

const workspaceNavigation = createWorkspaceNavigation({ state, view,
  viewers: { wikiViewer, pathViewer, factsPopup, documentationViewer, systemView, mapBlocks, methodWorkbench, projectOverview },
  search: { searchNavigation, navigationSearch, semanticSearch },
  actions: { updateDisplayProvider, updateDiscovery, openProject, draw, filteredNodes, displayedAggregate,
    renderCodeProjects, renderSelection, loadIngress, paintPathGraph, controls, status, closeTransientStudy },
});
navigationHistory = new NavigationHistory({ getSnapshot: workspaceNavigation.capture, restore: workspaceNavigation.restore, onError: error => status(error.message, true),
  onChange: ({ canBack, canForward }) => {
    activityLayout.historyControls.back.disabled = !canBack;
    activityLayout.historyControls.forward.disabled = !canForward;
  } });
activityLayout.historyControls.back.addEventListener('click', () => navigationHistory.go(-1));
activityLayout.historyControls.forward.addEventListener('click', () => navigationHistory.go(1));
bindBrowserMapLink($('open-browser-map'), workspaceNavigation.capture, window, message => status(message, true));
/** Событие сохраняет прежнюю запись до обработчика; изменение текста заменяет её, не создавая десятки Back. */
function observeNavigation(event) {
  const interaction = navigationInteraction(event);
  if (!interaction) return;
  searchNavigation.cancel();
  navigationHistory.checkpoint({ cancelRestore: true });
  navigationHistory.scheduleCommit({ replace: interaction === 'replace' });
}
for (const event of ['click', 'change', 'input', 'keydown']) document.addEventListener(event, observeNavigation, true);

/** Восстанавливает выбранный проект и незавершённую серверную задачу. */
async function initialize() {
  try { $('project-blocks').checked = localStorage.getItem('code-atlas-project-blocks') !== 'false'; } catch { /* Блоки включены по умолчанию. */ }
  try { $('show-labels').checked = localStorage.getItem('code-atlas-show-labels') === 'true'; } catch { /* По умолчанию подписи скрыты. */ }
  view.setShowLabels($('show-labels').checked);
  try {
    const health = await api('/health');
    $('health').textContent = health.offline ? `Локальный режим · Graphify ${health.graphifyVersion || ''}` : 'Локальный сервер';
    const algorithms = await api('/grouping-algorithms');
    const modsOff = new URLSearchParams(location.search).get('mods') === 'off';
    state.algorithms = algorithms.algorithms.filter(item => !modsOff || !item.id.startsWith('mod.'));
    state.algorithm = algorithms.default || 'graphistry';
    $('algorithm').replaceChildren();
    for (const item of state.algorithms) $('algorithm').append(new Option(item.label, item.id));
    $('algorithm').value = state.algorithm;
    const catalog = await refreshProjects();
    let saved = '';
    try { saved = localStorage.getItem('code-atlas-project'); } catch { /* Хранение выбора необязательно. */ }
    const browserMap = readBrowserMapURL(location.href);
    if (browserMap) history.replaceState(browserMap.snapshot, '', browserMap.href);
    await navigationHistory.start({ ...workspaceNavigation.capture().route, repo: state.projects.some(project => project.id === saved) ? saved : null });
    if (!await remoteRepository.resume() && catalog.activeJob) await watchJob(catalog.activeJob);
  } catch (error) { $('health').textContent = 'Сервер недоступен'; status(error.message, true); }
}
/** Reports only the committed map scope, independent of a pending replacement. */
function desktopMapScope() {
  return {
    projectId: state.project?.id,
    snapshotId: state.graph.metadata?.snapshotId,
    nodeId: state.selected,
    revision: state.revision,
    loading: state.loading || state.pendingMapLoad,
    view: $('view-mode').value,
  };
}
window.addEventListener('pagehide', () => rememberDesktopMapScope(() => sessionStorage, desktopMapScope));
window.atlasDesktopAutomation = createDesktopAutomation({
  document,
  location,
  activity: agentActivity,
  readScope: desktopMapScope,
  openProject: (projectId, snapshotId, signal) => {
    if (!state.projects.some(project => project.id === projectId && project.snapshotId === snapshotId)) return false;
    return openProject(projectId, false, { expectedSnapshotId: snapshotId, signal });
  },
  openNode: (nodeId, signal) => selectSemanticNode(nodeId, { signal }),
  returnToMap: () => {
    if (agentActivity.isOpen()) agentActivity.action('close');
    closeTransientStudy();
    $('graph').focus({ preventScroll: true });
  },
});
initialize();

/** Metric selection only redraws captured facts; it does not rebuild the graph. */
$('node-metric').addEventListener('change', () => draw(false));

/** Mod changes reload the map with its existing URL/history; safe mode bypasses executable frontend mods. */
window.addEventListener('storage', event => {if(event.key==='atlas-mods-revision') location.reload();});
try {
  await loadMods({safe:new URLSearchParams(location.search).get('mods')==='off'});
  if(modErrors.length) status('Ошибка мода: ' + modErrors.join('; ') + '. Встроенная карта доступна.');
  draw(false);
} catch(error) {status('Моды недоступны: ' + error.message + '. Встроенная карта доступна.');}
