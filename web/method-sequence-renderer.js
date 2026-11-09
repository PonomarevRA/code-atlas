import { decorateModCard } from './mod-runtime.js';
import { resetSvgViewport } from './svg-viewport.js';
import { revealProgressiveDetails, focusProgressiveDetails, restoreProgressiveFocus } from './progressive-navigation.js';
import { bindSequenceInspection, sequenceInspectionId, selectedSequenceCard, appendSequenceSource, bindSequenceAction } from './sequence-card-interactions.js';
import { appendFlowCard } from './flow-card.js';
import { connectionCurve } from './edge-curves.js';
import { capturePathTransition, animatePathTransition } from './path-transition.js';
import { internalBlockEdge } from './method-sequence-blocks.js';
import { sequenceEdgeRouter, includeRouteBounds } from './method-sequence-routing.js';
import { wrapDiagramText } from './diagram-text.js';
import { sequencePresentation } from './sequence-presentation.js';
import { appendCodeText } from './code-text.js';
import { appendValueChoice, appendSimpleMethod, appendSequenceEntry, appendLoopControl, appendLoopUpdate } from './sequence-card-symbols.js';
import { appendComplexityBadge } from './complexity-badge.js';
import { sequenceSurfaceOptions, sequenceActionPoint, sequenceRowGeometry } from './sequence-card-layout.js';

/** Safe SVG text and accessible actions shared by sequence frames and occurrence cards. */
function svg(tag, attributes, text) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Transitions are compact diagram symbols, not executable methods or source locations. */
export function appendSequenceTransition(group, node, centered = false) {
  const left = centered ? -node.w / 2 : 0, top = centered ? -node.h / 2 : 0;
  group.classList.toggle('method-sequence-transition', true);
  group.setAttribute('data-transition-kind', node.kind);
  if (node.kind !== 'await') group.setAttribute('style', 'cursor:default');
  const explanation = node.explanation || {
    await: 'Ожидание результата await. После завершения продолжить со следующего шага этого метода.',
    resume: node.uncertain ? 'Нормальный возврат из раскрытого тела не установлен. Подробности в исходнике.' : 'Вызванный метод завершён: вернуться к следующему шагу вызывающего метода.',
    'method-throw': 'Метод завершён исключением; обычное продолжение не установлено.',
  }[node.kind] || node.label;
  group.setAttribute('aria-label', `${node.stepNumber ? node.stepNumber + ' · ' : ''}${node.label}. ${explanation}`);
  const waiting = node.kind === 'await', uncertain = node.uncertain || node.kind === 'method-throw';
  if (node.kind === 'join') {
    group.append(svg('circle', { cx: left + node.w / 2, cy: top + node.h / 2, r: node.w / 2 - 1,
      fill: '#eef4ef', stroke: '#8ba599' }));
    group.append(svg('text', { x: left + node.w / 2, y: top + node.h / 2 + 5, 'text-anchor': 'middle',
      'font-size': 18, fill: '#3d6252' }, '→'));
    group.append(svg('title', {}, explanation));
    return;
  }
  group.append(svg('rect', { x: left, y: top, width: node.w, height: node.h, rx: node.h / 2,
    fill: waiting ? '#e7f1f7' : '#eef4ef', stroke: uncertain ? '#ab7a35' : waiting ? '#749bb4' : '#8ba599',
    ...(uncertain ? { 'stroke-dasharray': '4 3' } : {}) }));
  const label = `${waiting ? '◷ ' : '→ '}${node.label}`;
  group.append(svg('text', { x: left + node.w / 2, y: top + node.h / 2 + 4, 'text-anchor': 'middle',
    'font-size': 12, 'font-weight': 550, fill: waiting ? '#345d78' : '#3d6252' },
    label.length > 38 ? label.slice(0, 37) + '…' : label));
  group.append(svg('title', {}, explanation));
}

