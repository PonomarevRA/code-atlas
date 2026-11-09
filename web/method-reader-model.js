import { declarationSummary } from './card-documentation.js';

/** Exact call occurrences keep repeated/chained calls distinct, using captured end coordinates. */
export function readerCallEvent(description, step) {
  const site = step.site || step;
  const matches = (description.events || []).filter(event => event.line === site.line && event.column === site.column);
  return matches.find(event => site.endLine == null || event.endLine === site.endLine && event.endColumn === site.endColumn);
}

/** Preview only immediate source steps; a collapsed branch is labelled, never flattened into executed calls. */
export function readerPreview(description, limit = 4) {
  const steps = description?.methodFlow?.steps || [];
  return { steps: steps.slice(0, limit), total: steps.length, limited: steps.length > limit,
    analyzed: description?.methodFlow?.status === 'parsed' };
}

/** Names and purposes are declaration facts; do not invent a role from a type name alone. */
export function readerPurpose(node, description) {
  return declarationSummary(description?.documentation) || node?.callableFacts?.description
    || (node?.commentScope === 'declaration' ? node.comment : '') || '';
}

/** Short declared owner names distinguish several LoadAsync methods without inventing a business role. */
export function readerNodeLabel(node, fallback = '') {
  if (!node) return fallback;
  const owner = node.callableFacts?.declaringType?.split('.').pop();
  return owner && node.label?.startsWith('.') ? `${owner}${node.label}` : node.label || fallback;
}

/** One snapshot adjacency index provides typed dependencies even for targets outside the bounded path. */
export function readerDependencyIndex(graph) {
  const outgoing = new Map();
  for (const edge of graph?.edges || []) {
    if (!['calls', 'reads', 'writes', 'initializes_from', 'inherits', 'inherits_from', 'extends', 'implements', 'dispatches_to'].includes(edge.relation)) continue;
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    outgoing.get(edge.source).push(edge);
  }
  return outgoing;
}

/** Restore addresses only, with a fixed origin and bounded local return trail; source/API data stays out of history. */
export function normalizeReaderState(value) {
  if (!value || !['method', 'compare'].includes(value.mode)) return null;
  const id = text => typeof text === 'string' && text.length > 0 && text.length <= 6000 ? text : null;
  const origin = data => ({ ownerNodeId: id(data?.ownerNodeId), occurrenceId: id(data?.occurrenceId),
    originMode: data?.originMode === 'documentation' ? 'documentation' : 'diagram',
    line: Number.isSafeInteger(data?.line) && data.line > 0 ? data.line : null,
    candidateCount: Number.isSafeInteger(data?.candidateCount) && data.candidateCount >= 0 ? data.candidateCount : null,
    candidateCountExact: data?.candidateCountExact !== false,
    targetIds: [...new Set(Array.isArray(data?.targetIds) ? data.targetIds.map(id).filter(Boolean).slice(0, 50) : [])] });
  const scroll = offset => Number.isFinite(offset) ? Math.min(10000000, Math.max(0, offset)) : 0;
  const frame = data => ({ mode: data.mode === 'compare' ? 'compare' : 'method', focusId: id(data.focusId),
    callSite: origin(data.callSite), scrollTop: scroll(data.scrollTop) });
  return { projectId: id(value.projectId), snapshotId: id(value.snapshotId), mode: value.mode, focusId: id(value.focusId), origin: origin(value.origin), callSite: origin(value.callSite),
    trail: (Array.isArray(value.trail) ? value.trail : []).slice(-16).map(frame), scrollTop: scroll(value.scrollTop) };
}
