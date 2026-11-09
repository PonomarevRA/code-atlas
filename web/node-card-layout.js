import { flowLayout } from './flow-layout.js';
import { naturalLayout } from './natural-layout.js';
import { pathControlTreeLayout } from './path-control-tree-layout.js';
import { flowCardMetrics, FLOW_CARD } from './flow-card.js';
import { codeReadingMetrics } from './code-reading-card.js';
import { annotationCardMetrics } from './annotation-card.js';
import { groupOverviewLayout } from './group-overview-model.js';
import { structuralCardLayout } from './structural-card-layout.js';
import { ARCHITECTURE_RESOURCE_FOOTER } from './architecture-resource-style.js';

/**
 * Обычные карточки используют ранги и интервалы пути от входа. Вложенность
 * определяется вызывающим кодом, сюда приходят только реальные видимые связи.
 * SCC служат меткой циклов; порядок чтения не является порядком исполнения.
 * Системные агрегаты резервируют строку типа; обычная карта кода её не получает.
 */
export function nodeCardLayout(nodes = [], edges = [], { natural = false, reading = false, viewportWidth = 0 } = {}) {
  if (nodes.length && nodes.every(node => node.groupSummary)) return groupOverviewLayout(nodes, flowLayout(nodes, edges));
  if (nodes.some(node => node.system)) return (natural ? naturalLayout : flowLayout)(nodes, edges, {
    cardWidth: FLOW_CARD.width, cardHeight: FLOW_CARD.height + ARCHITECTURE_RESOURCE_FOOTER, rankGap: FLOW_CARD.rankGap, rowGap: FLOW_CARD.rowGap,
  });
  const sized = [], incoming = new Set();
  for (const node of nodes) {
    const metrics = node.kind === 'attribute' ? annotationCardMetrics() : reading ? codeReadingMetrics(node) : flowCardMetrics(node);
    sized.push({ id: String(node.id), w: metrics.width, h: metrics.height });
  }
  const ids = new Set(sized.map(node => node.id));
  const connections = [];
  for (const edge of edges) {
    const source = String(edge.source), target = String(edge.target);
    if (!ids.has(source) || !ids.has(target)) continue;
    connections.push({ ...edge, source, target });
    if (source !== target) incoming.add(target);
  }
  if (reading && nodes.length && nodes.every(node => node.readingStructure)) {
    const structure = structuralCardLayout(nodes, sized, viewportWidth);
    if (connections.length) {
      const dependencies = flowLayout(sized, connections);
      structure.edgeKinds = dependencies.edgeKinds;
      for (const [id, point] of structure.positions) point.cycleId = dependencies.positions.get(id)?.cycleId;
    }
    return structure;
  }
  sized.sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const root = sized.find(node => !incoming.has(node.id)) || sized[0];
  const layout = flowLayout(sized, connections);
  const tree = pathControlTreeLayout(sized, connections, [], root?.id, { natural });
  let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
  for (const node of sized) {
    const point = tree.positions.get(node.id);
    const original = layout.positions.get(node.id);
    layout.positions.set(node.id, { ...original, ...point, width: node.w, height: node.h });
    left = Math.min(left, point.x - node.w / 2); right = Math.max(right, point.x + node.w / 2);
    top = Math.min(top, point.y - node.h / 2); bottom = Math.max(bottom, point.y + node.h / 2);
  }
  // Внешний канал обратных связей тоже должен попадать в «Вместить».
  if (sized.length) layout.bounds = { x: left - 160, y: top - 160, width: right - left + 320, height: bottom - top + 320 };
  return layout;
}
