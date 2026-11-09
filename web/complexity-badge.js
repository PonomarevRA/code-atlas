import { appendHealthBadge } from './health-badge.js';
/** Shared lexical metrics stay separate from camera, grouping and call expansion depth. */
export function complexityText(metrics) {
  if (!metrics || metrics.score == null) return 'Сложность неизвестна: анализ тела недоступен.';
  const prefix = metrics.status === 'partial' ? 'Не менее ' : '';
  return `${prefix}${metrics.score} · структурная сложность (${metrics.version}). ` +
    `Вложенность: максимум ${metrics.maxNestingDepth}, средняя ${metrics.averageNestingDepth ?? 'не установлена'}. ` +
    `Ветвления: ${metrics.branchingCount}; циклы: ${metrics.loopCount}. ` +
    'Балл = ветвления + циклы + максимальная вложенность. Красный от 10, жёлтый от 5. ' +
    'Локальное тело метода; вызываемые методы и вложенные функции исключены. ' +
    (metrics.status === 'partial' ? 'Анализ неполный. ' : '') +
    `Источник: ${metrics.sourceKind || 'не установлен'}.`;
}

/**
 * Shared score presentation for map cards and HTML lists; missing facts never become zero.
 * @param {object|null} metrics Captured local complexity, optionally unavailable or partial.
 * @returns {{score:number|null,label:string,color:string,description:string,status:string}} Display facts, without changing metrics.
 */
export function complexityPresentation(metrics) {
  const score = Number.isFinite(metrics?.score) ? metrics.score : null;
  let color = '#7b8784';
  if (score !== null) {
    color = '#327f70';
    if (score >= 5) color = '#986718';
    if (score >= 10) color = '#b13e43';
  }
  const label = score === null ? '?' : `${metrics.status === 'partial' ? '≥' : ''}${score > 999 ? '999+' : score}`;
  return { score, label, color, description: complexityText(metrics), status: metrics?.status || 'unavailable' };
}

/** Show structural complexity by default; explicit H/D choices retain their own identity and uncertainty. */
export function appendComplexityBadge(group, metrics, x, y, health = null) {
  const mode = document.getElementById?.('node-metric')?.value || 'complexity';
  if (mode !== 'complexity' && (health || metrics)) return appendHealthBadge(group, health, x, y, mode);
  if (!metrics) return;
  const { score, label, color } = complexityPresentation(metrics);
  const badge = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  for (const [key, value] of Object.entries({ class: 'code-complexity-badge', transform: `translate(${x},${y})`,
    role: 'img', tabindex: 0, 'aria-label': complexityText(metrics), 'data-complexity-score': score ?? '',
    'data-complexity-status': metrics.status })) badge.setAttribute(key, value);
  const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  circle.setAttribute('r', 12); circle.setAttribute('fill', color); badge.append(circle);
  const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  for (const [key, value] of Object.entries({ y: 4, 'text-anchor': 'middle', fill: '#fff', 'font-size': label.length > 2 ? 8 : 11, 'font-weight': 700 })) text.setAttribute(key, value);
  text.textContent = label; badge.append(text);
  const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
  title.textContent = complexityText(metrics); badge.append(title); group.append(badge);
}
