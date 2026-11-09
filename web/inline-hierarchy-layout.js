import { reserveInlineSpace } from './inline-space.js';
const HEADER_HEIGHT = 112;
const LEAF_WIDTH = 320;
const MIN_LEAF_WIDTH = 160;
const PADDING = 32;
const GAP = 32;
const ROOT_GAP = 40;
const MAX_FRAME_WIDTH = 1020;

/** Rectangles always use top-left document coordinates, independent of camera/zoom. */
function rectangle(x, y, width, height) { return { x, y, width, height }; }
/** Stable scene ordering makes permutations of items/roots produce identical geometry. */
function sortIds(ids, byId) {
  return [...new Set(ids)].filter(id => byId.has(id)).sort((a, b) => String(a).localeCompare(String(b), 'en'));
}
/** Фиксирует состав рядов по исходному бюджету: раскрытие увеличивает размеры, но не число колонок. */
function measure(id, byId, budget, ancestors = new Set(), header = null) {
  const item = byId.get(id);
  const childIds = item?.expanded && !ancestors.has(id) ? sortIds(item.children || [], byId) : [];
  const headerWidth = header?.width || Math.min(LEAF_WIDTH, budget);
  const headerHeight = header?.height || HEADER_HEIGHT;
  if (!childIds.length) return { id, width: headerWidth, height: headerHeight, headerWidth, headerHeight, rows: [] };
  const next = new Set(ancestors); next.add(id);
  const usable = Math.max(MIN_LEAF_WIDTH, budget - 2 * PADDING);
  const required = MIN_LEAF_WIDTH;
  let columns = Math.min(3, childIds.length);
  while (columns > 1 && (usable - GAP * (columns - 1)) / columns < required) columns--;
  const childBudget = (usable - GAP * (columns - 1)) / columns;
  const children = childIds.map(child => measure(child, byId, childBudget, next));
  const rows = [];
  for (let index = 0; index < children.length; index += columns) {
    const boxes = children.slice(index, index + columns);
    rows.push({ boxes, height: Math.max(...boxes.map(box => box.height)) });
  }
  // Одна и та же колонка во всех рядах раздвигается целиком, сохраняя соседство.
  const columnWidths = Array.from({ length: columns }, (_, column) => Math.max(...children.filter((_, index) => index % columns === column).map(box => box.width)));
  const contentWidth = columnWidths.reduce((sum, width) => sum + width, 0) + GAP * (columns - 1);
  const contentHeight = rows.reduce((sum, row) => sum + row.height, 0) + GAP * (rows.length - 1);
  return { id, width: Math.max(headerWidth, contentWidth + 2 * PADDING),
    height: headerHeight + 2 * PADDING + contentHeight, headerWidth, headerHeight, columnWidths, rows };
}

/**
 * Pure layout: x/y are HEADER centers, cardBounds covers the entire expanded frame.
 * Исходные корни сохраняют геометрию карты; вложенные ряды имеют ≤3 неизменных колонок.
 * Глубокие рамки могут расширять исходный бюджет, не переставляя существующие карточки.
 * Заголовки сохраняют исходный размер; новые имеют высоту 112px, padding/gap 32px защищают кнопки.
 * No camera or DOM mutation.
 */
export function inlineHierarchyLayout(scene, baseline = new Map()) {
  const byId = new Map((scene.items || []).map(item => [item.id, item]));
  const roots = sortIds(scene.roots || [], byId);
  const positions = new Map(), frames = [];
  const measured = roots.map(id => measure(id, byId, MAX_FRAME_WIDTH, new Set(), baseline.get(id)));
  const columns = Math.min(3, Math.max(1, roots.length));
  const columnWidths = Array.from({ length: columns }, (_, column) => Math.max(0, ...measured.filter((_, index) => index % columns === column).map(box => box.width)));
  const totalWidth = columnWidths.reduce((sum, width) => sum + width, 0) + ROOT_GAP * Math.max(0, columns - 1);
  const columnX = [];
  let currentX = -totalWidth / 2;
  for (const width of columnWidths) { columnX.push(currentX); currentX += width + ROOT_GAP; }
  const place = (box, left, top, parentId = null, depth = 0) => {
    if (positions.has(box.id)) return;
    const cardBounds = rectangle(left, top, box.width, box.height);
    const headerBounds = rectangle(left, top, box.headerWidth, box.headerHeight);
    positions.set(box.id, { id: box.id, x: left + box.headerWidth / 2, y: top + box.headerHeight / 2,
      width: box.width, height: box.height, headerBounds, cardBounds });
    if (box.rows.length) frames.push({ id: box.id, parentId, depth, ...cardBounds, headerBounds, cardBounds });
    let rowY = top + box.headerHeight + PADDING;
    for (const row of box.rows) {
      let childX = left + PADDING;
      for (const [column, child] of row.boxes.entries()) {
        place(child, childX, rowY, box.id, depth + 1); childX += box.columnWidths[column] + GAP;
      }
      rowY += row.height + GAP;
    }
  };
  const anchors = reserveInlineSpace(measured, baseline);
  let top = 0;
  for (let index = 0; index < measured.length; index += columns) {
    const row = measured.slice(index, index + columns);
    for (let column = 0; column < row.length; column++) {
      const anchor = anchors.get(row[column].id);
      place(row[column], anchor?.x ?? columnX[column], anchor?.y ?? top);
    }
    top += Math.max(...row.map(box => box.height), 0) + ROOT_GAP;
  }
  const rootBounds = roots.map(id => positions.get(id).cardBounds);
  const left = rootBounds.length ? Math.min(...rootBounds.map(box => box.x)) : 0;
  const upper = rootBounds.length ? Math.min(...rootBounds.map(box => box.y)) : 0;
  const right = rootBounds.length ? Math.max(...rootBounds.map(box => box.x + box.width)) : 0;
  const bottom = rootBounds.length ? Math.max(...rootBounds.map(box => box.y + box.height)) : 0;
  return { positions, frames, containers: frames, bounds: rectangle(left, upper, right - left, bottom - upper),
    constants: { headerHeight: HEADER_HEIGHT, padding: PADDING, gap: GAP, rootGap: ROOT_GAP, maxFrameWidth: MAX_FRAME_WIDTH } };
}
