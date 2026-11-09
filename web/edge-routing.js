import { connectionControls } from './edge-curves.js';

/** Стабильное смещение в [-1, 1]; зависит от id связи, а не порядка SVG или времени. */
function jitter(key) {
  let hash = 2166136261;
  for (const char of String(key)) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
  return (hash >>> 0) / 4294967295 * 2 - 1;
}
/** Точка выхода луча из центра эллипса на его границу. */
function gateway(box, toward) {
  const dx = toward.x - box.cx, dy = toward.y - box.cy;
  const divisor = Math.hypot(dx / box.rx, dy / box.ry) || 1;
  return { x: box.cx + dx / divisor, y: box.cy + dy / divisor };
}
/** Опорные точки в мировых координатах; пучок локален паре проектов, а не всему графу. */
export function edgeAnchors(source, target, { sourceContainer, targetContainer, sourceRegion, targetRegion, key = '', bundled = true } = {}) {
  const start = { x: source.x, y: source.y }, end = { x: target.x, y: target.y };
  if (!bundled) return [start, end];
  const dx = end.x - start.x, dy = end.y - start.y, distance = Math.hypot(dx, dy);
  if (distance < .001) {
    const radius = Math.max(18, (source.r || 7) + 12);
    return [start, { x: start.x + radius, y: start.y - radius }, { x: start.x + radius * 2, y: start.y }, { x: start.x + radius, y: start.y + radius }, end];
  }
  if (sourceContainer && targetContainer && sourceContainer.id !== targetContainer.id) {
    const forward = String(sourceContainer.id) < String(targetContainer.id);
    const first = forward ? sourceContainer : targetContainer, second = forward ? targetContainer : sourceContainer;
    const length = Math.hypot(second.cx - first.cx, second.cy - first.cy) || 1;
    const normal = { x: -(second.cy - first.cy) / length, y: (second.cx - first.cx) / length };
    const lane = (forward ? 10 : -10) + jitter(key) * 4;
    const sourceGate = gateway(sourceContainer, { x: targetContainer.cx + normal.x * lane, y: targetContainer.cy + normal.y * lane });
    const targetGate = gateway(targetContainer, { x: sourceContainer.cx + normal.x * lane, y: sourceContainer.cy + normal.y * lane });
    const middle = { x: (sourceGate.x + targetGate.x) / 2 + normal.x * lane, y: (sourceGate.y + targetGate.y) / 2 + normal.y * lane };
    return [start, sourceGate, middle, targetGate, end];
  }
  if (sourceRegion && targetRegion) {
    const sameRegion = sourceRegion.id === targetRegion.id;
    const forward = sameRegion ? String(source.id ?? JSON.stringify(start)) < String(target.id ?? JSON.stringify(end)) : String(sourceRegion.id) < String(targetRegion.id);
    const first = forward ? sourceRegion : targetRegion, second = forward ? targetRegion : sourceRegion;
    const length = Math.hypot(second.x - first.x, second.y - first.y);
    const normal = length > .001 ? { x: -(second.y - first.y) / length, y: (second.x - first.x) / length } : { x: 0, y: 1 };
    const lane = (forward ? 4 : -4) + jitter(key) * 2;
    const middle = { x: (first.x + second.x) / 2 + normal.x * lane, y: (first.y + second.y) / 2 + normal.y * lane };
    if (sameRegion) return [start, middle, end];
    return [start, { x: sourceRegion.x, y: sourceRegion.y }, middle, { x: targetRegion.x, y: targetRegion.y }, end];
  }
  const offset = Math.min(24, distance * .08) * (.6 + jitter(key) * .4);
  return [start, { x: (start.x + end.x) / 2 - dy / distance * offset, y: (start.y + end.y) / 2 + dx / distance * offset }, end];
}
/** Собирает локальные опоры ячеек 4×4: внутренние связи объединяются без общего центра проекта. */
export function localRoutingRegions(points, containers) {
  const boxes = new Map(containers.map(box => [String(box.id), box]));
  const bins = new Map(), membership = new Map(), result = new Map();
  const ordered = [...points].sort((a, b) => String(a.id).localeCompare(String(b.id), 'en'));
  for (const point of ordered) {
    const box = boxes.get(String(point.codeProjectId || '__unassigned__'));
    if (!box) continue;
    const column = Math.max(0, Math.min(3, Math.floor((point.x - box.x) / box.width * 4)));
    const row = Math.max(0, Math.min(3, Math.floor((point.y - box.y) / box.height * 4)));
    const id = JSON.stringify([box.id, column, row]);
    if (!bins.has(id)) bins.set(id, { id, x: 0, y: 0, count: 0 });
    const bin = bins.get(id); bin.x += point.x; bin.y += point.y; bin.count++;
    membership.set(point.id, id);
  }
  for (const bin of bins.values()) { bin.x /= bin.count; bin.y /= bin.count; }
  for (const [id, binId] of membership) result.set(id, bins.get(binId));
  return result;
}
/** Общий коридор задаёт смещение двух опор, а не дополнительное выравнивание кривой в центре. */
function corridorControls(from, middle, to, clamp) {
  const offset = { x: (middle.x - (from.x + to.x) / 2) * 4 / 3,
    y: (middle.y - (from.y + to.y) / 2) * 4 / 3 };
  return connectionControls(from, to, { axis: 'direct', offset }).map(clamp);
}

