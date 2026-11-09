import { flowEdgePath } from './flow-layout.js';
import { implementationEdgeRouter } from './implementation-edge-routing.js';
import { connectionCurve } from './edge-curves.js';
import { RectangleIndex, cardRectangle } from './rectangle-index.js';

/** Делит кривую де Кастельжо: обе половины сохраняют её геометрию и контрольные оболочки. */
function splitCurve(points) {
  const left = [points[0]], right = [points.at(-1)];
  let level = points;
  while (level.length > 1) {
    const next = [];
    for (let index = 1; index < level.length; index++) next.push({
      x: (level[index - 1].x + level[index].x) / 2, y: (level[index - 1].y + level[index].y) / 2,
    });
    left.push(next[0]); right.push(next.at(-1)); level = next;
  }
  return [left, right.reverse()];
}

/** Уточняет занятую оболочку, чтобы свободная широкая дуга не становилась квадратным обходом. */
function curveIntersectsCard(points, card) {
  const pending = [{ points, depth: 0 }];
  const cardLeft = card.x - card.w / 2, cardRight = card.x + card.w / 2;
  const cardTop = card.y - card.h / 2, cardBottom = card.y + card.h / 2;
  while (pending.length) {
    const segment = pending.pop();
    let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
    for (const point of segment.points) {
      left = Math.min(left, point.x); right = Math.max(right, point.x);
      top = Math.min(top, point.y); bottom = Math.max(bottom, point.y);
    }
    if (left >= cardRight || right <= cardLeft || top >= cardBottom || bottom <= cardTop) continue;
    if (segment.depth >= 10 || (left > cardLeft && right < cardRight && top > cardTop && bottom < cardBottom)) return true;
    for (const half of splitCurve(segment.points)) pending.push({ points: half, depth: segment.depth + 1 });
  }
  return false;
}

/** Проверяет сегменты M/L/Q/C, включая выход и вход возле своих карточек. */
function intersectsCard(path, cards) {
  const commands = path.match(/[MLQC][^MLQC]*/g) || [];
  let from = null;
  for (const command of commands) {
    const values = command.slice(1).match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);
    const end = { x: values.at(-2), y: values.at(-1) };
    if (command[0] !== 'M' && from) {
      const points = [from];
      for (let index = 0; index < values.length; index += 2) {
        points.push({ x: values[index], y: values[index + 1] });
      }
      const box = { left: Infinity, right: -Infinity, top: Infinity, bottom: -Infinity };
      for (const point of points) {
        box.left = Math.min(box.left, point.x); box.right = Math.max(box.right, point.x);
        box.top = Math.min(box.top, point.y); box.bottom = Math.max(box.bottom, point.y);
      }
      for (const card of cards.query(box)) if (curveIntersectsCard(points, card)) return true;
    }
    from = end;
  }
  return false;
}

/** Кубический отрезок сохраняет прямой свободный участок между соседними изгибами. */
function straightCurve(from, to) {
  return ` C ${from.x + (to.x - from.x) / 3} ${from.y + (to.y - from.y) / 3}`
    + ` ${to.x - (to.x - from.x) / 3} ${to.y - (to.y - from.y) / 3} ${to.x} ${to.y}`;
}

/** Плавно огибает опору: квадратичная дуга выражена кубической с общей касательной. */
function cornerCurve(from, corner, to) {
  return ` C ${from.x + (corner.x - from.x) * 2 / 3} ${from.y + (corner.y - from.y) * 2 / 3}`
    + ` ${to.x + (corner.x - to.x) * 2 / 3} ${to.y + (corner.y - to.y) * 2 / 3} ${to.x} ${to.y}`;
}

/**
 * Превращает найденный свободный коридор в плавную кривую. Радиус каждого изгиба
 * уменьшается только при занятой контрольной оболочке; опоры берутся между узлами пути.
 */
