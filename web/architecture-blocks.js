/**
 * Логические группы ресурсов не утверждают выбранный broker, сервер БД или runtime-соединение.
 */
const LABELS = {kafka: 'Kafka', rabbit: 'RabbitMQ', database: 'БД'};

/**
 * Стабильный ID категории включает владельца; одинаковые имена разных проектов не склеиваются.
 * @param {string} ownerId Canonical ID владельца из текущего ответа API.
 * @param {string} kind Категория ресурса: kafka, rabbit или database.
 * @returns {string} ID логической группы в пределах canonical владельца.
 */
export function architectureBlockId(ownerId, kind) {
  return 'architecture-block:' + encodeURIComponent(ownerId) + ':' + kind;
}

/**
 * Строит производную проекцию блоков с раскрываемыми ресурсами, не изменяя canonical facts.
 * @param {object} graph Текущая раскрытая canonical модель с nodes/edges.
 * @param {Set<string>} expanded IDs групп, чьи дочерние ресурсы видны.
 * @param {Set<string>} pinned Закреплённые дочерние ID остаются видимыми и в закрытой группе.
 * @returns {{graph: object, blocks: Map}} Производная модель и группы; входные узлы/рёбра не изменяются.
 * Поля configurationCandidates остаются альтернативами; ни один найденный host не выбирается автоматически.
 */
export function architectureBlocks(graph, expanded = new Set(), pinned = new Set()) {
  const blocks = new Map(), membership = new Map();
  for (const node of graph.nodes || []) {
    if (!node.resourceKind || !node.ownerId || !LABELS[node.kind]) continue;
    const id = architectureBlockId(node.ownerId, node.kind);
    if (!blocks.has(id)) blocks.set(id, {id, kind: node.kind, ownerId: node.ownerId, resourceBlock: true,
      label: LABELS[node.kind] + ' · привязка неизвестна', status: 'unknown', runtimeSelectionKnown: false,
      expanded: expanded.has(id), children: [], proof: {basis: 'derived-resource-group', runtimeSelectionKnown: false}});
    blocks.get(id).children.push(node); membership.set(node.id, id);
  }
  const nodes = (graph.nodes || []).filter(node => !membership.has(node.id) || expanded.has(membership.get(node.id)) || pinned.has(node.id)).map(node => ({...node}));
  for (const block of blocks.values()) { block.children.sort((a, b) => a.label.localeCompare(b.label) || a.id.localeCompare(b.id)); nodes.push(block); }
  const edges = new Map();
  for (const original of graph.edges || []) {
    const source = membership.get(original.source) || original.source;
    const target = membership.get(original.target) || original.target;
    const key = JSON.stringify([source, target, original.relation]);
    if (!edges.has(key)) edges.set(key, {...original, source, target, weight: 0, evidence: [], factIds: [], evidenceTruncated: false});
    const edge = edges.get(key); edge.weight += original.weight || 1;
    const factId = original.factId || original.id;
    if (factId && !edge.factIds.includes(factId)) edge.factIds.push(factId);
    for (const proof of original.evidence || []) {
      if (edge.evidence.length < 12) edge.evidence.push({...proof}); else edge.evidenceTruncated = true;
    }
    edge.evidenceTruncated ||= Boolean(original.evidenceTruncated);
  }
  for (const block of blocks.values()) for (const child of block.children) {
    if (!block.expanded && !pinned.has(child.id)) continue;
    edges.set(JSON.stringify([block.id, child.id, 'logical-member']), {source: block.id, target: child.id,
      relation: 'logical-member', weight: 1, derived: true, runtimeSelectionKnown: false,
      evidence: [{type: 'derived-resource-group', confidence: 'structural'}]});
  }
  return {graph: {...graph, nodes, edges: [...edges.values()]}, blocks};
}
