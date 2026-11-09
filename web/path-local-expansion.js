import { reserveInlineSpace } from './inline-space.js';
import { PATH_SPACING } from './path-spacing.js';
import { pathStubKey } from './path-control-tree-layout.js';

/** Собирает метод, его условия и окончания ветвей в один перемещаемый участок. */
function methodAreas(scene) {
  const areas = new Map();
  for (const node of scene.visuals) {
    if (!node.ownerId) areas.set(node.id, { id: node.id, points: [], stubs: [] });
  }
  for (const node of scene.visuals) {
    const point = scene.positions.get(node.id);
    if (point) areas.get(node.ownerId || node.id)?.points.push([node.id, point]);
  }
  const byId = new Map(scene.visuals.map(node => [node.id, node]));
  for (const stub of scene.stubs) {
    const key = pathStubKey(stub), point = scene.positions.stubPositions?.get(key);
    const source = byId.get(stub.source);
    if (point) areas.get(source?.ownerId || source?.id)?.stubs.push([key, point]);
  }
  for (const area of areas.values()) {
    const origin = scene.positions.get(area.id);
    let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
    for (const [, point] of [...area.points, ...area.stubs]) {
      left = Math.min(left, point.x - point.w / 2); top = Math.min(top, point.y - point.h / 2);
      right = Math.max(right, point.x + point.w / 2); bottom = Math.max(bottom, point.y + point.h / 2);
    }
    Object.assign(area, { origin, left: left - origin.x, top: top - origin.y, width: right - left, height: bottom - top });
  }
  return areas;
}

/** Подбирает ближайшую свободную полосу ниже деталей вызывающего метода, сохраняя колонку нового вызова. */
function clearCallLane(area, anchor, sourceArea, sourceAnchor) {
  const left = anchor.x + area.left, right = left + area.width;
  let top = anchor.y + area.top;
  const obstacles = [...sourceArea.points, ...sourceArea.stubs];
  // Каждый сдвиг проходит нижнюю границу одного препятствия; количество проходов ограничено его деталями.
  for (let attempt = 0; attempt <= obstacles.length; attempt++) {
    let next = top;
    for (const [, point] of obstacles) {
      const x = sourceAnchor.x + point.x - sourceArea.origin.x;
      const y = sourceAnchor.y + point.y - sourceArea.origin.y;
      if (left >= x + point.w / 2 || right <= x - point.w / 2) continue;
      if (top >= y + point.h / 2 + PATH_SPACING.vertical || top + area.height <= y - point.h / 2 - PATH_SPACING.vertical) continue;
      next = Math.max(next, y + point.h / 2 + PATH_SPACING.vertical);
    }
    if (next === top) break;
    top = next;
  }
  return { x: anchor.x, y: top - area.top };
}

/** Возвращает свободных соседей в прежние места: пустота внутри общей оболочки ветви не является препятствием. */
function restoreFreeNeighbours(areas, positions, stubPositions, previous, ownerId) {
  const neighbours = [];
  for (const area of areas.values()) if (area.id !== ownerId && previous.has(area.id)) neighbours.push(area);
  neighbours.sort((a, b) => previous.get(a.id).x - previous.get(b.id).x || previous.get(a.id).y - previous.get(b.id).y);
  for (const area of neighbours) {
    const old = previous.get(area.id), current = positions.get(area.id);
    const dx = old.x - current.x, dy = old.y - current.y;
    if (!dx && !dy) continue;
    const memberIds = new Set(area.points.map(([id]) => id)), stubIds = new Set(area.stubs.map(([id]) => id));
    const candidates = [], obstacles = [];
    for (const [id, point] of positions) {
      if (memberIds.has(id)) candidates.push([id, { ...point, x: point.x + dx, y: point.y + dy }, positions]);
      else obstacles.push(point);
    }
    for (const [id, point] of stubPositions) {
      if (stubIds.has(id)) candidates.push([id, { ...point, x: point.x + dx, y: point.y + dy }, stubPositions]);
      else obstacles.push(point);
    }
    let clear = true;
    for (const [, point] of candidates) {
      for (const obstacle of obstacles) {
        if (Math.abs(point.x - obstacle.x) < (point.w + obstacle.w) / 2 + PATH_SPACING.horizontal
            && Math.abs(point.y - obstacle.y) < (point.h + obstacle.h) / 2 + PATH_SPACING.vertical) { clear = false; break; }
      }
      if (!clear) break;
    }
    if (clear) for (const [id, point, target] of candidates) target.set(id, point);
  }
}

/**
 * Размещает новые методы напротив места вызова, сохраняя прежние якоря остальных методов.
 * Лексические деревья перемещаются целиком. Минимальные сдвиги вправо/вниз освобождают
 * место для выросших участков; общие вызовы и циклы не создают копий методов.
 * Геометрия в пикселях, входные сцены и исходные свидетельства не изменяются.
 */
