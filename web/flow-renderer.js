import { decorateModCard } from './mod-runtime.js';
import { flowEdgeKey } from './flow-layout.js';
import { nodeCardLayout } from './node-card-layout.js';
import { inlineFlowEdgeRouter } from './inline-flow-edges.js';
import { appendFlowCard, flowCardMetrics } from './flow-card.js';
import { nodeColor, groupColor, projectColor } from './colors.js';
import { edgeHighlighter } from './highlight.js';
import { classifyCodeNode } from './code-semantics.js';
import { resetSvgViewport } from './svg-viewport.js';
import { appendCodeReadingCard } from './code-reading-card.js';
import { appendAnnotationCard } from './annotation-card.js';
import { appendGroupOverviewCard } from './group-overview-card.js';
import { ARCHITECTURE_RESOURCE_FOOTER, decorateArchitectureResource, appendArchitectureTypeBadge } from './architecture-resource-style.js';
const NS = 'http://www.w3.org/2000/svg';
/** Создаёт безопасный SVG элемент карточного представления. */
function svgElement(tag, attributes, text) {
  const element = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  if (text !== undefined) element.textContent = text;
  return element;
}
/** Изолирует карточки и их оформление от существующего топологического renderer. */
export class FlowRenderer {
  /** Использует только публичные контейнеры и callbacks общего GraphView. */
  constructor(view) {
    this.view = view;
    this.cards = new Map();
  }
  /** Раскладывает неизменённые факты; карточки показывают статические зависимости, не трассу выполнения. */
  render({ mode, items, edges, selected = null }) {
    const view = this.view;
    const overview = mode === 'overview';
    const summaryBoard = items.length > 0 && items.every(item => item.groupSummary);
    const layout = nodeCardLayout(items, edges, { natural: view.groupingProvider === 'natural', reading: view.codeReading, viewportWidth: view.svg.clientWidth });
    const positions = new Map();
    const definitions = new Map(items.map(item => [String(item.id), item]));
    const counts = new Map();
    for (const [id, position] of layout.positions) {
      const item = definitions.get(String(id));
      const color = overview ? groupColor(item, view.colorProjects || []) : nodeColor(item, view.colorProjects || []);
      positions.set(String(id), { ...position, id: String(id), r: 0, color, codeProjectId: item.codeProjectId, labelWidth: position.width + 24, labelBottom: position.height / 2 + 16 });
      counts.set(String(id), { incoming: 0, outgoing: 0 });
    }
    resetSvgViewport(view);
    view.nodeElements.clear();
    view.communityElements.clear();
    view.installGeometry({ positions, containers: [] });
    if (view.architectureClusters?.length) {
      // Hook уже разместил кластеры. Старые ранги не должны уменьшать их масштаб при «Вместить».
      const points = [...positions.values()];
      const left = Math.min(...points.map(point => point.x - point.width / 2));
      const top = Math.min(...points.map(point => point.y - point.height / 2));
      const right = Math.max(...points.map(point => point.x + point.width / 2));
      const bottom = Math.max(...points.map(point => point.y + point.height / 2));
      // Сохраняем место для внешних каналов направленных связей.
      view.flowBounds = {x: left - 160, y: top - 160, width: right - left + 320, height: bottom - top + 320};
    } else view.flowBounds = layout.bounds;
    this.structureOrigin = layout.sections?.length ? { left: 0, top: 36, scale: layout.initialScale } : null;
    for (const section of layout.sections || []) {
      const title = svgElement('text', { x: section.x, y: section.y, class: 'code-structure-title',
        'data-structure-section': section.kind, 'aria-hidden': 'true' }, section.label);
      title.append(svgElement('title', {}, section.label));
      view.viewport.append(title);
    }
    for (const point of positions.values()) {
      if (!point.guide) continue;
      const { x, top, bottom } = point.guide;
      const left = point.x - point.width / 2;
      view.viewport.append(svgElement('path', { d: `M${x},${top} V${bottom} H${left}`,
        class: 'code-structure-guide', 'aria-hidden': 'true' }));
    }
    this.cards.clear();
    this.selected = selected;
    const lines = [];
    const orderedEdges = [...edges].sort((left, right) => {
      const key = edge => JSON.stringify([edge.source, edge.target, edge.relation || '', edge.id || '']);
      return key(left).localeCompare(key(right));
    });
    const routing = new Map();
    for (const [id, point] of positions) routing.set(id, { ...point, w: point.width, h: point.height });
    const route = inlineFlowEdgeRouter(routing);
    for (const [index, edge] of orderedEdges.entries()) {
      const source = positions.get(String(edge.source));
      const target = positions.get(String(edge.target));
      if (!source || !target) continue;
      const weight = overview ? Number(edge.weight) || 1 : 1;
      counts.get(source.id).outgoing += weight;
      counts.get(target.id).incoming += weight;
      const routed = route(routing.get(source.id), routing.get(target.id), index);
      const path = svgElement('path', { class: 'flow-edge', d: routed.d, fill: 'none', stroke: source.color, 'marker-end': view.sourceMarker(source.color, -2), 'data-source': edge.source, 'data-target': edge.target, 'data-edge-kind': layout.edgeKinds.get(flowEdgeKey(edge)) || 'forward' });
      if (summaryBoard) path.classList.toggle('group-summary-edge', true);
      if (edge.relation === 'dispatches_to') path.setAttribute('stroke-dasharray', '5 4');
      if (edge.system) {
        path.classList.add('system-edge');
        path.setAttribute('tabindex', 0);
        path.setAttribute('role', 'button');
        path.setAttribute('aria-label', `${definitions.get(source.id)?.label} → ${definitions.get(target.id)?.label}: ${edge.relation}, вес ${edge.weight}`);
        path.addEventListener('click', () => view.onSystemEdge?.(edge));
        path.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); view.onSystemEdge?.(edge); } });
      }
      const evidence = edge.relation === 'dispatches_to' || edge.evidence ? view.edgeEvidence(edge) : edge.relation || `${weight} статических связей`;
      path.append(svgElement('title', {}, `${definitions.get(source.id)?.label || source.id} → ${definitions.get(target.id)?.label || target.id}\n${evidence}`));
      view.viewport.append(path);
      if (edge.relation === 'dispatches_to') {
        view.viewport.append(svgElement('text', { x: (source.x + target.x) / 2, y: (source.y + target.y) / 2 - 8, class: 'path-edge-label', 'text-anchor': 'middle' }, 'Возможная реализация'));
      }
      lines.push({ line: path, source: source.id, target: target.id });
    }
    this.highlight = edgeHighlighter(lines, 'active');
    for (const item of items) {
      const id = String(item.id);
      const point = positions.get(id);
      if (!point) continue;
      const project = (view.colorProjects || []).find(owner => String(owner.id) === String(item.codeProjectId));
      const palette = projectColor(project?.label || 'Без проекта', { unassigned: !item.codeProjectId || item.codeProjectId === '__unassigned__' });
      const semantic = classifyCodeNode(item);
      const showSemantics = !item.system && (!overview || item.architecture);
      const accessibleLabel = `${item.label || id}. Входящих: ${counts.get(id).incoming}, исходящих: ${counts.get(id).outgoing}${point.cycleId ? '. Участвует в цикле зависимостей' : ''}.`;
      const group = svgElement('g', { class: 'flow-card', 'data-node-id': id, transform: `translate(${point.x},${point.y})`, role: 'button', tabindex: 0, 'aria-label': accessibleLabel, 'aria-pressed': id === selected });
      const structure = item.readingStructure;
      if (structure) {
        group.setAttribute('data-code-structure', structure.kind);
        const hierarchy = structure.kind === 'html' ? `. Уровень ${structure.depth + 1}${structure.parentLabel ? `, внутри ${structure.parentLabel}` : ''}` : `. ${structure.label}`;
        group.setAttribute('aria-label', accessibleLabel + hierarchy);
      }
      const resource = decorateArchitectureResource(group, item);
      if (resource) group.setAttribute('aria-label', `${resource.label}: ${accessibleLabel}`);
      const context = item.system && (item.repositoryName || item.repositoryNames?.length)
        ? item.repositoryName || `${item.repositoryNames.length} репозиториев`
        : project?.label || (item.system ? item.kindLabel : overview ? 'Смешанная группа' : 'Без проекта');
      const annotation = item.callableFacts?.kind === 'constructor' ? (item.callableFacts.modifiers?.includes('static') ? 'Статический конструктор · запуск типа' : 'Конструктор · создание объекта') : item.callableFacts?.kind === 'method' ? 'Метод · отдельный вызов' : item.system ? item.kindLabel : point.cycleId ? '↻ Цикл зависимостей' : overview ? `${item.nodeCount} узлов` : item.kind || 'Символ';
      const surface = { semantic, palette, color: point.color, counts: counts.get(id), showSemantics };
      if (item.groupSummary) appendGroupOverviewCard(group, item, { ...surface, width: point.width, height: point.height });
      else if (item.kind === 'attribute') appendAnnotationCard(group, item, { ...surface, width: point.width, height: point.height });
      else if (view.codeReading && !item.system) appendCodeReadingCard(group, item, { ...surface, width: point.width, height: point.height });
      else appendFlowCard(group, { width: point.width, height: point.height, title: item.displayLabel || item.label || id,
        complexity: item.complexity, health: item.health,
        context, note: resource ? '' : annotation, semantic, palette, color: point.color, counts: counts.get(id), showSemantics,
        footerHeight: resource ? ARCHITECTURE_RESOURCE_FOOTER : 0,
        documentation: item.system ? null : flowCardMetrics(item).documentation });
      if (resource) appendArchitectureTypeBadge(group, item, { x: -point.width / 2 + 13, y: point.height / 2 - 25 });
      group.append(svgElement('title', {}, `${item.label || id}\n${item.repositoryName || (item.repositoryNames || []).join(', ') || project?.label || ''}\n${item.file || ''}${overview && !item.system ? `\n${item.nodeCount} узлов` : ''}${point.cycleId ? '\nСильно связная компонента: цикл статических зависимостей' : ''}\nIN/OUT: показанные связи (с учётом лимита). Только чтение.`));
      const select = () => overview ? view.onCommunity(id) : view.onSelect(id);
      group.addEventListener('click', select);
      group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } });
      if (showSemantics && !summaryBoard && view.bindMapTooltip) {
        view.bindMapTooltip(group, item, counts.get(id), { raw: !overview, highlight: active => this.highlight(active ? id : this.selected) });
      } else {
        group.addEventListener('mouseenter', () => this.highlight(id));
        group.addEventListener('mouseleave', () => this.highlight(this.selected));
        group.addEventListener('focus', () => this.highlight(id));
        group.addEventListener('blur', () => this.highlight(this.selected));
      }
      view.decorateNodeActions?.(group, id, { left: -point.width / 2, right: point.width / 2, top: -point.height / 2,
        width: point.width, height: point.height });
      if(!overview) decorateModCard(view, group, item, {left:-point.width/2,top:-point.height/2,width:point.width,height:point.height});
      view.viewport.append(group);
      this.cards.set(id, { group, point });
      (overview ? view.communityElements : view.nodeElements).set(id, { group, radius: 0 });
      if (item.changedFile || item.changedNodeCount) this.outline(id, 'change-ring', 5);
    }
    this.setSelected(selected);
  }
  /** Добавляет отдельную прямоугольную обводку, сохраняя цвет карточки и портов. */
  outline(id, className, padding) {
    const card = this.cards.get(String(id));
    if (!card) return;
    card.group.append(svgElement('rect', { class: className, x: -card.point.width / 2 - padding, y: -card.point.height / 2 - padding, width: card.point.width + padding * 2, height: card.point.height + padding * 2, rx: 9, fill: 'none' }));
  }
  /** Выделяет карточку без изменения её координат, портов и камеры. */
  setSelected(id) {
    this.cards.get(String(this.selected))?.group.querySelector('.selection-ring')?.remove();
    this.cards.get(String(this.selected))?.group.setAttribute('aria-pressed', false);
    this.selected = id;
    if (id !== null) {
      this.cards.get(String(id))?.group.setAttribute('aria-pressed', true);
      this.outline(id, 'selection-ring', 3);
    }
    this.highlight?.(id);
  }
  /** Синяя обводка отражает те же raw-id совпадения, что и топологический вид. */
  setSearchMatches(ids, communityCounts = new Map()) {
    const matches = new Set(ids);
    let visible = 0;
    for (const [id, card] of this.cards) {
      card.group.querySelector('.regex-ring')?.remove();
      const count = this.view.nodeElements.has(id) ? Number(matches.has(id)) : communityCounts.get(id) || 0;
      if (!count) continue;
      visible += count;
      this.outline(id, 'regex-ring', 9);
    }
    return visible;
  }
}
