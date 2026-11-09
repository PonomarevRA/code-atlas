import { el } from './dom.js';
import { complexityPresentation, complexityText } from './complexity-badge.js';

const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

/** Show captured metrics briefly; preserve unknowns, lower bounds and the full explanation on demand. */
export function renderComplexitySummary(metrics) {
  const presentation = complexityPresentation(metrics);
  const section = el('section', undefined, 'complexity-summary');
  section.setAttribute('aria-label', 'Структурная сложность');
  const header = el('div', undefined, 'complexity-summary-header');
  const badge = el('strong', presentation.label, 'complexity-summary-score');
  badge.setAttribute('data-color', presentation.color);
  header.append(badge, el('strong', 'Структурная сложность'));
  section.append(header);
  if (presentation.score === null) section.append(el('p', 'Оценка неизвестна', 'complexity-summary-status'));
  else {
    if (metrics.status === 'partial') section.append(el('p', 'Неполный анализ · балл не ниже указанного', 'complexity-summary-status'));
    const values = el('dl', undefined, 'complexity-summary-metrics');
    for (const [label, value] of [['Вложенность, макс.', metrics.maxNestingDepth],
      ['Вложенность, ср.', metrics.averageNestingDepth], ['Ветвления', metrics.branchingCount], ['Циклы', metrics.loopCount]]) {
      const item = el('div', undefined, 'complexity-summary-metric');
      item.append(el('dt', label), el('dd', Number.isFinite(value) ? number.format(value) : '?'));
      values.append(item);
    }
    section.append(values);
  }
  const details = el('details', undefined, 'complexity-summary-details');
  const toggle = el('summary', 'Как считается');
  toggle.setAttribute('tabindex', '0');
  details.append(toggle, el('p', complexityText(metrics)));
  section.append(details);
  return section;
}
