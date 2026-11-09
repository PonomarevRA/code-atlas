import { RectangleIndex } from './rectangle-index.js';

/** Keep only bounded geometry addresses; source text and API responses never enter history. */
export function normalizeProgressivePlacements(value, limit = 48) {
  const records = value instanceof Map ? [...value.values()] : Array.isArray(value) ? value : [];
  const result = [], seen = new Set();
  for (const item of records) {
    if (!item || !['body', 'candidates'].includes(item.kind)) continue;
    const validId = id => typeof id === 'string' && id.length > 0 && id.length <= 12000;
    if (!validId(item.id) || !validId(item.parentId) || seen.has(item.id)) continue;
    if (![item.x, item.y, item.width, item.height].every(Number.isFinite)) continue;
    if (Math.abs(item.x) > 1e7 || Math.abs(item.y) > 1e7 || item.width <= 0 || item.height <= 0 || item.width > 1e6 || item.height > 1e6) continue;
    result.push({ id: item.id, parentId: item.parentId, targetId: validId(item.targetId) ? item.targetId : null,
      kind: item.kind, x: item.x, y: item.y, width: item.width, height: item.height }); seen.add(item.id);
    if (Array.isArray(item.footprints) && item.footprints.length > 0 && item.footprints.length <= 320
      && item.footprints.every(shape => shape && [shape.x, shape.y, shape.width, shape.height].every(Number.isFinite)
        && shape.x >= 0 && shape.y >= 0 && shape.width > 0 && shape.height > 0
        && shape.x + shape.width <= item.width + 1e-6 && shape.y + shape.height <= item.height + 1e-6)) {
      result.at(-1).footprints = item.footprints.map(({ x, y, width, height }) => ({ x, y, width, height }));
    }
    if (result.length >= limit) break;
  }
  return result;
}

/** Card/frame footprints are obstacles; enclosing background frames are intentionally excluded. */
export function rectanglesOverlap(a, b, padding = 20) {
  return a.x < b.x + b.width + padding && a.x + a.width + padding > b.x
    && a.y < b.y + b.height + padding && a.y + a.height + padding > b.y;
}

/** Place a new area in the closest free right column; cached peers are never moved to make room. */
export function placeRightExpansion(anchor, local, obstacles, registry, record) {
  const cached = registry.get(record.id);
  const footprints = local.footprints || [{ x: 0, y: 0, width: local.width, height: local.height }];
  const box = { ...record, width: local.width, height: local.height,
    x: anchor.x + anchor.w / 2 + 70, y: anchor.y - local.entryY, footprints };
  // Addresses remember placement; enclosing background rectangles do not occupy their empty interior.
  const reservations = [];
  for (const item of registry.values()) {
    if (item.id === record.id || record.parentId.startsWith(item.id + '::')
      || item.id.endsWith('::inline-body') && record.parentId.startsWith(item.parentId + '::')) continue;
    for (const shape of item.footprints || [{ x: 0, y: 0, width: item.width, height: item.height }]) {
      reservations.push({ ...shape, x: item.x + shape.x, y: item.y + shape.y });
    }
  }
  const occupied = obstacles.concat(reservations);
  const index = new RectangleIndex(occupied, item => ({ left: item.x, top: item.y,
    right: item.x + item.width, bottom: item.y + item.height }));
  const collisionsAt = () => {
    const collisions = [];
    for (const shape of footprints) {
      const candidate = { ...shape, x: box.x + shape.x, y: box.y + shape.y };
      const nearby = index.query({ left: candidate.x - 20, top: candidate.y - 20,
        right: candidate.x + candidate.width + 20, bottom: candidate.y + candidate.height + 20 });
      for (const obstacle of nearby) if (rectanglesOverlap(candidate, obstacle)) collisions.push({ shape, obstacle });
    }
    return collisions;
  };
  // Old whole-frame reservations are recalculated once instead of retaining historical empty columns.
  if (cached?.footprints && cached.width === local.width && cached.height === local.height
    && cached.y === box.y && cached.x >= anchor.x + anchor.w / 2 + 20) {
    Object.assign(box, { x: cached.x, y: cached.y });
    if (!collisionsAt().length) { registry.set(record.id, box); return box; }
  }
  box.x = anchor.x + anchor.w / 2 + 70; box.y = anchor.y - local.entryY;
  for (let count = 0; count <= occupied.length * footprints.length; count++) {
    const collisions = collisionsAt();
    if (!collisions.length) break;
    box.x = Math.max(...collisions.map(({ shape, obstacle }) => obstacle.x + obstacle.width + 32 - shape.x));
  }
  registry.set(record.id, box);
  while (registry.size > 48) registry.delete(registry.keys().next().value);
  return box;
}

/** Translate only actual source cards into local occupied shapes, excluding enclosing method frames. */
export function progressiveFootprints(nodes, bounds) {
  return nodes.map(node => ({ x: node.x - node.w / 2 - bounds.x, y: node.y - node.h / 2 - bounds.y,
    width: node.w, height: node.h }));
}
