import { connectionCurve } from './edge-curves.js';

/** Лексикографический порядок не зависит от locale и порядка входного массива. */
const compareIds = (left, right) => left < right ? -1 : left > right ? 1 : 0;

/** Ключ классификации пары; параллельные факты остаются отдельными рёбрами вызывающего кода. */
export function flowEdgeKey(edge) {
  return JSON.stringify([String(edge.source), String(edge.target)]);
}

/** Создаёт две отсортированные смежности, не изменяя входные узлы или связи. */
function adjacency(ids, edges) {
  const outgoing = new Map(ids.map(id => [id, new Set()]));
  const incoming = new Map(ids.map(id => [id, new Set()]));
  for (const edge of edges) {
    const source = String(edge.source), target = String(edge.target);
    if (!outgoing.has(source) || !outgoing.has(target)) continue;
    outgoing.get(source).add(target); incoming.get(target).add(source);
  }
  const ordered = mapping => new Map([...mapping].map(([id, values]) => [id, [...values].sort(compareIds)]));
  return { outgoing: ordered(outgoing), incoming: ordered(incoming) };
}

/** Итеративный Косарайю выделяет SCC без ограничения глубины стека JavaScript. */
function stronglyConnected(ids, outgoing, incoming) {
  const visited = new Set(), finished = [];
  for (const root of ids) {
    if (visited.has(root)) continue;
    visited.add(root);
    const stack = [[root, 0]];
    while (stack.length) {
      const frame = stack[stack.length - 1], neighbors = outgoing.get(frame[0]);
      if (frame[1] < neighbors.length) {
        const neighbor = neighbors[frame[1]++];
        if (!visited.has(neighbor)) { visited.add(neighbor); stack.push([neighbor, 0]); }
      } else { finished.push(frame[0]); stack.pop(); }
    }
  }
  const assigned = new Set(), groups = [];
  for (const root of finished.reverse()) {
    if (assigned.has(root)) continue;
    const group = [], stack = [root]; assigned.add(root);
    while (stack.length) {
      const id = stack.pop(); group.push(id);
      for (const neighbor of incoming.get(id)) {
        if (!assigned.has(neighbor)) { assigned.add(neighbor); stack.push(neighbor); }
      }
    }
    groups.push(group.sort(compareIds));
  }
  return groups.sort((a, b) => compareIds(a[0], b[0]));
}

/** Строит DAG блоков SCC и слабые компоненты для независимой упаковки. */
function condense(groups, outgoing) {
  const owner = new Map();
  groups.forEach((members, index) => members.forEach(id => owner.set(id, index)));
  const next = groups.map(() => new Set()), previous = groups.map(() => new Set());
  for (const [source, targets] of outgoing) for (const target of targets) {
    const a = owner.get(source), b = owner.get(target);
    if (a !== b) { next[a].add(b); previous[b].add(a); }
  }
  const components = [], visited = new Set();
  for (let root = 0; root < groups.length; root++) {
    if (visited.has(root)) continue;
    const members = [], stack = [root]; visited.add(root);
    while (stack.length) {
      const id = stack.pop(); members.push(id);
      const neighbors = [...next[id], ...previous[id]].sort((a, b) => a - b);
      for (const neighbor of neighbors) if (!visited.has(neighbor)) { visited.add(neighbor); stack.push(neighbor); }
    }
    components.push(members.sort((a, b) => a - b));
  }
  return { owner, next, previous, components };
}

/** Ранги соответствуют длине максимального пути DAG; четыре прохода сокращают пересечения. */
function orderedRanks(component, next, previous) {
  const ranks = new Map(component.map(id => [id, 0]));
  const degrees = new Map(component.map(id => [id, previous[id].size]));
  const queue = component.filter(id => degrees.get(id) === 0);
  for (let offset = 0; offset < queue.length; offset++) {
    const id = queue[offset];
    for (const target of [...next[id]].sort((a, b) => a - b)) {
      ranks.set(target, Math.max(ranks.get(target), ranks.get(id) + 1));
      degrees.set(target, degrees.get(target) - 1);
      if (!degrees.get(target)) queue.push(target);
    }
  }
  const layers = [];
  for (const id of component) (layers[ranks.get(id)] ||= []).push(id);
  for (let pass = 0; pass < 4; pass++) {
    const forward = pass % 2 === 0, neighbors = forward ? previous : next;
    const order = new Map();
    for (const layer of layers) layer.forEach((id, index) => order.set(id, index));
    const indices = layers.map((_, index) => index);
    if (!forward) indices.reverse();
    for (const rank of indices) {
      const score = id => {
        const adjacent = [...neighbors[id]];
        if (!adjacent.length) return order.get(id);
        return adjacent.reduce((sum, neighbor) => sum + order.get(neighbor), 0) / adjacent.length;
      };
      const scores = new Map(layers[rank].map(id => [id, score(id)]));
      layers[rank].sort((a, b) => scores.get(a) - scores.get(b) || a - b);
      layers[rank].forEach((id, index) => order.set(id, index));
    }
  }
  return layers;
}

