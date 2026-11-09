import { buildPathScope } from './path-scope.js';
import { pathToNode } from './path-relations.js';
import { capturePathTransition, animatePathTransition } from './path-transition.js';

/** Обновляет локальное раскрытие при смене корня/снимка; каноническое состояние остаётся на общем view. */
export function preparePathScope(view, nodes, edges, selected) {
  view.inlineSceneActive = false;
  if (selected !== view.selectedId) view.pathReveal = null;
  view.pathSceneActive = true;
  view.compartments = [];
  const flowPath = view.displayProvider === 'flow';
  view.pathLayoutProvider = flowPath ? 'flow' : 'topology';
  view.mapTooltip?.hide(true);
  view.clearExpansion(true);
  const rootId = view.pathRootId || nodes.find(node => !String(node.id).startsWith('path-entry:'))?.id;
  if (view.pathDataRoot !== rootId || view.pathDataSnapshot !== view.pathSnapshotId) {
    view.pathDataRoot = rootId;
    view.pathDataSnapshot = view.pathSnapshotId;
    view.pathExpandedIds = new Set();
    view.pathCollapsedIds = new Set();
    view.pathReveal = null;
  }
  view.pathExpandedIds ||= new Set();
  view.pathCollapsedIds ||= new Set();
  view.pathData = { nodes, edges, selected };
  view.pathScope = buildPathScope(nodes, edges, {
    rootId, expandedIds: view.pathExpandedIds, collapsedIds: view.pathCollapsedIds,
  });

  return view.pathScope;
}

/** Раскрывает или сворачивает тело на месте, сохраняя экранную позицию нажатой карточки и масштаб. */
export function togglePathNode(view, id) {
  if (!view.pathData || id === view.pathScope?.focusId) return;
  const facts = view.pathScope?.stats.get(id);
  if (!facts?.canExpand) return;
  const before = capturePathTransition(view);
  const previous = before.get(id);
  const visibleBefore = new Set(view.pathScope.nodes.map(node => node.id));
  const camera = { ...view.camera };
  if (facts.expanded) {
    view.pathExpandedIds.delete(id);
    view.pathCollapsedIds.add(id);
  } else {
    view.pathExpandedIds.add(id);
    view.pathCollapsedIds.delete(id);
  }
  const { nodes, edges } = view.pathData;
  view.pathExpansionAnchor = facts.expanded ? null : { ownerId: id };
  try {
    view.renderPathCards(nodes, edges, view.selectedId);
  } finally {
    view.pathExpansionAnchor = null;
  }
  const current = view.positions?.get(id);
  view.camera = camera;
  if (previous && current) {
    view.camera.x += (previous.x - current.x) * camera.scale;
    view.camera.y += (previous.y - current.y) * camera.scale;
  }
  view.transform();
  view.pathReveal = facts.expanded ? null : { ownerId: id, nodeIds: view.pathScope.nodes.filter(node => !visibleBefore.has(node.id)).map(node => node.id) };
  view.updatePathRelations();
  const offset = previous && current ? { x: previous.x - current.x, y: previous.y - current.y } : { x: 0, y: 0 };
  if (view.pathFlatFlow && (view.pathReading || view.groupingProvider === 'natural')) animatePathTransition(view, before, offset);
  view.nodeElements.get(id)?.group.querySelector('.path-card-expand')?.focus?.();
}

/** Находит доступную цепочку от корня, не включая входную карточку и недостижимые узлы анализа. */
export function reachablePathNodeIds(view) {
  const { nodes = [], edges = [] } = view.pathData || {};
  const valid = new Set(nodes.filter(node => !String(node.id).startsWith('path-entry:')).map(node => node.id));
  const root = view.pathScope?.focusId || view.pathRootId;
  const outgoing = new Map();
  for (const edge of edges) {
    if (!valid.has(edge.source) || !valid.has(edge.target)) continue;
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    outgoing.get(edge.source).push(edge.target);
  }
  const reachable = new Set();
  const pending = valid.has(root) ? [root] : [];
  for (let index = 0; index < pending.length; index += 1) {
    const id = pending[index];
    if (reachable.has(id)) continue;
    reachable.add(id);
    for (const target of outgoing.get(id) || []) {
      if (!reachable.has(target)) pending.push(target);
    }
  }
  return reachable;
}

/** Раскрывает всю захваченную цепочку на месте; серверные лимиты сохраняются, камера и выбор не меняются. */
export function expandAllPathNodes(view) {
  if (!view.pathSceneActive || !view.pathData) return false;
  const reachable = view.reachablePathNodeIds();
  if (!reachable.size) return false;
  const camera = { ...view.camera };
  const selected = view.selectedId;
  view.pathExpandedIds = reachable;
  view.pathReveal = null;
  view.pathCollapsedIds.clear();
  const { nodes, edges } = view.pathData;
  view.renderPathCards(nodes, edges, selected);
  view.camera = camera;
  view.transform();
  return true;
}

/** Возвращает первоначальное компактное раскрытие корня и прозрачных dispatch-переходов. */
export function collapseAllPathNodes(view) {
  if (!view.pathData) return;
  view.pathExpandedIds.clear();
  view.pathReveal = null;
  view.pathCollapsedIds.clear();
  const { nodes, edges } = view.pathData;
  view.renderPathCards(nodes, edges, view.selectedId);
}

/** Начало горизонтального пути стоит у левого края с читаемым масштабом; общий обзор доступен через «Вместить». */
export function fitPathInitial(view) {
  const root = view.positions?.get(view.pathScope?.focusId);
  if ((view.groupingProvider === 'natural' || view.pathReading && view.displayProvider === 'flow') && root) {
    const scale = .9;
    view.camera = { scale, x: 24 - (root.x - root.w / 2) * scale,
      y: 48 - (root.y - root.h / 2) * scale };
    view.transform();
    return;
  }
  view.fit();
  if (view.camera.scale < 0.75) {
    view.camera.scale = 0.9;
    view.focusPathNode(view.pathScope?.focusId);
  }
}

/**
   * Выделяет следующий шаг и ставит его в центр окна.
   * Сначала выбор, затем камера: обновление инспектора не должно возвращать обзор всего пути.
   */
export function followPathNode(view, id) {
  if (String(id).startsWith('path-entry:')) return;
  if (!view.positions?.has(id) && !view.revealPathNode(id)) return;
  view.setSelected(id);
  view.onSelect(id);
  view.focusPathNode(id);
}

/** Явный переход к скрытой связи раскрывает кратчайшую известную цепочку, не уменьшая масштаб. */
export function revealPathNode(view, id) {
  const path = pathToNode(view.pathData, view.pathScope?.focusId || view.pathRootId, id);
  if (!path.length) return false;
  const camera = { ...view.camera };
  for (const ancestor of path.slice(0, -1)) {
    view.pathExpandedIds.add(ancestor);
    view.pathCollapsedIds.delete(ancestor);
  }
  view.renderPathCards(view.pathData.nodes, view.pathData.edges, view.selectedId);
  view.camera = camera;
  view.transform();
  return view.positions.has(id);
}

/**
   * Ставит шаг пути в центр окна, не пересобирая схему.
   * Текущий масштаб сохраняется: переход к ссылке меняет только центр камеры.
   */
export function focusPathNode(view, id) {
  const point = view.positions?.get(id);
  if (!point) return;
  const width = view.svg.clientWidth || 960;
  const height = view.svg.clientHeight || 640;
  const scale = view.camera.scale || 1;
  view.camera = {
    x: width / 2 - point.x * scale,
    y: height / 2 - point.y * scale,
    scale,
  };
  view.transform();
}
