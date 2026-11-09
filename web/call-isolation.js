const CALL_RELATIONS = Object.freeze(['calls', 'dispatches_to']);
const MAX_NODES = 240;
const MAX_EDGES = 1200;

/** Фильтрует анализ по полной области изоляции; предел обычной SVG-карты не обрезает реализации. */
export function isolateExecutionPath(data, scope) {
  if (!scope || scope.missingRoot) return data;
  const ids = scope.allNodeIds;
  const steps = (data.steps || []).filter(step => ids.has(step.nodeId));
  if (!steps.some(step => step.nodeId === scope.rootId)) return data;
  return { ...data, rootNodeId: scope.rootId, steps,
    edges: (data.edges || []).filter(edge => ids.has(edge.source) && ids.has(edge.target)) };
}

/** Ограничивает только отображение; неверные значения возвращают безопасный предел по умолчанию. */
function displayLimit(value, fallback, minimum, maximum) {
  if (typeof value !== 'number' && !(typeof value === 'string' && value.trim())) return fallback;
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(minimum, Math.min(maximum, Math.trunc(number))) : fallback;
}

/** Индексирует существующие направленные связи; null допускает все отношения системной карты. */
function directedGraph(graph, relations) {
  const byId = new Map();
  for (const node of Array.isArray(graph?.nodes) ? graph.nodes : []) {
    if (node?.id != null && !byId.has(node.id)) byId.set(node.id, node);
  }
  let allowed = null;
  if (relations !== null) {
    const configured = Array.isArray(relations) || relations instanceof Set ? relations : CALL_RELATIONS;
    allowed = new Set(configured);
  }
  const outgoing = new Map();
  const edges = [];
  for (const edge of Array.isArray(graph?.edges) ? graph.edges : []) {
    if (!edge || !byId.has(edge.source) || !byId.has(edge.target)) continue;
    if (allowed && !allowed.has(edge.relation)) continue;
    edges.push(edge);
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    outgoing.get(edge.source).push(edge);
  }
  return { byId, outgoing, edges };
}

/**
 * Изолирует корень и все достижимые исходящие вызовы полного сохранённого графа.
 * BFS проходит через все цели до применения пределов: фильтр/страница/предел SVG не обрывают цепочку.
 * По умолчанию включаются calls и dispatches_to; null включает все направленные отношения.
 * Кандидат dispatch остаётся статическим кандидатом, выбор реализации при запуске не вычисляется.
 * Узлы возвращаются в порядке BFS, начиная с корня; связи сохраняют исходный порядок и метаданные.
 * nodeIds описывает только возвращённые узлы, allNodeIds — полную область до ограничений.
 * maxNodes ограничен 1..240, maxEdges — 0..1200. Объекты графа не изменяются и не копируются.
 */
export function isolateCallChain(graph, rootId, options = {}) {
  return isolateCallRoots(graph, [rootId], options);
}

/** Обходит union реальных корней одним BFS; display cap применяется после полного обхода. */
export function isolateCallRoots(graph, rootIds, { relations = CALL_RELATIONS, maxNodes = MAX_NODES, maxEdges = MAX_EDGES } = {}) {
  const indexed = directedGraph(graph, relations);
  const allNodeIds = new Set();
  const roots = [...new Set(rootIds)].filter(id => indexed.byId.has(id));
  const rootId = roots[0] ?? null;
  if (!roots.length) {
    return { rootId, nodes: [], edges: [], totalNodes: 0, totalEdges: 0, truncated: false,
      nodeIds: new Set(), allNodeIds, missingRoot: true };
  }

  const pending = [...roots];
  for (const id of roots) allNodeIds.add(id);
  // Общая цель добавляется однажды, поэтому циклы и рекурсия не создают бесконечный обход.
  for (let index = 0; index < pending.length; index += 1) {
    for (const edge of indexed.outgoing.get(pending[index]) || []) {
      if (allNodeIds.has(edge.target)) continue;
      allNodeIds.add(edge.target);
      pending.push(edge.target);
    }
  }

  const nodeLimit = displayLimit(maxNodes, MAX_NODES, 1, MAX_NODES);
  const edgeLimit = displayLimit(maxEdges, MAX_EDGES, 0, MAX_EDGES);
  const nodes = [];
  const nodeIds = new Set();
  for (const id of pending.slice(0, nodeLimit)) {
    nodes.push(indexed.byId.get(id));
    nodeIds.add(id);
  }
  const edges = [];
  let totalEdges = 0;
  for (const edge of indexed.edges) {
    if (!allNodeIds.has(edge.source) || !allNodeIds.has(edge.target)) continue;
    totalEdges++;
    if (edges.length < edgeLimit && nodeIds.has(edge.source) && nodeIds.has(edge.target)) edges.push(edge);
  }
  return { rootId, nodes, edges, totalNodes: allNodeIds.size, totalEdges,
    truncated: nodes.length < allNodeIds.size || edges.length < totalEdges,
    nodeIds, allNodeIds, missingRoot: false };
}
