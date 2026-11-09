import { codeProjectId, aggregateCommunities } from './filters.js';
import { topologyLayout } from './topology-layout.js';
/** Составной id исключает смешение одинакового сообщества в разных проектах. */
export function projectCommunityId(node) {
  return JSON.stringify([codeProjectId(node), String(node.community ?? 'ungrouped')]);
}
/** Разбивает сообщества по точному владельцу; считает индуцированные связи между фрагментами. */
export function aggregateProjectFragments(graph, nodes, options = {}) {
  const projects = new Map((graph.codeProjects || []).map(project => [String(project.id), project]));
  const groups = new Map((graph.communities || []).map(group => [String(group.id), group]));
  const definitions = new Map();
  const displayNodes = [];
  for (const node of nodes) {
    const id = projectCommunityId(node);
    const owner = codeProjectId(node);
    const original = String(node.community ?? 'ungrouped');
    if (!definitions.has(id)) {
      const projectLabel = projects.get(owner)?.label || (owner === '__unassigned__' ? 'Без проекта' : owner);
      definitions.set(id, { ...(groups.get(original) || {}), id, originalCommunityId: original, originalCommunityLabel: groups.get(original)?.label || 'Группа', codeProjectId: owner, projectLabel, label: `${groups.get(original)?.label || original} · ${projectLabel}` });
    }
    displayNodes.push({ ...node, community: id });
  }
  const aggregate = aggregateCommunities({ ...graph, communities: [...definitions.values()] }, displayNodes, options);
  return { ...aggregate, displayNodes };
}
/** Суммирует направленные веса: конечный положительный edge.weight, иначе единичный вес факта. */
export function aggregateProjectLinks(nodes, edges) {
  const membership = new Map(nodes.map(node => [node.id, codeProjectId(node)]));
  const totals = new Map();
  for (const id of membership.values()) if (!totals.has(id)) totals.set(id, { incoming: 0, outgoing: 0, internal: 0 });
  const pairs = new Map();
  for (const edge of edges) {
    const source = membership.get(edge.source), target = membership.get(edge.target);
    if (source === undefined || target === undefined) continue;
    const weight = Number.isFinite(edge.weight) && edge.weight > 0 ? edge.weight : 1;
    if (source === target) { totals.get(source).internal += weight; continue; }
    totals.get(source).outgoing += weight;
    totals.get(target).incoming += weight;
    const key = JSON.stringify([source, target]);
    if (!pairs.has(key)) pairs.set(key, { source, target, weight: 0 });
    pairs.get(key).weight += weight;
  }
  const links = [...pairs.values()].sort((a, b) => JSON.stringify([a.source, a.target]).localeCompare(JSON.stringify([b.source, b.target]), 'en'));
  return { links, totals };
}
/** Устраняет пересечение прямоугольников минимальным сдвигом по одной оси, сохраняя топологическую форму. */
function separateRectangles(boxes, gap = 65) {
  for (let pass = 0; pass < 160; pass++) {
    let moved = false;
    for (let first = 0; first < boxes.length; first++) {
      for (let second = first + 1; second < boxes.length; second++) {
        const a = boxes[first], b = boxes[second];
        const dx = b.centerX - a.centerX, dy = b.centerY - a.centerY;
        const overlapX = (a.width + b.width) / 2 + gap - Math.abs(dx);
        const overlapY = (a.height + b.height) / 2 + gap - Math.abs(dy);
        if (overlapX <= 0 || overlapY <= 0) continue;
        moved = true;
        if (overlapX < overlapY) {
          const offset = (overlapX + .01) / 2 * (dx < 0 ? -1 : 1);
          a.centerX -= offset; b.centerX += offset;
        } else {
          const offset = (overlapY + .01) / 2 * (dy < 0 ? -1 : 1);
          a.centerY -= offset; b.centerY += offset;
        }
      }
    }
    if (!moved) break;
  }
}
/** Двухуровневая раскладка; контент вписан в эллипсы, метаграф учитывает их внешние bounds. */
export function projectContainerGeometry(points, edges, projects = [], projectGraph = null) {
  const owners = new Map();
  for (const point of points) {
    const owner = codeProjectId(point);
    if (!owners.has(owner)) owners.set(owner, []);
    owners.get(owner).push(point);
  }
  const projectNames = new Map(projects.map(project => [String(project.id), project.label]));
  const containers = [], positions = new Map();
  const padding = 42;
  for (const [id, members] of owners) {
    const ids = new Set(members.map(point => point.id));
    const localEdges = edges.filter(edge => ids.has(edge.source) && ids.has(edge.target));
    const layout = topologyLayout(members, localEdges, { iterations: 160, padding: 18 });
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const point of layout.values()) {
      minX = Math.min(minX, point.x - point.r); maxX = Math.max(maxX, point.x + point.r);
      minY = Math.min(minY, point.y - point.r); maxY = Math.max(maxY, point.y + point.r);
    }
    const label = projectNames.get(id) || (id === '__unassigned__' ? 'Без проекта' : id);
    const width = Math.max(320, Math.min(680, String(label).length * 16.5 + 50), maxX - minX + padding * 2);
    const charactersPerLine = Math.max(12, Math.floor((width - 40) / 16.5));
    const labelLines = [];
    for (let offset = 0; offset < String(label).length; offset += charactersPerLine) labelLines.push(String(label).slice(offset, offset + charactersPerLine));
    const header = 68 + Math.max(1, labelLines.length) * 34;
    const height = Math.max(190, maxY - minY + padding * 2 + header);
    // Углы прямоугольника попадают на эллипс: (1/√2)² + (1/√2)² = 1.
    // Весь контент, включая полные круги узлов и область заголовка, остаётся внутри.
    containers.push({ id, label, labelLines, header, x: 0, y: 0,
      width: width * Math.SQRT2, height: height * Math.SQRT2,
      contentWidth: width, contentHeight: height, members: layout, minX, minY });
  }
  containers.sort((a, b) => String(a.id).localeCompare(String(b.id), 'en'));
  const sourceGraph = projectGraph || { nodes: points, edges };
  const projectLinks = aggregateProjectLinks(sourceGraph.nodes, sourceGraph.edges);
  // Радиус по корню площади уменьшает пустые промежутки у вытянутых блоков.
  // Разделение внешних прямоугольников гарантирует непересечение эллипсов.
  const projectNodes = containers.map(box => ({ id: box.id, r: Math.sqrt(box.width * box.height) / 2 }));
  const centers = topologyLayout(projectNodes, projectLinks.links, { iterations: 240, padding: 65 });
  for (const box of containers) {
    // Уплотняем только расстояния метаграфа, оставляя размеры блоков прежними.
    // Затем внешние границы эллипсов задают минимальный зазор; общий fit уже
    // не превращает блоки обратно в маленькие точки между длинными рёбрами.
    box.centerX = centers.get(box.id).x * .5;
    box.centerY = centers.get(box.id).y * .5;
  }
  separateRectangles(containers);
  const result = [];
  for (const box of containers) {
    const x = box.centerX - box.width / 2, y = box.centerY - box.height / 2;
    const contentX = box.centerX - box.contentWidth / 2, contentY = box.centerY - box.contentHeight / 2;
    for (const point of box.members.values()) positions.set(point.id, { ...point, x: point.x - box.minX + contentX + padding, y: point.y - box.minY + contentY + padding + box.header });
    const totals = projectLinks.totals.get(box.id) || { incoming: 0, outgoing: 0, internal: 0 };
    result.push({ id: box.id, label: box.label, labelLines: box.labelLines, x, y,
      width: box.width, height: box.height, cx: box.centerX, cy: box.centerY,
      rx: box.width / 2, ry: box.height / 2, contentX, contentY,
      contentWidth: box.contentWidth, contentHeight: box.contentHeight,
      ...totals, statsScope: sourceGraph.scope || 'между показанными узлами' });
  }
  return { positions, containers: result };
}
