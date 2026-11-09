import { applyModTheme } from './mod-runtime.js';
import { cardDocumentation } from './card-documentation.js';
import { wrapDiagramText } from './diagram-text.js';
import { appendCodeText } from './code-text.js';
import { appendComplexityBadge } from './complexity-badge.js';
const NS = 'http://www.w3.org/2000/svg';
export const FLOW_CARD = Object.freeze({ width: 260, height: 124, header: 42, rankGap: 96, rowGap: 38 });

/** Путь и обычные карточки резервируют одинаковое место для пояснения и нижней подписи. */
export function flowCardMetrics(node, width = FLOW_CARD.width) {
  const documentation = cardDocumentation(node, width);
  return { width, height: FLOW_CARD.height + 30 + documentation.height, documentation };
}

/** SVG принимает только текст: имена и условия из репозитория не исполняются как HTML. */
function svg(tag, attributes, text) {
  const element = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

/** Короткая подпись не изменяет размер карточки; полное значение остаётся в tooltip renderer. */
function short(text, limit) {
  const value = String(text || '');
  return value.length > limit ? `${value.slice(0, Math.max(1, limit - 2))}…` : value;
}

/**
 * Общая поверхность Flow: карта, раскрытая иерархия и путь используют одну типографику,
 * полосу роли и боковые IN/OUT. Координаты локальны относительно group; события,
 * raw ID, tooltip, выделение и дополнительные действия принадлежат вызывающему renderer.
 * footerHeight резервирует место для действий реализации, не меняя основную карточку.
 */
export function appendFlowCard(group, { width, height, left = -width / 2, top = -height / 2,
  title, context = '', note = '', semantic, palette, color, counts = { incoming: 0, outgoing: 0 },
  showSemantics = true, showCounts = true, contextLines = 1, contextIsCode = false, footerHeight = 0, documentation = null,
  bodyClass = '', portClass = '', roleClass = '', noteClass = '', inPort = true, outPort = true, complexity = null, health = null,
  titleFontSize = null, titleColumns = null, titleLines = 2, titleLineHeight = 17, headerHeight = FLOW_CARD.header, contextY = null, noteY = null, contextFontSize = null }) {
  group.setAttribute('data-card-design', 'flow');
  group.classList.toggle('atlas-flow-card', true);
  const contentHeight = height - footerHeight;
  const bodyHeight = contentHeight - (documentation?.height || 0);
  const centerX = left + width / 2;
  const centerY = top + height / 2;
  group.append(svg('rect', { x: left, y: top, width, height, rx: 8,
    class: `flow-card-body ${bodyClass}`.trim(), fill: '#fbfcfa', stroke: color }));
  group.append(svg('rect', { x: left + 1, y: top + 1, width: width - 2, height: headerHeight, rx: 7,
    class: 'flow-card-header', fill: showSemantics ? semantic.fill : palette.fill }));
  if (showSemantics) {
    group.setAttribute('data-code-role', semantic.id);
    group.append(svg('rect', { x: left + 1, y: top + 1, width: 5, height: height - 2, rx: 2,
      class: 'code-role-stripe', fill: semantic.color }));
    if (contextLines === 1) group.append(svg('text', { x: centerX, y: top + bodyHeight / 2 + 24,
      'text-anchor': 'middle', class: `flow-card-role ${roleClass}`.trim(), fill: semantic.color }, short(semantic.label, Math.max(5, Math.floor(width / 6)))));
  }
  const label = String(title || '');
  const titleInset = complexity ? 44 : 12;
  const titleLimit = titleColumns ?? Math.max(5, Math.floor((width - titleInset - 12) / (titleFontSize ? titleFontSize * .62 : 9.4)));
  for (const [line, text] of wrapDiagramText(label, titleLimit, titleLines).lines.entries()) {
    appendCodeText(group, { x: left + titleInset, y: top + 18 + line * titleLineHeight, class: 'flow-card-name',
      ...(titleFontSize ? { style: `font-size:${titleFontSize}px` } : {}) }, text);
  }
  appendComplexityBadge(group, complexity, left + 24, top + 20, health);
  const contextLimit = Math.max(5, Math.floor((width - 24) / 6.7));
  for (const [line, text] of wrapDiagramText(context, contextLimit, contextLines).lines.entries()) {
    const attributes = { x: left + 12, y: top + (contextY ?? bodyHeight / 2 + 7) + line * 17, class: 'flow-card-context',
      ...(contextFontSize ? { style: `font-size:${contextFontSize}px` } : {}) };
    if (contextIsCode) appendCodeText(group, attributes, text);
    else group.append(svg('text', attributes, text));
  }
  for (const [side, x, enabled, count] of [['in', left, inPort, counts.incoming], ['out', left + width, outPort, counts.outgoing]]) {
    if (!enabled) continue;
    group.append(svg('circle', { cx: x, cy: centerY, r: 5, fill: color,
      class: `flow-port flow-port-${side} ${portClass}`.trim(), 'data-port': side }));
    if (showCounts && width >= 180) group.append(svg('text', { x: side === 'in' ? x + 12 : x - 12,
      y: top + bodyHeight / 2 + 40, 'text-anchor': side === 'in' ? 'start' : 'end',
      class: 'flow-port-label' }, `${side.toUpperCase()} ${count}`));
  }
  for (const [index, line] of (documentation?.lines || []).entries()) {
    const description = svg('text', { x: left + 12, y: top + bodyHeight + 12 + index * 17, class: 'flow-card-description' }, line);
    description.append(svg('title', {}, documentation.text));
    group.append(description);
  }
  group.append(svg('text', { x: centerX, y: top + (noteY ?? contentHeight - 7),
    'text-anchor': 'middle', class: `flow-card-note ${noteClass}`.trim() }, short(note, Math.max(5, Math.floor((width - 24) / 6)))));
  applyModTheme(group);
}
