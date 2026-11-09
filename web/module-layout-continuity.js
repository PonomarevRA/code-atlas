/** Source-pilot geometry facade. Membership is owned by Python, never by this module. */
import {stableArchitectureGeometry} from './architecture-layout.js';

const MAX_UNITS = 200;
const GAP = 36;

/** Compare stable IDs independently of host locale and response ordering. */
function compareIds(a, b) {
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

/** Copy finite center-based card geometry, using current dimensions and label width. */
function copyPositions(input) {
  if (!(input instanceof Map) || input.size > MAX_UNITS) throw new Error('Expected a Map of at most 200 cards.');
  const result = new Map();
  for (const [id, point] of input) {
    if (typeof id !== 'string' || !id || id.length > 1024 || !point || typeof point !== 'object') {
      throw new Error('Invalid card identity or geometry.');
    }
    const width = point.width ?? point.w ?? (point.r === undefined ? 100 : point.r * 2);
    const height = point.height ?? point.h ?? (point.r === undefined ? 70 : point.r * 2);
    const labelWidth = point.labelWidth ?? 0;
    if (![point.x, point.y, width, height, labelWidth].every(Number.isFinite)
        || Math.abs(point.x) > 1e9 || Math.abs(point.y) > 1e9
        || width <= 0 || height <= 0 || labelWidth < 0 || Math.max(width, height, labelWidth) > 1e6) {
      throw new Error('Card geometry must have bounded finite centers and positive dimensions.');
    }
    result.set(id, {...point, width: Math.max(100, width, labelWidth), height: Math.max(70, height)});
  }
  const ordered = new Map();
  for (const id of [...result.keys()].sort(compareIds)) ordered.set(id, result.get(id));
  return ordered;
}

/** Include card/label extents and the same gap used by the existing stable helper. */
function intersects(a, b) {
  return Math.abs(a.x - b.x) < (a.width + b.width) / 2 + GAP
    && Math.abs(a.y - b.y) < (a.height + b.height) / 2 + GAP;
}

/** Validate an explicit coordinate-frame identity; absence never implies equality. */
function validFrame(value) {
  return typeof value === 'string' && value.length > 0 && value.length <= 256 && !/[\x00-\x1f]/.test(value);
}

/**
 * Reuse existing stable geometry, then repair only non-pinned dimension collisions.
 * @param {Map<string,object>} currentPositions Current renderer centers and card dimensions.
 * @param {Map<string,object>} previousPositions Previous same-frame card geometry.
 * @param {object} options Explicit frameId, previousFrameId and pinnedIds (array or Set).
 * @returns {object} Detached positions Map and geometry-only displacement/conflict evidence.
 *
 * Matching declared frames permit old centers to be reused. Missing/changed frames
 * trigger a fresh placement using current centers; unknown prior pin locations are
 * reported. Two conflicting pins stay fixed and are explicitly unreadable. Removed
 * cards do not reserve space. The facade does not route edges, infer membership,
 * mutate renderer/session maps or qualify the installed UI.
 */
export function moduleLayoutContinuity(currentPositions, previousPositions = new Map(), options = {}) {
  const positions = copyPositions(currentPositions), previous = copyPositions(previousPositions);
  const {frameId, previousFrameId, pinnedIds = []} = options;
  if (!validFrame(frameId)) throw new Error('An explicit current coordinate frame is required.');
  if (!(pinnedIds instanceof Set) && !Array.isArray(pinnedIds)) throw new Error('Invalid pins.');
  if (pinnedIds.size > MAX_UNITS || pinnedIds.length > MAX_UNITS) throw new Error('Too many pins.');
  const pins = new Set(pinnedIds);
  for (const id of pins) if (typeof id !== 'string' || !id || id.length > 1024) throw new Error('Invalid pin ID.');
  const comparable = validFrame(previousFrameId) && frameId === previousFrameId;
  const registry = new Map();
  if (comparable) {
    for (const [id, point] of previous) if (positions.has(id)) registry.set(id, {...point});
  }
  // Existing placement owns stable-center reuse. Operate on detached maps only.
  stableArchitectureGeometry(positions, registry);
  for (const id of pins) {
    const point = positions.get(id);
    const anchor = comparable && previous.has(id) ? previous.get(id) : currentPositions.get(id);
    if (point && anchor) { point.x = anchor.x; point.y = anchor.y; }
  }
  const occupied = new Map();
  const order = [...positions.keys()].sort((a, b) => Number(pins.has(b)) - Number(pins.has(a)) || compareIds(a, b));
  const repaired = [];
  for (const id of order) {
    const point = positions.get(id);
    const collides = [...occupied.values()].some(peer => intersects(point, peer));
    if (collides && !pins.has(id)) {
      const right = Math.max(0, ...[...occupied.values()].map(peer => peer.x + peer.width / 2));
      point.x = right + point.width / 2 + GAP;
      point.y = 0;
      repaired.push(id);
    }
    occupied.set(id, point);
  }
  const overlaps = [], ids = [...positions.keys()].sort();
  for (let i = 0; i < ids.length; i++) {
    for (const peer of ids.slice(i + 1)) {
      if (intersects(positions.get(ids[i]), positions.get(peer))) {
        overlaps.push({source: ids[i], target: peer, bothPinned: pins.has(ids[i]) && pins.has(peer)});
      }
    }
  }
  const displacement = [];
  if (comparable) {
    for (const [id, point] of positions) {
      const old = previous.get(id);
      if (old) displacement.push({id, distance: Math.hypot(point.x - old.x, point.y - old.y)});
    }
  }
  const unknownPins = [...pins].filter(id => !positions.has(id) || !comparable || !previous.has(id)).sort();
  return {positions, report: {
    version: 'atlas-module-layout-continuity/v1', frameId, previousFrameId: validFrame(previousFrameId) ? previousFrameId : null,
    comparable, fallbackReason: comparable ? null : 'coordinate-frame-changed-or-unknown',
    sharedCards: displacement.length, displacement,
    maxDisplacement: displacement.length ? Math.max(...displacement.map(item => item.distance)) : null,
    meanDisplacement: displacement.length ? displacement.reduce((sum, item) => sum + item.distance, 0) / displacement.length : null,
    repairedNonPinned: repaired, unknownPins, overlaps, readableWithoutOverlaps: overlaps.length === 0,
    interpretation: 'Centers and card/label extents only; no membership, semantic or edge-routing quality claim.',
  }};
}
