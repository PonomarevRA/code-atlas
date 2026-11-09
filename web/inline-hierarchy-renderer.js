import { decorateModCard } from './mod-runtime.js';
import { renderInlineMethodBody } from './inline-method-renderer.js';
import { appendNodeActions } from './node-actions.js';
import { classifyCodeNode, codeConnectionCounts } from './code-semantics.js';
import { nodeColor, groupColor, projectColor } from './colors.js';
import { inlineFlowEdgeRouter } from './inline-flow-edges.js';
import { appendFlowCard, flowCardMetrics } from './flow-card.js';
import { fieldOf, appendFieldRow } from './field-table.js';
import { connectionCurve } from './edge-curves.js';
import { appendSimpleMethod } from './sequence-card-symbols.js';
import { appendSequenceDefinition, appendProgressiveDetailsAction } from './method-sequence-renderer.js';
import { resetSvgViewport } from './svg-viewport.js';
import { appendCodeReadingCard, codeReadingMetrics } from './code-reading-card.js';
import { selectedSequenceCard } from './sequence-card-interactions.js';
import { appendAnnotationCard, annotationCardMetrics } from './annotation-card.js';

const NS = 'http://www.w3.org/2000/svg';
const KIND_LABELS = { project: 'Проект', file: 'Файл', community: 'Группа', raw: 'Символ' };

