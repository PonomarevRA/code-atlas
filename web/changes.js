/** Добавляет визуальную отметку файла, не изменяя узлы сохранённого графа. */
export function markChangedNodes(nodes, changedNodeIds = []) {
  const changed = new Set(changedNodeIds);
  return nodes.map(node => ({ ...node, changedFile: changed.has(node.id) }));
}
/** Считает отмеченные узлы только в переданном (уже отфильтрованном) наборе. */
export function markChangedCommunities(communities, nodes, changedNodeIds = []) {
  const changed = new Set(changedNodeIds);
  const counts = new Map();
  for (const node of nodes) {
    if (!changed.has(node.id)) continue;
    const id = String(node.community ?? 'ungrouped');
    counts.set(id, (counts.get(id) || 0) + 1);
  }
  return communities.map(group => ({ ...group, changedNodeCount: counts.get(String(group.id)) || 0 }));
}
