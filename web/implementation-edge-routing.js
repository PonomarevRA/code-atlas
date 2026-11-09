import { RectangleIndex } from './rectangle-index.js';

/** Локальные маршруты Flow проходят по промежуткам между карточками, сохраняя исходные связи. */

/** Открытые границы позволяют стрелке прикрепиться к порту на краю карточки. */
function crosses(a, b, rect) {
  if (a.x === b.x) return a.x > rect.left && a.x < rect.right
    && Math.max(a.y, b.y) > rect.top && Math.min(a.y, b.y) < rect.bottom;
  return a.y > rect.top && a.y < rect.bottom
    && Math.max(a.x, b.x) > rect.left && Math.min(a.x, b.x) < rect.right;
}

/** Проверяет все сегменты предлагаемого маршрута, включая короткие отрезки у портов. */
function clearRoute(points, obstacleIndex) {
  for (let index = 1; index < points.length; index += 1) {
    const a = points[index - 1], b = points[index];
    const box = { left: Math.min(a.x, b.x), right: Math.max(a.x, b.x),
      top: Math.min(a.y, b.y), bottom: Math.max(a.y, b.y) };
    for (const rect of obstacleIndex.query(box)) if (crosses(a, b, rect)) return false;
  }
  return true;
}

/** Добавляет координату один раз; сортировка даёт детерминированную сетку свободных промежутков. */
function coordinates(values) {
  return [...new Set(values)].sort((a, b) => a - b);
}

/** Очередь A* выбирает короткий маршрут; при равной оценке предпочитает близость к цели. */
class RouteQueue {
  constructor() { this.items = []; }
  before(a, b) { return a.score < b.score || (a.score === b.score && a.distance < b.distance); }
  push(item) {
    const items = this.items;
    items.push(item);
    let index = items.length - 1;
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2);
      if (!this.before(items[index], items[parent])) break;
      [items[index], items[parent]] = [items[parent], items[index]];
      index = parent;
    }
  }
  pop() {
    const items = this.items, first = items[0], last = items.pop();
    if (!items.length) return first;
    items[0] = last;
    let index = 0;
    while (index * 2 + 1 < items.length) {
      let child = index * 2 + 1;
      if (child + 1 < items.length && this.before(items[child + 1], items[child])) child += 1;
      if (!this.before(items[child], items[index])) break;
      [items[index], items[child]] = [items[child], items[index]];
      index = child;
    }
    return first;
  }
}

/** Объединяет занятые интервалы одной линии сетки, чтобы проверять сегменты бинарным поиском. */
function mergedIntervals(rectangles, axis, coordinate) {
  const intervals = [];
  for (const rect of rectangles) {
    if (axis === 'x' && coordinate > rect.left && coordinate < rect.right) intervals.push([rect.top, rect.bottom]);
    if (axis === 'y' && coordinate > rect.top && coordinate < rect.bottom) intervals.push([rect.left, rect.right]);
  }
  intervals.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const interval of intervals) {
    const last = merged.at(-1);
    if (last && interval[0] <= last[1]) last[1] = Math.max(last[1], interval[1]);
    else merged.push([...interval]);
  }
  return merged;
}

/** Общая сетка одного render повторно использует координаты портов и занятые интервалы. */
function routeGrid(obstacles, ports) {
  const xs = [], ys = [];
  for (const port of ports) { xs.push(port.x); ys.push(port.y); }
  for (const rect of obstacles) {
    xs.push(rect.left - 8, rect.right + 8);
    ys.push(rect.top - 8, rect.bottom + 8);
  }
  const x = coordinates(xs), y = coordinates(ys);
  const intervalsX = new Map(), intervalsY = new Map();
  const blocked = (a, b) => {
    const vertical = a.x === b.x;
    const coordinate = vertical ? a.x : a.y;
    const cache = vertical ? intervalsX : intervalsY;
    if (!cache.has(coordinate)) cache.set(coordinate, mergedIntervals(obstacles, vertical ? 'x' : 'y', coordinate));
    const intervals = cache.get(coordinate);
    const from = Math.min(vertical ? a.y : a.x, vertical ? b.y : b.x);
    const to = Math.max(vertical ? a.y : a.x, vertical ? b.y : b.x);
    let left = 0, right = intervals.length;
    while (left < right) {
      const middle = Math.floor((left + right) / 2);
      if (intervals[middle][1] <= from) left = middle + 1;
      else right = middle;
    }
    return left < intervals.length && intervals[left][0] < to;
  };
  return { x, y, blocked };
}