export function localPathExpansion(scene, previous, ownerId) {
  if (!previous?.has(ownerId)) return scene.positions;
  const areas = methodAreas(scene), anchors = new Map(), baseline = new Map();
  for (const area of areas.values()) {
    const old = previous.get(area.id);
    if (old) anchors.set(area.id, { x: old.x, y: old.y });
  }
  const byId = new Map(scene.visuals.map(node => [node.id, node]));
  const incoming = new Map(), lanes = new Map();
  const incomingEdges = [...scene.visualEdges].sort((a, b) => {
    const sourceA = byId.get(a.source), sourceB = byId.get(b.source);
    const knownA = previous.has(sourceA?.ownerId || a.source), knownB = previous.has(sourceB?.ownerId || b.source);
    const rankA = areas.get(sourceA?.ownerId || a.source)?.origin.x ?? Infinity;
    const rankB = areas.get(sourceB?.ownerId || b.source)?.origin.x ?? Infinity;
    return Number(knownB) - Number(knownA) || rankA - rankB || (a.sourceLine ?? Infinity) - (b.sourceLine ?? Infinity)
      || a.source.localeCompare(b.source) || a.target.localeCompare(b.target);
  });
  for (const edge of incomingEdges) {
    if (!areas.has(edge.target) || anchors.has(edge.target)) continue;
    if (!incoming.has(edge.target)) incoming.set(edge.target, edge);
  }
  // BFS-ранги исходной сцены задают порядок, поэтому новая цепочка получает якоря от её начала.
  const ordered = [...areas.values()].sort((a, b) => a.origin.x - b.origin.x || a.origin.y - b.origin.y || a.id.localeCompare(b.id));
  for (const area of ordered) {
    if (anchors.has(area.id)) continue;
    const edge = incoming.get(area.id), source = byId.get(edge?.source);
    const sourceOwner = source?.ownerId || source?.id || ownerId;
    const sourceArea = areas.get(sourceOwner) || areas.get(ownerId);
    const sourceAnchor = anchors.get(sourceOwner) || anchors.get(ownerId);
    const sourcePoint = scene.positions.get(source?.id) || sourceArea.origin;
    const x = sourceAnchor.x + sourcePoint.x - sourceArea.origin.x + sourcePoint.w / 2 + PATH_SPACING.horizontal - area.left;
    const desiredTop = sourceAnchor.y + sourcePoint.y - sourceArea.origin.y - area.height / 2;
    const lane = edge?.source || sourceOwner;
    const top = Math.max(desiredTop, lanes.get(lane) ?? -Infinity);
    const anchor = clearCallLane(area, { x, y: top - area.top }, sourceArea, sourceAnchor);
    anchors.set(area.id, anchor);
    lanes.set(lane, anchor.y + area.top + area.height + PATH_SPACING.vertical);
  }
  // Автоматически раскрытая единственная реализация может быть выше своего слота вызова.
  // Сначала освобождаем место внутри новой ветви, затем двигаем внешних соседей целиком.
  const localBoxes = [], localBaseline = new Map();
  for (const area of areas.values()) {
    if (previous.has(area.id)) continue;
    const anchor = anchors.get(area.id);
    localBoxes.push({ id: area.id, width: area.width, height: area.height });
    localBaseline.set(area.id, { x: anchor.x + area.left, y: anchor.y + area.top, width: area.width, height: area.height });
  }
  const local = reserveInlineSpace(localBoxes, localBaseline, { gapX: PATH_SPACING.horizontal, gapY: PATH_SPACING.vertical });
  for (const [id, box] of local) {
    const area = areas.get(id);
    anchors.set(id, { x: box.x - area.left, y: box.y - area.top });
  }
  const boxes = [], clusters = new Map();
  for (const area of areas.values()) {
    const anchor = anchors.get(area.id);
    const clusterId = !previous.has(area.id) ? ownerId : area.id;
    if (!clusters.has(clusterId)) clusters.set(clusterId, { id: clusterId, areas: [], x: Infinity, y: Infinity, right: -Infinity, bottom: -Infinity });
    const cluster = clusters.get(clusterId);
    cluster.areas.push(area);
    cluster.x = Math.min(cluster.x, anchor.x + area.left); cluster.y = Math.min(cluster.y, anchor.y + area.top);
    cluster.right = Math.max(cluster.right, anchor.x + area.left + area.width);
    cluster.bottom = Math.max(cluster.bottom, anchor.y + area.top + area.height);
  }
  for (const cluster of clusters.values()) {
    const width = cluster.right - cluster.x, height = cluster.bottom - cluster.y;
    baseline.set(cluster.id, { x: cluster.x, y: cluster.y, width, height });
    boxes.push({ id: cluster.id, width, height });
  }
  const reserved = reserveInlineSpace(boxes, baseline, { gapX: PATH_SPACING.horizontal, gapY: PATH_SPACING.vertical });
  const positions = new Map(), stubPositions = new Map();
  for (const cluster of clusters.values()) {
    const box = reserved.get(cluster.id);
    for (const area of cluster.areas) {
      const anchor = anchors.get(area.id);
      const dx = anchor.x - area.origin.x + box.x - cluster.x, dy = anchor.y - area.origin.y + box.y - cluster.y;
      for (const [id, point] of area.points) positions.set(id, { ...point, x: point.x + dx, y: point.y + dy });
      for (const [id, point] of area.stubs) stubPositions.set(id, { ...point, x: point.x + dx, y: point.y + dy });
    }
  }
  restoreFreeNeighbours(areas, positions, stubPositions, previous, ownerId);
  positions.stubPositions = stubPositions;
  return positions;
}
