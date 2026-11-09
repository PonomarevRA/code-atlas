import { codeProjectId } from './filters.js';
import { classifyCodeNode } from './code-semantics.js';
import { folderReadingOrder, symbolReadingOrder } from './code-structure.js';
const HTML_PAGE_SIZE = 48;
export const DEPENDENCY_RELATIONS = new Set(['calls', 'references', 'imports', 'inherits', 'inherits_from', 'extends', 'implements', 'dispatches_to', 'reads', 'writes', 'initializes_from']);

/**
 * Отличает одинаково названные области только сохранёнными путём/манифестом/языком.
 * @param {object} project Публичный codeProject из того же снимка, без чтения рабочего дерева.
 * @returns {string} Подпись исходных фактов либо пустая строка; бизнес-назначение не угадывается.
 */
export function codeProjectSourceSubtitle(project) {
  if(!project)return '';
  const parts=[];
  if(typeof project.path==='string'&&project.path)parts.push(project.path);
  if(typeof project.manifest==='string'&&project.manifest)parts.push(project.manifest);
  else if(typeof project.language==='string'&&project.language)parts.push(project.language);
  return parts.join(' · ');
}

/** До 32 непосредственных родителей текущей страницы доступны из соседнего файла; глобальные фильтры сохраняются. */
function inheritanceContext(graph, nodes, ordered, { page, pageSize, relation }) {
  const size = Math.max(1, Math.min(240, Math.trunc(pageSize) || 240));
  const last = Math.max(0, Math.ceil(ordered.length / size) - 1);
  const current = Math.max(0, Math.min(last, Math.trunc(page) || 0));
  const visible = new Set(ordered.slice(current * size, (current + 1) * size).map(node => node.id));
  const owned = new Set(ordered.map(node => node.id));
  const byId = new Map(nodes.map(node => [node.id, node]));
  const parentIds = new Set();
  for (const edge of graph.edges || []) {
    if (!['inherits', 'inherits_from', 'extends', 'implements'].includes(edge.relation) || relation && edge.relation !== relation) continue;
    if (visible.has(edge.source) && !owned.has(edge.target) && byId.has(edge.target)) parentIds.add(edge.target);
  }
  const ids = [...parentIds].sort();
  return { nodes: ids.slice(0, 32).map(id => ({ ...byId.get(id), inheritanceContext: true })), truncated: ids.length > 32 };
}
/**
 * Строит обзор выбранного уровня по полному индуцированному набору, затем применяет страницы.
 * @param {object} graph Сохранённый граф с edges/codeProjects; не изменяется.
 * @param {Array<object>} nodes Узлы разрешённого фильтра; только их реальные связи участвуют в проекции.
 * @param {object} options level/projectId/file/preset/relation задают область; page/pageSize — ограниченную страницу.
 * @returns {object} Агрегаты, рёбра и membership с исходными ID; одинаковые подписи различаются только сохранёнными sourceSubtitle.
 */
