import { $, el } from './dom.js';
import { hasHttpEntryEvidence, isCallableDeclaration } from './discovery-text.js';

/** Сохраняет все совпадения, но точное имя и имя символа ставит раньше совпадения только по пути. */
export function rankNavigationMatches(query, nodes, { aliases = new Map() } = {}) {
  const typed = String(query).trim().toLocaleLowerCase();
  const aliasQuery = typed.split(/\s+/u).some(word => aliases.has(word));
  const normalized = typed.split(/\s+/u).map(word => aliases.get(word) || word).join(" ");
  const words = normalized.split(/\s+/).filter(Boolean);
  const ranked = [];
  for (let index = 0; index < nodes.length; index++) {
    const node = nodes[index];
    const name = String(node.label || '').toLocaleLowerCase();
    const callable = name.replace(/^\./, '').replace(/\(\)$/, '');
    let score = 4;
    if (name === normalized || callable === normalized) score = 0;
    else if (callable.startsWith(normalized)) score = 1;
    else if (words.every(word => name.includes(word))) score = 2;
    else if (words.some(word => name.includes(word))) score = 3;
    // Domain abbreviations lead to callable declarations before files and data fields.
    if (aliasQuery) score += isCallableDeclaration(node) ? -5 : 2;
    if (hasHttpEntryEvidence(node)) score = -6;
    ranked.push({ node, score, index });
  }
  ranked.sort((left, right) => left.score - right.score || left.index - right.index);
  return ranked.map(item => item.node);
}
/** Поиск для навигации: ввод меняет только список результатов, выбор открывает реальный символ. */
export function createNavigationSearch({ model, getNodes, getProjects, onSelect, getScopeKey, onVisibilityChange = () => {} }) {
  let lastScope = null;
  let lastQuery = null;
  let matches = [];
  let limit = 20;
  let resultButtons = [];
  const input = $('navigation-search');
  /** Закрытие результатов не меняет запрос; Escape возвращает фокус в поле. */
  function dismiss(returnFocus = false) {
    const popup = $('navigation-search-results');
    if (popup) popup.hidden = true;
    if (returnFocus) input.focus?.({ preventScroll: true });
    onVisibilityChange();
  }
  /** Один выбор работает одинаково мышью и клавиатурой и сохраняет scope перехода. */
  function select(node, scope) {
    const popup = $('navigation-search-results');
    if (popup) popup.hidden = true;
    onSelect(node.id, { scope });
  }
  /** Повторно использует результат, пока запрос и текущий фильтр не изменились. */
  function refresh({ reveal = false } = {}) {
    const query = $('navigation-search').value.trim();
    const scopeControl = $('navigation-search-scope');
    const scope = scopeControl?.value === 'current' ? 'current' : 'all';
    const nodes = getNodes(scope);
    // Фильтрация часто создаёт новую array при draw. Стабильный ключ не сбрасывает «Ещё» по identity массива.
    const key = JSON.stringify([scope, getScopeKey ? getScopeKey(scope) : nodes.map(node => [node.id, node.label, node.file])]);
    if (key !== lastScope || query !== lastQuery) {
      matches = query ? rankNavigationMatches(query, model.search(query, nodes), { aliases: model.searchAliases }) : [];
      lastScope = key;
      lastQuery = query;
      limit = 20;
    }
    const projects = new Map(getProjects().map(project => [String(project.id), project.label]));
    const list = $('navigation-results');
    const popup = $('navigation-search-results');
    if (popup && reveal) popup.hidden = false;
    list.replaceChildren();
    resultButtons = [];
    const area = scope === 'current' ? 'в текущем фильтре' : 'во всём репозитории';
    const aliases = String(query).toLowerCase().split(/\s+/u).filter(term=>model.searchAliases?.has(term));
    const aliasNotice = aliases.length ? ` ${aliases.map(term=>`${term} → ${model.searchAliases.get(term)}`).join(', ')}: соответствия из комментариев проекта.` : '';
    $('navigation-count').textContent = query ? (matches.length ? `${matches.length} совпадений по имени, пути и комментариям ${area}. Сначала подтверждённые входы, затем символы.${aliasNotice} ↓ — выбрать, Enter — открыть.` : `Совпадений ${area} нет. Попробуйте часть имени или область «Весь репозиторий».`) : `Поиск ${area}. Например: название продукта, Orders или имя файла. Ввод не скрывает узлы карты.`;
    for (const node of matches.slice(0, limit)) {
      const button = el('button', undefined, 'navigation-result');
      button.append(el('strong', node.label || node.id));
      button.append(el('small', `${projects.get(String(node.codeProjectId)) || 'Без проекта'} · ${node.file || 'файл не указан'}${node.line ? `:${node.line}` : ''}`));
      const index = resultButtons.length;
      button.addEventListener('click', () => select(node, scope));
      button.addEventListener('keydown', event => {
        if (event.altKey || event.ctrlKey || event.metaKey) return;
        if (event.key === 'Escape') { event.preventDefault?.(); event.stopPropagation?.(); dismiss(true); }
        else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault?.(); event.stopPropagation?.();
          const target = index + (event.key === 'ArrowDown' ? 1 : -1);
          (resultButtons[target] || input).focus?.();
        }
      });
      resultButtons.push(button);
      list.append(button);
    }
    $('navigation-more').hidden = matches.length <= limit;
  }
  $('navigation-search').addEventListener('input', () => refresh({ reveal: true }));
  $('navigation-search').addEventListener('focus', () => refresh({ reveal: true }));
  $('navigation-search').addEventListener('keydown', event => {
    const popup = $('navigation-search-results');
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'Escape' && popup) { event.preventDefault?.(); event.stopPropagation?.(); dismiss(); }
    else if (['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key) && matches.length) {
      event.preventDefault?.(); event.stopPropagation?.();
      if (event.key === 'Enter') {
        if (event.repeat) return;
        const scope = $('navigation-search-scope')?.value === 'current' ? 'current' : 'all';
        select(matches[0], scope);
      } else {
        if (popup) popup.hidden = false;
        (event.key === 'ArrowDown' ? resultButtons[0] : resultButtons.at(-1))?.focus?.();
      }
    }
  });
  $('navigation-search-scope')?.addEventListener('change', () => refresh({ reveal: true }));
  $('navigation-more').addEventListener('click', () => { limit += 20; refresh(); });
  return { refresh };
}
