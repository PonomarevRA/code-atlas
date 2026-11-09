/**
 * Стабильные центры архитектурных узлов; работают с временной геометрией обоих GraphView renderer.
 */
import {layoutArchitectureClusters} from './architecture-clusters.js';
const GAP = 36;

/**
 * Пересечение ограничивающих прямоугольников включает подписи и отступ.
 */
function overlaps(a, b) {
  return Math.abs(a.x - b.x) < (a.width + b.width) / 2 + GAP
    && Math.abs(a.y - b.y) < (a.height + b.height) / 2 + GAP;
}

/**
 * Меняет только координаты временной positions Map, сохраняя размеры/цвета renderer.
 * @param {Map|object} geometry Временная Map либо {positions,containers} renderer; координаты меняются на месте.
 * @param {Map} registry До240 сохранённых центров текущего контекста/шкалы/renderer.
 * @param {?object} context Необязательные nodes/pinnedIds/previousPositions включают семантические кластеры.
 * @returns {Map|object} Тот же geometry; сохранённые центры не перемещаются, новым выделяется свободное место.
 * Локальная занятость включает до240 текущих позиций сверх registry, даже если кеш уже заполнен.
 * FlowRenderer продолжает читать ту же Map после installGeometry, поэтому она обновляется на месте.
 * Семантический режим хранит до24 дополнительных pin-центров/размеров в metadata того же registry.
 */
export function stableArchitectureGeometry(geometry, registry, context = null) {
  if (Array.isArray(context?.nodes)) return layoutArchitectureClusters(geometry, registry, context);
  const positions = geometry instanceof Map ? geometry : geometry.positions;
  // Current-frame placements occupy space even after the persistent registry reaches its quota.
  const occupied = new Map(registry);
  for (const [id, point] of positions) {
    const width = Math.max(100, point.width || point.w || (point.r || 60) * 2, point.labelWidth || 0);
    const height = Math.max(70, point.height || point.h || (point.r || 45) * 2);
    const cached = registry.get(String(id));
    if (cached) {
      point.x = cached.x; point.y = cached.y;
      registry.set(String(id), {...cached, width: Math.max(width, cached.width), height: Math.max(height, cached.height)});
      occupied.set(String(id), registry.get(String(id)));
      continue;
    }
    const candidate = {x: point.x || 0, y: point.y || 0, width, height};
    const peers = [...occupied.values()];
    if (peers.some(peer => overlaps(candidate, peer))) {
      const right = Math.max(0, ...peers.map(peer => peer.x + peer.width / 2));
      candidate.x = right + width / 2 + GAP;
      candidate.y = 0;
    }
    point.x = candidate.x; point.y = candidate.y;
    occupied.set(String(id), candidate);
    if (registry.size < 240) registry.set(String(id), candidate);
  }
  return geometry;
}

/**
 * Экспорт получает только конечные координаты узлов текущей filtered области.
 * @param {Map} registry Сохранённая геометрия текущей шкалы.
 * @param {Set<string>} ids Canonical или явно объявленные display IDs для экспорта.
 * @param {?Map} currentPositions Текущий кадр дополняет/перекрывает кеш без роста постоянного registry.
 * @returns {object} Словарь ID → {x,y}, без отсутствующих координат.
 */
export function architecturePositions(registry, ids, currentPositions = null) {
  const result = {};
  for (const id of ids) {
    const point = currentPositions?.get(id) || registry.get(id);
    if (point) result[id] = {x: point.x, y: point.y};
  }
  return result;
}
