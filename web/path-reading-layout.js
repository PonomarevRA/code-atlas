import { PATH_SPACING } from './path-spacing.js';
import { implementationComparisonGroups, implementationComparisonRows } from './implementation-comparison-rows.js';

/** Порядок соседних карточек следует строкам вызовов; повторные цели обходятся один раз. */
function readingOrder(nodes, edges, rootId) {
  const byId = new Map(nodes.map(node => [node.id, node]));
  const outgoing = new Map(nodes.map(node => [node.id, []]));
  for (const edge of edges) {
    if (byId.has(edge.source) && byId.has(edge.target)) outgoing.get(edge.source).push(edge);
  }
  for (const list of outgoing.values()) {
    const line = edge => edge.sourceLine ?? (byId.get(edge.target).ownerId ? byId.get(edge.target).line : null) ?? Infinity;
    list.sort((a, b) => line(a) - line(b) || a.target.localeCompare(b.target));
  }
  const order = new Map(), ranks = new Map();
  const pending = byId.has(rootId) ? [{ id: rootId, rank: 0 }] : [];
  for (let index = 0; index < pending.length; index++) {
    const { id, rank } = pending[index];
    if (order.has(id)) continue;
    order.set(id, order.size);
    ranks.set(id, rank);
    for (const edge of outgoing.get(id)) pending.push({ id: edge.target, rank: rank + 1 });
  }
  for (const node of nodes) {
    if (order.has(node.id)) continue;
    order.set(node.id, order.size); ranks.set(node.id, 0);
  }
  return { order, ranks };
}

/**
 * Кратчайшая глубина от корня задаёт ранг: общий метод остаётся рядом с первым вызовом.
 * Ранги идут вправо; широкий ранг занимает до шести колонок вместо длинной
 * вертикальной полосы. Возможные реализации одного интерфейса стоят в отдельной колонке
 * без переноса. Внутри ранга читаем строки исходника слева направо, сверху вниз.
 * Верхнее выравнивание держит начало большого пути рядом с корнем. Это порядок чтения,
 * а не новые связи или вычисленная последовательность исполнения. Карточки содержат размеры
 * w/h; результат — центры в пикселях карты. Вход не изменяется.
 */
export function pathReadingLayout(nodes, edges, rootId) {
  let cardWidth = 260, cardHeight = 154;
  for (const node of nodes) {
    cardWidth = Math.max(cardWidth, node.w);
    cardHeight = Math.max(cardHeight, node.h);
  }
  const gap = PATH_SPACING.horizontal, rowGap = PATH_SPACING.vertical;
  const { order, ranks } = readingOrder(nodes, edges, rootId);
  const comparisonGroups = implementationComparisonGroups(edges);
  const layers = new Map();
  for (const node of nodes) {
    const rank = ranks.get(node.id);
    if (!layers.has(rank)) layers.set(rank, []);
    layers.get(rank).push(node);
  }
  const positions = new Map();
  let x = 80;
  for (const rank of [...layers.keys()].sort((a, b) => a - b)) {
    const layer = layers.get(rank);
    layer.sort((a, b) => order.get(a.id) - order.get(b.id));
    const rows = implementationComparisonRows(layer, comparisonGroups, Math.min(6, layer.length));
    let columns = 0;
    for (const [row, items] of rows.entries()) {
      columns = Math.max(columns, items.length);
      for (const [column, node] of items.entries()) {
        positions.set(node.id, { x: x + column * (cardWidth + gap) + cardWidth / 2,
          y: 80 + row * (cardHeight + rowGap) + cardHeight / 2 });
      }
    }
    x += columns * (cardWidth + gap);
  }
  return positions;
}
