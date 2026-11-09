import { wrapDiagramText } from './diagram-text.js';
import { sequencePresentation } from './sequence-presentation.js';

/** Share visible source and annotation geometry between row placement and SVG rendering. */
export function sequenceRowGeometry(node) {
  const expression = String(node.implicitReturn && node.reuseExpressionResult ? node.label
    : node.sourceStatement || node.sourceExpression || node.label);
  const inset = node.complexity ? 44 : 12;
  const columns = Math.max(8, Math.floor(((node.w || 260) - inset - 12) / 7.3));
  const lines = wrapDiagramText(expression, columns).lines;
  const { summary, notice } = sequencePresentation(node);
  const annotation = notice || summary;
  const annotationY = Math.max(54, 20 + (lines.length - 1) * 17 + 21);
  const lineOffset = node.canExpand ? 48 : node.definitionId ? 27 : 10;
  // Undocumented source-only rows need code plus one footer, not space for an
  // absent explanation. Calls with actions/inventory retain their reserved rows.
  let height = 33 + Math.max(1, lines.length) * 17 + (lineOffset - 10);
  if (node.complexity) height = Math.max(height, 58 + lineOffset - 10);
  if (annotation) height = Math.max(height, annotationY + lineOffset + 24);
  if (node.kind === 'call' && (annotation || node.canExpand || node.candidateCount > 1)) {
    height = Math.max(height, 120 + (node.candidateCount > 1 ? 28 : 0));
  }
  if (node.kind === 'await') height = Math.max(height, 54);
  return { lines, inset, annotation, annotationY, lineY: height - lineOffset, height };
}

/** Use the same content-based height for standalone source rows and shared linear blocks. */
export function sequenceRowHeight(node) { return sequenceRowGeometry(node).height; }

/** Path and inline cards use the same typography and reserved metadata/footer positions. */
export function sequenceSurfaceOptions(node) {
  return { titleFontSize: 14, titleLines: node.decision ? 3 : 2,
    titleLineHeight: 21, headerHeight: node.decision ? 68 : 52, contextY: node.decision ? 88 : 68,
    noteY: node.h - (node.canExpand ? 48 : 28), contextFontSize: 11,
    footerHeight: node.canExpand ? 56 : 32 };
}

/** A focus action sits left of explanation; source and expand actions occupy the separate bottom row. */
export function sequenceActionPoint(node, kind, centered = false) {
  const left = centered ? -node.w / 2 : 0, top = centered ? -node.h / 2 : 0;
  const upper = kind === 'focus' || kind === 'read';
  const right = kind === 'read' || kind === 'source' || kind === 'expand';
  return { x: left + (right ? node.w - 12 : 12), y: top + node.h - (upper ? 28 : 10),
    'text-anchor': right ? 'end' : 'start' };
}