/** Раскладывает SCC сетками внутри рангов: большой цикл не превращается в одну длинную колонку. */
function placeComponent(component, groups, next, previous, dimensions) {
  const { cardWidth, cardHeight, rankGap, rowGap } = dimensions;
  const layers = orderedRanks(component, next, previous), sizes = new Map();
  for (const id of component) {
    const columns = Math.min(4, Math.ceil(Math.sqrt(groups[id].length)));
    const rows = Math.ceil(groups[id].length / columns);
    sizes.set(id, { columns, width: columns * cardWidth + (columns - 1) * rowGap,
      height: rows * cardHeight + (rows - 1) * rowGap });
  }
  const heights = layers.map(layer => layer.reduce((sum, id) => sum + sizes.get(id).height, 0) + (layer.length - 1) * rowGap * 2);
  const height = Math.max(...heights), points = new Map();
  let x = 0;
  for (let rank = 0; rank < layers.length; rank++) {
    const width = Math.max(...layers[rank].map(id => sizes.get(id).width));
    let y = (height - heights[rank]) / 2;
    for (const id of layers[rank]) {
      const size = sizes.get(id), left = x + (width - size.width) / 2;
      groups[id].forEach((nodeId, index) => points.set(nodeId, {
        x: left + (index % size.columns) * (cardWidth + rowGap) + cardWidth / 2,
        y: y + Math.floor(index / size.columns) * (cardHeight + rowGap) + cardHeight / 2,
        width: cardWidth, height: cardHeight, r: 0,
      }));
      y += size.height + rowGap * 2;
    }
    x += width + rankGap;
  }
  return { points, width: x - rankGap, height };
}

/** Создаёт детерминированную карточную раскладку направленного графа без изменения фактов. */
export function flowLayout(nodes = [], edges = [], options = {}) {
  const dimensions = {};
  for (const [key, fallback] of Object.entries({ cardWidth: 216, cardHeight: 68, rankGap: 96, rowGap: 28 })) {
    const value = Number(options[key]);
    dimensions[key] = Number.isFinite(value) && value > 0 ? value : fallback;
  }
  const ids = [...new Set(nodes.map(node => String(node.id)))].sort(compareIds);
  if (!ids.length) return { positions: new Map(), bounds: { x: 0, y: 0, width: 0, height: 0 }, containers: [], cycles: [], edgeKinds: new Map() };
  const { outgoing, incoming } = adjacency(ids, edges);
  const groups = stronglyConnected(ids, outgoing, incoming);
  const { owner, next, previous, components } = condense(groups, outgoing);
  const cycleIds = new Map(), cycles = [];
  groups.forEach((members, index) => {
    if (members.length > 1 || outgoing.get(members[0]).includes(members[0])) {
      const id = `cycle:${members[0]}`;
      cycleIds.set(index, id); cycles.push({ id, nodeIds: [...members] });
    }
  });
  const blocks = components.map(component => placeComponent(component, groups, next, previous, dimensions));
  const gutter = 180, totalArea = blocks.reduce((sum, block) => sum + (block.width + gutter) * (block.height + gutter), 0);
  const shelfWidth = Math.max(...blocks.map(block => block.width), Math.sqrt(totalArea) * 1.5);
  const positions = new Map();
  let x = 0, y = 0, rowHeight = 0, right = 0, bottom = 0;
  for (const block of blocks) {
    if (x && x + block.width > shelfWidth) { x = 0; y += rowHeight + gutter; rowHeight = 0; }
    for (const [id, point] of block.points) positions.set(id, { ...point, x: point.x + x, y: point.y + y, cycleId: cycleIds.get(owner.get(id)) || null });
    right = Math.max(right, x + block.width); bottom = Math.max(bottom, y + block.height);
    x += block.width + gutter; rowHeight = Math.max(rowHeight, block.height);
  }
  const edgeKinds = new Map();
  for (const [source, targets] of outgoing) for (const target of targets) {
    const key = flowEdgeKey({ source, target });
    edgeKinds.set(key, source === target ? 'self' : owner.get(source) === owner.get(target) ? 'back' : 'forward');
  }
  // Запас включает внешние каналы циклов и петли, чтобы fit не обрезал их.
  return { positions, bounds: { x: -160, y: -160, width: right + 320, height: bottom + 320 }, containers: [], cycles, edgeKinds };
}

/** Соединяет OUT→IN; только обратные переходы и самоссылки используют верхний канал. */
export function flowEdgePath(source, target, index = 0) {
  const sx = source.x + source.width / 2, sy = source.y;
  const tx = target.x - target.width / 2, ty = target.y;
  const numericIndex = Number(index);
  const lane = Number.isFinite(numericIndex) ? Math.abs(Math.trunc(numericIndex)) % 6 : 0;
  // Принадлежность SCC не требует обхода: короткая связь вправо остаётся монотонной.
  if (tx > sx) {
    return connectionCurve({ x: sx, y: sy }, { x: tx, y: ty });
  }
  const bend = 48 + lane * 14;
  const top = Math.min(sy - source.height / 2, ty - target.height / 2) - bend;
  const middleX = (sx + tx) / 2, middleY = top;
  const right = sx + bend, left = tx - bend;
  const sourceAbove = sy - source.height / 2 - 6, targetAbove = ty - target.height / 2 - 6;
  const span = (right - left) / 4;
  // Выходные дуги поднимаются снаружи карточек. Верхние половины встречаются
  // в средней точке с общей касательной, без прямоугольной перемычки.
  return `M ${sx} ${sy} C ${right} ${sy}, ${right} ${(sy + sourceAbove) / 2}, ${right} ${sourceAbove}`
    + ` C ${right} ${top}, ${middleX + span} ${middleY}, ${middleX} ${middleY}`
    + ` C ${middleX - span} ${middleY}, ${left} ${top}, ${left} ${targetAbove}`
    + ` C ${left} ${(ty + targetAbove) / 2}, ${left} ${ty}, ${tx} ${ty}`;
}
