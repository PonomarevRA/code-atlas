import { wrapDiagramText } from './diagram-text.js';
import { appendCodeText } from './code-text.js';
import { valueChoicePortOffset } from './method-sequence-compact.js';
import { appendComplexityBadge } from './complexity-badge.js';

/** Shared path/inline symbols render supplied facts; explicit source rows keep their own callbacks. */
function svg(tag, attrs, text) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

/** A loop header shows its actual source predicate and update without a large empty card body. */
export function appendLoopControl(group, node, centered = false) {
  const left = centered ? -node.w / 2 : 0, top = centered ? -node.h / 2 : 0;
  group.classList.toggle('method-loop-control', true);
  group.append(svg('rect', { class: 'mod-card-body', x: left, y: top, width: node.w, height: node.h, rx: 10, fill: '#fff5dc', stroke: '#8a6420' }));
  for (const [index, text] of wrapDiagramText(node.sourceExpression || node.label, 32, 3).lines.entries()) appendCodeText(group,
    { x: left + 12, y: top + 20 + index * 16, 'font-size': 12, 'font-family': 'monospace' }, text);
  group.append(svg('text', { x: left + 12, y: top + node.h - 27, 'font-size': 10, fill: '#8a6420' }, `Цикл ${node.controlKind || ''}`));
  group.append(svg('text', { x: left + 12, y: top + node.h - 10, 'font-size': 9, fill: '#758a7e' }, `Строка ${node.line || '?'}`));
  for (const [port, x, y] of [['in', left + node.w / 2, top], ['repeat', left + node.w * 3 / 4, top + node.h],
    ['body', left + node.w, top + node.h / 2], ['exit', left + node.w / 2, top + node.h]]) {
    group.append(svg('circle', { cx: x, cy: y, r: 4, fill: '#8a6420', 'data-port': port }));
  }
}

/** A short for-update reads down: input on top, then output below for the repeat corridor. */
export function appendLoopUpdate(group, node, centered = false) {
  const left = centered ? -node.w / 2 : 0, top = centered ? -node.h / 2 : 0;
  group.classList.toggle('method-loop-update', true);
  group.append(svg('rect', { class: 'mod-card-body', x: left, y: top, width: node.w, height: node.h, rx: 10, fill: '#edf5ef', stroke: '#71988b' }));
  for (const [index, text] of wrapDiagramText(node.sourceExpression || node.label, 22, 2).lines.entries()) appendCodeText(group,
    { x: left + 12, y: top + 17 + index * 14, 'font-size': 11, 'font-family': 'monospace' }, text);
  group.append(svg('text', { x: left + 12, y: top + node.h - 10, 'font-size': 9, fill: '#758a7e' }, `Обновить · строка ${node.line || '?'}`));
  for (const [port, y] of [['in', top], ['out', top + node.h]]) group.append(svg('circle', {
    cx: left + node.w / 2, cy: y, r: 4, fill: '#71988b', 'data-port': port }));
}

