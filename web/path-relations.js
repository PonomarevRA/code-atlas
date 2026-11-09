import { methodOwnerId } from './path-row-layout.js';

/** Направление к реальному блоку, относительно указанной карточки; не порядок исполнения. */
export function pathDirection(from, to) {
  if (!from || !to) return { arrow: '…', label: 'после раскрытия', visible: false };
  const dx = to.x - from.x, dy = to.y - from.y;
  if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return { arrow: '↺', label: 'этот блок', visible: true };
  const horizontal = Math.abs(dx) > Math.abs(dy) * .4;
  const vertical = Math.abs(dy) > Math.abs(dx) * .4;
  const x = horizontal ? Math.sign(dx) : 0, y = vertical ? Math.sign(dy) : 0;
  const arrows = { '1,0': '→', '-1,0': '←', '0,1': '↓', '0,-1': '↑', '1,1': '↘', '1,-1': '↗', '-1,1': '↙', '-1,-1': '↖' };
  const labels = [];
  if (x) labels.push(x > 0 ? 'справа' : 'слева');
  if (y) labels.push(y > 0 ? 'ниже' : 'выше');
  return { arrow: arrows[`${x},${y}`], label: labels.join(', '), visible: true };
}

/** Имя владельца различает методы с одинаковыми названиями Calculate/Resolve. */
export function pathNodeName(node) {
  if (!node) return '';
  const file = String(node.file || '').split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '');
  const owner = node.declaringType || node.typeName || node.owner || node.className || file;
  const label = String(node.label || node.id).replace(/^\./, '');
  return owner ? `${owner}.${label}` : label;
}

/**
 * Прямые связи берутся из raw pathData, а не из структурного if/return дерева.
 * Повторные рёбра группируются по соседу, сохраняя все свидетельства и различие кандидатов.
 */
export function pathRelations(data, selectedId, positions, numbers = new Map(), reveal = null) {
  const selected = methodOwnerId(selectedId || '');
  const nodes = new Map((data?.nodes || []).filter(node => !String(node.id).startsWith('path-entry:')).map(node => [node.id, node]));
  if (!nodes.has(selected)) return null;
  const groups = { incoming: new Map(), outgoing: new Map() };
  for (const edge of data.edges || []) {
    for (const kind of ['incoming', 'outgoing']) {
      if ((kind === 'outgoing' ? edge.source : edge.target) !== selected) continue;
      const id = kind === 'outgoing' ? edge.target : edge.source;
      if (!nodes.has(id)) continue;
      if (!groups[kind].has(id)) groups[kind].set(id, { id, edges: [] });
      groups[kind].get(id).edges.push(edge);
    }
  }
  const describe = (id, relative = selected) => {
    const from = positions.get(relative), to = positions.get(id);
    const direction = pathDirection(from, to);
    // Видимость цели не зависит от того, скрыли ли сам выбранный метод сворачиванием его caller.
    const unknownOrigin = to && !from;
    return { id, label: pathNodeName(nodes.get(id)), number: numbers.get(id), arrow: unknownOrigin ? '•' : direction.arrow,
      direction: unknownOrigin ? 'выбранный блок скрыт; направление не определено' : direction.label, visible: Boolean(to) };
  };
  const result = { selected: describe(selected), incoming: [], outgoing: [], revealed: [], revealOwner: null };
  for (const kind of ['incoming', 'outgoing']) {
    for (const group of groups[kind].values()) {
      const candidate = group.edges.every(edge => edge.relation === 'dispatches_to' || ['candidate', 'inferred'].includes(String(edge.confidence || '').toLowerCase()));
      result[kind].push({ ...describe(group.id), edges: group.edges, candidate });
    }
  }
  if (reveal && nodes.has(reveal.ownerId)) {
    result.revealOwner = describe(reveal.ownerId);
    for (const id of reveal.nodeIds) if (nodes.has(id) && positions.has(id)) result.revealed.push(describe(id, reveal.ownerId));
  }
  return result;
}

/** Кратчайшая известная цепочка до скрытого узла; shared targets и циклы не дублируются. */
export function pathToNode(data, rootId, targetId) {
  const valid = new Set((data?.nodes || []).map(node => node.id));
  if (!valid.has(rootId) || !valid.has(targetId)) return [];
  const outgoing = new Map();
  for (const edge of data.edges || []) {
    if (!valid.has(edge.source) || !valid.has(edge.target)) continue;
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    outgoing.get(edge.source).push(edge.target);
  }
  const parent = new Map([[rootId, null]]), pending = [rootId];
  for (let index = 0; index < pending.length && !parent.has(targetId); index++) {
    for (const next of outgoing.get(pending[index]) || []) {
      if (parent.has(next)) continue;
      parent.set(next, pending[index]);
      pending.push(next);
    }
  }
  if (!parent.has(targetId)) return [];
  const path = [];
  for (let id = targetId; id !== null; id = parent.get(id)) path.push(id);
  return path.reverse();
}
