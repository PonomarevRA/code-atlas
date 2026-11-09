import { aggregateCommunities, codeProjectId } from './filters.js';
import { aggregateProjectFragments, aggregateProjectLinks } from './project-blocks.js';

export const GROUP_PAGE_SIZE = 6;
export const GROUP_CARD = Object.freeze({ width: 320, height: 204 });

/** Считает состав по полному фильтру; подписи групп остаются фактами алгоритма, а не назначением модуля. */
function inventories(nodes) {
  const result = new Map();
  for (const node of nodes) {
    const owner = codeProjectId(node);
    if (!result.has(owner)) result.set(owner, { groups: new Set(), files: new Set(), nodeCount: 0 });
    const inventory = result.get(owner);
    inventory.nodeCount++;
    inventory.groups.add(String(node.community ?? 'ungrouped'));
    if (node.file) inventory.files.add(node.file);
  }
  return result;
}

/** Название в локальном обзоре опирается на файлы проекта, а исходная метка алгоритма сохраняется отдельно. */
function labelProjectGroups(items, files, projectPath = '') {
  const prefix = projectPath.replaceAll('\\', '/').replace(/\/$/, '') + '/';
  const labels = new Map();
  for (const item of items) {
    const paths = files.get(String(item.id)), folders = new Map();
    for (const file of paths) {
      const normalized = file.replaceAll('\\', '/');
      const relative = normalized.startsWith(prefix) ? normalized.slice(prefix.length) : normalized;
      const folder = relative.includes('/') ? relative.slice(0, relative.lastIndexOf('/')) : 'Корень проекта';
      folders.set(folder, (folders.get(folder) || 0) + 1);
    }
    const ordered = [...folders].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'ru'));
    const label = ordered[0]?.[0] || 'Без исходного файла';
    if (!labels.has(label)) labels.set(label, []);
    labels.get(label).push(item);
    item.groupSummary.files = paths.size;
    item.groupSummary.sampleFiles = [...paths].sort().slice(0, 2);
    item.groupSummary.algorithmLabel = item.label;
  }
  for (const [label, members] of labels) {
    members.sort((a, b) => String(a.id).localeCompare(String(b.id), 'en'));
    for (const [index, item] of members.entries()) {
      item.label = members.length > 1 ? `${label} · группа ${index + 1}` : label;
      item.displayLabel = item.label;
    }
  }
}

/** Показывает максимум шесть проектов или групп проекта; пагинация не меняет членство и исходные ID. */
export function groupOverview(graph, nodes, { projectId = null, page = 0, relation = '', fragments = true, changedNodeIds = [] } = {}) {
  const scoped = projectId === null ? nodes : nodes.filter(node => codeProjectId(node) === projectId);
  const inventory = inventories(scoped);
  let items, edges;
  if (projectId === null) {
    const projects = new Map((graph.codeProjects || []).map(project => [String(project.id), project]));
    const namespaces = new Map();
    for (const project of projects.values()) {
      const parts = String(project.label || '').split('.');
      if (parts.length > 1) namespaces.set(parts[0], (namespaces.get(parts[0]) || 0) + 1);
    }
    items = [];
    for (const [owner, composition] of inventory) {
      const project = projects.get(owner);
      const label = project?.label || (owner === '__unassigned__' ? 'Без проекта' : owner);
      const parts = label.split('.');
      // Повторяющийся namespace сокращается только на карточке; полное имя остаётся в label и tooltip.
      const displayLabel = (namespaces.get(parts[0]) || 0) > 1 ? parts.slice(1).join('.') : label;
      items.push({ id: JSON.stringify(['project', owner]), label, displayLabel,
        codeProjectId: owner, targetProjectId: owner, architecture: true, architectureLevel: 'projects',
        nodeCount: composition.nodeCount, fileCount: composition.files.size, projectKind: project?.kind,
        groupSummary: { scope: 'project', files: composition.files.size, groups: composition.groups.size } });
    }
    const facts = relation ? graph.edges.filter(edge => edge.relation === relation) : graph.edges;
    const links = aggregateProjectLinks(scoped, facts).links;
    edges = links.map(edge => ({ ...edge, source: JSON.stringify(['project', edge.source]), target: JSON.stringify(['project', edge.target]) }));
  } else {
    const aggregate = fragments ? aggregateProjectFragments(graph, scoped, { relation }) : aggregateCommunities(graph, scoped, { relation });
    items = aggregate.communities.map(group => ({ ...group, codeProjectId: projectId,
      originalCommunityId: group.originalCommunityId ?? String(group.id),
      displayLabel: group.originalCommunityLabel || group.label,
      groupSummary: { scope: 'group', files: 0 },
    }));
    const files = new Map(items.map(group => [String(group.id), new Set()]));
    for (const node of scoped) {
      const id = fragments ? JSON.stringify([codeProjectId(node), String(node.community ?? 'ungrouped')]) : String(node.community ?? 'ungrouped');
      if (node.file) files.get(id)?.add(node.file);
    }
    const project = (graph.codeProjects || []).find(owner => String(owner.id) === projectId);
    labelProjectGroups(items, files, project?.path);
    edges = aggregate.communityEdges;
  }
  const changed = new Set(changedNodeIds), changedCounts = new Map();
  for (const node of scoped) {
    if (!changed.has(node.id)) continue;
    const id = projectId === null ? JSON.stringify(['project', codeProjectId(node)])
      : fragments ? JSON.stringify([codeProjectId(node), String(node.community ?? 'ungrouped')]) : String(node.community ?? 'ungrouped');
    changedCounts.set(id, (changedCounts.get(id) || 0) + 1);
  }
  for (const item of items) item.changedNodeCount = changedCounts.get(String(item.id)) || 0;
  items.sort((a, b) => String(a.label).localeCompare(String(b.label), 'ru') || String(a.id).localeCompare(String(b.id), 'en'));
  const pageCount = Math.max(1, Math.ceil(items.length / GROUP_PAGE_SIZE));
  const current = Math.max(0, Math.min(pageCount - 1, Math.trunc(page) || 0));
  const visible = items.slice(current * GROUP_PAGE_SIZE, (current + 1) * GROUP_PAGE_SIZE);
  const visibleIds = new Set(visible.map(item => String(item.id)));
  return { items: visible, allItems: items, nodes: scoped,
    edges: edges.filter(edge => visibleIds.has(String(edge.source)) && visibleIds.has(String(edge.target))),
    totalEdges: edges.length, total: items.length, page: current, pageCount };
}

/** Компактная таблица обзора: координаты не притворяются порядком исполнения или причинностью. */
export function groupOverviewLayout(nodes, layout) {
  if (!nodes.length) return layout;
  const columns = Math.min(3, nodes.length), gapX = 72, gapY = 44;
  for (const [index, node] of nodes.entries()) {
    const id = String(node.id);
    layout.positions.set(id, { ...layout.positions.get(id), width: GROUP_CARD.width, height: GROUP_CARD.height,
      x: GROUP_CARD.width / 2 + (index % columns) * (GROUP_CARD.width + gapX),
      y: GROUP_CARD.height / 2 + Math.floor(index / columns) * (GROUP_CARD.height + gapY) });
  }
  layout.bounds = { x: -64, y: -64, width: columns * (GROUP_CARD.width + gapX) - gapX + 128,
    height: Math.ceil(nodes.length / columns) * (GROUP_CARD.height + gapY) - gapY + 128 };
  return layout;
}
