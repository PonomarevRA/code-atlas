export const htmlBlock = node => node?.htmlFacts?.role === 'block';
/** Read the original preorder index without relying on a tag name or raw ID. */
export const ordinal = node => Number.isInteger(node.htmlFacts?.elementIndex) ? node.htmlFacts.elementIndex : Infinity;

/** Return parent context from the full captured file, even when the current page filters it out. */
export function htmlReadingOrder(snapshotNodes, visible, file) {
  if (!file || !visible.some(htmlBlock)) return null;
  const blocks = snapshotNodes.filter(node => node.file === file && htmlBlock(node));
  blocks.sort((a, b) => ordinal(a) - ordinal(b) || String(a.id).localeCompare(String(b.id), 'en'));
  const structure = new Map();
  for (const [order, node] of blocks.entries()) {
    // Captured HTML5 is in preorder. Backward-only parents also reject a corrupt cycle.
    const parent = structure.get(node.htmlFacts.parentNodeId);
    structure.set(node.id, { kind: 'html', order, depth: parent ? parent.depth + 1 : 0,
      label: 'Структура HTML', parentLabel: parent?.nodeLabel, nodeLabel: node.label });
  }
  const ordered = visible.map(node => ({ ...node, readingStructure: structure.get(node.id)
    || { kind: 'source', label: 'Другие символы файла' } }));
  ordered.sort((a, b) => (a.readingStructure.order ?? Infinity) - (b.readingStructure.order ?? Infinity)
    || compareSourcePosition(a, b));
  return ordered;
}

/** Use actual coordinates for symbols without a call path; missing positions follow known ones. */
export function compareSourcePosition(a, b) {
  const line = node => Number.isInteger(node.line) && node.line > 0 ? node.line : Infinity;
  return line(a) - line(b) || String(a.label || a.id).localeCompare(String(b.label || b.id), 'ru')
    || String(a.id).localeCompare(String(b.id), 'en');
}

/** Organize files by their saved path, retaining the synthetic navigation IDs. */
export function folderReadingOrder(groups) {
  for (const group of groups) {
    const path = String(group.targetFile || '').replaceAll('\\', '/');
    const divider = path.lastIndexOf('/');
    const label = !path ? 'Без исходного файла' : divider < 0 ? 'Корень проекта' : path.slice(0, divider) || '/';
    group.readingStructure = { kind: 'folder', label, folder: divider < 0 ? '' : path.slice(0, divider + 1) };
  }
  groups.sort((a, b) => Number(!a.targetFile) - Number(!b.targetFile)
    || a.readingStructure.folder.localeCompare(b.readingStructure.folder, 'ru', { numeric: true })
    || a.label.localeCompare(b.label, 'ru', { numeric: true }) || a.id.localeCompare(b.id, 'en'));
  return groups;
}

/** Use structure only for ordinary file reading. Call/dispatch views retain the existing layout. */
export function symbolReadingOrder(graph, scoped, file, relation) {
  const html = htmlReadingOrder(graph.nodes || scoped, scoped, file);
  if (html) return html;
  const ids = new Set(scoped.map(node => node.id));
  const hasCalls = graph.edges.some(edge => (!relation || edge.relation === relation)
    && (edge.relation === 'calls' || edge.relation === 'dispatches_to') && ids.has(edge.source) && ids.has(edge.target));
  if (hasCalls) return [...scoped].sort((a, b) => String(a.label || a.id).localeCompare(String(b.label || b.id), 'ru')
    || String(a.id).localeCompare(String(b.id), 'en'));
  return [...scoped].sort(compareSourcePosition).map(node => ({ ...node,
    readingStructure: { kind: 'source', label: 'Порядок исходника' } }));
}
