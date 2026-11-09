import { projectContainerGeometry } from './project-blocks.js';
import { topologyLayout } from './topology-layout.js';
import { groupColor } from './colors.js';
import { groupedNodeGeometry } from './topology-groups.js';
/** Рассчитывает обзор по реальным межгрупповым рёбрам, учитывая размеры групп. */
export function communityGeometry(communities, edges = [], projects = null, projectGraph = null, paletteProjects = projects || []) {
  const points = communities.map(group => ({
    id: String(group.id),
    codeProjectId: group.codeProjectId,
    r: Math.max(22, Math.min(95, 14 + Math.sqrt(Math.max(0, Number(group.nodeCount) || 0)) * 2)),
    color: groupColor(group, paletteProjects),
  }));
  const links = edges.map(edge => ({ ...edge, source: String(edge.source), target: String(edge.target) }));
  for (let index = 0; index < communities.length; index++) {
    const group = communities[index];
    if (!group.architecture || group.architectureLevel === 'projects') continue;
    const length = Math.min(50, String(group.displayLabel || group.label).length);
    points[index].labelWidth = Math.min(25, length) * 12 + 24;
    points[index].labelBottom = points[index].r + 24 + Math.ceil(length / 25) * 23;
  }
  if (communities.length && communities.every(group => group.architectureLevel === 'projects')) return architectureProjectGeometry(points, links, communities);
  if (projects) return projectContainerGeometry(points, links, projects, projectGraph);
  return topologyLayout(points, links, { iterations: 200, padding: 18 });
}
/** Рассчитывает детали по реальным видимым связям; цвет сохраняет принадлежность проекту. */
export function nodeGeometry(nodes, communities = [], edges = [], projects = null, projectGraph = null, paletteProjects = projects || []) {
  return groupedNodeGeometry(nodes, communities, edges, projects, projectGraph, paletteProjects);
}

/** Компактный обзор проектов: силовые центры и непересекающиеся области кружка вместе с полной подписью. */
function architectureProjectGeometry(points, edges, communities) {
  const definitions = new Map(communities.map(group => [String(group.id), group]));
  const compact = points.map(point => ({ ...point, r: Math.max(24, Math.min(42, point.r * .45)) }));
  const positions = topologyLayout(compact, edges, { iterations: 180, padding: 18 });
  for (const point of positions.values()) {
    const label = String(definitions.get(point.id).displayLabel || definitions.get(point.id).label);
    point.labelLines = [];
    for (let offset = 0; offset < label.length;) {
      let length = Math.min(18, label.length - offset);
      // Сохраняем границы namespace, если следующий компонент целиком не помещается.
      const separator = label.slice(offset, offset + length).lastIndexOf('.');
      if (offset + length < label.length && separator > 0) length = separator + 1;
      point.labelLines.push(label.slice(offset, offset + length));
      offset += length;
    }
    point.labelWidth = Math.max(point.r * 2 + 24, Math.min(18, label.length) * 15 + 24);
    point.labelBottom = point.r + 30 + point.labelLines.length * 30;
    point.x *= .45; point.y *= .45;
  }
  const ordered = [...positions.values()];
  for (let pass = 0; pass < 200; pass++) {
    let moved = false;
    for (let first = 0; first < ordered.length; first++) for (let second = first + 1; second < ordered.length; second++) {
      const a = ordered[first], b = ordered[second];
      const heightA = a.r + a.labelBottom, heightB = b.r + b.labelBottom;
      const dx = b.x - a.x, dy = b.y + (b.labelBottom - b.r) / 2 - a.y - (a.labelBottom - a.r) / 2;
      const overlapX = (a.labelWidth + b.labelWidth) / 2 + 22 - Math.abs(dx);
      const overlapY = (heightA + heightB) / 2 + 22 - Math.abs(dy);
      if (overlapX <= 0 || overlapY <= 0) continue;
      moved = true;
      // У широких подписей разрешаем горизонтальный сдвиг охотнее, иначе они выстраиваются в длинную колонку.
      if (overlapX / 1.8 < overlapY) { const move = (overlapX + .01) / 2 * (dx < 0 ? -1 : 1); a.x -= move; b.x += move; }
      else { const move = (overlapY + .01) / 2 * (dy < 0 ? -1 : 1); a.y -= move; b.y += move; }
    }
    if (!moved) break;
  }
  // На больших страницах фиксированный бюджет релаксации может оставить пересечения.
  // Передвигаем только такие элементы вправо за уже размещённые области; сдвиг монотонен.
  for (let index = 0; index < ordered.length; index++) {
    const point = ordered[index];
    for (let pass = 0; pass <= index; pass++) {
      let moved = false;
      for (let previous = 0; previous < index; previous++) {
        const other = ordered[previous];
        const overlapX = Math.min(point.x + point.labelWidth / 2, other.x + other.labelWidth / 2) - Math.max(point.x - point.labelWidth / 2, other.x - other.labelWidth / 2);
        const overlapY = Math.min(point.y + point.labelBottom, other.y + other.labelBottom) - Math.max(point.y - point.r, other.y - other.r);
        if (overlapX <= 0 || overlapY <= 0) continue;
        point.x = other.x + other.labelWidth / 2 + point.labelWidth / 2 + 22;
        moved = true;
      }
      if (!moved) break;
    }
  }
  return positions;
}
