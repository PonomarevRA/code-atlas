import { PATH_SPACING } from './path-spacing.js';
import { implementationComparisonGroups, implementationComparisonRows } from './implementation-comparison-rows.js';

/** Стабильный порядок ID применяется только при одинаковых строках исходника. */
const compareIds = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const GAP = PATH_SPACING.horizontal, ROW_GAP = PATH_SPACING.vertical;

/** Ключ геометрии пустой ветви не является новым узлом или ребром анализируемого графа. */
export const pathStubKey = stub => JSON.stringify([stub.source, stub.armId, stub.caption]);

/** Строит локальные деревья владельцев; вызовы резервируют место ветви, но методы не дублируются. */
function methodTrees(nodes, edges, stubs) {
  const byId = new Map(nodes.map(node => [node.id, { ...node, children: [] }]));
  const roots = nodes.filter(node => !node.ownerId).map(node => byId.get(node.id));
  const linked = new Set(), slots = new Set();
  for (const edge of edges) {
    const source = byId.get(edge.source), target = byId.get(edge.target);
    if (!source || !target) continue;
    const owner = source.ownerId || source.id;
    if (target.ownerId === owner && !linked.has(target.id)) {
      source.children.push({ node: target, armId: edge.armId, line: target.line });
      linked.add(target.id);
    } else if (source.decision && !target.ownerId) {
      const key = JSON.stringify([source.id, edge.armId, target.id]);
      if (slots.has(key)) continue;
      slots.add(key);
      source.children.push({ node: { id: key, targetId: target.id, w: target.w, h: target.h, slot: true, children: [] },
        armId: edge.armId, line: edge.sourceLine });
    }
  }
  for (const node of nodes) {
    if (!node.ownerId || linked.has(node.id)) continue;
    byId.get(node.ownerId)?.children.push({ node: byId.get(node.id), line: node.line });
  }
  for (const stub of stubs) {
    const owner = byId.get(stub.source);
    if (!owner) continue;
    owner.children.push({ node: { id: pathStubKey(stub), w: 260, h: 62, stub: true, children: [] }, armId: stub.armId });
  }
  for (const node of byId.values()) {
    const armOrder = new Map((node.armIds || []).map((id, index) => [id, index]));
    node.children.sort((a, b) => (armOrder.get(a.armId) ?? -1) - (armOrder.get(b.armId) ?? -1)
      || (a.line ?? Infinity) - (b.line ?? Infinity) || compareIds(a.node.id, b.node.id));
  }
  return { roots, byId };
}

/** Считает размеры снизу вверх: каждое поддерево получает отдельную непересекающуюся полосу. */
function sizeTree(root) {
  const order = [], pending = [root], seen = new Set();
  while (pending.length) {
    const node = pending.pop();
    if (seen.has(node)) continue;
    seen.add(node); order.push(node);
    for (const child of node.children) pending.push(child.node);
  }
  for (let index = order.length - 1; index >= 0; index--) {
    const node = order[index];
    let width = 0, height = 0;
    for (const child of node.children) {
      width = Math.max(width, child.node.width);
      height += child.node.height + ROW_GAP;
    }
    if (node.children.length) height -= ROW_GAP;
    node.width = node.w + (node.children.length ? GAP + width : 0);
    node.height = Math.max(node.h, height);
  }
}

/** Размещает вложенные if/switch/циклы вправо, а альтернативы — в разных вертикальных полосах. */
function placeTree(root, x, y, positions, stubPositions, callPositions) {
  const pending = [{ node: root, x, y }];
  for (let index = 0; index < pending.length; index++) {
    const item = pending[index], node = item.node;
    const point = { x: item.x + node.w / 2, y: item.y + node.height / 2 };
    if (node.stub) stubPositions.set(node.id, { ...point, w: node.w, h: node.h });
    else if (node.slot && !callPositions.has(node.targetId)) callPositions.set(node.targetId, point);
    else if (!node.slot) positions.set(node.id, point);
    let childY = item.y;
    for (const child of node.children) {
      pending.push({ node: child.node, x: item.x + node.w + GAP, y: childY });
      childY += child.node.height + ROW_GAP;
    }
  }
}

