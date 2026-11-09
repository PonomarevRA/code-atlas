import { el } from './dom.js';
import { renderHtmlPreview } from './html-preview.js';

/** Captured HTML facts remain readable without running mod scripts or rendering source markup. */
export function renderHtmlFacts(panel, node, onSelect, context = {}) {
  const facts = node?.htmlFacts;
  if (!facts) return;
  panel.append(el('h3', facts.role === 'duplicate-group' ? 'Повтор HTML-разметки' : 'HTML-структура'));
  panel.append(el('p', facts.treeKind || facts.parser ? 'HTML5-дерево снимка; JavaScript и состояние браузера не выполнялись.' : 'Статические факты снимка; DOM и поведение страницы не проверялись.', 'small muted'));
  renderHtmlPreview(panel, node, context);
  if (facts.generated) panel.append(el('p', 'Элемент добавлен HTML5-парсером; собственного диапазона в исходнике нет.', 'small muted'));
  if (facts.rangeStatus === 'start-tag-only') panel.append(el('p', 'Точно известен открывающий тег; полный исходный диапазон не установлен.', 'small muted'));
  if (facts.cssSelector) panel.append(el('p', 'CSS-селектор'), el('code', facts.cssSelector, 'source'));
  if (facts.parentNodeId) {
    const parent = el('button', '↑ Родитель HTML5', 'connection');
    parent.type = 'button';
    parent.addEventListener('click', () => onSelect(facts.parentNodeId));
    panel.append(parent);
  }
  const children = facts.childNodeIds || [];
  for (const id of children.slice(0, 20)) {
    const child = el('button', `↓ ${context.nodeLabel?.(id) || 'HTML-элемент'}`, 'connection');
    child.type = 'button';
    child.addEventListener('click', () => onSelect(id));
    panel.append(child);
  }
  if (children.length > 20) panel.append(el('p', `${children.length} дочерних элементов; для полного списка используйте CSS-поиск.`, 'small muted'));
  if (context.projectId && context.snapshotId && facts.role !== 'duplicate-group') {
    renderSelectorSearch(panel, node, onSelect, context);
  }
  if (facts.title) panel.append(el('p', facts.title));
  if (facts.sourceRole === 'unknown-output') panel.append(el('p', 'Происхождение output неизвестно. Дубли не оценивались; задайте atlas-html.json.', 'small muted'));
  if (facts.status === 'partial' || facts.truncated) panel.append(el('p', 'Данные неполные; проверьте ограничения анализа.', 'small muted'));
  for (const warning of facts.warnings || []) panel.append(el('p', warning, 'small muted'));
  if (facts.recommendation) panel.append(el('p', facts.recommendation));
  if (facts.occurrenceCount) panel.append(el('p', `${facts.occurrenceCount} мест · точное совпадение исходной разметки`));
  for (const occurrence of (facts.occurrences || []).slice(0, 40)) {
    const label = `${occurrence.file}:${occurrence.line}–${occurrence.endLine}`;
    if (occurrence.nodeId) {
      const button = el('button', label, 'connection');
      button.type = 'button';
      button.addEventListener('click', () => onSelect(occurrence.nodeId));
      panel.append(button);
    } else panel.append(el('p', label, 'small'));
  }
  for (const id of [...new Set(facts.duplicateGroupIds || [])].slice(0, 20)) {
    const button = el('button', 'Показать повтор разметки', 'connection');
    button.type = 'button';
    button.addEventListener('click', () => onSelect(id));
    panel.append(button);
  }
  for (const reference of (facts.references || []).slice(0, 12)) {
    panel.append(el('p', `${reference.attribute}: ${reference.value} → ${reference.targetFile || 'цель не разрешена'}`, 'small'));
  }
}

/** Query only the captured file; late responses cannot select another card or execute source HTML. */
function renderSelectorSearch(panel, node, onSelect, context) {
  const form = el('form');
  const label = el('label', 'CSS-поиск в HTML-файле');
  const input = el('input');
  input.type = 'text';
  input.maxLength = 8192;
  input.placeholder = 'Например: nav > a[href]';
  input.setAttribute('aria-label', 'CSS-селектор HTML');
  label.append(input);
  const submit = el('button', 'Найти');
  submit.type = 'submit';
  const result = el('div', undefined, 'connections');
  result.setAttribute('aria-live', 'polite');
  form.append(label, submit, result);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    submit.disabled = true;
    result.replaceChildren(el('p', 'Поиск в сохранённом HTML5-дереве…', 'small'));
    try {
      const fetcher = context.fetcher || globalThis.fetch;
      const response = await fetcher(`/api/projects/${encodeURIComponent(context.projectId)}/html-query`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({snapshotId: context.snapshotId, nodeId: node.id, selector: input.value || '*', limit: 30})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.error || 'Не удалось выполнить CSS-поиск.');
      result.replaceChildren(el('p', `${data.total} элементов · показано ${data.nodes.length}`, 'small'));
      for (const item of data.nodes) {
        const button = el('button', `${item.label}${item.line ? ` · строка ${item.line}` : ' · добавлен парсером'}`, 'connection');
        button.type = 'button';
        button.addEventListener('click', () => onSelect(item.id));
        result.append(button);
      }
      if (data.nextOffset !== null) result.append(el('p', 'Уточните селектор; полный список доступен через query_html с nextOffset.', 'small muted'));
      if (data.captureTruncated) result.append(el('p', 'Захват дерева неполный.', 'small muted'));
    } catch (error) {
      result.replaceChildren(el('p', error.message, 'small'));
    } finally {
      submit.disabled = false;
    }
  });
  panel.append(form);
}
