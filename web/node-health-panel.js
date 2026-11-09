import { organizationCoverageText } from './health-organization.js';
import { renderReadability } from './health-readability.js';
import { el } from './dom.js';
import { renderComplexitySummary } from './complexity-summary.js';

/**
 * Render captured health facts for exactly one selected code card, without measuring or fetching source.
 * @param {HTMLElement} panel Retained right-side Health content, independent of Details.
 * @param {object|null} node Selected real symbol, or null after clearing/changing scope.
 * @param {string} projectId Repository used by the explicit measurement/planning link.
 */
export function renderNodeHealth(panel, node, projectId) {
  panel.replaceChildren();
  panel.dataset.nodeId = node?.id || '';
  if (!node) {
    panel.append(el('p', 'Выберите карточку на карте или в списке слева, чтобы увидеть её оценки и рекомендации.', 'muted'));
    return;
  }
  panel.append(el('h3', node.label || node.id), el('p', node.file || 'Исходный файл не указан', 'small muted'));
  const health = node.health;
  if (!health) panel.append(el('p', 'Здоровье этой карточки не оценено. Отсутствие оценки не означает хорошее или плохое состояние кода.', 'muted'));
  else {
    const metrics = el('dl', undefined, 'node-health-metrics');
    for (const [label, score] of [['Здоровье H', health.score], ['Структура', health.structuralScore],
      ['Документация D', health.documentationScore], ['Организация O', health.organizationScore], ['Прозрачность T', health.transparencyScore]]) {
      metrics.append(el('dt', label), el('dd', `${score ?? '?'} / 100`));
    }
    panel.append(metrics);
    renderReadability(panel, health);
    if (health.components) panel.append(el('p', `Доступные локальные показатели: поток ${health.components.flow ?? '?'} / 100; размер ${health.components.size ?? '?'} / 100.`, 'small'));
    if (health.organization) panel.append(el('p', organizationCoverageText(health.organization.coverage), 'small muted'));
    else if (health.unknownReasons?.length) panel.append(el('p', `Не хватает обязательных данных: ${health.unknownReasons.join('; ')}.`, 'small muted'));
    else if (health.organizationScore == null) panel.append(el('p', 'Контекст файла и папки не оценён; итоговый O неизвестен.', 'small muted'));
    const docs = node.documentationHealth;
    let documentation = 'Документация не оценена.';
    if (docs?.documented === true) documentation = 'Описание метода найдено.';
    if (docs?.documented === false) documentation = 'Описание метода отсутствует.';
    panel.append(el('p', `Статус: ${health.status || 'не установлен'}. ${documentation} Точность текста требует ревью.`, 'small muted'));
    if (health.score == null && health.status === 'partial') panel.append(el('p', 'Расчёт выполнен, но итоговый H неизвестен: для него не хватает части обязательных данных. Это не ожидание фоновой задачи.', 'small muted'));
    const findings = (health.findings || []).slice(0, 8);
    if (findings.length) {
      panel.append(el('h4', 'Рекомендации для карточки'));
      for (const finding of findings) panel.append(el('p', finding.message, 'small'));
    }
  }
  panel.append(renderComplexitySummary(node.complexity));
  const link = el('a', 'Измерить текущий код и спланировать изменения →');
  link.href = '/health.html?' + new URLSearchParams({ repo: projectId || '', node: node.id, file: node.file || '' });
  panel.append(link);
}
