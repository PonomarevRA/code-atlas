import { placeInlineMethodDetails } from './progressive-inline-layout.js';
import { nodeCardLayout } from './node-card-layout.js';
import { FLOW_CARD } from './flow-card.js';
import { reserveInlineSpace } from './inline-space.js';
import { FIELD_TABLE, fieldOf, fieldRowHeight } from './field-table.js';
import { PATH_SPACING } from './path-spacing.js';
import { htmlBlock, ordinal } from './code-structure.js';

const PADDING = 32;

/** Ранги пути размещают внешнюю карту и дочерние карточки; размеры раскрытий в рангах не участвуют. */
function baseLayout(ids, edges, items = new Map(), reading = false) {
  const fields = ids.filter(id => fieldOf(items.get(id)));
  const blocks = ids.filter(id => !fieldOf(items.get(id)));
  const nodes = blocks.map(id => ({ ...items.get(id)?.node, id, readingKind: items.get(id)?.kind }));
  // Inline frames already convey depth. Keep HTML siblings in a single ordered column.
  if (reading && nodes.length && nodes.every(htmlBlock)) {
    nodes.sort((a, b) => ordinal(a) - ordinal(b) || String(a.id).localeCompare(String(b.id), 'en'));
    for (const node of nodes) node.readingStructure = { kind: 'html', label: '', depth: 0 };
  }
  const layout = nodeCardLayout(nodes, edges, { reading });
  const bounds = new Map();
  const blockShift = fields.length ? FIELD_TABLE.width + PATH_SPACING.horizontal : 0;
  const blockLeft = layout.positions.size ? Math.min(...[...layout.positions.values()].map(point => point.x - point.width / 2)) : 0;
  const blockTop = layout.positions.size ? Math.min(...[...layout.positions.values()].map(point => point.y - point.height / 2)) : 0;
  for (const [id, point] of layout.positions) bounds.set(id, { x: point.x - point.width / 2 + blockShift - (fields.length ? blockLeft : 0),
    y: point.y - point.height / 2 - (fields.length ? blockTop : 0), width: point.width, height: point.height, ...(point.cycleId ? { cycleId: point.cycleId } : {}) });
  fields.sort((a, b) => (fieldOf(items.get(a)).line - fieldOf(items.get(b)).line) || ids.indexOf(a) - ids.indexOf(b));
  let rowTop = 0;
  for (const id of fields) {
    const height = fieldRowHeight(items.get(id));
    bounds.set(id, { x: 0, y: rowTop, width: FIELD_TABLE.width, height });
    rowTop += height + FIELD_TABLE.gap;
  }
  return bounds;
}

/** Измеряет поддерево, раздвигая исходную локальную Flow-раскладку вместо перепаковки детей. */
function measure(id, items, edges, baseline, bodies, reading = false, ancestors = new Set()) {
  const item = items.get(id), header = baseline.get(id);
  const body = bodies?.get(id), simple = body?.inlineSimple && body.byId.get(id);
  const headerWidth = simple ? Math.max(header?.width || 0, simple.w) : header?.width || FLOW_CARD.width;
  // Expanded reading cards need a separate focus row above line/source metadata.
  const focusRow = reading && body && !simple ? 22 : 0;
  const headerHeight = simple ? simple.h : (header?.height || FLOW_CARD.height) + focusRow;
  const childIds = item?.expanded && !ancestors.has(id) ? (item.children || []).filter(child => items.has(child)) : [];
  const next = new Set(ancestors); next.add(id);
  const childBase = baseLayout(childIds, edges, items, reading);
  const children = childIds.map(child => measure(child, items, edges, childBase, bodies, reading, next));
  const anchors = reserveInlineSpace(children, childBase);
  let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
  for (const child of children) {
    const point = anchors.get(child.id);
    left = Math.min(left, point.x); top = Math.min(top, point.y);
    right = Math.max(right, point.x + child.width); bottom = Math.max(bottom, point.y + child.height);
  }
  const bodyWidth = body?.bounds.width || 0, bodyHeight = body?.bounds.height || 0;
  const width = children.length ? Math.max(headerWidth, right - left + PADDING * 2) : headerWidth;
  const height = children.length ? headerHeight + bottom - top + PADDING * 2 : headerHeight;
  return { id, body, bodyOffsetY: headerHeight + (children.length ? bottom - top + PADDING * 2 : 0) + PADDING, cycleId: header?.cycleId || null, headerWidth, headerHeight, width: Math.max(width, bodyWidth ? bodyWidth + PADDING * 2 : 0), height: height + (bodyHeight ? bodyHeight + PADDING * 2 : 0), children, anchors,
    contentX: children.length ? left : 0, contentY: children.length ? top : 0,
    table: childIds.some(child => fieldOf(items.get(child))) };
}

/**
 * Flow-иерархия сохраняет исходные корни, карточки и локальные ранги при любом раскрытии.
 * positions содержат центры заголовков, frames — полную область дочерних блоков.
 * Сохраняет raw ID и семантику связей; геометрия не является порядком исполнения.
 */
export function inlineFlowLayout(scene, baseline = new Map(), { reading = false } = {}) {
  const items = new Map(scene.items.map(item => [item.id, item]));
  const roots = scene.roots.filter(id => items.has(id));
  const rootBase = new Map([...baseLayout(roots, scene.edges || [], items, reading), ...baseline]);
  const boxes = roots.map(id => measure(id, items, scene.edges || [], rootBase, scene.methodBodies, reading));
  const anchors = reserveInlineSpace(boxes, rootBase);
  const positions = new Map(), frames = [], methodBodies = new Map();
  /** Переносит локальную геометрию целиком; родитель не изменяет ряды и размеры заголовков детей. */
  function place(box, left, top, parentId = null, depth = 0) {
    if (positions.has(box.id)) return;
    const headerBounds = { x: left, y: top, width: box.headerWidth, height: box.headerHeight };
    const cardBounds = { x: left, y: top, width: box.width, height: box.height };
    positions.set(box.id, { id: box.id, x: left + box.headerWidth / 2, y: top + box.headerHeight / 2,
      width: box.width, height: box.height, cycleId: box.cycleId, headerBounds, cardBounds });
    if (box.body && !box.body.detached) methodBodies.set(box.id, { scene: box.body, x: left + PADDING - box.body.bounds.x, y: top + box.bodyOffsetY - box.body.bounds.y });
    if (box.children.length || box.body && !box.body.detached && !box.body.inlineSimple) frames.push({ id: box.id, parentId, depth, table: box.table, ...cardBounds, headerBounds, cardBounds });
    for (const child of box.children) {
      const anchor = box.anchors.get(child.id);
      place(child, left + PADDING + anchor.x - box.contentX,
        top + box.headerHeight + PADDING + anchor.y - box.contentY, box.id, depth + 1);
    }
  }
  for (const box of boxes) {
    const anchor = anchors.get(box.id);
    place(box, anchor.x, anchor.y);
  }
  const details = placeInlineMethodDetails(scene, positions);
  for (const [id, placement] of details.placements) methodBodies.set(id, placement);
  const obstacles = details.rectangles;
  const bounds = { x: 0, y: 0, width: 0, height: 0 };
  if (boxes.length) {
    const rectangles = obstacles;
    bounds.x = Math.min(...rectangles.map(box => box.x)); bounds.y = Math.min(...rectangles.map(box => box.y));
    bounds.width = Math.max(...rectangles.map(box => box.x + box.width)) - bounds.x;
    bounds.height = Math.max(...rectangles.map(box => box.y + box.height)) - bounds.y;
  }
  return { positions, frames, methodBodies, containers: frames, bounds, design: 'flow' };
}
