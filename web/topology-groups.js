import { aggregateProjectLinks } from './project-blocks.js';
import { nodeColor } from './colors.js';

export const TOPOLOGY_CARD = Object.freeze({ width: 246, height: 112 });
const GAP = 22, PAD = 20, HEADER = 54;

/** Обход соседей оставляет связанные элементы рядом; стабильные ID разрешают равенства, а не задают порядок выполнения. */
function connectedOrder(items, edges, keyOf = item => item.id) {
  const byId = new Map(items.map(item => [keyOf(item), item]));
  const neighbours = new Map([...byId.keys()].map(id => [id, new Set()]));
  for (const edge of edges) {
    if (!byId.has(edge.source) || !byId.has(edge.target)) continue;
    neighbours.get(edge.source).add(edge.target); neighbours.get(edge.target).add(edge.source);
  }
  const result = [], seen = new Set();
  const sorted = ids => [...ids].sort((a, b) => String(a).localeCompare(String(b), 'en'));
  for (const first of sorted(byId.keys())) {
    if (seen.has(first)) continue;
    const queue = [first]; seen.add(first);
    for (let cursor = 0; cursor < queue.length; cursor++) {
      const id = queue[cursor]; result.push(byId.get(id));
      for (const next of sorted(neighbours.get(id))) {
        if (seen.has(next)) continue;
        seen.add(next); queue.push(next);
      }
    }
  }
  return result;
}

/** Полочные ряды учитывают полные прямоугольники, включая заголовки и внутренние карточки. */
function packBoxes(boxes, budget, gap = GAP) {
  let x = 0, y = 0, rowHeight = 0, width = 0;
  for (const box of boxes) {
    if (x && x + box.width > budget) { x = 0; y += rowHeight + gap; rowHeight = 0; }
    box.x = x; box.y = y;
    width = Math.max(width, x + box.width);
    rowHeight = Math.max(rowHeight, box.height); x += box.width + gap;
  }
  return { width, height: boxes.length ? y + rowHeight : 0 };
}

/**
 * Группирует символы по сохранённым экспертным сообществам и реальным файлам.
 * Связи определяют соседство, но не меняются и не превращаются в порядок исполнения.
 * Размеры и координаты выражены в пикселях SVG; входные nodes/edges не изменяются.
 * Без проектов возвращает совместимый Map позиций с compartments; с проектами —
 * {positions, compartments, containers}, где containers задают внешние рамки проектов.
 */
