import { wrapDiagramText } from './diagram-text.js';

/** Resolve a visible occurrence to its real symbol; ambiguous calls inspect the declaration, never the first candidate. */
export function sequenceInspectionId(node) {
  if (node.kind === 'call' || node.kind === 'source-call') {
    if (node.definitionId) return node.definitionId;
    if (node.readTargetIds?.length === 1) return node.readTargetIds[0];
  }
  return node.sourceOwnerId;
}

/** A card selects inspector details for isolation; explicit source/read/expand actions keep their own callbacks. */
export function bindSequenceInspection(view, group, node) {
  const id = sequenceInspectionId(node);
  if (!id) return;
  view.sequenceInspectionIds ||= new Set(); view.sequenceInspectionIds.add(id);
  group.setAttribute('data-inspection-id', id);
  view.bindPathAction(group, () => (view.onSequenceSelect || view.onSelect)?.(id, node.id));
}

/** Inspector targets may be shared by many steps; only the clicked occurrence receives selection. */
export function selectedSequenceCard(view, inspectionId) {
  const occurrenceId = view.selectedSequenceOccurrenceId;
  if (occurrenceId && view.nodeElements.get(occurrenceId)?.inspectionId === inspectionId) return occurrenceId;
  return inspectionId;
}

/** Source-only steps expose a real call-site link after body click becomes inspector selection. */
export function appendSequenceSource(view, group, node, centered = false) {
  if (node.definitionId || node.transition && !node.compactRow || !node.sourceOwnerId || !(node.line > 0)) return;
  const action = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  const left = centered ? -node.w / 2 : 0, top = centered ? -node.h / 2 : 0;
  for (const [key, value] of Object.entries({ x: left + node.w - 12, y: top + node.h - 10,
    'text-anchor': 'end', 'font-size': node.compactLoopUpdate ? 9 : 10,
    class: 'path-card-source', role: 'button', tabindex: 0,
    'aria-label': `Открыть строку ${node.line}: ${node.label}` })) action.setAttribute(key, value);
  action.textContent = node.compactLoopUpdate ? '↗' : 'Открыть код ↗';
  bindSequenceAction(view, group, action, node, () => (centered ? view.onInlineSource : view.onPathSource)?.(node.sourceOwnerId, node.line),
    `Открыть код вызывающего метода на строке ${node.line}.`);
}

/** Keep one occurrence on every action and expose its explanation on keyboard focus as well as hover. */
export function bindSequenceAction(view, group, action, node, callback, explanation) {
  action.setAttribute('data-call-occurrence-id', node.id);
  action.setAttribute('aria-description', explanation);
  const ns = 'http://www.w3.org/2000/svg';
  const title = document.createElementNS(ns, 'title'); title.textContent = explanation; action.append(title);
  const hint = document.createElementNS(ns, 'g');
  hint.setAttribute('class', 'sequence-action-hint'); hint.setAttribute('visibility', 'hidden');
  hint.setAttribute('pointer-events', 'none'); hint.setAttribute('aria-hidden', 'true');
  const x = Number(action.getAttribute?.('x') ?? action.attributes?.x) || 0;
  const y = Number(action.getAttribute?.('y') ?? action.attributes?.y) || 0;
  const end = (action.getAttribute?.('text-anchor') ?? action.attributes?.['text-anchor']) === 'end';
  const width = Math.min(380, Math.max(160, explanation.length * 6 + 16));
  const left = end ? x - width : x;
  const lines = wrapDiagramText(explanation, Math.floor((width - 16) / 6)).lines;
  const height = 14 + lines.length * 14;
  const box = document.createElementNS(ns, 'rect');
  for (const [key, value] of Object.entries({ x: left, y: y - height - 10, width, height, rx: 5,
    fill: '#fffdf6', stroke: '#8cad9c' })) box.setAttribute(key, value);
  const text = document.createElementNS(ns, 'text');
  for (const [key, value] of Object.entries({ x: left + 8, y: y - height + 8, 'font-size': 10, fill: '#17382c' })) text.setAttribute(key, value);
  for (const [index, line] of lines.entries()) {
    const span = document.createElementNS(ns, 'tspan');
    span.setAttribute('x', left + 8); span.setAttribute('dy', index ? 14 : 0); span.textContent = line; text.append(span);
  }
  hint.append(box, text);
  const show = () => hint.setAttribute('visibility', 'visible');
  const hide = () => hint.setAttribute('visibility', 'hidden');
  action.addEventListener('focus', show); action.addEventListener('blur', hide);
  action.addEventListener('mouseenter', show); action.addEventListener('mouseleave', hide);
  view.bindPathAction(action, callback);
  group.append(action, hint);
}