/** Продолжает касательную коридора на треть длины входного/выходного участка; bounds ограничивает вылет опоры. */
function gatewayControl(gate, control, distance, direction, clamp) {
  const dx = control.x - gate.x, dy = control.y - gate.y;
  const length = Math.hypot(dx, dy);
  if (!length) return gate;
  return clamp({ x: gate.x + direction * dx / length * distance / 3,
    y: gate.y + direction * dy / length * distance / 3 });
}

/** Связи используют две опоры; шлюзы проектов и самоссылки сохраняют отдельные безопасные каналы. */
export function routeEdge(source, target, options = {}) {
  const points = edgeAnchors(source, target, options);
  const bounds = options.bounds || {
    minX: Math.min(...points.map(point => point.x)), maxX: Math.max(...points.map(point => point.x)),
    minY: Math.min(...points.map(point => point.y)), maxY: Math.max(...points.map(point => point.y)),
  };
  // Кривая Безье лежит в выпуклой оболочке своих контролей: ограничение сохраняет прежний fit.
  const clamp = point => ({ x: Math.max(bounds.minX, Math.min(bounds.maxX, point.x)), y: Math.max(bounds.minY, Math.min(bounds.maxY, point.y)) });
  for (let index = 1; index < points.length - 1; index++) points[index] = clamp(points[index]);
  let path = `M ${points[0].x} ${points[0].y}`;
  if (points.length === 2) return `${path} L ${points[1].x} ${points[1].y}`;
  const firstPoint = points[0], lastPoint = points.at(-1);
  const self = Math.hypot(lastPoint.x - firstPoint.x, lastPoint.y - firstPoint.y) < .001;
  if (!self && points.length === 3) {
    const [first, second] = corridorControls(firstPoint, points[1], lastPoint, clamp);
    return `${path} C ${first.x} ${first.y} ${second.x} ${second.y} ${lastPoint.x} ${lastPoint.y}`;
  }
  if (!self && points.length === 5) {
    const [start, exit, middle, enter, end] = points;
    const [first, second] = corridorControls(exit, middle, enter, clamp);
    const approach = connectionControls(start, exit, { axis: 'direct' })[0];
    const departure = connectionControls(enter, end, { axis: 'direct' })[1];
    const exitControl = gatewayControl(exit, first, Math.hypot(exit.x - start.x, exit.y - start.y), -1, clamp);
    const enterControl = gatewayControl(enter, second, Math.hypot(end.x - enter.x, end.y - enter.y), -1, clamp);
    return `${path} C ${approach.x} ${approach.y} ${exitControl.x} ${exitControl.y} ${exit.x} ${exit.y}`
      + ` C ${first.x} ${first.y} ${second.x} ${second.y} ${enter.x} ${enter.y}`
      + ` C ${enterControl.x} ${enterControl.y} ${departure.x} ${departure.y} ${end.x} ${end.y}`;
  }
  for (let index = 0; index < points.length - 1; index++) {
    const previous = points[Math.max(0, index - 1)], from = points[index];
    const to = points[index + 1], following = points[Math.min(points.length - 1, index + 2)];
    // Уменьшенная касательная ограничивает вылет кривой за локальный коридор.
    const first = clamp({ x: from.x + (to.x - previous.x) / 8, y: from.y + (to.y - previous.y) / 8 });
    const second = clamp({ x: to.x - (following.x - from.x) / 8, y: to.y - (following.y - from.y) / 8 });
    path += ` C ${first.x} ${first.y} ${second.x} ${second.y} ${to.x} ${to.y}`;
  }
  return path;
}