/** Кратчайшие ранги вызовов между методами отделены от lexical-вложенности их условий. */
function methodLayers(roots, byId, edges, rootId) {
  const methods = new Map(roots.map(node => [node.id, node]));
  const outgoing = new Map(roots.map(node => [node.id, []]));
  for (const edge of edges) {
    const source = byId.get(edge.source), target = methods.get(edge.target);
    const owner = source?.ownerId || source?.id;
    if (target && outgoing.has(owner) && target.id !== owner) outgoing.get(owner).push({ id: target.id, line: edge.sourceLine });
  }
  for (const list of outgoing.values()) list.sort((a, b) => (a.line ?? Infinity) - (b.line ?? Infinity) || compareIds(a.id, b.id));
  const seen = new Set(), layers = [];
  const incoming = new Set();
  for (const targets of outgoing.values()) for (const target of targets) incoming.add(target.id);
  const rootIds = roots.map(node => node.id).sort(compareIds);
  // Несвязанные цепочки тоже начинаются с входа: алфавитное имя callee
  // не должно захватывать его раньше настоящего caller.
  const starts = [rootId, ...rootIds.filter(id => !incoming.has(id)), ...rootIds];
  for (const start of starts) {
    if (!methods.has(start) || seen.has(start)) continue;
    const pending = [{ id: start, rank: 0 }]; seen.add(start);
    for (let index = 0; index < pending.length; index++) {
      const { id, rank } = pending[index];
      (layers[rank] ||= []).push(methods.get(id));
      for (const target of outgoing.get(id)) {
        if (seen.has(target.id)) continue;
        seen.add(target.id); pending.push({ id: target.id, rank: rank + 1 });
      }
    }
  }
  return layers;
}

/**
 * Общая раскладка условий для Natural и обычного Flow. Дерево внутри метода учитывает
 * полные размеры поддеревьев: соседняя альтернатива не может попасть внутрь вложенного if.
 * Тела методов упаковываются рангами до шести колонок; реализации одного интерфейса
 * получают вертикальную колонку, учитывающую высоту каждого дерева. Вызовы, повторы
 * и циклы остаются исходными связями; геометрические слоты не рисуют новые карточки.
 * Возвращает центры в пикселях и отдельно окончания пустых ветвей. Вход не меняется.
 */
export function pathControlTreeLayout(nodes, edges, stubs, rootId, { natural = false } = {}) {
  const { roots, byId } = methodTrees(nodes, edges, stubs);
  for (const root of roots) sizeTree(root);
  const layers = methodLayers(roots, byId, edges, rootId);
  const comparisonGroups = implementationComparisonGroups(edges);
  const positions = new Map(), stubPositions = new Map(), callPositions = new Map();
  let x = 80;
  for (const layer of layers) {
    // Arms продолжают разные полосы, а статические реализации стоят друг под другом.
    const desiredY = node => callPositions.get(node.id)?.y ?? 0;
    layer.sort((a, b) => desiredY(a) - desiredY(b));
    const rows = implementationComparisonRows(layer, comparisonGroups, layer.length > 8 ? 6 : 1);
    let columns = 0;
    for (const row of rows) columns = Math.max(columns, row.length);
    const widths = Array(columns).fill(0), heights = [];
    for (const row of rows) {
      let height = 0;
      for (const [column, node] of row.entries()) {
        widths[column] = Math.max(widths[column], node.width);
        height = Math.max(height, node.height);
      }
      heights.push(height);
    }
    const offsets = [0];
    for (let column = 1; column < columns; column++) offsets.push(offsets[column - 1] + widths[column - 1] + GAP);
    let y = natural ? -heights.reduce((total, height) => total + height + ROW_GAP, -ROW_GAP) / 2 : 80;
    for (let row = 0; row < heights.length; row++) {
      const first = rows[row][0];
      const callPoint = callPositions.get(first.id);
      if (callPoint) y = row === 0 ? callPoint.y - heights[row] / 2 : Math.max(y, callPoint.y - heights[row] / 2);
      for (let column = 0; column < columns; column++) {
        const node = rows[row][column];
        if (node) placeTree(node, x + offsets[column], y + (heights[row] - node.height) / 2,
          positions, stubPositions, callPositions);
      }
      y += heights[row] + ROW_GAP;
    }
    x += widths.reduce((total, width) => total + width + GAP, 0);
  }
  return { positions, stubPositions };
}
