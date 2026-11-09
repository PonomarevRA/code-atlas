const GAP = 24;
const SECTION_GAP = 32;
const HEADING_HEIGHT = 36;
const INDENT = 30;
const MAX_VISUAL_DEPTH = 8;

/** Place contiguous reading sections within the initial camera width, with one row per HTML element. */
export function structuralCardLayout(nodes, metrics, viewportWidth) {
  const width = metrics[0].w;
  const initialScale = viewportWidth > 0 ? Math.min(.9, Math.max(.5, (viewportWidth - 48) / (width + 40))) : .9;
  const available = viewportWidth > 0 ? Math.max(width, (viewportWidth - 48) / initialScale) : 1020;
  const columns = Math.min(3, Math.max(1, Math.floor((available + GAP) / (width + GAP))));
  const indentBudget = Math.max(0, available - width);
  const positions = new Map(), sections = [];
  let top = 0, right = width;
  for (let start = 0; start < nodes.length;) {
    const structure = nodes[start].readingStructure;
    let end = start + 1;
    while (end < nodes.length && nodes[end].readingStructure.kind === structure.kind
      && nodes[end].readingStructure.label === structure.label) end++;
    if (structure.label) {
      sections.push({ kind: structure.kind, label: structure.label, x: 0, y: top + 18 });
      top += HEADING_HEIGHT;
    }
    const isTree = structure.kind === 'html';
    const columnCount = isTree ? 1 : columns;
    for (let row = start; row < end; row += columnCount) {
      let rowHeight = 0;
      for (let index = row; index < Math.min(end, row + columnCount); index++) {
        const node = nodes[index], card = metrics[index];
        const depth = node.readingStructure.depth || 0;
        const indent = Math.min(MAX_VISUAL_DEPTH, depth) * Math.min(INDENT, indentBudget / MAX_VISUAL_DEPTH);
        const left = isTree ? indent : (index - row) * (width + GAP);
        const guide = isTree && indent >= 4 ? { x: left - Math.min(12, indent / 2), top, bottom: top + card.h / 2 } : null;
        positions.set(card.id, { x: left + card.w / 2, y: top + card.h / 2,
          width: card.w, height: card.h, guide });
        rowHeight = Math.max(rowHeight, card.h);
        right = Math.max(right, left + card.w);
      }
      top += rowHeight + GAP;
    }
    top += SECTION_GAP - GAP;
    start = end;
  }
  return { positions, sections, initialScale, edgeKinds: new Map(), bounds: { x: -30, y: -16, width: right + 60, height: top + 16 } };
}
