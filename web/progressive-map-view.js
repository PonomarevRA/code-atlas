import { $, el } from './dom.js';
import { createNodeResult } from './result-list.js';
import { createMapPageReader, mapOverviewCards, MAP_PAGE_SIZE } from './progressive-map.js';

const OVERVIEW_PAGE_SIZE = 18;
const formatCount = value => Number(value || 0).toLocaleString('ru-RU');

/** Render the selected aggregate or bounded detail page using the existing map and result controls. */
export function createProgressiveMapView({ view, api, getMap, commitPage, selectNode, openFull, onNavigate }) {
  const toolbar = $('progressive-map-controls');
  const previous = $('progressive-map-previous');
  const next = $('progressive-map-next');
  const retry = $('progressive-map-retry');
  let failedPage = null;
  let allowPrevious = false, allowNext = false;

  const reader = createMapPageReader(api, getMap, {
    pending() {
      failedPage = null;
      retry.hidden = true;
      toolbar.setAttribute('aria-busy', 'true');
      previous.disabled = true;
      next.disabled = true;
      $('progressive-map-state').textContent = 'Загружаем детали карты…';
    },
    commit(page, data) {
      const hadFocus = toolbar.contains(document.activeElement) || view.svg.contains(document.activeElement);
      resetStatus();
      commitPage(page, data);
      onNavigate();
      const firstCard = view.nodeElements.values().next().value?.group;
      if (hadFocus) {
        (firstCard || view.svg).focus({preventScroll:true});
      }
    },
    failed(page, error) {
      resetStatus();
      failedPage = page;
      retry.hidden = false;
      $('progressive-map-state').textContent = error.message;
    },
  });

  /** Clear transient progress while retaining the last successfully rendered scope. */
  function resetStatus() {
    toolbar.setAttribute('aria-busy', 'false');
    retry.hidden = true;
    failedPage = null;
    previous.disabled = !allowPrevious;
    next.disabled = !allowNext;
  }

  /** Repaint the aggregate selection and discard any pending detail response. */
  function showOverview({ projectScope = null, groups = false, offset = 0, projectOffset = 0 } = {}) {
    const map = getMap();
    if (!map) return;
    reader.cancel();
    resetStatus();
    map.section = {projectScope, groups, offset, projectOffset};
    commitPage(null, null);
    onNavigate();
  }

  /** Translate a card ID using current visible data, never an external route label. */
  function openCard(id) {
    const map = getMap();
    const item = !map?.page && map?.cards.get(id);
    if (!item) return;
    if (item.kind === 'project') {
      showOverview({projectScope:id, groups:true, projectOffset:map.section.offset});
      (view.communityElements.values().next().value?.group || view.svg).focus({preventScroll:true});
      return;
    }
    return reader.read({groupId:id, projectScope:map.section.projectScope, offset:0});
  }

  /** Return a detail page to its own project/group overview, then to the top-level cards. */
  function back() {
    const map = getMap();
    if (!map) return;
    const key = map.page?.groupId || map.section.projectScope;
    const projectOffset = map.section.projectOffset || 0;
    if (map.page) showOverview(map.section);
    else showOverview({offset:projectOffset});
    (view.communityElements.get(key)?.group || view.communityElements.values().next().value?.group || view.svg).focus({preventScroll:true});
  }

  /** Go to a bounded page in the same selected section. */
  function changePage(direction) {
    const map = getMap();
    if (!map) return;
    if (!map.page) {
      showOverview({ ...map.section, offset: Math.max(0, map.section.offset + direction * OVERVIEW_PAGE_SIZE) });
      return;
    }
    const offset = direction > 0 ? map.detail.nextOffset : Math.max(0, map.page.offset - MAP_PAGE_SIZE);
    if (offset != null) void reader.read({...map.page, offset});
  }

  /** Render one page without changing the selected scope or issuing network requests. */
  function render(map, fit, selected, defaultProjects) {
    toolbar.hidden = false;
    $('back-overview').hidden = !map.page && !map.section.projectScope && !map.section.groups;
    $('back-overview').textContent = map.page ? '← Группы' : '← Проекты';
    const group = map.data.communities.find(item => item.id === map.page?.groupId);
    const project = map.data.codeProjects.find(item => item.id === map.section.projectScope);
    const projects = defaultProjects && !map.section.groups && !map.section.projectScope && map.data.codeProjects.length > 0;
    $('progressive-map-groups').hidden = Boolean(map.page || map.section.projectScope || !defaultProjects || !map.data.codeProjects.length);
    $('progressive-map-groups').textContent = projects ? 'Все группы' : 'Все проекты';
    let items, total, offset, nextOffset, caption;
    if (map.page) {
      const {nodes, edges, total: count, nextOffset: nextPage, edgesTruncated} = map.detail;
      items = nodes; total = count; offset = map.page.offset; nextOffset = nextPage;
      $('view-title').textContent = group?.label || project?.label || 'Детали карты';
      caption = `${formatCount(edges.length)} связей между показанными узлами${edgesTruncated ? ' · список связей ограничен' : ''}`;
      view.render(nodes, edges, selected, map.data.communities);
      map.cards.clear();
    } else {
      const result = mapOverviewCards(map.data, map.section.projectScope, {projects, offset:map.section.offset, limit:OVERVIEW_PAGE_SIZE});
      items = result.cards; total = result.total; offset = map.section.offset; nextOffset = result.nextOffset;
      map.cards = new Map(items.map(item => [item.id, item]));
      $('view-title').textContent = project?.label || (projects ? 'Код · проекты' : 'Группы кода');
      caption = projects ? 'Выберите проект, чтобы увидеть его группы.' : 'Выберите группу, чтобы загрузить её узлы и связи.';
      view.renderOverview(items, result.edges, null);
    }
    $('view-instruction').textContent = caption;
    const noun = map.page ? 'узлов' : projects ? 'проектов' : 'групп';
    const range = items.length ? `${formatCount(offset + 1)}–${formatCount(offset + items.length)} из ${formatCount(total)} ${noun}` : `0 ${noun}`;
    if (toolbar.getAttribute('aria-busy') !== 'true' && !failedPage) $('progressive-map-state').textContent = range;
    $('visible-count').textContent = map.page ? `${range} · ${caption}` : range;
    $('node-count').textContent = formatCount(map.data.metadata.nodeCount);
    $('graph-stats').textContent = `${formatCount(map.data.metadata.nodeCount)} узлов · ${formatCount(map.data.metadata.edgeCount)} связей в снимке`;
    $('results-count').textContent = range;
    const results = $('results');
    results.replaceChildren();
    for (const item of items) {
      if (map.page) results.append(createNodeResult(item, selected, selectNode));
      else {
        const button = el('button', undefined, 'result');
        button.type = 'button';
        button.append(el('strong', item.label || item.id), el('small', `${formatCount(item.nodeCount)} узлов`));
        button.addEventListener('click', () => openCard(item.id));
        results.append(button);
      }
    }
    $('more').hidden = true;
    allowPrevious = offset > 0;
    allowNext = nextOffset != null;
    const loading = toolbar.getAttribute('aria-busy') === 'true';
    previous.disabled = loading || !allowPrevious;
    next.disabled = loading || !allowNext;
    if (fit) {
      if (view.codeReading) view.fitCodeInitial();
      else view.fit();
    }
  }

  $('progressive-map-full').addEventListener('click', openFull);
  $('progressive-map-groups').addEventListener('click', () => {
    const map = getMap();
    if (map) showOverview({groups:!map.section.groups});
  });
  previous.addEventListener('click', () => changePage(-1));
  next.addEventListener('click', () => changePage(1));
  retry.addEventListener('click', () => { if (failedPage) void reader.read(failedPage); });
  return {
    render, openCard, back, showOverview, read: reader.read,
    /** Cancel the pending page without replacing the committed map. */
    cancel() { reader.cancel(); resetStatus(); },
    /** Hide on view changes and release any pending request or retry state. */
    hide() { reader.cancel(); resetStatus(); toolbar.hidden = true; },
  };
}