/** One compact selector owns the predicate and two value rows with distinct output ports. */
export function appendValueChoice(view, group, node, centered = false) {
  if (node.guardGeometry) return appendCompactGuard(view, group, node, centered);
  const left = centered ? -node.w / 2 : 0, top = centered ? -node.h / 2 : 0;
  group.classList.toggle('method-value-choice', true);
  group.append(svg('rect', { class: 'mod-card-body', x: left, y: top, width: node.w, height: node.h, rx: 10, fill: '#f6faf7', stroke: '#71988b' }));
  group.append(svg('rect', { class: 'mod-card-header', x: left + 1, y: top + 1, width: node.w - 2, height: 35, rx: 9, fill: '#e1eee8' }));
  const heading = node.hideCondition ? 'Выбор значения' : node.condition;
  for (const [index, text] of wrapDiagramText(heading, 42).lines.entries()) appendCodeText(group,
    { x: left + 12, y: top + 15 + index * 14, 'font-size': 11, 'font-family': 'monospace' }, text);
  group.append(svg('circle', { cx: left, cy: top + node.h / 2, r: 4, fill: '#71988b', 'data-port': 'in' }));
  // The first expression can wrap twice; separate it from the second outcome's label.
  group.append(svg('line', { x1: left + 8, x2: left + node.w - 8,
    y1: top + node.h / 2 + 5, y2: top + node.h / 2 + 5, stroke: '#99b5a5',
    'stroke-width': 1, class: 'method-value-divider' }));
  for (const [index, choice] of node.valueChoices.entries()) {
    const y = top + node.h / 2 + valueChoicePortOffset(index);
    const row = svg('g', { role: 'button', tabindex: 0, 'data-value-choice': index,
      'aria-label': `${choice.label}: ${choice.expression}. Исходник строки ${choice.line || node.line}.` });
    row.append(svg('text', { x: left + 12, y: y - 14, 'font-size': 8, fill: '#758a7e' }, choice.label));
    for (const [line, text] of wrapDiagramText(choice.expression, 40).lines.entries()) appendCodeText(row,
      { x: left + 12, y: y - 2 + line * 13, 'font-size': 11, 'font-family': 'monospace' }, text);
    row.append(svg('circle', { cx: left + node.w, cy: y, r: 4, fill: '#3c897b', 'data-port': `out-${index}` }));
    row.append(svg('title', {}, `${choice.label}: ${choice.expression}`));
    view.bindPathAction(row, () => (centered ? view.onInlineSource : view.onPathSource)?.(node.sourceOwnerId, choice.line || node.line));
    group.append(row);
  }
  group.append(svg('text', { x: left + 12, y: top + node.h - 6, 'font-size': 9, fill: '#758a7e' }, `Строка ${node.line || '?'} · выбор значения`));
}

/** One source predicate and its two short outcomes replace synthetic evaluation/result cards. */
function appendCompactGuard(view, group, node, centered) {
  const left = centered ? -node.w / 2 : 0, top = centered ? -node.h / 2 : 0;
  const geometry = node.guardGeometry;
  const color = node.assignmentSelection ? '#71988b' : '#8a6420';
  if (node.assignmentSelection) group.classList.toggle('method-value-assignment', true);
  group.classList.toggle('method-compact-guard', true);
  group.append(svg('rect', { class: 'mod-card-body', x: left, y: top, width: node.w, height: node.h,
    rx: 10, fill: '#f7faf8', stroke: color }));
  group.append(svg('rect', { class: 'mod-card-header', x: left + 1, y: top + 1, width: node.w - 2,
    height: geometry.headerHeight - 1, rx: 9, fill: node.assignmentSelection ? '#e1eee8' : '#ffedc2' }));
  for (const [index, text] of geometry.heading.entries()) appendCodeText(group,
    { x: left + 12, y: top + 19 + index * 15, 'font-size': 11, 'font-family': 'monospace' }, text);
  if (node.compactEvaluation) group.append(svg('text', { x: left + 12, y: top + geometry.headerHeight - 7,
    'font-size': 9, fill: '#8a6420' }, 'Слева направо · короткое замыкание'));
  group.append(svg('circle', { cx: geometry.inlineRows ? left + node.w / 2 : left,
    cy: geometry.inlineRows ? top : top + node.h / 2, r: 4, fill: color, 'data-port': 'in' }));
  for (const [index, choice] of geometry.rows.entries()) {
    const row = svg('g', { role: 'button', tabindex: 0, 'data-value-choice': index,
      'aria-label': `${choice.label}: ${choice.expression}. Исходник строки ${choice.line || node.line}.` });
    row.append(svg('line', { x1: left + 8, x2: left + node.w - 8, y1: top + choice.top,
      y2: top + choice.top, stroke: '#b8cabb', class: 'method-value-divider' }));
    if (geometry.inlineRows) {
      for (const [line, text] of choice.labelLines.entries()) row.append(svg('text', {
        x: left + 12, y: top + choice.top + 16 + line * 12, 'font-size': 9, fill: '#607b6e' }, text));
    } else row.append(svg('text', { x: left + 12, y: top + choice.top + 12, 'font-size': 9, fill: '#607b6e' },
      choice.label === 'истина' ? 'Да' : choice.label === 'иначе' ? 'Нет' : choice.label));
    for (const [line, text] of choice.lines.entries()) {
      const attrs = { x: left + (geometry.inlineRows ? 86 : 12),
        y: top + choice.top + (geometry.inlineRows ? 16 : 27) + line * 14, 'font-size': 11 };
      if (choice.skipped) row.append(svg('text', { ...attrs, fill: '#607b6e' }, text));
      else appendCodeText(row, { ...attrs, 'font-family': 'monospace' }, text);
    }
    row.append(svg('circle', { cx: left + node.w, cy: top + choice.top + choice.height / 2,
      r: 4, fill: color, 'data-port': `out-${index}` }));
    row.append(svg('title', {}, `${choice.label}: ${choice.expression}`));
    view.bindPathAction(row, () => (centered ? view.onInlineSource : view.onPathSource)?.(node.sourceOwnerId, choice.line || node.line));
    group.append(row);
  }
  group.append(svg('text', { x: left + 12, y: top + node.h - 10, 'font-size': 9, fill: '#758a7e' }, `Строка ${node.line || '?'}`));
}

