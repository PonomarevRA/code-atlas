import { appendFlowCard } from './flow-card.js';

/** Объясняет состав и следующий шаг вместо безымянной карточки с одним количеством узлов. */
export function appendGroupOverviewCard(group, item, surface) {
  const summary = item.groupSummary;
  const context = summary.scope === 'project'
    ? `${summary.groups} групп · ${summary.files} файлов\n${item.nodeCount} символов\nОткройте группы этого проекта`
    : `${summary.files} файлов · ${item.nodeCount} символов\n${(summary.sampleFiles || []).map(file => file.split(/[\\/]/).pop()).join('\n') || 'Исходные файлы не определены'}`;
  appendFlowCard(group, { ...surface, title: item.displayLabel || item.label,
    titleFontSize: 16, titleColumns: 29, titleLines: 3, titleLineHeight: 18, headerHeight: 64,
    context, contextLines: 4, contextY: 86, contextFontSize: 12,
    showSemantics: false, showCounts: false, note: 'Зависимости — при выборе или наведении', noteY: 176 });
  group.setAttribute('data-group-summary', summary.scope);
  const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
  title.textContent = `${item.label}\n${(summary.sampleFiles || []).join('\n')}\n${summary.algorithmLabel ? `Метка алгоритма: ${summary.algorithmLabel}\n` : ''}Группы выделены алгоритмом; локальные подписи и примеры взяты из файлов проекта.`;
  group.append(title);
}
