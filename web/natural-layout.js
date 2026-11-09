import { flowLayout } from './flow-layout.js';
import { implementationComparisonGroups, implementationComparisonRows } from './implementation-comparison-rows.js';

/** Стабильный порядок ID разрешает равенства, не приписывая коду порядок исполнения. */
const compareIds = (a, b) => a < b ? -1 : a > b ? 1 : 0;

/** Кратчайшая глубина и первый вызывающий образуют каркас; остальные связи сохраняет renderer. */
function branchLayers(nodes, edges, rootId) {
  const ids = nodes.map(node => String(node.id)).sort(compareIds);
  const outgoing = new Map(ids.map(id => [id, []]));
  const incoming = new Map(ids.map(id => [id, 0]));
  for (const edge of edges) {
    const source = String(edge.source), target = String(edge.target);
    if (!outgoing.has(source) || !outgoing.has(target) || source === target) continue;
    outgoing.get(source).push({ target, line: Number(edge.sourceLine) || Infinity });
    incoming.set(target, incoming.get(target) + 1);
  }
  for (const neighbors of outgoing.values()) neighbors.sort((a, b) => a.line - b.line || compareIds(a.target, b.target));
  const roots = ids.filter(id => !incoming.get(id));
  if (outgoing.has(String(rootId))) roots.unshift(String(rootId));
  roots.push(...ids);
  const assigned = new Set(), layers = [], parents = new Map();
  for (const root of roots) {
    if (assigned.has(root)) continue;
    const queue = [{ id: root, rank: 0 }];
    assigned.add(root);
    for (let index = 0; index < queue.length; index++) {
      const { id, rank } = queue[index];
      (layers[rank] ||= []).push(id);
      for (const neighbor of outgoing.get(id)) {
        if (assigned.has(neighbor.target)) continue;
        assigned.add(neighbor.target);
        parents.set(neighbor.target, id);
        queue.push({ id: neighbor.target, rank: rank + 1 });
      }
    }
  }
  return { layers, parents };
}

/**
 * Размещает родственные карточки вокруг вызывающего: ствол идёт вправо, ветви расходятся
 * по вертикали. Широкий уровень складывается в шесть колонок, а соседние карточки
 * разделяет воздух. Реализации одного интерфейса стоят друг под другом в колонке без
 * переноса. Размеры в пикселях учитывают весь текст; вход и реальные связи
 * не меняются. Итеративный обход поддерживает глубокие пути без переполнения стека.
 */
export function naturalLayout(nodes = [], edges = [], options = {}) {
  const layout = flowLayout(nodes, edges, options);
  if (!nodes.length) return layout;
  const { layers, parents } = branchLayers(nodes, edges, options.rootId);
  const comparisonGroups = implementationComparisonGroups(edges);
  let width = Number(options.cardWidth) || 260, height = Number(options.cardHeight) || 154;
  for (const node of nodes) {
    width = Math.max(width, Number(node.w) || 0);
    height = Math.max(height, Number(node.h) || 0);
  }
  const gap = Number(options.rankGap) || 112, rowGap = Number(options.rowGap) || 48;
  let x = 80;
  for (const layer of layers) {
    const families = new Map();
    for (const id of layer) {
      const owner = parents.get(id) || `root:${id}`;
      if (!families.has(owner)) families.set(owner, { owner, ids: [], desired: layout.positions.get(owner)?.y || 0 });
      families.get(owner).ids.push(id);
    }
    const branches = [...families.values()];
    branches.sort((a, b) => a.desired - b.desired || compareIds(a.owner, b.owner));
    // Малые развилки читаются как дерево; широкие уровни занимают общий компактный
    // блок вместо отдельной полосы для каждой ветви. Родственные ID остаются рядом.
    const ordered = [];
    let desired = 0;
    for (const branch of branches) {
      ordered.push(...branch.ids);
      desired += branch.desired * branch.ids.length;
    }
    desired /= ordered.length;
    const columns = ordered.length <= 8 ? 1 : Math.min(6, Math.ceil(Math.sqrt(ordered.length)));
    const rows = implementationComparisonRows(ordered.map(id => ({ id })), comparisonGroups, columns);
    let usedColumns = 0;
    for (const [row, items] of rows.entries()) {
      usedColumns = Math.max(usedColumns, items.length);
      for (const [column, { id }] of items.entries()) {
        const old = layout.positions.get(id);
        layout.positions.set(id, { ...old, width, height,
          x: x + width / 2 + column * (width + gap),
          y: desired + (row - (rows.length - 1) / 2) * (height + rowGap),
        });
      }
    }
    x += usedColumns * (width + gap);
  }
  let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
  for (const point of layout.positions.values()) {
    left = Math.min(left, point.x - width / 2); right = Math.max(right, point.x + width / 2);
    top = Math.min(top, point.y - height / 2); bottom = Math.max(bottom, point.y + height / 2);
  }
  layout.bounds = { x: left, y: top, width: right - left, height: bottom - top };
  return layout;
}

/** Переносит готовое оформление топологии в ветвящуюся геометрию без старых рамок сетки. */
export function naturalGeometry(geometry, edges) {
  const original = geometry.positions || geometry;
  const sized = [];
  for (const [id, point] of original) {
    const box = point.cardBounds;
    sized.push({ id, w: box ? box.right - box.left : point.labelWidth || point.r * 2 + 24,
      h: box ? box.bottom - box.top : point.r + (point.labelBottom || point.r + 32) });
  }
  const positions = naturalLayout(sized, edges).positions;
  const result = new Map();
  for (const [id, point] of original) {
    const next = positions.get(String(id));
    const moved = { ...point, x: next.x, y: next.y };
    if (point.cardBounds) {
      const dx = next.x - point.x, dy = next.y - point.y;
      moved.cardBounds = { left: point.cardBounds.left + dx, right: point.cardBounds.right + dx,
        top: point.cardBounds.top + dy, bottom: point.cardBounds.bottom + dy };
    } else moved.y -= ((point.labelBottom || point.r + 32) - point.r) / 2;
    result.set(id, moved);
  }
  return result;
}