/** SVG строки являются только текстом; в описаниях проекта нельзя исполнять HTML. */
function svg(tag, attributes, text) {
  const node = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Добавляет aliases общего GraphView к top-left bounds, не меняя layout. */
function bounds(box) {
  return { ...box, left: box.x, top: box.y, right: box.x + box.width, bottom: box.y + box.height };
}

/** Цвет проекта использует тот же label, что и существующие Topology/Flow карточки. */
function paletteFor(item, projects) {
  const id = item?.node?.codeProjectId;
  const project = projects.find(candidate => String(candidate.id) === String(id));
  return projectColor(project?.label || id || item?.label || item?.id, { unassigned: item?.kind === 'raw' && !id });
}

/** Иерархия блоков и реальные связи рисуются разными слоями; renderer не управляет камерой или историей. */
export class InlineHierarchyRenderer {
  constructor(view) {
    this.view = view;
    this.cards = new Map();
  }

  /** Получает готовую scene/layout: исходные узлы, IDs и связи остаются неизменными. */
  render(scene, layout, selectedId = null) {
    const view = this.view;
    view.pathSceneActive = false;
    const focus = this.actionFocus();
    this.scene = scene;
    this.cards.clear();
    resetSvgViewport(view);
    view.nodeElements.clear();
    view.communityElements.clear();
    view.blockElements = new Map();
    view.mapTooltip?.hide?.(true);
    const items = new Map(scene.items.map(item => [item.id, item]));
    const positions = new Map();
    for (const [id, point] of layout.positions) {
      const item = items.get(id);
      const box = bounds(point.cardBounds);
      const header = bounds(point.headerBounds);
      // Действия находятся внутри заголовка, как на обычной Flow-карточке.
      const right = box.right;
      positions.set(id, { ...point, id, r: 0, color: (item?.kind === 'raw' ? nodeColor : groupColor)(item?.node || {}, view.colorProjects || []),
        headerBounds: header, panelBounds: box, cardBounds: { ...box, right, width: right - box.x } });
    }
    view.installGeometry({ positions, containers: [] });
    // Flow-петли используют внешний канал до 118px: fit должен включать его, как обычная карта.
    if (layout.bounds) view.flowBounds = { x: layout.bounds.x - 160, y: layout.bounds.y - 160,
      width: layout.bounds.width + 320, height: layout.bounds.height + 320 };
    const frames = [...layout.frames].sort((left, right) => left.depth - right.depth);
    this.visibleFrames = new Set(frames.map(frame => frame.id));
    for (const frame of frames) this.renderFrame(frame, items.get(frame.id));
    // Outer references and local call edges share all disclosed card obstacles.
    view.progressiveDetails = new Map();
    view.sequenceInspectionIds = new Set();
    view.progressiveAreas = new Map();
    for (const [id, placement] of layout.methodBodies || []) {
      if (placement.scene.detached) view.progressiveDetails.set(id, placement.scene.rootId);
      for (const item of placement.scene.expansions || []) {
        view.progressiveDetails.set(item.occurrenceId, item.entryId);
        view.progressiveAreas.set(item.occurrenceId, { ...item.bounds, kind: item.kind,
          x: item.bounds.x + placement.x, y: item.bounds.y + placement.y });
      }
    }
    view.inlineDetailPositions = new Map([...positions].map(([id, point]) => [id, { ...point,
      w: point.headerBounds.width, h: point.headerBounds.height }]));
    for (const [, placement] of layout.methodBodies || []) for (const node of placement.scene.content) {
      view.inlineDetailPositions.set(node.id, { ...node, x: node.x + placement.x, y: node.y + placement.y });
    }
    this.renderEdges(scene.edges, positions, items);
    const counts = codeConnectionCounts(scene.edges);
    for (const item of scene.items) {
      const point = positions.get(item.id);
      if (point) this.renderCard(item, point, counts.get(String(item.id)) || { incoming: 0, outgoing: 0 });
    }
    for (const [id, placement] of layout.methodBodies || []) renderInlineMethodBody(view, items.get(id), placement);
    this.setSelected(selectedId);
    this.restoreActionFocus(focus);
    return this;
  }

  /** Одна рамка принадлежит одному раскрытому контейнеру; она не является ребром вызова. */
  renderFrame(frame, item) {
    const box = frame.cardBounds || frame;
    const palette = paletteFor(item, this.view.colorProjects || []);
    const group = svg('g', { class: 'inline-hierarchy-frame', 'data-owner-id': frame.id, 'data-parent-id': frame.parentId || '',
      role: 'group', 'aria-label': `Содержимое: ${item?.label || frame.id}` });
    group.append(svg('rect', { x: box.x, y: box.y, width: box.width, height: box.height, rx: 12,
      fill: palette.fill, stroke: palette.stroke, 'fill-opacity': .45 }));
    const header = frame.headerBounds || this.view.positions.get(frame.id)?.headerBounds || box;
    group.append(svg('text', { x: box.x + 12, y: header.y + header.height + 21,
      class: 'flow-card-note' }, `${this.scene.methodBodies?.has(item?.id) ? "Тело метода · условия, вызовы и данные" : `Внутренние блоки · ${item?.visibleChildren || 0}/${item?.totalChildren || 0}`}${item?.childrenTruncated ? ' · лимит' : ''}`));
    // Method details already carry their own frame caption.
    if (this.view.codeReading && this.scene.methodBodies?.has(item?.id)) group.querySelector('.flow-card-note')?.remove();
    if (frame.table) {
      for (const [label, offset] of [['Поле', 18], ['Тип', 263], ['Nullable', 542]]) {
        group.append(svg('text', { x: box.x + 32 + offset, y: header.y + header.height + 30,
          class: 'field-table-heading' }, label));
      }
      group.querySelector('.flow-card-note')?.setAttribute('visibility', 'hidden');
    }
    this.view.viewport.append(group);
  }

  /** Боковые OUT→IN повторяют Flow карты и сохраняют relation/weight/confidence, включая кандидатов. */
  renderEdges(edges, positions, items) {
    this.edges = [];
    const routing = new Map(), obstacles = new Map();
    for (const [id, point] of positions) {
      const panel = point.panelBounds, header = point.headerBounds;
      // Reference edges leave the expanded panel at its header level, not through the method body.
      routing.set(id, { ...point, panelIndex: undefined, x: panel.left + panel.width / 2, y: header.top + header.height / 2, w: panel.width, h: 0 });
      const container = this.visibleFrames.has(id) && !this.scene.methodBodies?.has(id);
      const box = container ? header : panel;
      obstacles.set(id, { x: box.left + box.width / 2, y: box.top + box.height / 2, w: box.width, h: box.height });
    }
    for (const [id, point] of this.view.inlineDetailPositions || []) if (!positions.has(id)) obstacles.set(id, point);
    const route = inlineFlowEdgeRouter(routing, { obstacles });
    for (const [index, edge] of edges.entries()) {
      const source = positions.get(edge.source), target = positions.get(edge.target);
      if (!source || !target) continue;
      const routed = route(routing.get(edge.source), routing.get(edge.target), index);
      const sourcePort = { x: source.headerBounds.right, y: source.headerBounds.top + source.headerBounds.height / 2 };
      const sourceBoundary = { x: source.panelBounds.right, y: sourcePort.y };
      const targetPort = { x: target.headerBounds.left, y: target.headerBounds.top + target.headerBounds.height / 2 };
      const targetBoundary = { x: target.panelBounds.left, y: targetPort.y };
      let pathData = routed.d;
      if (pathData && sourcePort.x !== sourceBoundary.x) pathData = connectionCurve(sourcePort, sourceBoundary) + pathData.replace(/^M\s+[-+.\deE]+\s+[-+.\deE]+\s*/, '');
      if (pathData && targetPort.x !== targetBoundary.x) pathData += connectionCurve(targetBoundary, targetPort).replace(/^M\s+[-+.\deE]+\s+[-+.\deE]+\s*/, '');
      const candidate = edge.candidate || edge.relation === 'dispatches_to' || /candidate|inferred/i.test(String(edge.confidence || ''));
      const path = svg('path', { class: `inline-hierarchy-edge${candidate ? ' candidate' : ''}`, d: pathData, fill: 'none',
        stroke: source.color, 'marker-end': this.view.sourceMarker(source.color, -2),
        'data-source': edge.source, 'data-target': edge.target, 'data-relation': edge.relation || '',
        'data-weight': edge.weight ?? edge.count ?? 1, 'data-confidence': edge.confidence || '', 'data-candidate': String(Boolean(candidate)) });
      if (candidate) path.setAttribute('stroke-dasharray', '5 4');
      if (['reads', 'writes', 'initializes_from'].includes(edge.relation)) path.setAttribute('opacity', '.5');
      const evidence = this.view.edgeEvidence?.(edge) || edge.relation || 'Статическая связь';
      path.append(svg('title', {}, `${items.get(edge.source)?.label || edge.source} → ${items.get(edge.target)?.label || edge.target}\n${evidence}\nВес: ${edge.weight ?? edge.count ?? 1}. Статическая связь, не порядок исполнения.`));
      this.view.viewport.append(path);
      this.edges.push({ path, source: edge.source, target: edge.target });
    }
    this.renderContainment(items, routing, route, edges);
  }

  /** Пунктир показывает принадлежность контейнеру из scene, не добавляя вызовов или весов в граф. */
  renderContainment(items, positions, route, edges) {
    const explicit = new Set(edges.map(edge => JSON.stringify([edge.source, edge.target])));
    for (const item of items.values()) {
      if (!item.parentId || explicit.has(JSON.stringify([item.parentId, item.id]))) continue;
      if (this.visibleFrames.has(item.parentId)) continue; // The visible frame already shows containment without another long arrow.
      if (fieldOf(item)) continue; // Принадлежность строки показывает общая рамка; реальные рёбра рисуются выше.
      const source = positions.get(item.parentId), target = positions.get(item.id);
      if (!source || !target) continue;
      const path = svg('path', { class: 'inline-hierarchy-containment', d: route(source, target, 0).d,
        fill: 'none', stroke: source.color, 'stroke-dasharray': '2 6',
        'data-source': item.parentId, 'data-target': item.id, 'data-relation': 'display-containment' });
      path.append(svg('title', {}, `${items.get(item.parentId)?.label} содержит ${item.label}. Принадлежность блоку; не вызов метода.`));
      this.view.viewport.append(path);
    }
  }

  /** Выбор заголовка не входит внутрь и не раскрывает: эти действия выполняют отдельные кнопки. */
  renderCard(item, point, counts) {
    const view = this.view;
    const header = point.headerBounds;
    const node = item.node || {};
    const semantic = classifyCodeNode(node);
    const palette = paletteFor(item, view.colorProjects || []);
    const label = String(item.label || node.label || item.id);
    const kindLabel = KIND_LABELS[item.kind] || 'Блок';
    const group = svg('g', { class: 'inline-hierarchy-card', 'data-node-id': item.id, 'data-raw-id': item.rawId || '',
      'data-kind': item.kind, 'data-code-role': semantic.id, transform: `translate(${point.x},${point.y})`,
      tabindex: 0, role: 'button', 'aria-label': `${kindLabel}: ${label}. Выбор блока.`, 'aria-pressed': 'false' });
    const left = header.left - point.x, top = header.top - point.y;
    const status = item.totalChildren ? `Показано ${item.visibleChildren} из ${item.totalChildren} блоков · ${item.totalMembers} символов` : `IN ${counts.incoming} · OUT ${counts.outgoing}`;
    const project = (view.colorProjects || []).find(owner => String(owner.id) === String(node.codeProjectId));
    const displayLabel = node.displayLabel || (item.kind === 'file' ? String(item.file || label).split(/[\\/]/).pop() : label);
    const callable = node.callableFacts;
    const note = callable?.kind === 'constructor' ? (callable.modifiers?.includes('static') ? 'Статический конструктор · запуск типа' : 'Конструктор · при создании объекта') : callable?.kind === 'method' ? 'Метод · при отдельном вызове' : point.cycleId ? '↻ Цикл зависимостей' : item.kind === 'raw' ? node.kind || 'Символ' : `${item.totalMembers} узлов`;
    const readingNode = { ...node, readingKind: item.kind, label: displayLabel,
      ...(item.kind !== 'raw' ? { nodeCount: item.totalMembers } : {}) };
    const annotation = node.kind === 'attribute';
    const metrics = annotation ? annotationCardMetrics(header.width) : view.codeReading ? codeReadingMetrics(readingNode, header.width) : flowCardMetrics(node, header.width);
    const body = this.scene.methodBodies?.get(item.id);
    const simple = body?.inlineSimple && body.byId.get(item.id);
    if (simple) {
      const display = { ...simple, w: header.width, h: header.height };
      appendSimpleMethod(view, group, display, true);
      appendSequenceDefinition(view, group, display, { w: header.width, h: header.height }, true);
    } else if (annotation) appendAnnotationCard(group, node, { width: header.width, height: header.height, left, top, semantic, palette, color: point.color });
    else if (fieldOf(item) && item.parentId) {
      appendFieldRow(group, { item, width: header.width, height: header.height, left, top, color: point.color, counts });
    } else if (view.codeReading) appendCodeReadingCard(group, readingNode, { width: header.width, height: header.height,
      left, top, semantic, palette, color: point.color, counts, bodyClass: 'inline-hierarchy-card-body' });
    else appendFlowCard(group, { width: header.width, height: header.height, left, top,
      complexity: node.complexity,
      title: displayLabel, context: project?.label || 'Без проекта', note,
      semantic, palette, color: point.color, counts, bodyClass: 'inline-hierarchy-card-body',
      // У компактных заголовков Topology сохраняем размеры; полное пояснение остаётся в подсказке.
      documentation: header.height >= metrics.height ? metrics.documentation : null });
    group.append(svg('title', {}, `${kindLabel}: ${label}\n${node.file || ''}\n${metrics.documentation.text}\n${semantic.evidence}\n${status}${item.childrenTruncated ? '\nДочерние блоки ограничены лимитом.' : ''}\nИерархия исходников, не порядок исполнения.`));
    const select = () => {
      if (view.onBlockSelect) view.onBlockSelect(item.id);
      else if (item.rawId) view.onSelect?.(item.rawId);
      else view.onCommunity?.(item.id);
    };
    group.addEventListener('click', select);
    group.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      if (!event.repeat) select();
    });
    appendProgressiveDetailsAction(view, group, { ...item.node, id: item.id, label, w: header.width, h: header.height }, true);
    if (view.codeReading && body && !simple) group.querySelector('.path-card-details')?.setAttribute('y', top + header.height - 54);
    if (item.bodyLimit) group.append(svg('text', { x: -point.headerBounds.width / 2 + 12,
      y: point.headerBounds.height / 2 - 18, class: 'flow-card-note' }, item.bodyLimit));
    const actions = appendNodeActions(group, { id: item.id, label, expanded: item.expanded,
      x: header.right - point.x - 48, y: top + 16,
      enterLabel: view.canReadBlock?.(item) ? `Просмотреть реализацию: ${label}` : `Войти в блок: ${label}`,
      enter: item.enter && view.onEnterBlock && !annotation && !(view.codeReading && simple) ? id => view.onEnterBlock(id) : null,
      onToggle: (item.totalChildren || view.canReadBlock?.(item)) && view.onToggleBlock ? id => view.onToggleBlock(id) : null,
      reading: (view.codeReading || annotation) && !(fieldOf(item) && item.parentId) ? { left, top, width: header.width, height: header.height, compact: annotation,
        enterText: item.rawId ? 'Схема →' : 'Внутрь →', sourceId: item.rawId,
        source: !simple && item.rawId && node.file ? id => view.onPathSource?.(id, node.line) : null } : null });
    const card = { group, point, item, actions, radius: 0, card: { width: header.width, height: header.height } };
    this.cards.set(item.id, card);
    view.blockElements.set(item.id, card);
    (item.rawId ? view.nodeElements : view.communityElements).set(item.rawId || item.id, card);
    if (item.rawId && view.bindMapTooltip) view.bindMapTooltip(group, node, counts, { raw: true });
    if (item.rawId) decorateModCard(view, group, { ...node, id: item.id, definitionId: item.rawId }, {left,top,width:header.width,height:header.height});
    view.viewport.append(group);
  }

  /** Скрытый raw ID подсвечивает ближайшего видимого владельца; выбранный ID и раскладка сохраняются. */
  setSelected(id) {
    this.selectedId = id;
    const visibleId = this.cards.has(id) ? id : this.scene?.membership?.get(id) ?? id;
    const selectedCard = selectedSequenceCard(this.view, id);
    const selectedStep = selectedCard !== id;
    for (const [blockId, card] of this.cards) {
      const selected = !selectedStep && id != null && (blockId === visibleId || card.item.rawId === id);
      card.group.classList.toggle('selected', selected);
      card.group.setAttribute('aria-pressed', String(selected));
    }
    for (const edge of this.edges || []) edge.path.classList.toggle('active', edge.source === visibleId || edge.target === visibleId);
    for (const [occurrenceId, item] of this.view.nodeElements) if (item.inspectionId) {
      const selected = id != null && occurrenceId === selectedCard;
      item.group.classList.toggle('selected', selected);
      item.group.setAttribute('aria-pressed', String(selected));
    }
  }

  /** Aggregates подсвечиваются по реальным memberIds, не по сходству имени или цвета. */
  setSearchMatches(ids) {
    const matches = new Set(ids);
    const visible = new Set();
    for (const card of this.cards.values()) {
      const matched = [];
      for (const id of card.item.memberIds || (card.item.rawId ? [card.item.rawId] : [])) {
        if (matches.has(id)) { matched.push(id); visible.add(id); }
      }
      card.group.classList.toggle('match', matched.length > 0);
      card.group.setAttribute('data-match-count', matched.length);
    }
    return visible.size;
  }

  /** Фокус action хранит только IDs и восстанавливается после relayout, если действие ещё существует. */
  actionFocus() {
    const node = document.activeElement;
    const id = node?.getAttribute?.('data-owner-id');
    const action = node?.getAttribute?.('data-node-action');
    return this.cards.has(id) && action ? { id, action } : null;
  }

  /** Выбор карточки/camera не меняется при возврате keyboard focus к новому action DOM. */
  restoreActionFocus(focus) {
    if (!focus) return;
    const actions = this.cards.get(focus.id)?.actions;
    const node = focus.action === 'enter' ? actions?.enter : actions?.toggle;
    node?.focus?.({ preventScroll: true });
  }
}