/** One source row keeps its source/definition/expansion actions within a shared code block. */
export function appendSequenceRow(group, node, centered = false) {
  const left = centered ? -node.w / 2 : 0, top = centered ? -node.h / 2 : 0;
  group.classList.toggle('method-sequence-row', true);
  group.setAttribute('data-line-block', node.blockId);
  // A shared block paints its background outside this row; keep blank-space clicks on this occurrence.
  group.append(svg('rect', { x: left, y: top, width: node.w, height: node.h,
    fill: 'transparent', 'pointer-events': 'all', class: 'method-row-hit-target' }));
  if (node.compactSource && !node.compactRow) {
    group.append(svg('rect', { x: left, y: top, width: node.w, height: node.h, rx: 8, fill: '#fbfcfa', stroke: '#71988b', class: 'mod-card-body' }));
    for (const x of [left, left + node.w]) group.append(svg('circle', { cx: x, cy: top + node.h / 2, r: 4, fill: '#71988b' }));
  }
  if (node.rowIndex) group.append(svg('line', { x1: left + 8, x2: left + node.w - 8, y1: top, y2: top, stroke: '#dce7df' }));
  if (node.kind === 'await') group.append(svg('rect', { x: left + 6, y: top + 6, width: node.w - 12, height: node.h - 12, rx: 5, fill: '#e7f1f7' }));
  const { lines, inset, annotation, annotationY, lineY } = sequenceRowGeometry(node);
  for (const [row, text] of lines.entries()) {
    appendCodeText(group, { x: left + inset, y: top + 20 + row * 17, 'font-size': 12, 'font-family': 'monospace' }, text);
  }
  appendComplexityBadge(group, node.complexity, left + 24, top + 20, node.health);
  if (annotation) group.append(svg('text', { x: left + 12, y: top + annotationY, 'font-size': 11, fill: '#496858', class: 'method-row-annotation' },
    annotation.length > 36 ? annotation.slice(0, 35) + '…' : annotation));
  group.append(svg('text', { x: left + 12, y: top + lineY, 'font-size': 9, fill: '#758a7e' }, `Строка ${node.line || '?'}`));
}

/** An explicit definition action keeps the called symbol separate from its caller's call-site line. */
export function appendSequenceDefinition(view, group, node, point, centered = false) {
  if (!node.definitionId || node.transition) return;
  const action = svg('text', { x: (centered ? -point.w / 2 : 0) + 12,
    y: (centered ? point.h / 2 : point.h) - 10, class: 'path-card-source', role: 'button', tabindex: 0,
    'aria-label': `Открыть определение ${node.label}`, 'data-definition-id': node.definitionId }, node.definitionLabel || 'Открыть код ↗');
  bindSequenceAction(view, group, action, node,
    () => (centered ? view.onInlineSource : view.onPathSource)?.(node.definitionId, node.definitionLine),
    'Открыть код выбранного вызываемого метода.');
}

/** Reading source-like rows is a separate action from revealing diagram cards here. */
export function appendMethodReadAction(view, group, node, centered = false) {
  if (!node.canExpand || !view.onReadCall) return;
  const action = svg('text', { ...sequenceActionPoint(node, 'read', centered),
    'font-size': 10, class: 'path-card-read', role: 'button', tabindex: 0,
    'aria-label': `Прочитать объяснение вызова ${node.label}` }, 'Объяснение');
  bindSequenceAction(view, group, action, node, () => view.onReadCall(node, action),
    'Открыть объяснение; карта и место вызова сохранятся.');
}

/** An explicit rightward jump is available when the caller and its details cannot both fit. */
export function appendProgressiveDetailsAction(view, group, node, centered = false) {
  if (!view.progressiveDetails?.has(node.id)) return;
  const focused = view.readableFocusTrail?.at(-1)?.occurrenceId === node.id;
  const initialLabel = focused ? 'Вернуться к масштабу' : 'Прочитать этот блок';
  const action = svg('text', { ...sequenceActionPoint(node, 'focus', centered),
    'font-size': 10, class: 'path-card-details', role: 'button', tabindex: 0,
    'aria-label': `${initialLabel}: ${node.label}` });
  const label = svg('tspan', {}, initialLabel); action.append(label);
  bindSequenceAction(view, group, action, node, () => {
    const restored = restoreProgressiveFocus(view, node.id);
    const focused = !restored && focusProgressiveDetails(view, node.id);
    if (!restored && !focused) return;
    label.textContent = restored ? 'Прочитать этот блок' : 'Вернуться к масштабу';
    action.setAttribute('aria-label', label.textContent);
  }, 'Читаемый масштаб этого блока; повторное действие вернёт камеру.');
}