export function groupedNodeGeometry(nodes, communities = [], edges = [], projects = null, projectGraph = null, paletteProjects = []) {
  const definitions = new Map(communities.map(group => [String(group.id), group]));
  const names = new Map((projects || paletteProjects).map(project => [String(project.id), project.label]));
  const owners = new Map();
  for (const node of nodes) {
    const project = String(node.codeProjectId || '__unassigned__');
    const community = node.community == null ? '' : String(node.community);
    const expert = definitions.has(community);
    const ownerId = projects ? project : JSON.stringify([project, expert ? community : '']);
    if (!owners.has(ownerId)) {
      const projectLabel = names.get(project) || (project === '__unassigned__' ? 'Без проекта' : project);
      const label = projects ? projectLabel : (expert ? definitions.get(community).label : (names.get(project) || 'Файлы и символы'));
      owners.set(ownerId, { id: ownerId, project, label, files: new Map(), members: [] });
    }
    const owner = owners.get(ownerId);
    const file = String(node.file || '');
    const fileId = JSON.stringify([expert ? community : '', file]);
    if (!owner.files.has(fileId)) owner.files.set(fileId, { id: JSON.stringify([ownerId, fileId]), file, community, label: file.split(/[\\/]/).pop() || (expert ? definitions.get(community).label : 'Без пути исходника'), communityLabel: expert ? definitions.get(community).label : '', members: [] });
    owner.files.get(fileId).members.push(node); owner.members.push(node);
  }
  const ownerOf = new Map();
  for (const owner of owners.values()) for (const node of owner.members) ownerOf.set(node.id, owner.id);
  const ownerEdges = edges.map(edge => ({ source: ownerOf.get(edge.source), target: ownerOf.get(edge.target) }));
  const orderedOwners = connectedOrder([...owners.values()], ownerEdges);
  for (const owner of orderedOwners) {
    const fileOf = new Map();
    for (const file of owner.files.values()) for (const node of file.members) fileOf.set(node.id, file.id);
    const fileEdges = edges.map(edge => ({ source: fileOf.get(edge.source), target: fileOf.get(edge.target) }));
    owner.sections = connectedOrder([...owner.files.values()], fileEdges);
    for (const file of owner.sections) {
      file.ordered = connectedOrder(file.members, edges);
      file.columns = Math.min(3, Math.max(1, Math.ceil(Math.sqrt(file.members.length))));
      file.width = PAD * 2 + file.columns * TOPOLOGY_CARD.width + (file.columns - 1) * GAP;
      file.height = HEADER + PAD + Math.ceil(file.members.length / file.columns) * TOPOLOGY_CARD.height + (Math.ceil(file.members.length / file.columns) - 1) * GAP;
    }
    const bounds = packBoxes(owner.sections, 1650);
    owner.width = bounds.width + PAD * 2; owner.height = bounds.height + HEADER + PAD;
  }
  const area = orderedOwners.reduce((sum, owner) => sum + owner.width * owner.height, 0);
  packBoxes(orderedOwners, Math.max(1000, Math.min(2500, Math.sqrt(area * 1.35))), 58);
  const positions = new Map(), compartments = [], containers = [];
  const totals = aggregateProjectLinks(projectGraph?.nodes || nodes, projectGraph?.edges || edges).totals;
  for (const owner of orderedOwners) {
    if (projects) containers.push({ ...owner, members: undefined, files: undefined, sections: undefined, shape: 'rect', labelLines: [owner.label], contentX: owner.x, contentY: owner.y, contentWidth: owner.width, contentHeight: owner.height, cx: owner.x + owner.width / 2, cy: owner.y + owner.height / 2, rx: owner.width / 2, ry: owner.height / 2, ...(totals.get(owner.project) || { incoming: 0, outgoing: 0, internal: 0 }), statsScope: projectGraph?.scope || 'между показанными узлами' });
    for (const file of owner.sections) {
      const x = owner.x + PAD + file.x, y = owner.y + HEADER + file.y;
      const caption = file.communityLabel && file.communityLabel !== file.label ? `${file.label} · аналитическая группа: ${file.communityLabel}` : file.label;
      compartments.push({ id: file.id, x, y, width: file.width, height: file.height, label: caption, file: file.file, count: file.members.length, projectId: owner.project, color: nodeColor(file.members[0], paletteProjects), ownerLabel: projects ? '' : names.get(owner.project) || owner.project });
      for (let index = 0; index < file.ordered.length; index++) {
        const node = file.ordered[index];
        const left = x + PAD + index % file.columns * (TOPOLOGY_CARD.width + GAP);
        const top = y + HEADER + Math.floor(index / file.columns) * (TOPOLOGY_CARD.height + GAP);
        positions.set(node.id, { id: node.id, codeProjectId: node.codeProjectId, community: String(node.community ?? 'ungrouped'), x: left + TOPOLOGY_CARD.width / 2, y: top + TOPOLOGY_CARD.height / 2, r: 7, width: TOPOLOGY_CARD.width, height: TOPOLOGY_CARD.height, color: nodeColor(node, paletteProjects), cardBounds: { left, top, right: left + TOPOLOGY_CARD.width, bottom: top + TOPOLOGY_CARD.height } });
      }
    }
  }
  positions.compartments = compartments;
  return projects ? { positions, containers, compartments } : positions;
}
