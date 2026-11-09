/** Возвращает стабильный ключ для узлов, не отнесённых к внутреннему проекту. */
export function codeProjectId(node) { return String(node.codeProjectId ?? '__unassigned__'); }
/** Фильтрует узлы: слова через пробел объединены AND, проекты — OR; null означает все проекты. */
export function filterNodes(graph, { keywords = '', kind = '', relation = '', codeProjectIds = null, neighborOf = null } = {}) {
  const terms = keywords.trim().toLocaleLowerCase().split(/\s+/u).filter(Boolean);
  const allowedProjects = codeProjectIds === null ? null : new Set(codeProjectIds.map(String));
  let result = [];
  for (const node of graph.nodes) {
    if (kind && node.kind !== kind) continue;
    if (allowedProjects && !allowedProjects.has(codeProjectId(node))) continue;
    const text = `${node.label || ''} ${node.file || ''} ${node.id}`.toLocaleLowerCase();
    if (!terms.every(term => text.includes(term))) continue;
    result.push(node);
  }
  if (!relation && !neighborOf) return result;
  const ids = new Set(result.map(node => node.id));
  const connected = new Set();
  if (neighborOf && ids.has(neighborOf)) connected.add(neighborOf);
  for (const edge of graph.edges) {
    if (relation && edge.relation !== relation) continue;
    if (!ids.has(edge.source) || !ids.has(edge.target)) continue;
    if (neighborOf && edge.source !== neighborOf && edge.target !== neighborOf) continue;
    connected.add(edge.source); connected.add(edge.target);
  }
  return result.filter(node => connected.has(node.id));
}
/** Считает индуцированный подграф; встречные потоки агрегируются отдельно с сохранением источника. */
export function aggregateCommunities(graph, nodes, { relation = '' } = {}) {
  const definitions = new Map((graph.communities || []).map(group => [String(group.id), group]));
  const membership = new Map();
  const communities = new Map();
  for (const node of nodes) {
    const id = String(node.community ?? 'ungrouped');
    membership.set(node.id, id);
    if (!communities.has(id)) communities.set(id, { ...(definitions.get(id) || { id, label: 'Без группы' }), nodeCount: 0, internalEdges: 0, externalEdges: 0 });
    communities.get(id).nodeCount++;
  }
  const edges = [], pairs = new Map();
  for (const edge of graph.edges) {
    if (relation && edge.relation !== relation) continue;
    const source = membership.get(edge.source), target = membership.get(edge.target);
    if (source === undefined || target === undefined) continue;
    edges.push(edge);
    if (source === target) { communities.get(source).internalEdges++; continue; }
    communities.get(source).externalEdges++;
    communities.get(target).externalEdges++;
    const key = JSON.stringify([source, target]);
    if (!pairs.has(key)) pairs.set(key, { source, target, weight: 0 });
    pairs.get(key).weight++;
  }
  return { communities: [...communities.values()], communityEdges: [...pairs.values()], edges };
}
