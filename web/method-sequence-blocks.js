/** Compact only exclusive sequential runs; source IDs and transitions remain intact. */
import { sequenceRowHeight } from './sequence-card-layout.js';
const MAX_ROWS = 6;
const ROW_KINDS = new Set(['call', 'operation', 'await', 'value', 'return', 'throw', 'break', 'continue']);

/** A collapsed call is a row; expanded bodies and control boundaries remain separate cells. */
function rowNode(cell) {
  if (cell.cards.length !== 1 || cell.frames.length) return null;
  const node = cell.cards[0];
  return ROW_KINDS.has(node.kind) && !node.bodyExpanded ? node : null;
}

/** Stack neighboring actions only when their sole connecting edge is a normal next transition. */
export function compactLinearCells(cells, edges) {
  const incoming = new Map(), outgoing = new Map();
  for (const edge of edges) {
    if (!incoming.has(edge.target)) incoming.set(edge.target, []);
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    incoming.get(edge.target).push(edge); outgoing.get(edge.source).push(edge);
  }
  const result = [];
  for (let index = 0; index < cells.length;) {
    const run = [cells[index++]];
    while (run.length < MAX_ROWS && index < cells.length) {
      const previous = rowNode(run.at(-1)), next = rowNode(cells[index]);
      if (!previous || !next) break;
      const links = outgoing.get(previous.id) || [];
      if (links.length !== 1 || links[0].target !== next.id || links[0].sequenceKind !== 'next' || incoming.get(next.id)?.length !== 1) break;
      run.push(cells[index++]);
    }
    if (run.length === 1) { result.push(run[0]); continue; }
    const nodes = run.map(cell => cell.cards[0]), width = Math.max(...nodes.map(n => n.w));
    const blockId = nodes[0].id + '::lines';
    let height = 0;
    for (const [row, node] of nodes.entries()) {
      const rowHeight = sequenceRowHeight(node);
      Object.assign(node, { compactRow: true, blockId, rowIndex: row, rowCount: nodes.length,
        x: width / 2, y: height + rowHeight / 2, w: width, h: rowHeight });
      height += rowHeight;
    }
    result.push({ cards: nodes, frames: [], w: width, h: height,
      entry: run[0].entry, exits: run.at(-1).exits });
  }
  return result;
}

/** Derive the shared outline after translation/filtering; geometry includes all visible rows. */
export function sequenceLineBlocks(nodes) {
  const blocks = new Map();
  for (const node of nodes) {
    if (!node.compactRow) continue;
    const left = node.x - node.w / 2, top = node.y - node.h / 2;
    if (!blocks.has(node.blockId)) blocks.set(node.blockId, { id: node.blockId, x: left, y: top, w: node.w, h: 0 });
    const block = blocks.get(node.blockId);
    block.h = Math.max(block.h, top + node.h - block.y);
  }
  return [...blocks.values()];
}

/** Same-block adjacency is conveyed by row order, while the underlying edge stays queryable. */
export function internalBlockEdge(a, b, edge) {
  return Boolean(a?.blockId && a.blockId === b?.blockId && edge.sequenceKind === 'next');
}
