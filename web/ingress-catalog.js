import { $, el } from './dom.js';
import { createEntryMemory } from './ingress-catalog-memory.js';
import { IngressCatalogPopup } from './ingress-catalog-popup.js';
import { projectSearchAliases, discoveryTerms, discoveryText } from './discovery-text.js';
/** Ищет входы всего репозитория; фильтры карты ограничивают действие, а не скрывают результаты. */
export function projectIngressCatalog(entries, { kind = '', query = '', nodes = new Map(), projects = [], availableIds = new Set(), page = 0, pageSize = 6, aliases = new Map(), allowedIds = null, order = [] } = {}) {
  const counts = { http: 0, mcp: 0, kafka: 0, rabbit: 0, test: 0 }, matchingCounts = { http: 0, mcp: 0, kafka: 0, rabbit: 0, test: 0 };
  const owners = new Map(projects.map(project => [String(project.id), project.label]));
  const terms = discoveryTerms(query, aliases);
  const results = [];
  for (const entry of entries) {
    if (Object.hasOwn(counts, entry.kind)) counts[entry.kind]++;
    if (allowedIds && !allowedIds.has(entry.id)) continue;
    const handlers = [];
    for (const id of entry.handlerNodeIds || []) {
      const node = nodes.get(id);
      if (node) handlers.push({ node, available: availableIds.has(id) });
    }
    let searchable = `${entry.label || ''} ${entry.topic || ''} ${entry.route || ''} ${entry.queue || ''} ${entry.operationName || ''} ${entry.mcpType || ''}`;
    if (entry.kind === 'test') searchable += ` ${entry.framework || ''} ${(entry.tags || []).join(' ')} ${JSON.stringify(entry.metadata || [])} ${entry.file || ''}`;
    for (const handler of handlers) searchable += ` ${discoveryText(handler.node)}`;
    searchable = searchable.toLocaleLowerCase();
    if (!terms.every(choices => choices.some(term => searchable.includes(term)))) continue;
    if (Object.hasOwn(matchingCounts, entry.kind)) matchingCounts[entry.kind]++;
    if (kind && entry.kind !== kind) continue;
    results.push({ ...entry, handlers, projectLabel: owners.get(String(entry.codeProjectId)) || 'Без проекта' });
  }
  const ranks = new Map(order.map((id, index) => [id, index]));
  results.sort((a, b) => (ranks.get(a.id) ?? Infinity) - (ranks.get(b.id) ?? Infinity) || String(a.topic || a.route || a.queue || a.label).localeCompare(String(b.topic || b.route || b.queue || b.label)) || String(a.id).localeCompare(String(b.id)));
  const size = Math.max(1, Math.min(20, Math.trunc(pageSize) || 6));
  const pageCount = Math.max(1, Math.ceil(results.length / size));
  const currentPage = Math.max(0, Math.min(pageCount - 1, Math.trunc(page) || 0));
  return { counts, matchingCounts, total: results.length, entries: results.slice(currentPage * size, (currentPage + 1) * size), page: currentPage, pageCount };
}
/** Управляет независимым каталогом; не перерисовывает карту при поиске и пагинации. */
export function createIngressCatalog({ state, filteredNodes, selectNode, selectEvidence, retry, analyzePath, describeNode, memory = createEntryMemory() }) {
  let page = 0;
  let kind = '';
  let explicitKind = false, indexedProject = null;
  let indexedNodes = null, aliases = new Map(), listScope = null;
  let preferences = memory.read(null);
  const popup = new IngressCatalogPopup($('ingress-catalog-dialog'), $('ingress-catalog-search'));
  /** Сохраняет только компактный выбор пользователя; граф и результаты не сериализуются. */
  function save() { memory.write(state.project?.id, {...preferences, kind: explicitKind ? kind : null, page, query: $('ingress-catalog-search').value}); }
  /** Выбор запоминается без открытия/выполнения входа; переход выполняется отдельной кнопкой. */
  function remember(entry) {
    preferences.selectedId = entry.id;
    preferences.recent = [entry.id, ...preferences.recent.filter(id => id !== entry.id)].slice(0, 8);
    save();
  }
  /** Закрывает modal до действий с картой и сохраняет настоящий ID выбранного входа. */
  function activate(entry, action) { remember(entry); popup.close(); action(); }
  const kinds = [['test', 'Тесты'], ['http', 'HTTP API'], ['mcp', 'MCP'], ['kafka', 'Kafka'], ['rabbit', 'RabbitMQ'], ['', 'Все']];
  /** Показывает сохранённые входы по текущему каталогу; исчезнувшие ID не превращаются в действия карты. */
  function renderMemoryControls(ready) {
    $('ingress-catalog-memory').replaceChildren();
    for (const [id, label] of [['all', 'Все входы'], ['favorites', '★ Избранные'], ['recent', 'Недавние']]) {
      const button = el('button', label); button.type = 'button'; button.setAttribute('aria-pressed', String(preferences.scope === id));
      button.addEventListener('click', () => { preferences.scope = id; page = 0; save(); render(); });
      $('ingress-catalog-memory').append(button);
    }
    const last = (state.ingress.data?.entries || []).find(entry => entry.id === preferences.selectedId);
    $('ingress-catalog-last').replaceChildren();
    if (last) {
      const button = el('button', `Последний вход: ${last.label || last.id}`);
      button.addEventListener('click', () => { kind = last.kind; explicitKind = true; preferences.scope = 'all'; $('ingress-catalog-search').value = last.operationName || last.route || last.topic || last.queue || last.label; page = 0; save(); render(); });
      $('ingress-catalog-last').append(button);
    } else if (preferences.selectedId && ready && state.ingress.infrastructureStatus !== 'pending') {
      $('ingress-catalog-last').append(el('span', 'Запомненный вход отсутствует в доступном каталоге этого снимка.', 'small muted'));
    }
    $('ingress-catalog-summary').textContent = ready ? `${state.ingress.data.entries.length} входов · избранные и последние сохраняются для репозитория` : 'Индекс точек входа загружается…';
  }
  /** Показывает состояние загрузки явно: неизвестное количество никогда не выдаётся за ноль. */
  function render() {
    const status = state.ingress.status || 'idle';
    const ready = status === 'ready';
    const projectKey = state.project?.id || state.graph.metadata?.snapshotId || '';
    if (indexedProject !== projectKey) {
      indexedProject = projectKey; preferences = memory.read(state.project?.id);
      explicitKind = preferences.kind !== null; kind = preferences.kind ?? ''; page = preferences.page;
      $('ingress-catalog-search').value = preferences.query;
    }
    if (ready && !explicitKind && state.ingress.data?.entries.some(entry => entry.kind === 'test')) kind = 'test';
    if (indexedNodes !== state.nodes) { indexedNodes = state.nodes; aliases = projectSearchAliases(state.nodes.values()); }
    const scopeIds = preferences.scope === 'favorites' ? preferences.favorites : preferences.scope === 'recent' ? preferences.recent : null;
    const catalog = projectIngressCatalog(state.ingress.data?.entries || [], { pageSize: 20, allowedIds: scopeIds ? new Set(scopeIds) : null, order: preferences.scope === 'recent' ? preferences.recent : [], kind, aliases, query: $('ingress-catalog-search').value, nodes: state.nodes, projects: state.graph.codeProjects || [], availableIds: new Set(filteredNodes(false).map(node => node.id)), page });
    page = catalog.page;
    $('ingress-catalog-types').replaceChildren();
    for (const [id, label] of kinds) {
      let count = ready ? id ? catalog.counts[id] : state.ingress.data.entries.length : '…';
      if (ready && ['pending', 'error'].includes(state.ingress.infrastructureStatus) && ['http', 'mcp', 'kafka', 'rabbit'].includes(id)) count = catalog.counts[id] ? `≥${catalog.counts[id]}` : '…';
      const button = el('button', `${label} · ${count}`, id === kind ? 'selected' : '');
      button.setAttribute('aria-pressed', id === kind);
      button.addEventListener('click', () => { kind = id; explicitKind = true; page = 0; save(); render(); });
      $('ingress-catalog-types').append(button);
    }
    renderMemoryControls(ready);
    const message = status === 'loading' ? 'Ищем входы во всём репозитории…' : status === 'error' ? state.ingress.message : !ready ? 'Выберите репозиторий и дождитесь индекса входов.' : `${catalog.total} результатов во всём репозитории · страница ${page + 1} из ${catalog.pageCount}. Каталог не ограничен текущей страницей карты; фильтры карты сохраняются.`;
    $('ingress-catalog-status').textContent = `${preferences.scope === 'all' ? '' : preferences.scope === 'favorites' ? 'Избранные · ' : 'Недавние · '}${message}`;
    if (ready && ['pending', 'error'].includes(state.ingress.infrastructureStatus)) $('ingress-catalog-status').textContent += ` ${state.ingress.message}`;
    const discovery = state.ingress.data?.testDiscovery;
    if (ready && kind === 'test' && discovery?.status === 'partial') {
      $('ingress-catalog-status').textContent += ` Обнаружение неполное: ${discovery.coverage?.unresolvedDeclarations ?? '?'} объявлений не сопоставлены с картой. Список не гарантирует все тесты.`;
    }
    if (ready && state.ingress.data?.analysisModsCurrent === false) $('ingress-catalog-status').textContent += ' Правила модов изменились — обновите карту.';
    const entryDiscovery = state.ingress.data?.entrypointDiscovery;
    if (ready && entryDiscovery?.status === 'partial') $('ingress-catalog-status').textContent += ` Обнаружение входов неполное: ${entryDiscovery.coverage?.unmappedHandlers ?? '?'} обработчиков не сопоставлены; проверьте ограничения снимка.`;
    $('ingress-catalog-retry').hidden = status !== 'error' && state.ingress.infrastructureStatus !== 'error';
    $('ingress-catalog-search').disabled = false;
    $('ingress-catalog-previous').disabled = !ready || page === 0;
    $('ingress-catalog-next').disabled = !ready || page + 1 >= catalog.pageCount;
    const list = $('ingress-catalog-list');
    const scope = JSON.stringify([indexedProject, kind, preferences.scope, $('ingress-catalog-search').value, page]);
    if (scope !== listScope) { list.scrollTop = 0; listScope = scope; }
    list.replaceChildren();
    if (!ready) return;
    if (kind === 'test' && catalog.total) list.append(el('p', 'С чего начать: выберите тест-кейс, изучите его данные и подготовку, затем откройте схему от теста.', 'test-study-hint'));
    if (!catalog.entries.length) {
      const incomplete = ['pending', 'error'].includes(state.ingress.infrastructureStatus) && kind !== 'test';
      list.append(el('p', incomplete ? 'Общий поиск входов ещё не завершён. Отсутствие записей здесь не означает ноль входов.' : 'Подходящих входов в выбранном виде не найдено. Это не доказывает их отсутствие в программе.', 'small muted'));
      // Alternatives use this query's matches; changing protocol always requires an explicit action.
      for (const [id, label] of kinds) {
        if (!id || id === kind || !catalog.matchingCounts[id]) continue;
        const alternative = el('button', `Посмотреть ${label} · ${catalog.matchingCounts[id]}`);
        alternative.addEventListener('click', () => { kind = id; explicitKind = true; page = 0; save(); render(); });
        list.append(alternative);
      }
    }
    for (const entry of catalog.entries) {
      const card = el('article', undefined, 'ingress-catalog-card');
      card.setAttribute('data-entry-id', entry.id);
      card.setAttribute('data-selected', String(preferences.selectedId === entry.id));
      const name = entry.topic || entry.route || entry.queue || entry.label;
      const type = { kafka: 'Kafka', http: 'HTTP API', mcp: `MCP · ${entry.mcpType || ''}`, rabbit: 'RabbitMQ', test: `Тест · ${entry.framework || ''}` }[entry.kind] || entry.kind;
      card.append(el('span', type, 'pill'));
      const choose = el('button', `${entry.kind === 'http' ? `${entry.httpMethod || 'HTTP'} ` : ''}${name}`, 'ingress-entry-title');
      choose.setAttribute('aria-label', `Выбрать вход ${name}`); choose.setAttribute('aria-pressed', String(preferences.selectedId === entry.id));
      choose.addEventListener('click', () => { remember(entry); render(); }); card.append(choose);
      const favorite = el('button', preferences.favorites.includes(entry.id) ? '★' : '☆', 'ingress-entry-favorite');
      favorite.setAttribute('aria-label', `Избранное: ${name}`); favorite.setAttribute('aria-pressed', String(preferences.favorites.includes(entry.id)));
      favorite.addEventListener('click', () => { preferences.favorites = preferences.favorites.includes(entry.id) ? preferences.favorites.filter(id => id !== entry.id) : [entry.id, ...preferences.favorites].slice(0, 30); save(); render(); }); card.append(favorite, el('small', entry.projectLabel));
      if (entry.kind === 'test') {
        if (entry.file) card.append(el('small', `${entry.file.split('/').pop()}:${entry.line || '?'}`));
        if (entry.tags?.length) card.append(el('small', entry.tags.join(' · ')));
        if (entry.declaredStatus === 'disabled') card.append(el('small', 'Отключён в исходнике'));
        card.append(el('small', 'Результат запуска неизвестен'));
      }
      if (entry.configFile) card.append(el('small', `Конфигурация: ${entry.configFile}`));
      if (!entry.handlers.length) card.append(el('p', 'Обработчик не сопоставлен с символом графа.', 'small muted'));
      for (const handler of entry.handlers) {
        card.append(el('span', handler.node.label || handler.node.id, 'catalog-handler'));
        const button = el('button', 'Открыть обработчик');
        button.disabled = !handler.available;
        button.setAttribute('aria-label', `Открыть обработчик ${handler.node.label || handler.node.id}`);
        button.addEventListener('click', () => {
          activate(entry, () => selectNode(handler.node.id));
          $('graph').scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
        card.append(button);
        if (describeNode) {
          const description = el('button', 'Описание узла');
          description.disabled = !handler.available;
          description.setAttribute('aria-label', `Описание узла ${handler.node.label || handler.node.id}`);
          description.addEventListener('click', () => activate(entry, () => describeNode(handler.node.id)));
          card.append(description);
        }
        if (analyzePath) {
          const path = el('button', entry.kind === 'test' ? 'Схема от теста' : 'Путь выполнения');
          path.disabled = !handler.available;
          path.setAttribute('aria-label', `Путь выполнения ${handler.node.label || handler.node.id}`);
          path.addEventListener('click', () => activate(entry, () => analyzePath(handler.node.id, entry)));
          card.append(path);
        }
        if (!handler.available) card.append(el('small', 'Обработчик исключён фильтрами карты. Измените фильтры, чтобы открыть его.'));
      }
      const evidence = el('button', entry.kind === 'test' ? 'Данные и подготовка теста' : 'Подробнее о входе', 'text-button');
      evidence.addEventListener('click', () => { activate(entry, () => selectEvidence(entry)); $('selection').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
      card.append(evidence);
      list.append(card);
    }
  }
  $('ingress-catalog-search').addEventListener('input', () => { page = 0; save(); render(); });
  $('ingress-catalog-previous').addEventListener('click', () => { page--; save(); render(); });
  $('ingress-catalog-next').addEventListener('click', () => { page++; save(); render(); });
  $('ingress-catalog-retry').addEventListener('click', retry);
  $('ingress-catalog-open').addEventListener('click', () => { render(); popup.open(); });
  $('ingress-catalog-toolbar-open')?.addEventListener('click', () => { render(); popup.open(); });
  $('ingress-catalog-close').addEventListener('click', () => popup.close());
  /** Сбрасывает поисковую область при смене репозитория, сохраняя явный выбор вида входов. */
  function reset() { popup.close(); page = 0; explicitKind = false; indexedProject = null; $('ingress-catalog-search').value = ''; }
  return { render, reset, open: () => { render(); popup.open(); }, close: () => popup.close() };
}