/** A visible count opens the complete bounded name list without changing the scene or camera. */
export function appendImplementationAction(view, group, node, centered = false) {
  if (!(node.candidateCount > 1) || !view.onImplementationList) return;
  if (node.compactRow) group.querySelector('.method-row-annotation')?.setAttribute('visibility', 'hidden');
  const left = centered ? -node.w / 2 : 0, top = centered ? -node.h / 2 : 0;
  const count = `${node.candidateCountExact === false ? '≥ ' : ''}${node.candidateCount}`;
  const label = `Реализации: ${count}`;
  const action = svg('text', { x: left + 12, y: top + (node.compactRow ? 54 : node.h - 54),
    role: 'button', tabindex: 0, 'font-size': 11, fill: '#236b78', 'text-decoration': 'underline',
    'aria-label': `Показать реализации ${node.label}: ${node.candidateCount}`, class: 'method-implementation-list' }, label);
  action.append(svg('title', {}, (node.implementationItems || []).map(item => item.label).join('\n')));
  bindSequenceAction(view, group, action, node, () => view.onImplementationList(node, action),
    'Сравнить кандидатов; это возможные реализации вызова.');
  const items = node.implementationItems || [];
  if (!node.implementationListLimited && items.length && items.every(item => item.scope)) {
    const counts = { source: 0, delegate: 0, tests: 0 };
    for (const item of items) counts[item.scope]++;
    const groups = [];
    if (counts.source) groups.push(`${counts.source} в проекте`);
    if (counts.delegate) groups.push(`${counts.delegate} переадресует`);
    if (counts.tests) groups.push(`${counts.tests} в тестах`);
    group.append(svg('text', { x: left + 12, y: top + (node.compactRow ? 68 : node.h - 40),
      'font-size': 9, fill: '#607b6e', class: 'method-implementation-summary' }, groups.join(' · ')));
  }
}

/** Draw nested frame boundaries before their cards; inline callers can reuse the same layer. */
export function appendSequenceFrames(view, scene, dx = 0, dy = 0, exclude = null) {
  for (const frame of scene.layout.frames) {
    if (frame.simple) continue;
    if (frame.id === exclude) {
      appendDataHeading(view.viewport, frame, dx, dy);
      continue;
    }
    const group = svg('g', { class: 'path-method-frame method-sequence-frame', 'data-method-id': frame.sourceOwnerId });
    group.append(svg('rect', { x: frame.x + dx, y: frame.y + dy, width: frame.w, height: frame.h, rx: 14,
      fill: '#f3f8f4', 'fill-opacity': '.65', stroke: '#8cad9c' }));
    const role = frame.callableKind === 'constructor' ? 'Конструктор' : 'Метод';
    const heading = wrapDiagramText(`${role}: ${frame.label}`, Math.max(8, Math.floor((frame.w - 48) / 7)), 1).lines[0];
    group.append(svg('text', { x: frame.x + dx + 24, y: frame.y + dy + 18,
      class: 'method-sequence-frame-label', 'font-size': 11, fill: '#496858' }, heading));
    appendDataHeading(group, frame, dx, dy);
    group.append(svg('title', {}, `${frame.label} · локальная последовательность`));
    view.viewport.append(group);
  }
  for (const block of scene.layout.lineBlocks || []) {
    const group = svg('g', { class: 'method-sequence-code-block', 'data-block-id': block.id });
    group.append(svg('rect', { x: block.x + dx, y: block.y + dy, width: block.w, height: block.h,
      rx: 8, fill: '#fbfcfa', stroke: '#5f7d70' }));
    group.append(svg('rect', { x: block.x + dx + 1, y: block.y + dy + 1, width: 4, height: block.h - 2,
      rx: 2, fill: '#5f7d70' }));
    group.append(svg('title', {}, 'Последовательные строки одного метода. Ветви и раскрытые вызовы показаны отдельно.'));
    view.viewport.append(group);
  }
}

