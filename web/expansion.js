/** Собирает ограниченное окружение внутри текущего фильтра; факты и направления не изменяет. */
export function expansionNeighborhood(graph, nodeId, { nodes = graph.nodes, relation = '', relations = null, maxNodes = 25, maxEdges = 400 } = {}) {
  const allowed = new Map(nodes.map(node => [node.id, node]));
  if (!allowed.has(nodeId)) return null;
  const eligible = [];
  const neighbors = new Set();
  for (const edge of graph.edges) {
    if (!allowed.has(edge.source) || !allowed.has(edge.target)) continue;
    if (relation && edge.relation !== relation) continue;
    if (relations && !relations.includes(edge.relation)) continue;
    eligible.push(edge);
    if (edge.source === nodeId) neighbors.add(edge.target);
    if (edge.target === nodeId) neighbors.add(edge.source);
  }
  neighbors.delete(nodeId);
  const ordered = [...neighbors].sort((a, b) => String(a).localeCompare(String(b)));
  const visibleIds = [nodeId, ...ordered.slice(0, Math.max(0, maxNodes - 1))];
  const visible = new Set(visibleIds);
  const edges = eligible.filter(edge => visible.has(edge.source) && visible.has(edge.target));
  edges.sort((a, b) => {
    const directA = Number(a.source === nodeId || a.target === nodeId);
    const directB = Number(b.source === nodeId || b.target === nodeId);
    return directB - directA || JSON.stringify([a.source, a.target, a.relation]).localeCompare(JSON.stringify([b.source, b.target, b.relation]));
  });
  return { selectedId: nodeId, nodes: visibleIds.map(id => allowed.get(id)), edges: edges.slice(0, maxEdges), totalNeighbors: neighbors.size, totalEdges: edges.length, omittedNodes: neighbors.size - visibleIds.length + 1, omittedEdges: Math.max(0, edges.length - maxEdges) };
}
