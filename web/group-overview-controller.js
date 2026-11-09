import { $, el } from './dom.js';
import { groupOverview } from './group-overview-model.js';

/** Управляет обзором «проекты → группы → символы», используя существующий адрес и историю workspace. */
export function createGroupOverview({ state, view, resetBlocks, selectBlock, draw, controls, checkpoint, commit, onLayout = () => {} }) {
  const navigation = el('nav', undefined, 'architecture-navigation group-navigation');
  navigation.setAttribute('aria-label', 'Навигация по группам');
  navigation.hidden = true;
  // Переходы между страницами нужны и в режиме «Только карта» со скрытыми настройками.
  $('map-chrome-controls').after(navigation);

  /** Переход не меняет checkbox-фильтры; точный проект и страница сохраняются в URL/history. */
  function open(projectId = null, page = 0) {
    checkpoint(); resetBlocks();
    state.community = null; state.selected = null; state.focused = false; state.expandedId = null;
    state.answerRevision++;
    $('answer').replaceChildren();
    $('selection').replaceChildren(el('h2', 'Группы кода'), el('p', 'Выберите проект или группу. Кнопка в карточке открывает её содержимое.', 'muted'));
    state.architecture = { level: projectId === null ? 'projects' : 'files', projectId, file: null, page };
    draw(true); controls(); commit();
  }

  /** Рисует ограниченный обзор; связи относятся только к карточкам текущей страницы и появляются при фокусе. */
  function render(nodes, { fragments, relation, fit }) {
    const projectId = state.architecture.level === 'projects' ? null : state.architecture.projectId;
    const result = groupOverview(state.graph, nodes, { projectId, page: state.architecture.page, fragments, relation,
      changedNodeIds: state.comparison?.changedNodeIds || [] });
    state.architecture.page = result.page;
    view.renderOverview(result.items, result.edges, state.selectedBlock, null);
    const title = projectId === null ? 'Группы · начните с проекта'
      : `Группы · ${state.graph.codeProjects?.find(project => String(project.id) === projectId)?.label || 'Без проекта'}`;
    $('view-title').textContent = title;
    $('view-instruction').textContent = 'Проект → группы → символы. Кнопка в карточке открывает следующий уровень. Линии при выборе показывают статические зависимости, не порядок выполнения.';
    $('visible-count').textContent = `${result.items.length} из ${result.total} ${projectId === null ? 'проектов' : 'групп'} · страница ${result.page + 1} / ${result.pageCount} · ${result.edges.length} связей на странице из ${result.totalEdges} на этом уровне`;
    $('node-count').textContent = `${result.nodes.length} / ${state.graph.nodes.length}`;
    $('graph-stats').textContent = `${result.total} ${projectId === null ? 'проектов' : 'групп'} · ${result.nodes.length} символов в области`;
    $('back-overview').hidden = projectId === null;
    $('back-overview').textContent = '← Все проекты';
    $('reset').textContent = 'Обзор проектов';
    const home = el('button', 'Все проекты'); home.disabled = projectId === null;
    home.addEventListener('click', () => open());
    const previous = el('button', '← Страница'); previous.disabled = result.page === 0;
    previous.addEventListener('click', () => open(projectId, result.page - 1));
    const next = el('button', 'Страница →'); next.disabled = result.page + 1 >= result.pageCount;
    next.addEventListener('click', () => open(projectId, result.page + 1));
    navigation.replaceChildren(home, el('span', title), previous, el('span', `${result.page + 1} / ${result.pageCount}`), next);
    navigation.hidden = false;
    $('results-count').textContent = `${result.total} ${projectId === null ? 'проектов' : 'групп'} · страница ${result.page + 1}`;
    $('results').replaceChildren();
    for (const item of result.items) {
      const button = el('button', undefined, 'result');
      button.dataset.nodeId = item.id;
      button.append(el('strong', item.displayLabel || item.label), el('small', `${item.nodeCount} символов · ${item.groupSummary.files} файлов`));
      button.addEventListener('click', () => selectBlock(item.id));
      $('results').append(button);
    }
    $('more').hidden = true;
    onLayout();
    if (fit) view.fit();
    return result;
  }
  return { open, render, hide: () => { navigation.hidden = true; onLayout(); } };
}