/** Inline owners already have a frame; their field-count heading still belongs to the body. */
function appendDataHeading(group, frame, dx, dy) {
  const infrastructure = frame.infrastructureSection;
  if (infrastructure) group.append(svg('text', { x: frame.x + dx + infrastructure.x, y: frame.y + dy + infrastructure.y,
    'font-size': 11, fill: '#496858' }, `Ресурсы · показано ${infrastructure.shown} из ${infrastructure.total} · статический анализ`));
  const section = frame.dataSection;
  if (!section) return;
  group.append(svg('text', { x: frame.x + dx + section.x, y: frame.y + dy + section.y,
    class: 'method-sequence-data-heading', 'font-size': 11, fill: '#496858' },
    `Данные метода · показано ${section.shown} из ${section.total} полей`));
}

/** A smooth next-step curve uses vertical ports for wrapped rows and back edges. */
export function sequenceCurve(a, b, edge = {}) {
  if (edge.downwardJoin) return connectionCurve({ x: a.x, y: a.y + a.h / 2 },
    { x: b.x, y: b.y - b.h / 2 }, { axis: 'vertical' });
  if (edge.bypass && Number.isFinite(edge.bypassOffset)) {
    const fromY = a.y + a.h / 2, toY = b.y + b.h / 2, corridor = a.y + edge.bypassOffset;
    const radius = Math.min(24, Math.max(0, (b.x - a.x) / 6));
    return `M ${a.x} ${fromY} C ${a.x} ${fromY + (corridor - fromY) / 3} ${a.x} ${corridor} ${a.x + radius} ${corridor}`
      + ` L ${b.x - radius} ${corridor} C ${b.x} ${corridor} ${b.x} ${toY + (corridor - toY) / 3} ${b.x} ${toY}`;
  }
  if (b.x - b.w / 2 >= a.x + a.w / 2)
    return connectionCurve({ x: a.x + a.w / 2, y: a.y }, { x: b.x - b.w / 2, y: b.y });
  const down = b.y >= a.y;
  return connectionCurve({ x: a.x, y: a.y + (down ? a.h / 2 : -a.h / 2) },
    { x: b.x, y: b.y + (down ? -b.h / 2 : b.h / 2) }, { axis: 'vertical' });
}

/** Different symbol widths need port-based labels; long guards sit above their branch's first step. */
export function sequenceCaption(a, b, caption, edge = {}) {
  if (edge.bypass && Number.isFinite(edge.bypassOffset)) return { x: (a.x + b.x) / 2, y: a.y + edge.bypassOffset - 10 };
  if (b.x - b.w / 2 >= a.x + a.w / 2) {
    if (caption.length > 14) return { x: b.x, y: b.y - b.h / 2 - 12 };
    return { x: (a.x + a.w / 2 + b.x - b.w / 2) / 2, y: (a.y + b.y) / 2 - 10 };
  }
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 10 };
}

/** Expand cards on this diagram, preserving the clicked occurrence and camera scale. */
function toggle(view, id) {
  const before = capturePathTransition(view), anchor = before.get(id), camera = { ...view.camera };
  const node = view.pathSequenceScene?.byId.get(id);
  if (node && !view.pathExpandedIds.has(id)) for (const targetId of node.readTargetIds || []) view.pathMethodFacts?.retry(targetId);
  if (node?.kind === 'candidate' && !view.pathExpandedIds.has(id)) {
    for (const other of view.pathSequenceScene.visuals) if (other.kind === 'candidate' && other.ownerId === node.ownerId) view.pathExpandedIds.delete(other.id);
  }
  if (view.pathExpandedIds.has(id)) view.pathExpandedIds.delete(id); else view.pathExpandedIds.add(id);
  view.renderPathCards(view.pathData.nodes, view.pathData.edges, view.selectedId);
  const next = view.positions.get(id);
  view.camera = camera;
  if (anchor && next) { view.camera.x += (anchor.x - next.x) * camera.scale; view.camera.y += (anchor.y - next.y) * camera.scale; }
  view.transform();
  animatePathTransition(view, before, anchor && next ? { x: anchor.x - next.x, y: anchor.y - next.y } : undefined);
  revealProgressiveDetails(view, id, true);
  view.nodeElements.get(id)?.group.querySelector('.path-card-expand')?.focus?.({ preventScroll: true });
}

