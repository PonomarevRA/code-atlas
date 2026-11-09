/** Разделяет извлечённые условия и циклы; do относится к семейству while. */
function controlVisible(node, filters) {
  if (!node.decision) return true;
  if (node.dataSelection) return true;
  const loop = ['for', 'foreach', 'while', 'do'].includes(node.kind);
  return loop ? filters.loops !== false : filters.conditions !== false;
}

/**
 * Скрывает только карточки управления. Вызовы и выходы продолжаются от ближайшего
 * видимого владельца; raw branchIds, строки, уверенность и повторные вызовы сохраняются.
 * Возвращает новые массивы, не меняя анализ или стабильные ID выходов.
 */
export function filterPathControls(visuals, edges, stubs, filters = {}) {
  // При видимых условиях и циклах остаётся только отсеять рёбра без источника.
  if (filters && filters.loops !== false && filters.conditions !== false) {
    return { visuals: visuals.slice(), visualEdges: edges.filter(edge => edge.source), stubs: stubs.slice() };
  }
  const byId = new Map(visuals.map(node => [node.id, node]));
  const hidden = new Set(visuals.filter(node => !controlVisible(node, filters)).map(node => node.id));
  const parents = new Map();
  for (const edge of edges) if (byId.get(edge.target)?.decision) parents.set(edge.target, edge.source);
  const visibleEdges = [];
  for (const edge of edges) {
    if (hidden.has(edge.target)) continue;
    let source = edge.source;
    const seen = new Set();
    while (hidden.has(source) && !seen.has(source)) {
      seen.add(source);
      source = parents.get(source) || byId.get(source)?.ownerId;
    }
    if (!source || hidden.has(source)) continue;
    visibleEdges.push(source === edge.source ? edge : { ...edge, source, caption: '' });
  }
  return { visuals: visuals.filter(node => !hidden.has(node.id)), visualEdges: visibleEdges,
    stubs: stubs.filter(stub => !hidden.has(stub.source)) };
}