export function architectureGraph(graph, nodes, { level = 'projects', projectId = null, file = null, preset = 'dependencies', page = 0, pageSize = 240, relation = '', targetNodeId = null } = {}) {
  const scopedNodes = nodes.filter(node => (level === 'projects' || codeProjectId(node) === projectId) && (level !== 'symbols' || (node.file || null) === file));
  const sourceOrder = level === 'symbols' ? symbolReadingOrder(graph, scopedNodes, file, relation) : scopedNodes;
  // An indented tree is one card per row; keep a whole page usable on the canvas.
  const pageLimit = sourceOrder[0]?.readingStructure?.kind === 'html' ? HTML_PAGE_SIZE : 240;
  const size = Math.max(1, Math.min(pageLimit, Math.trunc(pageSize) || 240));
  const targetIndex = level === 'symbols' && targetNodeId !== null ? sourceOrder.findIndex(node => node.id === targetNodeId) : -1;
  const requestedPage = targetIndex >= 0 ? Math.floor(targetIndex / size) : page;
  const parents = level === 'symbols'
    ? inheritanceContext(graph, nodes, sourceOrder, { page: requestedPage, pageSize: size, relation })
    : { nodes: [], truncated: false };
  scopedNodes.push(...parents.nodes);
  const projects = new Map((graph.codeProjects || []).map(project => [String(project.id), project]));
  const repeatedLabels=new Map();
  for(const project of projects.values())repeatedLabels.set(project.label,(repeatedLabels.get(project.label)||0)+1);
  const membership = new Map(), groups = new Map(), groupFiles = new Map();
  for (const node of scopedNodes) {
    const owner = codeProjectId(node), path = node.file || null;
    const id = level === 'projects' ? JSON.stringify(['project', owner]) : JSON.stringify(['file', owner, path]);
    membership.set(node.id, level === 'symbols' ? node.id : id);
    if (level === 'symbols') continue;
    if (!groups.has(id)) {
      const label = level === 'projects' ? projects.get(owner)?.label || (owner === '__unassigned__' ? 'Без проекта' : owner) : path || 'Без исходного файла';
      groups.set(id, { id, label, codeProjectId: owner, targetProjectId: owner, targetFile: level === 'projects' ? null : path, nodeCount: 0, internalEdges: 0, externalEdges: 0, architecture: true, architectureLevel: level,
        projectKind: level === 'projects' ? projects.get(owner)?.kind : undefined,
        sourceSubtitle: level==='projects' && repeatedLabels.get(label)>1 ? codeProjectSourceSubtitle(projects.get(owner)) : '',
        roleCounts: {}, fileCount: 0, displayLabel: level === 'projects' ? label : path?.split(/[\\/]/).pop() || label });
      groupFiles.set(id, new Set());
    }
    groups.get(id).nodeCount++;
    const group = groups.get(id);
    const role = classifyCodeNode(node).id;
    group.roleCounts[role] = (group.roleCounts[role] || 0) + 1;
    if (path) groupFiles.get(id).add(path);
  }
  for (const group of groups.values()) group.fileCount = groupFiles.get(group.id).size;
  const rawEdges = [], pairs = new Map();
  for (const edge of graph.edges) {
    if (relation && edge.relation !== relation) continue;
    if (preset !== 'all' && !DEPENDENCY_RELATIONS.has(edge.relation)) continue;
    const source = membership.get(edge.source), target = membership.get(edge.target);
    if (source === undefined || target === undefined) continue;
    rawEdges.push(edge);
    if (level === 'symbols') continue;
    if (source === target) { groups.get(source).internalEdges++; continue; }
    groups.get(source).externalEdges++; groups.get(target).externalEdges++;
    const key = JSON.stringify([source, target]);
    if (!pairs.has(key)) pairs.set(key, { source, target, weight: 0 });
    pairs.get(key).weight++;
  }
  const orderedNodes = sourceOrder;
  const orderedGroups = [...groups.values()];
  if (level === 'files') folderReadingOrder(orderedGroups);
  else orderedGroups.sort((a, b) => a.label.localeCompare(b.label, 'ru') || a.id.localeCompare(b.id, 'en'));
  const total = level === 'symbols' ? orderedNodes.length : orderedGroups.length;
  const pageCount = Math.max(1, Math.ceil(total / size));
  const currentPage = Math.max(0, Math.min(pageCount - 1, Math.trunc(requestedPage) || 0));
  const visibleGroups = orderedGroups.slice(currentPage * size, (currentPage + 1) * size);
  const parentNodes = orderedNodes[0]?.readingStructure
    ? parents.nodes.map(node => ({ ...node, readingStructure: { kind: 'source', label: 'Родительские типы из других файлов' } }))
    : parents.nodes;
  const visibleNodes = level === 'symbols' ? [...orderedNodes.slice(currentPage * size, (currentPage + 1) * size), ...parentNodes] : scopedNodes;
  const visible = new Set(level === 'symbols' ? visibleNodes.map(node => node.id) : visibleGroups.map(group => group.id));
  const allEdges = level === 'symbols' ? rawEdges : [...pairs.values()];
  return { groups: visibleGroups, edges: allEdges.filter(edge => visible.has(edge.source) && visible.has(edge.target)), allEdges, nodes: visibleNodes, scopedNodes, membership, rawEdges, total, page: currentPage, pageCount,
    inheritanceContextCount: parents.nodes.length, inheritanceContextTruncated: parents.truncated };
}
