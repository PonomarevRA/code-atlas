import { decorateModCard } from './mod-runtime.js';
import { buildProgressiveMethodScene } from './progressive-method-scene.js';
import { InlineMethodFacts } from './inline-method-body.js';
import { renderMethodSequence } from './method-sequence-renderer.js';
import { buildPathScene } from './path-scene.js';
import { buildEntryPathScene } from './entry-path-scene.js';
import { renderExecutionPath, setPathSearchMatches, pathEdgeEvidence, appendPathReferenceLinks, updatePathRelations, appendImplementationPorts, bindPathAction, appendPathSuccessorButtons } from './execution-path-renderer.js';
import { preparePathScope, togglePathNode, reachablePathNodeIds, expandAllPathNodes, collapseAllPathNodes, fitPathInitial, followPathNode, revealPathNode, focusPathNode } from './path-controller.js';
import { ContextExpansion } from './context-expansion.js';
import { FlowRenderer } from './flow-renderer.js';
import { nodeCardLayout } from './node-card-layout.js';
import { naturalGeometry } from './natural-layout.js';
import { displayProvider } from './display-providers.js';
import { edgeHighlighter } from './highlight.js';
import { projectColor } from './colors.js';
import { ingressLabel, ingressKindLabel } from './ingress.js';
import { routeEdge, localRoutingRegions } from './edge-routing.js';
import { connectionCurve } from './edge-curves.js';
import { inlineFlowEdgeRouter } from './inline-flow-edges.js';
import { communityGeometry, nodeGeometry } from './layout.js';
import { classifyCodeNode, codeConnectionCounts, codeRoleSummary } from './code-semantics.js';
import { MapTooltip } from './map-tooltip.js';
import { appendComplexityBadge } from './complexity-badge.js';
import { keyboardPanDelta } from './keyboard-pan.js';
import { architectureResourceKind, decorateArchitectureResource, appendArchitectureTypeBadge, appendArchitectureClusters } from './architecture-resource-style.js';
import { SvgViewport, resetSvgViewport } from './svg-viewport.js';
let markerSequence = 0;
const NS = 'http://www.w3.org/2000/svg';
/** Пересечение луча между центрами карточек с её прямоугольной границей. */
function landscapeEdgeAnchor(point, toward) {
  const box = point.cardBounds;
  if (!box) return point;
  const center = { x: (box.left + box.right) / 2, y: (box.top + box.bottom) / 2 };
  const target = toward.cardBounds ? {
    x: (toward.cardBounds.left + toward.cardBounds.right) / 2,
    y: (toward.cardBounds.top + toward.cardBounds.bottom) / 2,
  } : toward;
  const dx = target.x - center.x || (target.y === center.y ? 1 : 0);
  const dy = target.y - center.y;
  const divisor = Math.max(Math.abs(dx) / ((box.right - box.left) / 2), Math.abs(dy) / ((box.bottom - box.top) / 2)) || 1;
  return { ...point, x: center.x + dx / divisor, y: center.y + dy / divisor };
}

/** Создаёт SVG-элемент; текст всегда устанавливается безопасно через textContent. */
function element(tag, attributes, text) {
  const node = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}