/** A* обходит карточки по общей сетке; её соседство не меняет смысл исходного ребра. */
function freePath(start, end, grid) {
  const { x, y, blocked } = grid;
  const width = x.length;
  const startId = y.indexOf(start.y) * width + x.indexOf(start.x);
  const endId = y.indexOf(end.y) * width + x.indexOf(end.x);
  const point = id => ({ x: x[id % width], y: y[Math.floor(id / width)] });
  const distance = (a, b) => Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
  const cost = new Map([[startId, 0]]), previous = new Map(), closed = new Set();
  const open = new RouteQueue();
  open.push({ id: startId, score: distance(start, end), distance: distance(start, end) });
  while (open.items.length) {
    const { id } = open.pop();
    if (closed.has(id)) continue;
    if (id === endId) {
      const result = [];
      let current = id;
      while (current !== undefined) { result.push(point(current)); current = previous.get(current); }
      return result.reverse();
    }
    closed.add(id);
    const from = point(id), column = id % width, row = Math.floor(id / width), neighbors = [];
    if (column > 0) neighbors.push(id - 1);
    if (column + 1 < width) neighbors.push(id + 1);
    if (row > 0) neighbors.push(id - width);
    if (row + 1 < y.length) neighbors.push(id + width);
    for (const neighbor of neighbors) {
      if (closed.has(neighbor)) continue;
      const to = point(neighbor);
      if (blocked(from, to)) continue;
      const next = cost.get(id) + distance(from, to);
      if (next >= (cost.get(neighbor) ?? Infinity)) continue;
      cost.set(neighbor, next);
      previous.set(neighbor, id);
      const remaining = distance(to, end);
      open.push({ id: neighbor, score: next + remaining, distance: remaining });
    }
  }
  return null;
}

/** Убирает лишние точки на одной линии, сохраняя направление пути и порты. */
function simplify(points) {
  const result = [];
  for (const point of points) {
    const last = result.at(-1);
    if (last && last.x === point.x && last.y === point.y) continue;
    const before = result.at(-2);
    if (before && ((before.x === last.x && last.x === point.x) || (before.y === last.y && last.y === point.y))) result.pop();
    result.push(point);
  }
  return result;
}

/** Размещает короткую подпись в свободном месте; полное доказательство остаётся в title ребра. */
function captionPosition(text, source, target, points, obstacles, { preferTarget = false } = {}) {
  if (!text) return null;
  const label = String(text).length > 28 ? `${String(text).slice(0, 27)}…` : String(text);
  const width = Math.min(source.w - 16, label.length * 7);
  const candidates = [{ x: source.x, y: source.y + source.h / 2 + 20 }, { x: target.x, y: target.y - target.h / 2 - 16 }];
  if (preferTarget) candidates.reverse();
  for (let index = 1; index < points.length; index += 1) {
    const a = points[index - 1];
    const b = points[index];
    if (a.y === b.y && Math.abs(a.x - b.x) >= width) candidates.push({ x: (a.x + b.x) / 2, y: a.y - 4 });
  }
  for (const candidate of candidates) {
    const rect = { left: candidate.x - width / 2, right: candidate.x + width / 2, top: candidate.y - 12, bottom: candidate.y + 3 };
    let blocked = false;
    for (const card of obstacles.query(rect)) {
      if (rect.left < card.right && rect.right > card.left && rect.top < card.bottom && rect.bottom > card.top) { blocked = true; break; }
    }
    if (!blocked) return { ...candidate, text: label, width };
  }
  return null;
}

/**
 * Кэширует препятствия и сетку по panelIndex для одного render. Локальные линии обходят
 * тело метода, продолжения reference используют общую сетку без повторного сканирования карточек.
 * horizontal соединяет правый OUT с левым IN; по умолчанию сохранены вертикальные порты старой топологии.
 */