/** Draw a source-derived scene independently of grouping-provider geometry. */
export function renderMethodSequence(view, scene, selected) {
  resetSvgViewport(view); view.nodeElements.clear(); view.communityElements.clear();
  view.markerColors = new Map(); view.edgeDefinitions = svg('defs', {}); view.viewport.append(view.edgeDefinitions);
  view.progressiveDetails = new Map((scene.expansions || []).map(item => [item.occurrenceId, item.entryId]));
  view.sequenceInspectionIds = new Set();
  view.progressiveAreas = new Map((scene.expansions || []).map(item => [item.occurrenceId, { ...item.bounds, kind: item.kind }]));
  view.positions = new Map(scene.positions); view.pathFlatFlow = true; view.pathSequenceScene = scene;
  view.pathMethodNumbers = new Map(); view.pathEdgeElements = []; view.pathFrameElements = new Map();
  view.pathReferenceOwners = new Map(); view.pathPanelOwners = new Map();
  view.flowBounds = scene.bounds; view.containers = []; view.ingressPositions = [];
  appendSequenceFrames(view, scene);
  const route = sequenceEdgeRouter(scene, scene.positions, { curve: sequenceCurve });
  for (const edge of scene.visualEdges) {
    if (internalBlockEdge(scene.byId.get(edge.source), scene.byId.get(edge.target), edge)) continue;
    const a = scene.positions.get(edge.source), b = scene.positions.get(edge.target);
    if (!a || !b) continue;
    const routed = route(edge);
    const uncertain = ['unknown', 'exception', 'context', 'candidate'].includes(edge.sequenceKind);
    const path = svg('path', { class: 'path-edge method-sequence-edge', d: routed.d, fill: 'none',
      stroke: edge.sequenceKind === 'return' ? '#367b99' : '#5f7d70', 'marker-end': view.sourceMarker('#5f7d70', -2),
      'data-source': edge.source, 'data-target': edge.target, 'data-sequence-kind': edge.sequenceKind });
    if (uncertain || routed.data) path.setAttribute('stroke-dasharray', '5 4');
    if (routed.data) path.setAttribute('opacity', '.5');
    path.append(svg('title', {}, `${edge.caption} · статическая структура исходника`)); view.viewport.append(path);
    if (!routed.caption) continue;
    const label = svg('text', { x: routed.caption.x, y: routed.caption.y, class: 'path-edge-label', 'text-anchor': 'middle' }, routed.caption.text);
    label.append(svg('title', {}, edge.caption));
    view.viewport.append(label);
  }
  const extent = route.bounds();
  view.flowBounds = includeRouteBounds(scene.bounds, `M ${extent.x} ${extent.y} L ${extent.x + extent.width} ${extent.y + extent.height}`);
  for (const node of scene.visuals) {
    const point = scene.positions.get(node.id), control = node.decision;
    const { title, context, summary, contextIsCode } = sequencePresentation(node);
    const group = svg('g', { class: 'path-card method-sequence-card',
      transform: `translate(${point.x - point.w / 2},${point.y - point.h / 2})`, role: 'button', tabindex: 0,
      'data-node-id': node.id, 'data-source-owner-id': node.sourceOwnerId, 'data-step-number': node.stepNumber || '',
      'aria-label': title });
    if (node.compactLoopUpdate) appendLoopUpdate(group, node);
    else if (node.kind === 'loop') appendLoopControl(group, node);
    else if (node.valueChoices) appendValueChoice(view, group, node);
    else if (node.simpleRows) appendSimpleMethod(view, group, node);
    else if (node.kind === 'entry') appendSequenceEntry(group, node);
    else if (node.compactRow || node.compactSource) appendSequenceRow(group, node);
    else if (node.transition) appendSequenceTransition(group, node);
    else appendFlowCard(group, { width: point.w, height: point.h, left: 0, top: 0, title,
      complexity: node.complexity, health: node.health,
      context: context || (node.kind === 'method' ? 'Тело метода · локальный порядок' : ''), contextLines: node.kind === 'candidate' ? 1 : 2, contextIsCode,
      note: node.kind === 'candidate' ? `Строка ${node.line || '?'}` : node.parallel ? 'Порядок завершения задач неизвестен' : node.staticValue ? 'Данные снимка · не вычисляются' : `Строка ${node.line || '?'} · статическая схема`,
      ...sequenceSurfaceOptions(node), semantic: { id: control ? 'control' : 'source', color: control ? '#8a6420' : '#5f7d70', fill: control ? '#ffedc2' : '#e8f2eb' },
      palette: { text: '#17382c' }, color: '#5f7d70', showCounts: false });
    if (node.expansionNotice) group.append(svg('title', {}, node.expansionNotice));
    if (node.infrastructureEvidence) group.append(svg('title', {}, node.infrastructureEvidence));
    if (!node.transition || node.compactRow) group.append(svg('title', {}, `${node.label}\n${node.sourceStatement || node.sourceExpression || node.condition || ''}\n${context}\n${summary}\n${node.file || ''}:${node.line || '?'}\nНе runtime-трасса`));
    if (!node.transition || node.kind === 'await') bindSequenceInspection(view, group, node);
    else { group.setAttribute('role', 'img'); group.setAttribute('tabindex', -1); }
    appendSequenceDefinition(view, group, node, point);
    appendSequenceSource(view, group, node);
    appendImplementationAction(view, group, node);
    appendMethodReadAction(view, group, node);
    appendProgressiveDetailsAction(view, group, node);
    if (node.canExpand) {
      const action = svg('text', { x: point.w - 12, y: point.h - 10, 'text-anchor': 'end', class: 'path-card-expand',
        role: 'button', tabindex: 0, 'aria-label': `${node.bodyExpanded ? 'Свернуть' : 'Раскрыть'} вызов ${node.label}` }, node.bodyExpanded ? 'Свернуть' : 'Подробнее →');
      bindSequenceAction(view, group, action, node, () => toggle(view, node.id),
        node.bodyExpanded ? 'Свернуть детали этого вызова.' : 'Раскрыть детали справа на этой карте.');
    }
    decorateModCard(view, group, node, {left:0,top:0,width:point.w,height:point.h});
    view.viewport.append(group); view.nodeElements.set(node.id, { group, point, sourceOwnerId: node.sourceOwnerId,
      inspectionId: sequenceInspectionId(node), interactive: !node.transition || node.kind === 'await' });
  }
  view.highlightSelection = id => {
    const selectedCard = selectedSequenceCard(view, id);
    for (const [occurrenceId, item] of view.nodeElements) {
      const active = Boolean(item.interactive && id != null && occurrenceId === selectedCard);
      item.group.classList.toggle('selected', active);
      if (item.interactive) item.group.setAttribute('aria-pressed', String(active));
      item.group.querySelector('.sequence-selection-ring')?.remove();
      if (active) item.group.append(svg('rect', { class: 'sequence-selection-ring', x: 0, y: 0,
        width: item.point.w, height: item.point.h, rx: 8, fill: 'none', stroke: '#176b53',
        'stroke-width': 3, 'vector-effect': 'non-scaling-stroke', 'pointer-events': 'none', 'aria-hidden': 'true' }));
    }
  };
  view.highlightSelection(selected);
  view.selectedId = selected;
  view.transform();
  view.onPathScopeChange?.({ focusId: scene.rootId, label: scene.byId.get(scene.rootId)?.label,
    visible: view.pathScope.visible, total: view.pathScope.total, expanded: view.pathExpandedIds.size,
    canBack: false, canExpandAll: true, sequence: true });
}
