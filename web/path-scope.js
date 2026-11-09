/** Локальная проекция статического пути: раскрытие добавляет тело метода к существующему контексту. */

/** Индексирует только связи между доступными узлами; исходные объекты не меняются. */
function indexOutgoing(nodes, edges) {
  const byId = new Map(nodes.map(node => [node.id, node]));
  const outgoing = new Map(nodes.map(node => [node.id, []]));
  for (const edge of edges) {
    if (byId.has(edge.source) && byId.has(edge.target)) outgoing.get(edge.source).push(edge);
  }
  return { byId, outgoing };
}

/** Считает уникальные ещё скрытые методы, включая рекурсию без повторного обхода. */
function hiddenDescendants(id, outgoing, visibleIds) {
  const seen = new Set([id]);
  const pending = [id];
  let hidden = 0;
  while (pending.length) {
    for (const edge of outgoing.get(pending.pop()) || []) {
      if (seen.has(edge.target)) continue;
      seen.add(edge.target);
      if (!visibleIds.has(edge.target)) hidden += 1;
      pending.push(edge.target);
    }
  }
  return hidden;
}

/**
 * Раскрывает указанные видимые методы, оставляя вызывающие методы и соседние вызовы на месте.
 * Корень раскрыт всегда. Единственный dispatch показывается прозрачно, максимум на два перехода:
 * это кандидат реализации, а не доказательство выбора во время выполнения.
 * collapsedIds позволяет вручную свернуть автоматически показанное тело реализации.
 * Возвращаемые узлы — копии; controlFlow/events скрытых тел не попадают в renderer.
 */
export function buildPathScope(nodes, edges, { rootId, expandedIds = [], collapsedIds = [] } = {}) {
  const { byId, outgoing } = indexOutgoing(nodes, edges);
  const root = byId.has(rootId) ? rootId : nodes.find(node => !String(node.id).startsWith('path-entry:'))?.id;
  const requested = new Set(expandedIds);
  const collapsed = new Set(collapsedIds);
  const visibleIds = new Set();
  const effectiveExpanded = new Set();
  const pending = [];
  const scheduled = new Set();

  /** Планирует тело только уже видимого метода; сохранённое раскрытие скрытого потомка не создаёт сироту. */
  function schedule(id, autoDispatch = true) {
    effectiveExpanded.add(id);
    if (scheduled.has(id)) return;
    scheduled.add(id);
    pending.push({ id, autoDispatch });
  }

  /** Добавляет карточку и восстанавливает ранее раскрытое тело, если вызывающий снова виден. */
  function include(id) {
    if (!byId.has(id)) return;
    visibleIds.add(id);
    if (requested.has(id) && !collapsed.has(id)) schedule(id);
  }

  /** У единственного dispatch сохраняем интерфейс и показываем возможную реализацию рядом. */
  function includeDispatch(id) {
    const seen = new Set([id]);
    let current = id;
    for (let hop = 0; hop < 2; hop += 1) {
      const transitions = outgoing.get(current) || [];
      const targets = new Set(transitions.map(edge => edge.target));
      if (!transitions.length || targets.size !== 1 || transitions.some(edge => edge.relation !== 'dispatches_to')) break;
      const target = transitions[0].target;
      if (seen.has(target)) break;
      seen.add(target);
      include(target);
      const targetNode = byId.get(target);
      const targetEdges = outgoing.get(target) || [];
      const dispatchOnly = targetEdges.length > 0 && targetEdges.every(edge => edge.relation === 'dispatches_to');
      const hasControls = targetNode.controlFlow?.branches?.length || targetNode.controlFlow?.terminals?.length;
      // Промежуточный интерфейс не разворачиваем как тело: иначе его очередь обойдёт предел двух dispatch-переходов.
      if (!collapsed.has(target) && (!dispatchOnly || hasControls)) schedule(target, false);
      current = target;
    }
  }

  if (root != null) {
    include(root);
    schedule(root);
    includeDispatch(root);
    for (const node of nodes) {
      if (!String(node.id).startsWith('path-entry:')) continue;
      if ((outgoing.get(node.id) || []).some(edge => edge.target === root)) visibleIds.add(node.id);
    }
  }
  for (let index = 0; index < pending.length; index += 1) {
    const owner = pending[index];
    for (const edge of outgoing.get(owner.id) || []) {
      include(edge.target);
      if (owner.autoDispatch) includeDispatch(edge.target);
    }
  }

  const projectedNodes = [];
  const stats = new Map();
  for (const node of nodes) {
    if (!visibleIds.has(node.id)) continue;
    const expanded = effectiveExpanded.has(node.id);
    const direct = new Set((outgoing.get(node.id) || []).map(edge => edge.target));
    const branchCount = node.controlFlow?.branches?.length || 0;
    const terminals = node.controlFlow?.terminals?.length || 0;
    const transitions = outgoing.get(node.id) || [];
    const transparent = direct.size === 1 && !expanded && !branchCount && !terminals
      && transitions.every(edge => edge.relation === 'dispatches_to' && visibleIds.has(edge.target));
    stats.set(node.id, {
      directCalls: direct.size,
      hiddenDescendants: hiddenDescendants(node.id, outgoing, visibleIds),
      hiddenBranches: expanded ? 0 : branchCount,
      canExpand: !transparent && (direct.size > 0 || branchCount > 0 || terminals > 0 || Boolean(node.staticData)),
      expanded,
      contentKind: node.staticData ? 'данные' : node.declarationKind === 'class' ? 'методы' : 'вызовы',
      structuralRelations: transitions.some(edge => !['calls', 'dispatches_to'].includes(edge.relation)),
    });
    projectedNodes.push({ ...node, controlFlow: expanded ? node.controlFlow : undefined,
      staticData: expanded ? node.staticData : undefined, events: expanded ? node.events : [] });
  }
  const projectedEdges = [];
  for (const edge of edges) {
    if (!visibleIds.has(edge.source) || !visibleIds.has(edge.target)) continue;
    const entry = String(edge.source).startsWith('path-entry:');
    // Свёрнутая карточка не показывает связи своего тела даже к уже видимому общему методу.
    const sourceEdges = outgoing.get(edge.source) || [];
    const sourceTargets = new Set(sourceEdges.map(item => item.target));
    const transparentDispatch = edge.relation === 'dispatches_to' && sourceTargets.size === 1
      && sourceEdges.every(item => item.relation === 'dispatches_to') && !collapsed.has(edge.source);
    if (!effectiveExpanded.has(edge.source) && !entry && !transparentDispatch) continue;
    projectedEdges.push({ ...edge });
  }
  const methodNodes = projectedNodes.filter(node => !String(node.id).startsWith('path-entry:'));
  const methodTotal = nodes.filter(node => !String(node.id).startsWith('path-entry:')).length;
  return {
    nodes: projectedNodes, edges: projectedEdges, visibleIds, expandedIds: effectiveExpanded,
    detailIds: effectiveExpanded, stats, focusId: root, visible: methodNodes.length, total: methodTotal,
  };
}
