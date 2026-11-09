/** Детерминированная раскладка ограниченного видимого графа. Единица координат — пиксель мира SVG. */
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
/** Вычисляет связные компоненты без добавления искусственных рёбер между ними. */
function components(points, edges) {
  const adjacency = new Map(points.map(point => [point.id, []]));
  for (const edge of edges) {
    adjacency.get(edge.source).push(edge.target);
    adjacency.get(edge.target).push(edge.source);
  }
  const visited = new Set(), result = [];
  for (const point of points) {
    if (visited.has(point.id)) continue;
    const ids = [], pending = [point.id];
    visited.add(point.id);
    while (pending.length) {
      const id = pending.pop(); ids.push(id);
      for (const neighbor of adjacency.get(id)) if (!visited.has(neighbor)) { visited.add(neighbor); pending.push(neighbor); }
    }
    const set = new Set(ids);
    result.push({ points: points.filter(item => set.has(item.id)), edges: edges.filter(edge => set.has(edge.source) && set.has(edge.target)) });
  }
  return result;
}
/** Разводит окружности внутри текущей итерации; форма компоненты после этого снова учитывает связи. */
function collide(points, padding) {
  let maximumOverlap = 0;
  for (let first = 0; first < points.length; first++) {
    for (let second = first + 1; second < points.length; second++) {
      const a = points[first], b = points[second];
      let dx = b.x - a.x, dy = b.y - a.y, distance = Math.hypot(dx, dy);
      const overlap = a.r + b.r + padding - distance;
      if (overlap <= 0) continue;
      maximumOverlap = Math.max(maximumOverlap, overlap);
      if (distance < .00001) { dx = Math.cos(second * GOLDEN_ANGLE); dy = Math.sin(second * GOLDEN_ANGLE); distance = 1; }
      const shift = (overlap + .01) / 2;
      a.x -= dx / distance * shift; a.y -= dy / distance * shift;
      b.x += dx / distance * shift; b.y += dy / distance * shift;
    }
  }
  return maximumOverlap;
}
/** Оптимизирует одну компоненту: обратное расстоянию отталкивание и логарифмическое притяжение по рёбрам. */
function relax(component, iterations, padding) {
  const points = component.points;
  if (points.length < 2) { points[0].x = 0; points[0].y = 0; return; }
  const byId = new Map(points.map((point, index) => [point.id, index]));
  const degree = new Float64Array(points.length);
  const edges = [];
  let weightSum = 0;
  for (const edge of component.edges) {
    const source = byId.get(edge.source), target = byId.get(edge.target);
    const weight = Math.log1p(edge.weight);
    edges.push({ source, target, weight });
    degree[source]++; degree[target]++; weightSum += weight;
  }
  const meanWeight = weightSum / edges.length;
  const meanDegree = 2 * edges.length / points.length;
  const ideal = Math.max(35, points.reduce((sum, point) => sum + point.r * 2, 0) / points.length + padding);
  const initialRadius = ideal * Math.sqrt(points.length);
  for (let index = 0; index < points.length; index++) {
    const point = points[index];
    // Начальная спираль зависит только от упорядоченных id, не от порядка JSON или камеры.
    const radius = initialRadius * Math.sqrt((index + .5) / points.length);
    point.x = radius * Math.cos(index * GOLDEN_ANGLE);
    point.y = radius * Math.sin(index * GOLDEN_ANGLE);
  }
  const dx = new Float64Array(points.length), dy = new Float64Array(points.length);
  for (let iteration = 0; iteration < iterations; iteration++) {
    dx.fill(0); dy.fill(0);
    for (let first = 0; first < points.length; first++) {
      for (let second = first + 1; second < points.length; second++) {
        const a = points[first], b = points[second];
        const vx = a.x - b.x, vy = a.y - b.y;
        const distance = Math.max(.1, Math.hypot(vx, vy));
        const mass = Math.sqrt((degree[first] + 1) * (degree[second] + 1)) / (meanDegree + 1);
        const force = ideal * ideal * mass / distance;
        dx[first] += vx / distance * force; dy[first] += vy / distance * force;
        dx[second] -= vx / distance * force; dy[second] -= vy / distance * force;
      }
    }
    for (const edge of edges) {
      const a = points[edge.source], b = points[edge.target];
      const vx = b.x - a.x, vy = b.y - a.y;
      const distance = Math.max(.1, Math.hypot(vx, vy));
      const normalizedWeight = Math.max(.15, Math.min(3, edge.weight / meanWeight));
      // Чистая логарифмическая тяга оставляет слабые листья слишком далеко от ядра.
      // Дополнительная пружина включается только за допустимой длиной связи;
      // это локальная коррекция геометрии, которую общий fit не отменяет.
      const maximumLength = a.r + b.r + padding + ideal * Math.min(3.5, 2 / Math.sqrt(normalizedWeight));
      const overextension = Math.max(0, distance - maximumLength);
      const force = .42 * ideal * normalizedWeight * Math.log1p(distance / ideal) + .75 * overextension;
      dx[edge.source] += vx / distance * force; dy[edge.source] += vy / distance * force;
      dx[edge.target] -= vx / distance * force; dy[edge.target] -= vy / distance * force;
    }
    const temperature = ideal * (.25 * (1 - iteration / iterations) + .008);
    let centerX = 0, centerY = 0;
    for (let index = 0; index < points.length; index++) {
      const length = Math.max(.001, Math.hypot(dx[index], dy[index]));
      const step = Math.min(temperature, length);
      points[index].x += dx[index] / length * step;
      points[index].y += dy[index] / length * step;
      centerX += points[index].x; centerY += points[index].y;
    }
    for (const point of points) { point.x -= centerX / points.length; point.y -= centerY / points.length; }
    collide(points, padding);
  }
  // Финальная коррекция только остаточных долей пикселя, а не отдельная замена силовой раскладки.
  for (let pass = 0; pass < 60; pass++) if (collide(points, padding) < .05) break;
}
/** Упаковывает готовые компоненты в ряды, чтобы изоляты не растягивали весь граф. */
function pack(componentsList, padding) {
  for (const component of componentsList) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const point of component.points) {
      minX = Math.min(minX, point.x - point.r); maxX = Math.max(maxX, point.x + point.r);
      minY = Math.min(minY, point.y - point.r); maxY = Math.max(maxY, point.y + point.r);
    }
    Object.assign(component, { minX, minY, width: maxX - minX, height: maxY - minY });
  }
  componentsList.sort((a, b) => b.height - a.height || b.width - a.width || String(a.points[0].id).localeCompare(String(b.points[0].id), 'en'));
  const gap = Math.max(50, padding * 3);
  const area = componentsList.reduce((sum, component) => sum + (component.width + gap) * (component.height + gap), 0);
  const rowWidth = Math.max(Math.sqrt(area * 1.45), ...componentsList.map(component => component.width));
  let x = 0, y = 0, rowHeight = 0;
  const principal = componentsList[0];
  const dominant = componentsList.length > 1 && principal.width * principal.height > area * .6;
  for (const component of componentsList) {
    // Малые отдельные компоненты получают компактную полосу под основным графом,
    // а не расходуют целый высокий ряд справа от него.
    if (dominant && component === componentsList[1]) { x = 0; y = principal.height + gap; rowHeight = 0; }
    if (x && x + component.width > rowWidth) { x = 0; y += rowHeight + gap; rowHeight = 0; }
    for (const point of component.points) { point.x += x - component.minX; point.y += y - component.minY; }
    x += component.width + gap; rowHeight = Math.max(rowHeight, component.height);
  }
}
/** Возвращает копии узлов в Map. Максимум 320 узлов; неизвестные концы и self-loop не влияют на физику. */
export function topologyLayout(nodes, edges, { iterations = 180, padding = 14 } = {}) {
  if (nodes.length > 320) throw new RangeError('Раскладка поддерживает не более 320 видимых узлов.');
  if (!nodes.length) return new Map();
  const points = nodes.map(node => ({ ...node, r: Number.isFinite(node.r) ? Math.max(1, node.r) : 7 }));
  points.sort((a, b) => String(a.id).localeCompare(String(b.id), 'en'));
  const ids = new Set(points.map(point => point.id));
  const pairs = new Map();
  for (const edge of edges) {
    if (edge.source === edge.target || !ids.has(edge.source) || !ids.has(edge.target)) continue;
    const pair = [edge.source, edge.target].sort((a, b) => String(a).localeCompare(String(b), 'en'));
    const key = JSON.stringify(pair);
    if (!pairs.has(key)) pairs.set(key, { source: pair[0], target: pair[1], weight: 0 });
    pairs.get(key).weight += Number.isFinite(edge.weight) && edge.weight > 0 ? edge.weight : 1;
  }
  const links = [...pairs.values()].sort((a, b) => JSON.stringify([a.source, a.target]).localeCompare(JSON.stringify([b.source, b.target]), 'en'));
  const connected = components(points, links);
  const boundedIterations = Number.isFinite(iterations) ? Math.max(1, Math.min(350, Math.floor(iterations))) : 180;
  const gap = Number.isFinite(padding) ? Math.max(0, padding) : 14;
  for (const component of connected) relax(component, boundedIterations, gap);
  pack(connected, gap);
  return new Map(points.map(point => [point.id, point]));
}
