import { decorateModCard } from './mod-runtime.js';
import { appendSequenceFrames, sequenceCurve, appendSequenceTransition, appendSequenceDefinition, appendSequenceRow, appendImplementationAction, appendMethodReadAction, appendProgressiveDetailsAction } from './method-sequence-renderer.js';
import { bindSequenceInspection, sequenceInspectionId, appendSequenceSource, bindSequenceAction } from './sequence-card-interactions.js';
import { internalBlockEdge } from './method-sequence-blocks.js';
import { sequenceEdgeRouter, includeRouteBounds } from './method-sequence-routing.js';
import { cardDocumentation } from './card-documentation.js';
import { appendFlowCard } from './flow-card.js';
import { inlineFlowEdgeRouter } from './inline-flow-edges.js';
import { sequencePresentation } from './sequence-presentation.js';
import { sequenceSurfaceOptions } from './sequence-card-layout.js';
import { appendValueChoice, appendSimpleMethod, appendSequenceEntry, appendLoopControl, appendLoopUpdate } from './sequence-card-symbols.js';

const NS = 'http://www.w3.org/2000/svg';
/** SVG textContent сохраняет выражения исходников как текст. */
function svg(tag, attributes, text) {
  const node = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Переносит общую сцену пути в зарезервированную область метода без изменения raw-графа. */
export function renderInlineMethodBody(view, owner, placement) {
  const { scene, x, y } = placement;
  if (scene.inlineSimple) return; // The existing owner card contains the short method's source rows.
  const positions = new Map(view.inlineDetailPositions || []);
  const root = view.positions.get(owner.id);
  positions.set(owner.id, { x: root.x, y: root.y, w: root.headerBounds.width, h: root.headerBounds.height });
  for (const item of scene.content) {
    const point = { ...item, x: item.x + x, y: item.y + y };
    positions.set(item.id, point);
    const box = { x: point.x - point.w / 2, y: point.y - point.h / 2, left: point.x - point.w / 2,
      top: point.y - point.h / 2, right: point.x + point.w / 2, bottom: point.y + point.h / 2,
      width: point.w, height: point.h };
    view.positions.set(item.id, { ...point, r: 0, width: point.w, height: point.h, cardBounds: box, headerBounds: box });
  }
  if (scene.sequence) appendSequenceFrames(view, scene, x, y, scene.detached ? null : owner.id);
  const route = scene.sequence ? sequenceEdgeRouter(scene, positions, { dx: x, dy: y, curve: sequenceCurve }) : inlineFlowEdgeRouter(positions);
  for (const [index, edge] of scene.visualEdges.entries()) {
    if (scene.sequence && internalBlockEdge(scene.byId.get(edge.source), scene.byId.get(edge.target), edge)) continue;
    const source = positions.get(edge.source), target = positions.get(edge.target);
    if (!source || !target) continue;
    const routed = scene.sequence ? route(edge) : route(source, target, index);
    const path = svg('path', { class: 'inline-method-edge', d: routed.d,
      fill: 'none', stroke: '#5f7d70', 'data-owner-id': owner.id, 'data-source': edge.source,
      'data-target': edge.target, 'data-relation': edge.relation || 'source-body', 'data-sequence-kind': edge.sequenceKind || '',
      ...(['context', 'candidate'].includes(edge.sequenceKind) ? { 'stroke-dasharray': '5 4' } : {}),
      'marker-end': view.sourceMarker('#5f7d70', -2) });
    path.append(svg('title', {}, `${edge.caption || 'Структура тела'}\nИсходник: ${owner.node.file || ''}. Статический разбор.`));
    if (routed.data) { path.setAttribute('stroke-dasharray', '5 4'); path.setAttribute('opacity', '.5'); }
    if (scene.sequence && ['unknown', 'exception'].includes(edge.sequenceKind)) path.setAttribute('stroke-dasharray', '5 4');
    view.viewport.append(path);
    if (scene.sequence ? routed.caption : edge.caption) {
      const position = scene.sequence ? routed.caption
        : { x: (source.x + source.w / 2 + target.x - target.w / 2) / 2, y: (source.y + target.y) / 2 - 8 };
      const label = svg('text', { class: 'path-edge-label', ...position, ...(scene.sequence ? { 'text-anchor': 'middle' } : {}) },
        scene.sequence ? routed.caption.text : edge.caption);
      label.append(svg('title', {}, edge.caption)); view.viewport.append(label);
    }
  }
  if (scene.sequence && view.flowBounds) {
    const extent = route.bounds();
    view.flowBounds = includeRouteBounds(view.flowBounds, `M ${extent.x} ${extent.y} L ${extent.x + extent.width} ${extent.y + extent.height}`);
  }
  for (const item of scene.content) {
    const node = scene.byId.get(item.id) || item;
    const point = positions.get(item.id);
    const sourceNode = scene.byId.get(node.ownerId);
    const rawId = node.sourceOwnerId || sourceNode?.sourceOwnerId || owner.rawId;
    const line = node.bodyExpanded ? node.line : node.sourceLine || node.line;
    const file = node.file || sourceNode?.file || owner.node.file || '';
    const control = node.decision;
    const presentation = scene.sequence ? sequencePresentation(node) : null;
    const value = presentation ? presentation.context : node.condition || node.sourceExpression || node.staticData?.initializer || '';
    const label = presentation ? presentation.title : node.dataField ? `Данные: ${node.label}` : node.label;
    const group = svg('g', { class: `inline-method-card${control ? ' inline-method-control' : ''}`,
      'data-owner-id': owner.id, 'data-source-owner-id': rawId, 'data-source-line': line || '',
      'data-node-id': item.id, 'data-step-number': node.stepNumber || '', 'data-body-kind': control ? node.kind : node.dataField ? 'data' : node.valueOutcome ? 'value' : 'call-or-value',
      transform: `translate(${point.x},${point.y})`, role: 'button', tabindex: 0,
      'aria-label': `${label}. Исходник строки ${line || '?'}.` });
    if (node.compactLoopUpdate) appendLoopUpdate(group, node, true);
    else if (node.kind === 'loop') appendLoopControl(group, node, true);
    else if (node.valueChoices) appendValueChoice(view, group, node, true);
    else if (node.simpleRows) appendSimpleMethod(view, group, node, true);
    else if (node.kind === 'entry') appendSequenceEntry(group, node, true);
    else if (node.compactRow || node.compactSource) appendSequenceRow(group, node, true);
    else if (node.transition) appendSequenceTransition(group, node, true);
    else appendFlowCard(group, { width: point.w, height: point.h, title: label, context: value, contextLines: node.kind === 'candidate' ? 1 : 2, contextIsCode: presentation?.contextIsCode,
      complexity: node.complexity, health: node.health,
      note: node.kind === 'candidate' ? `Строка ${line || '?'}` : node.dataField || node.staticValue || node.dataSelection ? 'Данные снимка · не вычисляются' : node.callableFacts?.kind === 'constructor' ? 'Конструктор · создание объекта' : `Строка ${line || '?'} · ${String(sourceNode?.label || node.label || owner.label).slice(0, 30)}`,
      semantic: { id: control ? 'control' : 'source', color: control ? '#8a6420' : '#5f7d70', fill: control ? '#ffedc2' : '#e8f2eb' },
      footerHeight: node.canExpand || node.definitionId ? 30 : 0, ...(scene.sequence ? sequenceSurfaceOptions(node) : {}), documentation: !scene.sequence && node.bodyExpanded ? cardDocumentation(node, point.w) : null,
      palette: { text: control ? '#6b4a16' : '#17382c' }, color: control ? '#8a6420' : '#5f7d70', showCounts: false });
    if (!node.transition || node.compactRow) group.append(svg('title', {}, `${node.label}\n${node.sourceStatement || node.sourceExpression || node.condition || ''}\n${value}\n${presentation?.summary || ''}\n${file}:${line || '?'}\n${node.dataField || node.staticValue || node.dataSelection ? 'Данные снимка' : 'Факты исходника; не runtime-трасса'}`));
    if (node.expansionNotice) group.append(svg('title', {}, node.expansionNotice));
    if (!node.transition || node.kind === 'await') {
      bindSequenceInspection(view, group, { ...node, sourceOwnerId: rawId });
    } else { group.setAttribute('role', 'img'); group.setAttribute('tabindex', -1); }
    if (scene.sequence) appendSequenceDefinition(view, group, node, point, true);
    appendSequenceSource(view, group, node, true);
    if (scene.sequence) appendImplementationAction(view, group, node, true);
    appendMethodReadAction(view, group, node, true);
    appendProgressiveDetailsAction(view, group, node, true);
    if (node.canExpand) {
      const toggle = svg('text', { x: point.w / 2 - 12, y: point.h / 2 - 10, 'text-anchor': 'end',
        class: 'path-card-expand', role: 'button', tabindex: 0, 'data-node-action': 'inline', 'data-owner-id': item.id,
        'aria-label': `${node.bodyExpanded ? 'Свернуть' : 'Раскрыть'} реализацию ${node.label}` }, node.bodyExpanded ? 'Свернуть' : 'Подробнее →');
      bindSequenceAction(view, group, toggle, node, () => view.onToggleBlock?.(item.id),
        node.bodyExpanded ? 'Свернуть детали этого вызова.' : 'Раскрыть детали справа на этой карте.');
    }
    decorateModCard(view, group, { ...node, sourceOwnerId: rawId }, {left:-point.w/2,top:-point.h/2,width:point.w,height:point.h});
    view.viewport.append(group);
    view.nodeElements.set(item.id, { group, point, sourceOwnerId: rawId, inspectionId: sequenceInspectionId({ ...node, sourceOwnerId: rawId }) });
  }
}
