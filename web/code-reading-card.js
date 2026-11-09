import { appendFlowCard, FLOW_CARD } from './flow-card.js';
import { cardDocumentation } from './card-documentation.js';
import { wrapDiagramText } from './diagram-text.js';
import { sequenceSurfaceOptions } from './sequence-card-layout.js';

const symbolPlural = new Intl.PluralRules('ru');
const symbolForms = { one: 'символ', few: 'символа', many: 'символов', other: 'символа' };

/** Identify structural containers separately from callable symbols; neither implies execution order. */
function readingKind(node) {
  if (node.htmlFacts?.role === 'block') return 'HTML-элемент';
  const container = node.architectureLevel || node.readingKind;
  if (container === 'projects' || container === 'project') return node.projectKind === 'logical-area' ? 'Раздел по папке' : 'Проект';
  if (container === 'files' || container === 'file') return 'Файл';
  const kind = node.memberFacts?.kind || node.callableFacts?.kind || node.typeFacts?.kind
    || (node._callable_class ? 'class' : node.kind);
  return { method: 'Метод', function: 'Функция', constructor: 'Конструктор', class: 'Класс',
    interface: 'Интерфейс', field: 'Поле', property: 'Свойство' }[kind] || 'Символ';
}

/**
 * Measures the visible purpose and optional saved source subtitle with the existing header/action rows.
 * @param {object} node Snapshot facts; sourceSubtitle distinguishes duplicate project labels without changing identity.
 * @param {number} width Available card width in SVG units; defaults to the normal Flow card width.
 * @returns {object} Width/height, wrapped context, documentation facts and structural kind; the node is unchanged.
 */
export function codeReadingMetrics(node, width = FLOW_CARD.width) {
  const documentation = cardDocumentation(node, width), kind = readingKind(node);
  const purpose = [];
  if (documentation.summary) purpose.push(documentation.summary);
  if (documentation.ownerSummary) purpose.push(`О классе: ${documentation.ownerSummary}`);
  const description = purpose.join('\n') || (node.nodeCount != null ? `${kind} · ${node.nodeCount} ${symbolForms[symbolPlural.select(node.nodeCount)]}` : `${kind} · описание не добавлено`);
  let context = node.sourceSubtitle ? node.sourceSubtitle + '\n' + description : description;
  if (node.readingStructure?.kind === 'html') {
    const { depth, parentLabel } = node.readingStructure;
    context = `HTML-элемент · уровень ${depth + 1}${parentLabel ? `\nВнутри ${parentLabel}` : ''}`;
  }
  const lines = wrapDiagramText(context, Math.max(5, Math.floor((width - 24) / 6.7)), 4).lines;
  const extraLines = (Math.max(1, lines.length) - 1) * 17;
  const height = node.readingStructure?.kind === 'html' ? 118 + extraLines : Math.max(146, 122 + extraLines);
  return { width, height, context, documentation, kind };
}

/**
 * Reuses path typography and badges while the host retains dependencies, actions and source identity.
 * @param {SVGGElement} group Existing card group; receives literal SVG text and accessible source context.
 * @param {object} node Saved display facts, optional sourceSubtitle and documentation; no user code is executed.
 * @param {object} surface Width/height plus the existing color/palette/position contract from the host renderer.
 * @returns {void} Appends card elements; same saved subtitle is available in visible context, ARIA and tooltip.
 */
export function appendCodeReadingCard(group, node, { width, height, ...surface }) {
  const metrics = codeReadingMetrics(node, width);
  const title = String(node.displayLabel || node.label || node.id).replace(/^\.(?=\w)/, '');
  const presentation = sequenceSurfaceOptions({ h: height, canExpand: true });
  if (node.readingStructure?.kind === 'html') {
    Object.assign(presentation, { headerHeight: 42, contextY: 56, titleLineHeight: 17 });
  }
  appendFlowCard(group, { ...surface, width, height, title,
    complexity: node.complexity, health: node.health, context: metrics.context,
    ...presentation, contextLines: 4,
    // Long PascalCase file names need a conservative width at the shared 14 px title size.
    titleColumns: Math.max(5, Math.floor((width - (node.complexity ? 44 : 12) - 12) / 10.5)),
    note: node.line ? `Строка ${node.line}` : metrics.kind, noteY: height - 32, showCounts: false });
  group.setAttribute('data-reading-design', node.readingStructure ? 'structure' : 'entry-path');
  if(node.sourceSubtitle){
    group.setAttribute('data-source-subtitle',node.sourceSubtitle);
    group.setAttribute('aria-label',(group.getAttribute('aria-label')||title)+'. '+node.sourceSubtitle);
  }
  const note = group.querySelector('.flow-card-note');
  note.setAttribute('x', (surface.left ?? -width / 2) + 12);
  note.setAttribute('text-anchor', 'start');
  const tooltip = document.createElementNS('http://www.w3.org/2000/svg', 'title');
  tooltip.textContent = `${metrics.kind}: ${node.label || node.id}\n${node.sourceSubtitle || node.file || ''}\n${metrics.documentation.text}\nСвязи между карточками — зависимости, не порядок исполнения.`;
  group.append(tooltip);
}