/** Ограниченный SVG-граф с детерминированной раскладкой по сообществам и навигацией. */
export class GraphView {
  /** Подключает жесты к SVG; onSelect передаёт стабильный идентификатор узла. */
  constructor(svg, onSelect, onCommunity) {
    this.svg = svg;
    svg.setAttribute('tabindex', 0);
    this.viewport = svg.querySelector('#viewport');
    this.onSelect = onSelect;
    this.onCommunity = onCommunity;
    this.nodes = [];
    this.nodeElements = new Map();
    this.communityElements = new Map();
    this.camera = { x: 0, y: 0, scale: 1 };
    // Без SVG geometry API (например, в тестовом DOM) сохраняется обычная отрисовка.
    if (this.viewport?.getBBox) this.viewportWindow = new SvgViewport(this);
    this.bundled = true; this.edgeElements = [];
    svg.addEventListener('wheel', event => {
      event.preventDefault();
      this.zoom(event.deltaY < 0 ? 1.15 : 1 / 1.15);
    }, { passive: false });
    svg.addEventListener('pointerdown', event => {
      // Карточка пути и её кнопки не начинают перетаскивание: иначе захват указателя съедает клик «Далее».
      if (event.target.closest('.graph-node, .community-node, .inline-hierarchy-card, .inline-method-card, .node-action, .ingress-node, .flow-card, .system-edge, .path-card, .path-decision, .path-exit, .path-edge-label, .path-reference')) return;
      svg.focus?.({ preventScroll: true });
      this.drag = { x: event.clientX, y: event.clientY, camera: { ...this.camera } };
      svg.setPointerCapture(event.pointerId);
    });
    svg.addEventListener('pointermove', event => {
      if (!this.drag) return;
      this.camera.x = this.drag.camera.x + event.clientX - this.drag.x;
      this.camera.y = this.drag.camera.y + event.clientY - this.drag.y;
      this.transform();
    });
    svg.addEventListener('pointerup', () => { this.drag = null; });
    svg.addEventListener('pointercancel', () => { this.drag = null; });
    svg.addEventListener('keydown', event => {
      if (event.key === 'Escape') this.mapTooltip?.hide(true);
      const delta = keyboardPanDelta(event, svg);
      if (!delta) return;
      event.preventDefault();
      this.panCameraBy(delta.x, delta.y);
    });
    // Сворачивание панелей меняет viewport, но не является командой «Вместить».
    // Сохраняем выбранный масштаб и положение; fit вызывается при загрузке и явно.
    new ResizeObserver(() => this.transform()).observe(svg);
  }
  /** Выбирает только способ отображения, не меняя факты и группировку. */
  setDisplayProvider(id) {
    const provider = displayProvider(id).id;
    if (provider !== this.displayProvider) this.pathReveal = null;
    this.displayProvider = provider;
  }
  /** Natural меняет только размещение; исходные цвета, ID и направленные связи сохраняются. */
  groupGeometry(geometry, edges) {
    return this.groupingProvider === 'natural' ? naturalGeometry(geometry, edges) : geometry;
  }
  /** Лениво создаёт карточный renderer, сохраняя общий контракт навигации. */
  renderFlow(options) {
    this.pathSceneActive = false;
    this.compartments = [];
    this.mapTooltip?.hide(true);
    this.flowRenderer ||= new FlowRenderer(this);
    this.flowRenderer.render(options);
  }
  /**
   * Рисует агрегированные группы обоими renderer, сохраняя исходное поведение без positionHook.
   * @param {Array} communities Группы с ID, подписями и необязательными system/resourceBlock полями.
   * @param {Array} edges Направленные связи этих групп.
   * @param {*} selectedCommunity Выбранная группа либо null.
   * @param {?Array} projects Каталог проектов для исходной геометрии.
   * @param {?object} projectGraph Исходные сведения о связях проектов.
   * @param {object} options Необязательный синхронный positionHook получает временную геометрию
   * в installGeometry до отрисовки карточек/линий; не должен менять ID или сохранять временную Map.
   * @returns {*} Результат текущего renderer. Hook действует только внутри этого вызова и восстанавливается
   * в finally, включая ошибку; исключение hook/renderer передаётся вызывающему коду.
   */
  renderOverview(communities, edges, selectedCommunity, projects = null, projectGraph = null, {positionHook = null} = {}) {
    const previous = this.overviewGeometryHook;
    this.overviewGeometryHook = typeof positionHook === 'function' ? positionHook : null;
    try {
      const result = this.renderOverviewScene(communities, edges, selectedCommunity, projects, projectGraph);
      for (const node of communities) if (node.system) {
        const group = this.communityElements.get(String(node.id))?.group;
        group?.setAttribute('data-node-id', node.id);
        if (node.resourceBlock) {
          group?.setAttribute('aria-expanded', String(Boolean(node.expanded)));
          group?.setAttribute('aria-label', `${node.label}. ${node.expanded ? 'Свернуть' : 'Раскрыть'} ресурсы: ${node.children?.length || 0}.`);
        }
      }
      return result;
    } finally { this.overviewGeometryHook = previous; }
  }
  /** Внутренний renderer сохраняет исходное поведение при отсутствии positionHook. */
  renderOverviewScene(communities, edges, selectedCommunity, projects = null, projectGraph = null) {
    const baseline = () => this.displayProvider === 'flow'
      ? nodeCardLayout(communities, edges, { natural: this.groupingProvider === 'natural', reading: this.codeReading, viewportWidth: this.svg.clientWidth }).positions
      : this.groupGeometry(communityGeometry(communities, edges, projects, projectGraph, this.colorProjects || projects || []), edges);
    if (this.prepareInline?.(communities, true, baseline)) return;
    this.pathSceneActive = false;
    this.mapTooltip?.hide(true);
    this.clearExpansion(true);
    if (this.displayProvider === 'flow') return this.renderFlow({ mode: 'overview', items: communities, edges, selected: selectedCommunity });
    resetSvgViewport(this);
    this.nodeElements.clear();
    this.communityElements.clear();
    this.installGeometry(this.groupGeometry(communityGeometry(communities, edges, projects, projectGraph, this.colorProjects || projects || []), edges));
    for (const community of communities) {
      if (!community.architecture) continue;
      if (community.system && !architectureResourceKind(community)) continue;
      const point = this.positions.get(String(community.id));
      const width = point.labelWidth || Math.max(160, point.r * 2);
      point.cardBounds = { left: point.x - width / 2, right: point.x + width / 2, top: point.y - point.r, bottom: point.y + (point.labelBottom || point.r + 80) };
    }
    const lines = [];
    for (const edge of edges) {
      const source = this.positions.get(String(edge.source));
      const target = this.positions.get(String(edge.target));
      if (!source || !target) continue;
      const line = this.createEdge(edge, source, target, { class: 'community-edge', 'stroke-width': Math.min(6, 1 + Math.log1p(edge.weight || 1)) });
      const sourceLabel = communities.find(group => String(group.id) === String(edge.source))?.label || edge.source;
      const targetLabel = communities.find(group => String(group.id) === String(edge.target))?.label || edge.target;
      line.append(element('title', {}, `${sourceLabel} → ${targetLabel}\n${edge.weight || 1} направленных связей${edge.system ? ` · ${edge.relation}` : ''}`));
      if (edge.system) {
        line.classList.add('system-edge');
        line.setAttribute('tabindex', 0);
        line.setAttribute('role', 'button');
        line.setAttribute('aria-label', `${sourceLabel} → ${targetLabel}: ${edge.relation}, вес ${edge.weight}`);
        line.addEventListener('click', () => this.onSystemEdge?.(edge));
        line.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.onSystemEdge?.(edge); } });
      }
      this.viewport.append(line);
      lines.push({ line, source: String(edge.source), target: String(edge.target) });
    }
    const highlight = edgeHighlighter(lines, 'highlighted');
    const connectionCounts = codeConnectionCounts(edges);
    for (const community of communities) {
      const id = String(community.id);
      const point = this.positions.get(id);
      const group = element('g', { transform: `translate(${point.x},${point.y})`, class: `community-node${community.architecture ? ' architecture-node' : ''}`, tabindex: 0, role: 'button', 'aria-label': `${community.label}, ${community.system ? community.kindLabel : `${community.nodeCount} узлов`}${community.changedNodeCount ? `, ${community.changedNodeCount} узлов изменённых файлов` : ''}. ${community.system ? 'Показать свидетельства.' : 'Открыть группу.'}` });
      const resource = decorateArchitectureResource(group, community);
      const semantic = classifyCodeNode(community);
      const architectureCard = community.architecture && !community.system;
      if (resource) {
        const width = point.labelWidth || Math.max(160, point.r * 2);
        const bottom = point.labelBottom || point.r + 80;
        group.append(element('rect', { x: -width / 2, y: -point.r, width, height: point.r + bottom,
          rx: 12, class: 'architecture-resource-surface' }));
        group.append(element('rect', { x: -width / 2 + 1, y: -point.r + 1, width: 5,
          height: point.r + bottom - 2, rx: 2, class: 'architecture-resource-stripe' }));
        appendArchitectureTypeBadge(group, community, { x: -width / 2 + 13, y: -point.r + 10 });
      } else if (architectureCard) {
        const width = point.labelWidth || Math.max(160, point.r * 2);
        const bottom = point.labelBottom || point.r + 80;
        group.setAttribute('data-code-role', semantic.id);
        group.append(element('rect', { x: -width / 2, y: -point.r, width, height: point.r + bottom, rx: 12, class: 'code-landscape-card', fill: semantic.fill, stroke: semantic.color, 'stroke-width': 1.4 }));
        group.append(element('rect', { x: -width / 2 + 1, y: -point.r + 1, width: 5, height: point.r + bottom - 2, rx: 2, class: 'code-role-stripe', fill: semantic.color }));
      } else group.append(element('circle', { r: point.r, fill: point.color, 'fill-opacity': .13, stroke: point.color, 'stroke-width': 2 }));
      if (community.changedNodeCount) {
        group.append(element('circle', { r: point.r + 6, class: 'change-ring' }));
        group.append(element('text', { x: point.r, y: -point.r, class: 'change-count', 'text-anchor': 'middle' }, `Δ ${community.changedNodeCount}`));
      }
      if (community.architectureLevel !== 'projects') group.append(element('text', { y: 5, 'text-anchor': 'middle', class: 'community-count', fill: architectureCard ? semantic.color : point.color }, community.system ? community.metricLabel || community.kindLabel : Number(community.nodeCount).toLocaleString('ru-RU')));
      const label = String(community.label || id);
      if (community.architecture) {
        const rawDisplay = String(community.displayLabel || label);
        const display = !point.labelLines && rawDisplay.length > 50 ? `${rawDisplay.slice(0, 49)}…` : rawDisplay;
        if (point.labelLines) {
          // Ресурс имеет отдельную строку типа над названием; учитываем реальные ascender глифов.
          const titleTop = resource ? 70 : 50;
          for (const [index, line] of point.labelLines.entries()) group.append(element('text', { y: -point.r + titleTop + index * 26, 'text-anchor': 'middle', class: 'community-label architecture-project-label' }, line));
        } else for (let offset = 0; offset < display.length; offset += 25) group.append(element('text', { y: point.r + 24 + offset / 25 * 23, 'text-anchor': 'middle', class: 'community-label' }, display.slice(offset, offset + 25)));
      } else group.append(element('text', { y: point.r + 19, 'text-anchor': 'middle', class: 'community-label' }, label.length > 30 ? `${label.slice(0, 28)}…` : label));
      if (architectureCard) {
        const bottom = point.labelBottom || point.r + 80;
        const project = community.architectureLevel === 'projects';
        group.append(element('text', { y: project ? -point.r + 21 : bottom - 10, 'text-anchor': 'middle', class: 'code-role-label', fill: semantic.color }, semantic.label));
        if (project) group.append(element('text', { y: bottom - 14, 'text-anchor': 'middle', class: 'code-card-count' }, `${Number(community.nodeCount).toLocaleString('ru-RU')} символов · проект`));
      }
      group.append(element('title', {}, community.system ? `${label}\n${community.kindLabel} · системный узел` : `${label}\n${community.nodeCount} узлов · ${community.internalEdges || 0} внутренних связей · ${community.externalEdges || 0} внешних`));
      this.bindMapTooltip(group, community, connectionCounts.get(id), { raw: false, highlight: active => highlight(active ? id : null) });
      group.addEventListener('click', () => this.onCommunity(id));
      group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.onCommunity(id); } });
      this.decorateNodeActions?.(group, id, { right: (point.labelWidth || Math.max(160, point.r * 2)) / 2, top: -point.r });
      this.viewport.append(group);
      this.communityElements.set(id, { group, radius: point.r });
    }
    if (selectedCommunity) highlight(String(selectedCommunity));
  }
  /** Рисует детали по локальной раскладке реальных связей; подписи выделяются по выбранному узлу. */
  render(nodes, edges, selected, communities = [], projects = null, projectGraph = null) {
    const baseline = () => this.displayProvider === 'flow'
      ? nodeCardLayout(nodes, edges, { natural: this.groupingProvider === 'natural', reading: this.codeReading, viewportWidth: this.svg.clientWidth }).positions
      : this.groupGeometry(nodeGeometry(nodes, communities, edges, projects, projectGraph, this.colorProjects || projects || []), edges);
    if (this.prepareInline?.(nodes, false, baseline)) return;
    this.pathSceneActive = false;
    this.mapTooltip?.hide(true);
    this.clearExpansion(true);
    if (this.displayProvider === 'flow') return this.renderFlow({ mode: 'nodes', items: nodes, edges, selected });
    resetSvgViewport(this);
    this.nodeElements.clear();
    this.communityElements.clear();
    this.selectedId = selected;
    this.installGeometry(this.groupGeometry(nodeGeometry(nodes, communities, edges, projects, projectGraph, this.colorProjects || projects || []), edges));
    const nodeElements = new Map();
    const lines = [];
    for (const edge of edges) {
      const source = this.positions.get(edge.source), target = this.positions.get(edge.target);
      if (!source || !target) continue;
      const line = this.createEdge(edge, source, target, { class: edge.path ? 'graph-edge path-edge' : 'graph-edge', 'marker-end': 'url(#arrow)' });
      line.append(element('title', {}, edge.relation === 'dispatches_to' || edge.evidence ? this.edgeEvidence(edge) : edge.caption || edge.relation || 'связь'));
      this.viewport.append(line);
      const caption = edge.relation === 'dispatches_to' ? 'Возможная реализация' : edge.caption;
      if (caption) this.viewport.append(element('text', { x: (source.x + target.x) / 2, y: (source.y + target.y) / 2 - 6, class: 'path-edge-label', 'text-anchor': 'middle' }, caption));
      lines.push({ line, source: edge.source, target: edge.target });
    }
    const highlightEdges = edgeHighlighter(lines, 'active');
    let previousHover = null;
    const highlight = id => {
      highlightEdges(id);
      for (const nodeId of new Set([previousHover, id, this.selectedId])) {
        nodeElements.get(nodeId)?.classList.toggle('show-label', nodeId === id || nodeId === this.selectedId);
      }
      previousHover = id;
    };
    const connectionCounts = codeConnectionCounts(edges);
    for (const node of nodes) {
      const point = this.positions.get(node.id);
      const group = element('g', { transform: `translate(${point.x},${point.y})`, class: `graph-node topology-card${node.id === selected ? ' selected' : ''}`, tabindex: 0, role: 'button', 'aria-label': `${node.label}, ${node.kind || 'узел'}${node.changedFile ? ', узел изменённого файла' : ''}`, 'aria-pressed': node.id === selected });
      const left = -point.width / 2, top = -point.height / 2;
      // Внешняя полоса сохраняет цвет проекта; внутренняя поверхность даёт спокойный фон тексту.
      group.append(element('rect', { x: left, y: top, width: point.width, height: point.height, rx: 10, fill: point.color, stroke: node.id === selected ? '#17382c' : point.color, 'stroke-width': node.id === selected ? 2.5 : 1.2, class: 'topology-card-shell' }));
      group.append(element('rect', { x: left + 5, y: top + 1, width: point.width - 6, height: point.height - 2, rx: 8, fill: '#fbfdfb', class: 'topology-card-body' }));
      const semantic = classifyCodeNode(node);
      group.setAttribute('data-code-role', semantic.id);
      group.append(element('rect', { x: left + 5, y: top + 1, width: point.width - 6, height: 29, rx: 8, fill: semantic.color, 'fill-opacity': .11, class: 'topology-card-role-band' }));
      group.append(element('text', { x: left + 15, y: top + 19, class: 'topology-card-role', fill: semantic.color }, semantic.label));
      if (node.changedFile) group.append(element('rect', { x: left - 3, y: top - 3, width: point.width + 6, height: point.height + 6, rx: 12, class: 'change-ring', fill: 'none', stroke: '#b77b26', 'stroke-width': 2, 'stroke-dasharray': '4 2' }));
      const label = String(node.label || node.id);
      const labelLimit = node.complexity ? 24 : 28;
      const lines = [];
      for (let offset = 0; offset < Math.min(label.length, labelLimit * 2); offset += labelLimit) lines.push(label.slice(offset, offset + labelLimit));
      if (label.length > labelLimit * 2) lines[1] = `${lines[1].slice(0, labelLimit - 2)}…`;
      for (let index = 0; index < lines.length; index++) group.append(element('text', { x: left + (node.complexity ? 44 : 15), y: top + 50 + index * 17, class: 'topology-card-title' }, lines[index]));
      appendComplexityBadge(group, node.complexity, left + 24, top + 45, node.health);
      const filename = String(node.file || '').split(/[\\/]/).pop() || node.kind || 'символ';
      const location = `${filename}${node.line ? `:${node.line}` : ''}`;
      group.append(element('text', { x: left + 15, y: top + 96, class: 'topology-card-location' }, location.length > 33 ? `${location.slice(0, 31)}…` : location));
      group.append(element('title', {}, `${label}\n${node.file || ''}`));
      group.addEventListener('click', () => this.onSelect(node.id));
      this.bindMapTooltip(group, node, connectionCounts.get(String(node.id)), { highlight: active => highlight(active ? node.id : this.selectedId) });
      group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.onSelect(node.id); } });
      decorateModCard(this, group, node, {left,top,width:point.width,height:point.height});
      this.viewport.append(group);
      this.decorateNodeActions?.(group, node.id, { right: point.width / 2, top: -point.height / 2 });
      nodeElements.set(node.id, group);
      this.nodeElements.set(node.id, { group, radius: 16, card: { width: point.width, height: point.height } });
    }
    this.highlightSelection = highlight;
    highlight(selected);
  }
  /** Рисует раскрытые тела на месте: вызывающий метод и соседние вызовы сохраняют контекст. */
  renderPathCards(nodes, edges, selected) {
    this.scheduleSceneChange();
    const scope = preparePathScope(this, nodes, edges, selected);
    const root = nodes.find(node => node.id === scope.focusId);
    if (root?.methodFlow?.status === 'parsed') {
      const placementScope = JSON.stringify([this.pathProjectId, this.pathSnapshotId, scope.focusId, this.groupingProvider, this.pathControlFilters]);
      if (this.pathPlacementScope !== placementScope) {
        this.pathPlacementScope = placementScope;
        this.pathMethodPlacements = this.pathRestorePlacements || new Map(); this.pathRestorePlacements = null;
        this.pathVisibleRegions = new Set(this.pathMethodPlacements.keys());
      }
      this.pathMethodFacts ||= new InlineMethodFacts({ context: () => ({ projectId: this.pathProjectId, snapshotId: this.pathSnapshotId }),
        changed: () => { if (this.pathSceneActive && this.pathData) this.renderPathCards(this.pathData.nodes, this.pathData.edges, this.selectedId); } });
      this.pathMethodFacts.sync();
      const byId = new Map(nodes.map(node => [node.id, node]));
      const scene = buildProgressiveMethodScene({ nodes, edges }, scope.focusId, { expandedIds: this.pathExpandedIds,
        placements: this.pathMethodPlacements, visibleRegionIds: this.pathVisibleRegions || new Set(this.pathMethodPlacements.keys()),
        getStatus: id => this.pathMethodFacts.get(id), getNode: id => this.getSourceNode?.(id),
        getData: id => this.pathMethodFacts.get(id)?.data || byId.get(id), loadData: id => this.pathMethodFacts.load(id),
        controlFilters: this.pathControlFilters, implementationCatalog: this.pathImplementationCatalog });
      const released = this.pathMethodFacts.retain(scene.requestedTargets);
      renderMethodSequence(this, scene, selected);
      this.pathVisibleRegions = new Set(scene.expansions.map(area => area.bounds.id));
      if (released && scene.visuals.some(node => node.expansionFailed)) queueMicrotask(() => {
        if (this.pathPlacementScope === placementScope && this.pathSceneActive) this.renderPathCards(nodes, edges, this.selectedId);
      });
      return;
    }
    this.pathSequenceScene = null;
    const stableFlow = this.groupingProvider === 'natural' || this.displayProvider === 'flow' && (this.pathReading || nodes.some(node => String(node.id).startsWith('path-entry:')));
    const scene = stableFlow ? buildEntryPathScene(this, scope, selected)
      : buildPathScene(scope, { flowPath: this.displayProvider === 'flow', controlFilters: this.pathControlFilters, selected });
    renderExecutionPath(this, scene, selected);
  }

  /** Меняет только видимость условий/циклов; сохраняет камеру, выбор и раскрытие методов. */
  setPathControlFilters(filters) {
    this.pathControlFilters = { ...this.pathControlFilters, ...filters };
    if (!this.pathSceneActive || !this.pathData) return;
    const camera = { ...this.camera };
    this.renderPathCards(this.pathData.nodes, this.pathData.edges, this.selectedId);
    this.camera = camera;
    this.transform();
  }

  /** Продолжает локальный вызов от ссылки до реального метода; повторные свидетельства сохраняются в одной линии. */
  appendPathReferenceLinks(positions, frames, referenceEdges, arrowMarker, flowRouter) { return appendPathReferenceLinks(this, positions, frames, referenceEdges, arrowMarker, flowRouter); }

  /** Подсвечивает raw связи выбранного метода и обе стороны локальных ссылок без новых пересекающих карту дуг. */
  updatePathRelations() { return updatePathRelations(this); }

  /** Подключает краткие сведения к наведению и фокусу, сохраняя отдельное выделение связей. */
  bindMapTooltip(group, node, counts = { incoming: 0, outgoing: 0 }, { raw = true, highlight } = {}) {
    const show = () => {
      highlight?.(true);
      const host = this.svg.parentElement;
      // Renderer работает и с SVG-only фасадом в headless-проверках; popup нужен только живому HTML-контейнеру.
      if (!host?.getBoundingClientRect || typeof document.createElement !== 'function') return;
      const owner = this.mapTooltipOwner || this;
      owner.mapTooltip ||= new MapTooltip(host, (id, anchor) => owner.onDescribeNode?.(id, anchor));
      this.mapTooltip = owner.mapTooltip;
      const location = node.targetFile || node.file;
      this.mapTooltip.show({
        id: node.id, label: String(node.label || node.id), role: classifyCodeNode(node),
        location: location ? `${location}${node.line ? `:${node.line}` : ''}` : node.architectureLevel === 'projects' ? 'Проект в текущем репозитории' : 'Исходный файл не сопоставлен',
        incoming: counts?.incoming || 0, outgoing: counts?.outgoing || 0,
        summary: node.architecture ? [node.fileCount != null ? `${node.fileCount} файлов` : '', `${node.nodeCount || 0} символов`, codeRoleSummary(node.roleCounts)].filter(Boolean).join(' · ') : '',
        fieldFacts: node.fieldFacts,
        complexity: node.complexity, health: node.health,
        canDescribe: raw && !node.fieldFacts && !String(node.id).startsWith('path-entry:'),
      }, group);
    };
    const leave = () => { highlight?.(false); this.mapTooltip?.scheduleHide(); };
    group.addEventListener('mouseenter', show);
    group.addEventListener('focus', show);
    group.addEventListener('mouseleave', leave);
    group.addEventListener('blur', leave);
  }

  /** Порты вертикального Flow показывают места входа и выхода статических связей, не вычисленный runtime-порядок. */
  appendImplementationPorts(group, point, color, centered = false, inputOnly = false) { return appendImplementationPorts(this, group, point, color, centered, inputOnly); }

  /** Привязывает действие SVG к мыши и клавиатуре, не активируя выбор родительской карточки. */
  bindPathAction(control, action) { return bindPathAction(this, control, action); }

  /**
   * Перемещает обзор на экранные пиксели без изменения масштаба, выбора и геометрии.
   * Публикует один callback на клавишу; владелец истории может заменить текущее состояние без создания перехода.
   */
  panCameraBy(dx, dy) {
    if (!Number.isFinite(dx) || !Number.isFinite(dy) || (!dx && !dy)) return false;
    this.mapTooltip?.hide(true);
    this.camera.x += dx;
    this.camera.y += dy;
    this.transform();
    this.onCameraChange?.({ ...this.camera }, { kind: 'pan', source: 'keyboard' });
    return true;
  }

  /** Раскрывает или сворачивает тело на месте, сохраняя экранную позицию нажатой карточки и масштаб. */
  togglePathNode(id) { return togglePathNode(this, id); }

  /** Совместимое имя действия раскрытия: изменение всегда остаётся внутри существующей сцены. */
  expandPathNode(id) { this.togglePathNode(id); }

  /** Показывает тело, если оно ещё свёрнуто; существующий контекст остаётся видимым. */
  openPathScope(id) { if (!this.pathScope?.stats.get(id)?.expanded) this.togglePathNode(id); }

  /** Находит доступную цепочку от корня, не включая входную карточку и недостижимые узлы анализа. */
  reachablePathNodeIds() { return reachablePathNodeIds(this); }

  /** Раскрывает всю захваченную цепочку на месте; серверные лимиты сохраняются, камера и выбор не меняются. */
  expandAllPathNodes() { return expandAllPathNodes(this); }

  /** Возвращает первоначальное компактное раскрытие корня и прозрачных dispatch-переходов. */
  collapseAllPathNodes() { return collapseAllPathNodes(this); }

  /** Сброс локальных раскрытий для вызывающего интерфейса. */
  resetPathScope() { this.collapseAllPathNodes(); }

  /** Начальный кадр сохраняет читаемый текст, даже когда тело корня выше экрана; общий обзор доступен через «Вместить». */
  fitPathInitial() { return fitPathInitial(this); }

  /** Добавляет окружение рядом с сохранённым обзором; отдельный renderer использует исходные id. */
  renderExpansion(payload) {
    this.expansion ||= new ContextExpansion(this, viewport => {
      const child = Object.create(GraphView.prototype);
      Object.assign(child, { svg: this.svg, viewport, onSelect: this.onSelect, onCommunity: this.onCommunity,
        onDescribeNode: this.onDescribeNode, mapTooltipOwner: this, mapTooltip: this.mapTooltip,
        nodeElements: new Map(), communityElements: new Map(), camera: { ...this.camera },
        colorProjects: this.colorProjects, displayProvider: this.displayProvider, bundled: this.bundled });
      return child;
    });
    this.expansion.open(payload);
    this.scheduleSceneChange();
  }
  /** Сворачивает локальную область без потери исходного обзора. */
  clearExpansion(restoreCamera = true) { this.mapTooltip?.hide(true); this.expansion?.close(restoreCamera); }
  /** Обновляет выбранный видимый символ без перестроения SVG, геометрии и камеры. */
  setSelected(id, occurrenceId = null) {
    this.selectedSequenceOccurrenceId = occurrenceId;
    if (this.inlineSceneActive) return this.inlineHierarchyRenderer?.setSelected(id);
    if (this.expansion?.child?.nodeElements.has(id)) {
      this.expansion.selectedId = id;
      return this.expansion.child.setSelected(id);
    }
    if (this.displayProvider === 'flow' && !this.pathSceneActive) return this.flowRenderer?.setSelected(id);
    if (id !== this.selectedId) this.pathReveal = null;
    for (const nodeId of new Set([this.selectedId, id])) {
      const item = this.nodeElements.get(nodeId);
      if (!item) continue;
      const selected = nodeId === id;
      if (this.pathSceneActive && item.group.classList.contains('path-card')) {
        item.group.classList.toggle('selected', selected);
        item.group.querySelector('.path-card-body')?.setAttribute('stroke', selected ? '#17382c' : '#b7c9be');
        item.group.setAttribute('aria-pressed', selected);
        continue;
      }
      if (this.pathSceneActive && (item.group.classList.contains('path-decision') || item.group.classList.contains('path-exit'))) {
        item.group.classList.toggle('selected', selected);
        continue;
      }
      if (item.card) {
        item.group.classList.toggle('selected', selected);
        item.group.setAttribute('aria-pressed', selected);
        item.group.querySelector('.topology-card-shell')?.setAttribute('stroke', selected ? '#17382c' : this.positions.get(nodeId).color);
        item.group.querySelector('.topology-card-shell')?.setAttribute('stroke-width', selected ? 2.5 : 1.2);
        continue;
      }
      item.group.classList.toggle('selected', selected);
      if (!selected) item.group.classList.toggle('show-label', false);
      item.group.setAttribute('aria-pressed', selected);
      item.group.querySelector('circle')?.setAttribute('r', selected ? 10 : 6);
      item.group.querySelector('.selection-ring')?.remove();
      if (selected) item.group.append(element('circle', { r: 13, class: 'selection-ring' }));
      item.group.querySelector('.change-ring')?.setAttribute('r', selected ? 15 : 11);
      item.radius = selected ? 20 : 16;
      item.group.querySelector('.regex-ring')?.setAttribute('r', item.radius);
    }
    this.selectedId = id;
    this.highlightSelection?.(id);
    this.updatePathRelations();
  }
  /**
   * Выделяет следующий шаг и ставит его в центр окна.
   * Сначала выбор, затем камера: обновление инспектора не должно возвращать обзор всего пути.
   */
  followPathNode(id) { return followPathNode(this, id); }

  /** Явный переход к скрытой связи раскрывает кратчайшую известную цепочку, не уменьшая масштаб. */
  revealPathNode(id) { return revealPathNode(this, id); }

  /**
   * Ставит шаг пути в центр окна, не пересобирая схему.
   * Текущий масштаб сохраняется: переход к ссылке меняет только центр камеры.
   */
  focusPathNode(id) { return focusPathNode(this, id); }

  /**
   * Кнопки прямых следующих шагов внизу карточки.
   * Если подписи не входят в ширину, лишние не рисуются, а первая укорачивается: карточку не растягиваем.
   * @param {SVGGElement} group карточка пути
   * @param {Array<{target:string, caption?:string}>} edges непосредственные визуальные преемники
   * @param {number} cardWidth
   * @param {number} cardHeight
   * @returns {string[]} подписи, которые не поместились и остаются в подсказке
   */
  appendPathSuccessorButtons(group, edges, cardWidth, cardHeight) { return appendPathSuccessorButtons(this, group, edges, cardWidth, cardHeight); }

  /** Рисует производные входы отдельным слоем; raw positions и membership остаются неизменными. */
  renderIngress(entries, onSelect) {
    this.ingressPositions = [];
    const mapped = entries.filter(entry => entry.targets.some(id => this.positions.has(id)));
    if (!mapped.length || !this.positions.size) return;
    const bounds = this.geometryBounds();
    const width = bounds.maxX - bounds.minX;
    const columns = Math.min(mapped.length, width > 900 ? 3 : width > 560 ? 2 : 1);
    const rows = Math.ceil(mapped.length / columns);
    const colors = { http: '#376f94', kafka: '#995b36', rabbit: '#806196' };
    const layer = element('g', { class: 'ingress-layer' });
    for (const [index, entry] of mapped.entries()) {
      const column = index % columns, row = Math.floor(index / columns);
      const point = { id: entry.id, x: bounds.minX + (column + .5) * width / columns, y: bounds.minY - 80 - (rows - 1 - row) * 120, r: 14, color: colors[entry.kind] || '#536b5b' };
      this.ingressPositions.push(point);
      let routeFlow;
      if (this.displayProvider === 'flow') {
        const cards = new Map();
        for (const [id, card] of this.positions) cards.set(id, { ...card, w: card.width || card.w || 0, h: card.height || card.h || 0 });
        cards.set(entry.id, { ...point, w: 0, h: point.r * 2 });
        routeFlow = inlineFlowEdgeRouter(cards);
      }
      for (const targetId of entry.targets) {
        const target = this.positions.get(targetId);
        if (!target) continue;
        const targetX = target.x - (target.width || 0) / 2;
        const d = routeFlow ? routeFlow({ ...point, w: 0, h: point.r * 2 }, { ...target, w: target.width || target.w || 0, h: target.height || target.h || 0 }, index).d
          : connectionCurve(point, { x: targetX, y: target.y });
        const path = element('path', { class: 'ingress-edge', fill: 'none', stroke: point.color, 'marker-end': this.sourceMarker(point.color, target.width ? -2 : target.r), 'data-ingress-id': entry.id, 'data-target': targetId, d });
        path.append(element('title', {}, `${entry.label} → обработчик · производная связь по статическим свидетельствам`));
        layer.append(path);
      }
      const group = element('g', { class: 'ingress-node', 'data-ingress-id': entry.id, transform: `translate(${point.x},${point.y})`, role: 'button', tabindex: 0, 'aria-label': `${entry.kind}: ${entry.label}. Показать свидетельства. Двойное нажатие или Ctrl/Cmd+Enter — путь от входа при одном обработчике.` });
      group.append(element('path', { d: 'M0,-14 L14,0 L0,14 L-14,0 Z', fill: point.color, stroke: point.color }));
      const kind = ingressKindLabel(entry);
      group.append(element('text', { y: 36, 'text-anchor': 'middle', class: 'ingress-label' }, kind));
      group.append(element('text', { y: 66, 'text-anchor': 'middle', class: 'ingress-label' }, ingressLabel(entry)));
      group.append(element('title', {}, `${entry.origin}
${entry.label}`));
      group.addEventListener('click', () => onSelect(entry));
      group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(entry); } });
      layer.append(group);
    }
    this.viewport.append(layer);
  }
  /** Создаёт одну кривую на связь и сохраняет данные для переключения без повторной раскладки. */
  createEdge(edge, source, target, attributes) {
    const key = JSON.stringify([edge.source, edge.target, edge.relation || '', edge.id || '']);
    const options = { key, bounds: this.routingBounds, sourceRegion: this.routingRegions.get(source.id), targetRegion: this.routingRegions.get(target.id), sourceContainer: this.containerById.get(String(source.codeProjectId || '__unassigned__')), targetContainer: this.containerById.get(String(target.codeProjectId || '__unassigned__')) };
    const routeSource = landscapeEdgeAnchor(source, target);
    const routeTarget = landscapeEdgeAnchor(target, source);
    const path = element('path', { ...attributes, fill: 'none', stroke: source.color, 'marker-end': this.sourceMarker(source.color, target.cardBounds ? -2 : target.r), 'data-source': edge.source, 'data-target': edge.target, d: routeEdge(routeSource, routeTarget, { ...options, bundled: this.bundled !== false }) });
    if (edge.relation === 'dispatches_to') path.setAttribute('stroke-dasharray', '5 4');
    this.edgeElements.push({ path, source: routeSource, target: routeTarget, options });
    return path;
  }
  /** Общая текстовая подсказка сохраняет происхождение статической связи для обоих видов карты. */
  edgeEvidence(edge) { return pathEdgeEvidence(edge); }
  /** Переиспользует явно окрашенные стрелки; не зависит от поддержки context-stroke браузером. */
  sourceMarker(color, radius) {
    const key = JSON.stringify([color, radius]);
    if (!this.markerColors.has(key)) {
      const id = `flow-arrow-${markerSequence++}`;
      const marker = element('marker', { id, markerWidth: 8, markerHeight: 8, refX: (radius || 7) + 8, refY: 3, orient: 'auto', markerUnits: 'userSpaceOnUse' });
      marker.append(element('path', { d: 'M0,0 L6,3 L0,6', fill: color }));
      this.edgeDefinitions.append(marker);
      this.markerColors.set(key, `url(#${id})`);
    }
    return this.markerColors.get(key);
  }
  /** Меняет только d существующих путей: позиции, выделение и камера остаются прежними. */
  setBundled(bundled) {
    this.bundled = bundled;
    this.expansion?.child?.setBundled(bundled);
    for (const item of this.edgeElements || []) item.path.setAttribute('d', routeEdge(item.source, item.target, { ...item.options, bundled }));
    this.viewportWindow?.refreshBounds();
  }
  /** Notify local UI once after a synchronous scene render; camera/virtualizer changes do not reindex Find. */
  scheduleSceneChange() {
    if (!this.onSceneChange || this.sceneNotificationPending) return;
    this.sceneNotificationPending = true;
    queueMicrotask(() => {
      this.sceneNotificationPending = false;
      this.onSceneChange?.();
    });
  }
  /**
   * Устанавливает временную геометрию и рисует проектные контейнеры под рёбрами.
   * @param {Map|object} geometry Карта позиций либо {positions,containers,compartments} текущего renderer.
   * @returns {void} Необязательный scoped overviewGeometryHook применяется до чтения размеров/рисования;
   * координаты сохраняют identity временной Map для FlowRenderer. Ошибка hook передаётся вызывающему коду.
   */
  installGeometry(geometry) {
    if (this.overviewGeometryHook) geometry = this.overviewGeometryHook(geometry);
    this.scheduleSceneChange();
    this.flowBounds = null;
    this.edgeElements = [];
    this.ingressPositions = [];
    this.markerColors = new Map();
    this.edgeDefinitions = element('defs', {});
    this.viewport.append(this.edgeDefinitions);
    this.positions = geometry instanceof Map ? geometry : geometry.positions;
    this.architectureClusters = geometry.architectureClusters || this.positions.architectureClusters || [];
    appendArchitectureClusters(this.viewport, this.architectureClusters);
    this.containers = geometry instanceof Map ? [] : geometry.containers;
    this.compartments = geometry.compartments || this.positions.compartments || [];
    this.containerById = new Map(this.containers.map(box => [String(box.id), box]));
    this.routingBounds = this.geometryBounds();
    this.routingRegions = localRoutingRegions(this.positions.values(), this.containers);
    for (const box of this.containers) {
      const palette = projectColor(box.label, { unassigned: box.id === '__unassigned__' });
      const block = element('g', { class: `project-block${box.shape === 'rect' ? ' topology-project-block' : ''}`, 'data-project-id': box.id, role: 'group', 'aria-label': box.label });
      block.append(box.shape === 'rect' ? element('rect', { x: box.x, y: box.y, width: box.width, height: box.height, rx: 20, class: 'project-block-box', fill: palette.fill, stroke: palette.stroke }) : element('ellipse', { cx: box.cx, cy: box.cy, rx: box.rx, ry: box.ry, class: 'project-block-box', fill: palette.fill, stroke: palette.stroke }));
      const label = String(box.label);
      const titleLimit = Math.max(18, Math.floor((box.width - 40) / 9));
      for (const [index, line] of (box.labelLines || [label]).entries()) block.append(element('text', { x: box.contentX + 20, y: box.contentY + (box.shape === 'rect' ? 24 : 42) + index * 34, class: 'project-block-title', fill: palette.text, stroke: palette.fill }, box.shape === 'rect' && String(line).length > titleLimit ? `${String(line).slice(0, titleLimit - 2)}…` : line));
      const statsY = box.shape === 'rect' ? box.contentY + 44 : box.contentY + 42 + (box.labelLines || [label]).length * 34;
      block.append(element('text', { x: box.contentX + 20, y: statsY, class: 'project-block-stats', fill: palette.text, stroke: palette.fill }, `Вх: ${box.incoming} · Исх: ${box.outgoing}`));
      block.append(element('title', {}, `${label}\nВес входящих: ${box.incoming}; исходящих: ${box.outgoing}; внутренних: ${box.internal}.\nПодсчёт: ${box.statsScope}.`));
      this.viewport.append(block);
    }
    for (const box of this.compartments) {
      const block = element('g', { class: 'topology-compartment', role: 'group', 'aria-label': `${box.label}, ${box.count} символов`, 'data-group-id': box.id });
      block.append(element('rect', { x: box.x, y: box.y, width: box.width, height: box.height, rx: 12, fill: '#f3f7f4', stroke: box.color, 'stroke-opacity': .3 }));
      const label = String(box.label);
      const limit = Math.max(18, Math.floor((box.width - 40) / 7.5));
      block.append(element('text', { x: box.x + 20, y: box.y + 23, class: 'topology-compartment-heading', fill: '#344f41' }, label.length > limit ? `${label.slice(0, limit - 2)}…` : label));
      block.append(element('text', { x: box.x + 20, y: box.y + 41, class: 'topology-compartment-stats', fill: '#6a7e72' }, `${box.count} символов`));
      block.append(element('title', {}, `${label}\n${box.file}\nГруппировка по исходникам и сохранённым сообществам; не порядок исполнения.`));
      this.viewport.append(block);
    }
  }
  /** Меняет только подписи, не пересоздавая SVG и не двигая камеру. */
  setShowLabels(show) { this.svg.classList.toggle('labels-visible', show); }
  /** Добавляет независимую синюю подсветку к существующим узлам; геометрия и рёбра остаются прежними. */
  setSearchMatches(ids, communityCounts = new Map()) {
    if (this.inlineSceneActive) return this.inlineHierarchyRenderer?.setSearchMatches(ids, communityCounts) || 0;
    if (this.expansion?.child) {
      const merged = this.nodeElements;
      this.nodeElements = this.expansion.baseNodeElements;
      const child = this.expansion.child;
      this.expansion.child = null;
      const baseCount = this.setSearchMatches(ids, communityCounts);
      this.expansion.child = child;
      this.nodeElements = merged;
      let repeated = 0;
      for (const id of ids) if (this.expansion.baseNodeElements.has(id) && child.nodeElements.has(id)) repeated++;
      return baseCount + child.setSearchMatches(ids) - repeated;
    }
    if (this.pathSceneActive) return setPathSearchMatches(this, ids);
    if (this.displayProvider === 'flow') return this.flowRenderer?.setSearchMatches(ids, communityCounts) || 0;
    const matches = new Set(ids);
    let visibleCount = 0;
    for (const [id, item] of this.nodeElements) {
      item.group.querySelector('.regex-ring')?.remove();
      if (!matches.has(id)) continue;
      visibleCount++;
      item.group.append(item.card ? element('rect', { x: -item.card.width / 2 - 4, y: -item.card.height / 2 - 4, width: item.card.width + 8, height: item.card.height + 8, rx: 13, fill: 'none', stroke: '#2866ad', 'stroke-width': 3, class: 'regex-ring' }) : element('circle', { r: item.radius, class: 'regex-ring' }));
    }
    for (const [id, item] of this.communityElements) {
      item.group.querySelector('.regex-ring')?.remove();
      item.group.querySelector('.regex-count')?.remove();
      const count = communityCounts.get(id) || 0;
      if (!count) continue;
      visibleCount += count;
      item.group.append(element('circle', { r: item.radius + 12, class: 'regex-ring' }));
      item.group.append(element('text', { x: -item.radius, y: -item.radius, class: 'regex-count', 'text-anchor': 'middle' }, `⌕ ${count}`));
    }
    return visibleCount;
  }
  /** Перемещает камеру и обновляет экранное окно без перестроения геометрии или карточек. */
  transform() {
    this.viewport.setAttribute('transform', `translate(${this.camera.x},${this.camera.y}) scale(${this.camera.scale})`);
    this.expansion?.updateContextTransform();
    this.viewportWindow?.update();
    this.onViewportChange?.();
  }
  /** Меняет масштаб относительно центра окна в диапазоне 0.02–5. */
  zoom(factor) {
    const width = this.svg.clientWidth;
    const height = this.svg.clientHeight;
    const scale = Math.max(.02, Math.min(5, this.camera.scale * factor));
    const ratio = scale / this.camera.scale;
    this.camera.x = width / 2 - (width / 2 - this.camera.x) * ratio;
    this.camera.y = height / 2 - (height / 2 - this.camera.y) * ratio;
    this.camera.scale = scale;
    this.transform();
  }
  /** Общие границы для камеры и контролей кривых: переключение пучков не требует нового fit. */
  geometryBounds() {
    if (this.expansion?.bounds) return this.expansion.bounds;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const point of this.positions.values()) {
      if (point.cardBounds) {
        minX = Math.min(minX, point.cardBounds.left - 20); maxX = Math.max(maxX, point.cardBounds.right + 20);
        minY = Math.min(minY, point.cardBounds.top - 20); maxY = Math.max(maxY, point.cardBounds.bottom + 20);
        continue;
      }
      minX = Math.min(minX, point.x - point.r - 85); maxX = Math.max(maxX, point.x + point.r + 85);
      minY = Math.min(minY, point.y - point.r - 50); maxY = Math.max(maxY, point.y + (point.labelBottom || point.r + 50));
      if (point.labelWidth) { minX = Math.min(minX, point.x - point.labelWidth / 2); maxX = Math.max(maxX, point.x + point.labelWidth / 2); }
    }
    if (this.flowBounds) {
      const box = this.flowBounds;
      minX = Math.min(minX, box.x); maxX = Math.max(maxX, box.x + box.width);
      minY = Math.min(minY, box.y); maxY = Math.max(maxY, box.y + box.height);
    }
    for (const box of this.containers || []) {
      minX = Math.min(minX, box.x - 20); maxX = Math.max(maxX, box.x + box.width + 20);
      minY = Math.min(minY, box.y - 20); maxY = Math.max(maxY, box.y + box.height + 20);
    }
    for (const box of this.compartments || []) {
      minX = Math.min(minX, box.x - 20); maxX = Math.max(maxX, box.x + box.width + 20);
      minY = Math.min(minY, box.y - 20); maxY = Math.max(maxY, box.y + box.height + 20);
    }
    // Заголовки кластеров входят в fit наряду с карточками, а не обрезаются камерой.
    for (const box of this.architectureClusters || []) {
      minX = Math.min(minX, box.x - 20); maxX = Math.max(maxX, box.x + box.width + 20);
      minY = Math.min(minY, box.y - 20); maxY = Math.max(maxY, box.y + box.height + 20);
    }
    for (const point of this.ingressPositions || []) { minX = Math.min(minX, point.x - 240); maxX = Math.max(maxX, point.x + 240); minY = Math.min(minY, point.y - 30); maxY = Math.max(maxY, point.y + 80); }
    return { minX, minY, maxX, maxY };
  }
  /** Открывает Код в читаемом масштабе у выбранной карточки или начала карты. */
  fitCodeInitial() {
    if (!this.codeReading || !this.positions?.size) { this.fit(); return; }
    const selected = this.positions.get(this.selectedId) || this.positions.get(this.flowRenderer?.selected);
    const cards = [...this.positions.values()];
    const bounds = point => point.headerBounds || { left: point.x - point.width / 2, top: point.y - point.height / 2 };
    cards.sort((a, b) => bounds(a).top - bounds(b).top || bounds(a).left - bounds(b).left);
    const structureOrigin = this.inlineSceneActive ? null : this.flowRenderer?.structureOrigin;
    const root = selected ? bounds(selected) : structureOrigin || bounds(cards[0]);
    const scale = structureOrigin?.scale ?? .9;
    this.camera = { scale, x: 24 - root.left * scale, y: 48 - root.top * scale };
    this.transform();
  }
  /** Вмещает текущий набор с запасом для подписей; явная команда сохраняет общий обзор. */
  fit() {
    if (!this.positions?.size) return;
    const { minX, minY, maxX, maxY } = this.geometryBounds();
    const width = this.svg.clientWidth, height = this.svg.clientHeight;
    const scale = Math.max(.02, Math.min(1.6, (width - 70) / (maxX - minX), (height - 130) / (maxY - minY)));
    this.camera = { x: width / 2 - (minX + maxX) / 2 * scale, y: (height - 50) / 2 - (minY + maxY) / 2 * scale, scale };
    this.transform();
  }
}