export function implementationEdgeRouter(positions, frames, existingLabels = [], { horizontal = false, customPorts = false, portPositions = positions, labelIndex } = {}) {
  const obstaclesByPanel = new Map();
  const all = [];
  for (const item of positions.values()) {
    const rect = { left: item.x - item.w / 2, right: item.x + item.w / 2, top: item.y - item.h / 2, bottom: item.y + item.h / 2 };
    all.push(rect);
    if (!obstaclesByPanel.has(item.panelIndex)) obstaclesByPanel.set(item.panelIndex, []);
    obstaclesByPanel.get(item.panelIndex).push(rect);
  }
  const methodFrames = new Map();
  for (const frame of frames) if (!frame.decision) methodFrames.set(frame.panelIndex, frame);
  // Подписи разных switch arms не должны занимать одно место у общего source.
  const labelObstacles = labelIndex || new RectangleIndex([...all, ...existingLabels]);
  const obstacleIndexes = new Map();
  for (const [panel, rectangles] of obstaclesByPanel) obstacleIndexes.set(panel, new RectangleIndex(rectangles));
  const allObstacles = new RectangleIndex(all);
  const grids = new Map();
  const extraPorts = new Map();
  const sides = customPorts ? ['left', 'right', 'top', 'bottom'] : horizontal ? ['right', 'left'] : ['bottom', 'top'];
  const ports = [...portPositions.values()].flatMap(item => sides.map(side => outsidePort(item, side)));
  return (source, target, caption, { sourceSide = horizontal ? 'right' : 'bottom', targetSide = horizontal ? 'left' : 'top', via, preferTargetCaption = false } = {}) => {
    const obstacles = source.panelIndex === target.panelIndex ? obstaclesByPanel.get(source.panelIndex) : all;
    const obstacleIndex = source.panelIndex === target.panelIndex ? obstacleIndexes.get(source.panelIndex) : allObstacles;
    const first = boundaryPort(source, sourceSide), last = boundaryPort(target, targetSide);
    const start = outsidePort(source, sourceSide), end = outsidePort(target, targetSide);
    const frame = methodFrames.get(source.panelIndex);
    const gutter = frame ? frame.x + frame.w + 12 : Math.max(source.x + source.w / 2, target.x + target.w / 2) + 12;
    const below = frame ? frame.y + frame.h + 12 : Math.max(source.y + source.h / 2, target.y + target.h / 2) + 12;
    let points = horizontal
      ? [first, start, { x: start.x, y: below }, { x: end.x, y: below }, end, last]
      : [first, start, { x: gutter, y: start.y }, { x: gutter, y: end.y }, end, last];
    // У соседних Flow-карточек сначала используем короткий канал между колонками.
    // Обход под всей рамкой нужен только при занятом канале или обратном переходе.
    if (!via && horizontal && end.x >= start.x) {
      const middleX = (start.x + end.x) / 2;
      const direct = [first, start, { x: middleX, y: start.y }, { x: middleX, y: end.y }, end, last];
      if (clearRoute(direct, obstacleIndex)) points = direct;
    }
    if (via) points = [first, start, ...via, end, last];
    if (!clearRoute(points, obstacleIndex)) {
      const gridId = source.panelIndex === target.panelIndex ? source.panelIndex : "all";
      let grid = grids.get(gridId);
      // Иерархия использует порты полной рамки, а препятствия заголовков: их координаты тоже нужны сетке.
      if (!grid || !grid.x.includes(start.x) || !grid.x.includes(end.x) || !grid.y.includes(start.y) || !grid.y.includes(end.y)) {
        if (!extraPorts.has(gridId)) extraPorts.set(gridId, []);
        const additional = extraPorts.get(gridId);
        additional.push(start, end);
        grid = routeGrid(obstacles, [...ports, ...additional]);
        grids.set(gridId, grid);
      }
      const middle = freePath(start, end, grid);
      points = middle ? [first, ...middle, last] : [];
    }
    points = simplify(points);
    const d = points.map((point, index) => `${index ? 'L' : 'M'} ${point.x} ${point.y}`).join(' ');
    const label = captionPosition(caption, source, target, points, labelObstacles, { preferTarget: preferTargetCaption });
    if (label) labelObstacles.add({ left: label.x - label.width / 2 - 2, right: label.x + label.width / 2 + 2, top: label.y - 14, bottom: label.y + 5 });
    return { d, caption: label };
  };
}

/** A route can leave any card side; the 12px offset remains outside its own obstacle. */
function boundaryPort(item, side) {
  return { x: item.x + (side === 'right' ? item.w / 2 : side === 'left' ? -item.w / 2 : 0),
    y: item.y + (side === 'bottom' ? item.h / 2 : side === 'top' ? -item.h / 2 : 0) };
}

/** All custom sides enter the shared grid once, avoiding grid rebuilds for each loop/return. */
function outsidePort(item, side) {
  const point = boundaryPort(item, side);
  return { x: point.x + (side === 'right' ? 12 : side === 'left' ? -12 : 0),
    y: point.y + (side === 'bottom' ? 12 : side === 'top' ? -12 : 0) };
}

export { captionPosition as placeRouteCaption };