/** A complete short method shows its name and source rows without a separate return diagram. */
export function appendSimpleMethod(view, group, node, centered = false) {
  const left = centered ? -node.w / 2 : 0, top = centered ? -node.h / 2 : 0;
  group.classList.toggle('method-simple-card', true);
  group.append(svg('rect', { class: 'mod-card-body', x: left, y: top, width: node.w, height: node.h, rx: 10, fill: '#f7faf8', stroke: '#71988b' }));
  appendCodeText(group, { x: left + (node.complexity ? 44 : 12), y: top + 23, 'font-size': 14, 'font-weight': 650 },
    wrapDiagramText(node.label, node.complexity ? 29 : 34, 1).lines[0]);
  appendComplexityBadge(group, node.complexity, left + 24, top + 20, node.health);
  if (node.simpleSummary) group.append(svg('text', { x: left + 12, y: top + 41, 'font-size': 10, fill: '#496858' },
    wrapDiagramText(node.simpleSummary, 44, 1).lines[0]));
  for (const [index, source] of node.simpleRows.entries()) {
    const row = svg('g', { role: 'button', tabindex: 0, 'aria-label': `${source.expression}. Исходник строки ${source.line}.` });
    for (const [line, text] of wrapDiagramText(source.expression, 40).lines.entries()) appendCodeText(row,
      { x: left + 12, y: top + 49 + (node.simpleSummary ? 20 : 0) + index * 42 + line * 14, 'font-size': 11, 'font-family': 'monospace' }, text);
    row.append(svg('title', {}, source.expression));
    view.bindPathAction(row, () => (centered ? view.onInlineSource : view.onPathSource)?.(node.sourceOwnerId, source.line));
    group.append(row);
  }
  for (const x of [left, left + node.w]) group.append(svg('circle', { cx: x, cy: top + node.h / 2, r: 4, fill: '#71988b' }));
}

/** Entry styling stays visibly distinct from methods and value selectors. */
export function appendSequenceEntry(group, node, centered = false) {
  const left = centered ? -node.w / 2 : 0, top = centered ? -node.h / 2 : 0;
  group.classList.toggle('method-sequence-entry', true);
  group.append(svg('rect', { class: 'mod-card-body', x: left, y: top, width: node.w, height: node.h, rx: 18, fill: '#e3edf8', stroke: '#4c79a2', 'stroke-width': 2 }));
  group.append(svg('text', { x: left + 14, y: top + 22, 'font-size': 11, 'font-weight': 700, fill: '#365f87' }, `→ ВХОД · ${node.condition || ''}`));
  for (const [line, text] of wrapDiagramText(node.label, 38).lines.entries()) appendCodeText(group,
    { x: left + 14, y: top + 44 + line * 15, 'font-size': 12, 'font-family': 'monospace' }, text);
  group.append(svg('circle', { cx: left + node.w, cy: top + node.h / 2, r: 5, fill: '#4c79a2' }));
}
