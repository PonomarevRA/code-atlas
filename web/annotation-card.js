import { appendFlowCard } from './flow-card.js';

const SCOPES = { declaration: 'Объявление', type: 'Класс', parameter: 'Параметр', file: 'Файл' };

/** Annotation occurrences are metadata, so they reserve only a title, scope and source row. */
export function annotationCardMetrics(width = 260) { return { width, height: 100 }; }

/** Reuse safe Flow geometry while identifying a compact annotation instead of a callable block. */
export function appendAnnotationCard(group, node, { width, height, ...surface }) {
  const label = String(node.text || node.label || node.name || 'Атрибут');
  const title = label.startsWith('@') ? label : `@ ${label}`;
  appendFlowCard(group, { ...surface, width, height, title, titleFontSize: 12, titleLineHeight: 16,
    titleColumns: Math.max(8, Math.floor((width - 24) / 8.5)), headerHeight: 42, contextY: 60,
    context: `${SCOPES[node.scope] || node.scope || 'Метаданные'}${node.owner?.name ? ` · ${node.owner.name}` : ''}`,
    contextLines: 2, contextFontSize: 10, note: node.line ? `Строка ${node.line}` : 'Аннотация', noteY: height - 10,
    footerHeight: 22, showCounts: false, bodyClass: 'annotation-card-body' });
  const note = group.querySelector('.flow-card-note');
  note.setAttribute('x', (surface.left ?? -width / 2) + 12); note.setAttribute('text-anchor', 'start');
  group.setAttribute('data-card-type', 'annotation');
  const tooltip = document.createElementNS('http://www.w3.org/2000/svg', 'title');
  tooltip.textContent = `${title}\n${node.file || ''}:${node.line || '?'}\nАннотация исходника; действие при выполнении не вычислялось.`;
  group.append(tooltip);
}
