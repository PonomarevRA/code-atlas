/** Read saved map summaries and exact-snapshot pages without loading the full graph. */
export const MAP_PAGE_SIZE = 48;

const count = value => Number.isSafeInteger(value) && value >= 0;
const identifier = value => typeof value === 'string' && value.length > 0;
const nodeRecord = row => row && identifier(row.id);
const edgeRecord = row => row && identifier(row.source) && identifier(row.target);

/** Select the pilot on unfiltered overview routes; deep links retain their existing behavior. */
export function needsFullMap({ route = {}, filters = {}, focused = false, searchContext = {} } = {}) {
  return route.level === 'files' || route.level === 'symbols' || focused
    || Boolean(route.node || route.group || route.context || route.path || route.isolate
      || route.block || route.inline || route.isolateBlock || route.wikiNode || route.reading
      || filters.search || filters.kind || filters.relation || filters.preset === 'all'
      || filters.codeProjectIds != null || searchContext.codeQuery);
}

/** Construct a same-origin read for the selected project, revision and optional page scope. */
export function mapPartPath(scope, part, page = {}) {
  const query = new URLSearchParams({ snapshotId: scope.snapshotId, algorithm: scope.algorithm });
  if (part === 'map-group') {
    if (page.groupId != null) query.set('groupId', page.groupId);
    if (page.projectScope != null) query.set('projectScope', page.projectScope);
    query.set('offset', String(page.offset || 0));
    query.set('limit', String(MAP_PAGE_SIZE));
  }
  return `/projects/${encodeURIComponent(scope.projectId)}/${part}?${query}`;
}

/** Require exact revision and algorithm before either response can be rendered. */
function validateScope(data, scope) {
  if (data?.snapshotId !== scope.snapshotId || data.metadata?.snapshotId !== scope.snapshotId
      || data.metadata?.groupingAlgorithm !== scope.algorithm) {
    throw new Error('Снимок карты изменился. Откройте проект заново.');
  }
}

/** Validate the bounded aggregate response and adapt it to the empty raw-graph shell. */
export function mapOverviewGraph(data, scope) {
  validateScope(data, scope);
  if (!Array.isArray(data.communities) || !data.communities.every(nodeRecord)
      || !Array.isArray(data.communityEdges) || !data.communityEdges.every(edgeRecord)
      || !Array.isArray(data.codeProjects) || !data.codeProjects.every(nodeRecord)
      || !data.groupProjects || typeof data.groupProjects !== 'object' || Array.isArray(data.groupProjects)
      || !count(data.metadata.nodeCount) || !count(data.metadata.edgeCount)) {
    throw new Error('Сервер вернул неполный обзор карты. Повторите загрузку.');
  }
  return { ...data, nodes: [], edges: [] };
}

/** Validate the exact page and require edges to stay within the returned nodes. */
export function mapDetailPage(data, scope, page) {
  validateScope(data, scope);
  const offset = page.offset || 0;
  if ((data.groupId ?? null) !== (page.groupId ?? null)
      || (data.projectScope ?? null) !== (page.projectScope ?? null)
      || (data.offset !== undefined && data.offset !== offset)
      || !Array.isArray(data.nodes) || !data.nodes.every(nodeRecord) || data.nodes.length > MAP_PAGE_SIZE
      || !Array.isArray(data.edges) || !data.edges.every(edgeRecord)
      || !count(data.total)
      || (data.nextOffset != null && (!count(data.nextOffset) || data.nextOffset <= offset || data.nextOffset > data.total))) {
    throw new Error('Сервер вернул неполную страницу карты. Повторите загрузку.');
  }
  const ids = new Set(data.nodes.map(node => node.id));
  if (ids.size !== data.nodes.length || data.edges.some(edge => !ids.has(edge.source) || !ids.has(edge.target))) {
    throw new Error('Страница карты содержит связи за пределами загруженных узлов.');
  }
  return data;
}

/** Keep only the latest detail request; callers retain their current page until commit. */
export function createMapPageReader(request, getScope, { pending, commit, failed }) {
  let active = null;

  /** Abort and invalidate a page when the project, scope or selected group changes. */
  function cancel() {
    active?.abort();
    active = null;
  }

  /** Resolve one page for the current overview and reject obsolete results after each await. */
  async function read(page) {
    cancel();
    const scope = getScope();
    if (!scope) return false;
    const controller = new AbortController();
    active = controller;
    const current = () => active === controller && !controller.signal.aborted && getScope() === scope;
    pending(page);
    try {
      const data = await request(mapPartPath(scope, 'map-group', page), undefined, {signal: controller.signal});
      if (!current()) return false;
      commit(page, mapDetailPage(data, scope, page));
      return true;
    } catch (error) {
      if (current()) failed(page, error);
      return false;
    } finally {
      if (active === controller) active = null;
    }
  }

  return { read, cancel };
}

/** Derive project/group cards from captured summaries; never claim project-filtered edge counts. */
export function mapOverviewCards(overview, section, { projects = false, offset = 0, limit = 18 } = {}) {
  const catalog = projects ? overview.codeProjects : overview.communities;
  const rows = [];
  for (const record of catalog) {
    const members = section == null || projects ? record.nodeCount
      : Object.hasOwn(overview.groupProjects, record.id) && Object.hasOwn(overview.groupProjects[record.id] || {}, section)
        ? overview.groupProjects[record.id][section] : 0;
    if (!projects && section != null && !members) continue;
    rows.push({ ...record, codeProjectId: projects ? record.id : section, nodeCount: members,
      kind: projects ? 'project' : 'community', architecture: true });
  }
  rows.sort((a, b) => (b.nodeCount || 0) - (a.nodeCount || 0) || String(a.label || a.id).localeCompare(String(b.label || b.id), 'ru'));
  const cards = rows.slice(offset, offset + limit);
  const ids = new Set(cards.map(card => card.id));
  const edges = projects || section != null ? [] : overview.communityEdges.filter(edge => ids.has(edge.source) && ids.has(edge.target));
  return { cards, edges, total: rows.length, nextOffset: offset + cards.length < rows.length ? offset + cards.length : null };
}
