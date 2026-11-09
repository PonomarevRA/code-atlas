import { $, el } from './dom.js';
import { markChangedNodes, markChangedCommunities } from './changes.js';
/** Навигация по коду владеет переходами, страницами и breadcrumb; состояние остаётся у app. */
export function createCodeNavigation({ state, view, model, filteredNodes, renderIngress, renderContextExpansion, refreshRegexSearch, selectNode, selectBlock, resetBlocks, draw, controls, onDocumentation }) {
  /** Use the same filtered projection and pagination for direct symbol navigation. */
  function pageForNode(navigation, nodeId) {
    return model.architecture(filteredNodes(false), { ...navigation, targetNodeId: nodeId,
      preset: $('architecture-preset').value, relation: $('relation').value }).page;
  }
  /** Переходит по иерархии и сбрасывает только локальный выбор, сохраняя фильтры. */
  function navigateArchitecture(next, nodeId = null) {
    resetBlocks?.();
    onDocumentation(null);
    state.expandedId = null;
    state.ingress.page = 0;
    state.architecture = nodeId === null ? next : { ...next, page: pageForNode(next, nodeId) };
    state.selected = null;
    state.focused = false;
    state.answerRevision++;
    $('selection').replaceChildren(el('h2', 'Код'), el('p', 'Выберите проект, файл и символ для просмотра исходника.', 'muted'));
    $('answer').replaceChildren();
    draw(true);
    controls();
  }
  /** Раскрывает синтетический агрегат; его id никогда не становится выбранным символом. */
  function openArchitectureGroup(id) {
    const group = state.architectureGroups.find(item => item.id === id);
    if (!group) return;
    navigateArchitecture({ level: state.architecture.level === 'projects' ? 'files' : 'symbols', projectId: group.targetProjectId, file: group.targetFile, page: 0 });
    onDocumentation(group);
  }
  /** Рисует проекты/файлы целиком на уровне страницы; исходные символы остаются доступны постранично. */
  function drawArchitecture(fit) {
    view.colorProjects = state.graph.codeProjects || [];
    const matches = filteredNodes(false);
    const architecture = model.architecture(matches, { ...state.architecture, preset: $('architecture-preset').value, relation: $('relation').value });
    state.architecture.page = architecture.page;
    state.architectureMembership = architecture.membership;
    state.architectureGroups = architecture.groups;
    const changedIds = state.comparison?.changedNodeIds || [];
    const annotatedNodes = architecture.scopedNodes.map(node => ({ ...node, community: architecture.membership.get(node.id) }));
    const groups = markChangedCommunities(architecture.groups, annotatedNodes, changedIds);
    const symbols = state.architecture.level === 'symbols';
    const limit = symbols ? 1200 : 1500;
    const edges = architecture.edges.slice(0, limit);
    if (symbols) view.render(markChangedNodes(architecture.nodes, changedIds), edges, state.selected, state.graph.communities || [], $('project-blocks').checked ? state.graph.codeProjects || [] : null, { nodes: architecture.nodes, edges: architecture.edges, scope: 'между символами текущей страницы' });
    else view.renderOverview(groups, edges, null, null);
    renderIngress(architecture);
    if (fit && state.expandedId) view.fit();
    renderContextExpansion();
    if (fit) {
      if (state.expandedId) view.expansion?.focus();
      else if (view.codeReading) view.fitCodeInitial();
      else view.fit();
    }
    const noun = symbols ? 'символов' : state.architecture.level === 'files' ? 'файлов' : 'проектов';
    updateSummary({ architecture, matches, groups, edges, symbols, noun });
    renderBreadcrumbs(state.architecture, state.graph.codeProjects || []);
    updatePage(architecture);
    renderResults({ architecture, matches, groups, symbols, noun });
    $('focus').textContent = state.expandedId === state.selected ? 'Показаны связи узла' : 'Связи узла';
    refreshRegexSearch(matches);
  }
  /** Отделяет числа исходных фактов, элементов страницы и ограниченного SVG. */
  function updateSummary({ architecture, matches, groups, edges, symbols, noun }) {
    const scopeName = $('architecture-preset').value === 'all' ? 'Все связи' : 'Зависимости';
    $('view-title').textContent = `Код · ${symbols ? 'символы' : state.architecture.level === 'files' ? 'файлы' : 'проекты'}`;
    if (symbols && architecture.nodes.some(node => node.readingStructure?.kind === 'html')) {
      $('view-instruction').textContent = 'HTML5-дерево · отступ показывает вложенность. Выберите элемент для статического эскиза.';
    } else if (state.architecture.level === 'files') {
      $('view-instruction').textContent = 'Файлы сгруппированы по папкам. «Внутрь» открывает символы файла.';
    } else if (symbols && architecture.nodes.some(node => node.readingStructure?.kind === 'source')) {
      $('view-instruction').textContent = 'Символы идут в порядке строк. Для просмотра выберите карточку.';
    }
    $('back-overview').hidden = state.architecture.level === 'projects';
    $('empty').hidden = Boolean(state.graph.nodes.length);
    $('node-count').textContent = `${matches.length} / ${state.graph.nodes.length}`;
    $('graph-stats').textContent = `${scopeName} · ${architecture.scopedNodes.length} символов · ${architecture.rawEdges.length} фактов в выбранной области`;
    $('visible-count').textContent = `${symbols ? architecture.nodes.length : groups.length} из ${architecture.total} ${noun} · ${edges.length} из ${architecture.edges.length} связей между показанными элементами · всего ${architecture.allEdges.length} на этом уровне`;
    if (symbols && architecture.inheritanceContextCount) {
      const own = architecture.nodes.length - architecture.inheritanceContextCount;
      $('visible-count').textContent = `${own} из ${architecture.total} символов · ${architecture.inheritanceContextCount} родительских типов из других файлов · ${edges.length} связей${architecture.inheritanceContextTruncated ? ' · контекст наследования ограничен' : ''}`;
    }
    if (view.inlineSceneActive && state.inlineScene) {
      const scene = state.inlineScene;
      $('visible-count').textContent = `${scene.items.length} карточек · ${architecture.scopedNodes.length} символов в области · ${scene.edges.length} из ${scene.counts.totalProjectedEdges} связей между видимыми владельцами${scene.truncated ? ' · достигнут предел раскрытия' : ''}`;
    }
  }
  /** Показывает путь к текущему файлу и явные переходы на родительские уровни. */
  function renderBreadcrumbs(navigation, projects) {
    const crumbs = $('architecture-breadcrumbs');
    crumbs.replaceChildren();
    const root = el('button', 'Все проекты');
    root.addEventListener('click', () => navigateArchitecture({ level: 'projects', projectId: null, file: null, page: 0 }));
    crumbs.append(root);
    if (navigation.level !== 'projects') {
      const project = projects.find(item => String(item.id) === navigation.projectId);
      const button = el('button', project?.label || 'Без проекта');
      button.addEventListener('click', () => navigateArchitecture({ ...navigation, level: 'files', file: null, page: 0 }));
      crumbs.append(button);
    }
    if (navigation.level === 'symbols') crumbs.append(el('span', navigation.file || 'Без исходного файла'));
  }
  /** Обновляет границы страницы из результата проекции, не вычисляя их повторно. */
  function updatePage(architecture) {
    $('architecture-page').textContent = `${architecture.page + 1} / ${architecture.pageCount}`;
    $('architecture-previous').disabled = architecture.page === 0;
    $('architecture-next').disabled = architecture.page + 1 === architecture.pageCount;
  }
  /** Список выбирает raw-символы либо раскрывает агрегаты; эти действия не смешиваются. */
  function renderResults({ architecture, matches, groups, symbols, noun }) {
    const search = Boolean($('search').value.trim());
    const list = search ? matches : symbols ? architecture.nodes : groups;
    $('results-count').textContent = search ? `${matches.length} совпадений во всём фильтре` : `${architecture.total} ${noun} · страница ${architecture.page + 1}`;
    $('results').replaceChildren();
    for (const item of list.slice(0, search ? state.listLimit : 240)) {
      const button = el('button', undefined, `result${item.id === state.selected ? ' selected' : ''}`);
      button.dataset.nodeId = item.id;
      let subtitle = search || symbols ? item.file || 'Без исходного файла' : `${item.nodeCount} символов`;
      if (!search && item.readingStructure?.kind === 'html') {
        const {depth, parentLabel} = item.readingStructure;
        const location = item.line ? `строка ${item.line}` : 'добавлен HTML5';
        subtitle = parentLabel ? `Внутри ${parentLabel} · ${location}` : location;
        button.style.setProperty('--html-result-indent', `${Math.min(6, depth) * 8}px`);
        button.classList.add('html-tree-result');
        button.setAttribute('aria-label', `${item.label}. Уровень ${depth + 1}. ${subtitle}`);
      }
      button.append(el('strong', item.label || item.id), el('small', subtitle));
      button.addEventListener('click', () => search || symbols ? selectNode(item.id) : selectBlock(item.id));
      $('results').append(button);
    }
    $('more').hidden = !search || matches.length <= state.listLimit;
  }
  $('architecture-preset').addEventListener('change', () => { state.architecture.page = 0; draw(true); });
  $('architecture-previous').addEventListener('click', () => { state.expandedId = null; state.architecture.page--; draw(true); });
  $('architecture-next').addEventListener('click', () => { state.expandedId = null; state.architecture.page++; draw(true); });
  return { navigate: navigateArchitecture, open: openArchitectureGroup, render: drawArchitecture, pageForNode };
}