function smoothDetour(path, cards) {
  if (!path) return path;
  const values = path.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);
  const points = [];
  for (let index = 0; index < values.length; index += 2) points.push({ x: values[index], y: values[index + 1] });
  let from = points[0], result = `M ${from.x} ${from.y}`;
  for (let index = 1; index < points.length - 1; index++) {
    const before = points[index - 1], corner = points[index], after = points[index + 1];
    const incoming = Math.hypot(corner.x - before.x, corner.y - before.y);
    const outgoing = Math.hypot(after.x - corner.x, after.y - corner.y);
    let radius = Math.min(64, incoming / 2, outgoing / 2);
    let entry, exit, curve;
    while (true) {
      entry = { x: corner.x + (before.x - corner.x) * radius / incoming, y: corner.y + (before.y - corner.y) * radius / incoming };
      exit = { x: corner.x + (after.x - corner.x) * radius / outgoing, y: corner.y + (after.y - corner.y) * radius / outgoing };
      curve = cornerCurve(entry, corner, exit);
      if (!intersectsCard(`M ${entry.x} ${entry.y}${curve}`, cards)) break;
      // При нулевом радиусе остаётся исходная свободная опора маршрутизатора.
      radius = radius > .01 ? radius / 2 : 0;
    }
    result += straightCurve(from, entry) + curve;
    from = exit;
  }
  return result + straightCurve(from, points.at(-1));
}

/**
 * Обычные Flow-дуги сохраняются, когда их контрольная оболочка свободна.
 * На обычной карте и при раскрытии только затронутые дуги переходят
 * в свободные боковые каналы. Проверка оболочки консервативна и не пропускает пересечения.
 */
export function inlineFlowEdgeRouter(positions, { obstacles = positions } = {}) {
  const cards = new RectangleIndex(obstacles.values(), cardRectangle);
  let fallback;
  return (source, target, index) => {
    const d = flowEdgePath({ ...source, width: source.w, height: source.h },
      { ...target, width: target.w, height: target.h }, index);
    if (intersectsCard(d, cards)) {
      // Большинство связей проходит свободно; тяжёлая сетка нужна только для первого обхода.
      fallback ||= implementationEdgeRouter(obstacles, [], [], { horizontal: true, portPositions: positions });
      const routed = fallback(source, target);
      return { ...routed, d: smoothDetour(routed.d, cards) };
    }
    return { d };
  };
}

// Sequence diagrams reuse the same conservative collision check and smooth free corridors.
export { intersectsCard as curveCrossesCards, smoothDetour as smoothSequenceDetour };

/**
 * Подробные методы и ссылки используют то же правило двух опор, что обычная карта.
 * Свободный пролёт соединяется одной кривой; занятый сохраняет маршрут сетки
 * с плавными углами. Подписи и их размещение остаются у исходного маршрутизатора.
 */
export function smoothImplementationEdgeRouter(positions, frames = [], labels = [], { horizontal = false } = {}) {
  const route = implementationEdgeRouter(positions, frames, labels, { horizontal });
  const cards = new RectangleIndex(positions.values(), cardRectangle);
  for (const label of labels) cards.add({ x: (label.left + label.right) / 2, y: (label.top + label.bottom) / 2,
    w: label.right - label.left, h: label.bottom - label.top });
  return (source, target, caption) => {
    const result = route(source, target, caption);
    const from = horizontal ? { x: source.x + source.w / 2, y: source.y } : { x: source.x, y: source.y + source.h / 2 };
    const to = horizontal ? { x: target.x - target.w / 2, y: target.y } : { x: target.x, y: target.y - target.h / 2 };
    const forward = horizontal ? to.x > from.x : to.y > from.y;
    const direct = connectionCurve(from, to, { axis: horizontal ? 'horizontal' : 'vertical' });
    result.d = forward && !intersectsCard(direct, cards) ? direct : smoothDetour(result.d, cards);
    if (result.caption) {
      const label = result.caption;
      cards.add({ x: label.x, y: label.y - 5.5, w: label.width + 4, h: 19 });
    }
    return result;
  };
}
