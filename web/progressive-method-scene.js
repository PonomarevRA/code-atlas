import { buildMethodSequence } from './method-sequence.js';
import { sequenceLineBlocks } from './method-sequence-blocks.js';
import { declarationSummary } from './card-documentation.js';
import { placeRightExpansion, progressiveFootprints } from './progressive-placement.js';

export const PROGRESSIVE_LIMITS = Object.freeze({ cards: 320, frames: 24, depth: 12 });
const { cards: MAX_CARDS, frames: MAX_FRAMES, depth: MAX_DEPTH } = PROGRESSIVE_LIMITS;

/** Source order is fixed first; explicit method disclosures are attached afterwards in free right columns. */
export function buildProgressiveMethodScene(graph, rootId, options = {}) {
  const { expandedIds = new Set(), placements = new Map(), getData, loadData = () => {}, getNode } = options;
  const maxDepth = Math.max(1, Math.min(MAX_DEPTH, options.maxDepth || MAX_DEPTH));
  const nodes = new Map(graph.nodes.map(node => [node.id, node]));
  const raw = id => nodes.get(id) || getNode?.(id);
  const facts = id => getData?.(id) || raw(id);
  const scene = buildMethodSequence(graph, rootId, { ...options, localReading: true, expandedIds: new Set(), getNode });
  if (!scene) return null;
  scene.baselineBounds = { ...scene.bounds }; scene.expansions = [];
  scene.requestedTargets = new Set();
  scene.baselineIds = new Set(scene.visuals.map(node => node.id));
  const obstacles = scene.visuals.map(boxOf);
  const initialEdges = scene.visualEdges.slice();
  const depths = new Map(scene.visuals.map(node => [node.id, 0]));
  const ancestors = new Map(scene.visuals.map(node => [node.id, new Set([rootId])]));
  const outgoing = new Map();
  for (const edge of graph.edges) if (edge.relation === 'dispatches_to') {
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    outgoing.get(edge.source).push(edge.target);
  }
  // Saved but currently closed areas do not consume space; their addresses remain available for reopening.
  const livePlacements = new Map([...placements].filter(([key, record]) => !record.id.endsWith('::inline-body')
    && (!options.visibleRegionIds || options.visibleRegionIds.has(key))
    && (expandedIds.has(record.parentId) || !record.parentId.includes('::candidate:') && expandedIds.has(record.targetId))));

  /** Preserve known catalogue scopes and counts; targets outside the path are resolved from the full snapshot. */
  function candidates(call) {
    const result = [], seen = new Set();
    const append = item => { if (item?.id && !seen.has(item.id)) { seen.add(item.id); result.push(item); } };
    for (const item of call.implementationItems || []) append(item);
    if (!result.length) for (const id of call.readTargetIds || call.targetNodeIds || []) {
      for (const target of outgoing.get(id) || [id]) append({ id: target, label: raw(target)?.label || target });
    }
    return result;
  }
  /** Attach a bounded area, protecting all prior card coordinates and source block membership. */
  function attach(call, local, record) {
    if (scene.visuals.length + local.visuals.length > MAX_CARDS || scene.layout.frames.length + local.layout.frames.length > MAX_FRAMES) {
      scene.truncated = true; call.expansionNotice = 'Лимит деталей: сверните часть раскрытых методов.'; return false;
    }
    const entry = local.byId.get(local.rootId);
    // A reopened area's old address can be reused only after visible peers have received their slots.
    if (!livePlacements.has(record.id) && placements.has(record.id)) livePlacements.set(record.id, placements.get(record.id));
    const box = placeRightExpansion(call, { width: local.bounds.width, height: local.bounds.height,
      entryY: entry.y - local.bounds.y, footprints: progressiveFootprints(local.visuals, local.bounds) }, obstacles, livePlacements, record);
    const dx = box.x - local.bounds.x, dy = box.y - local.bounds.y;
    for (const node of local.visuals) { node.x += dx; node.y += dy; scene.visuals.push(node); obstacles.push(boxOf(node)); scene.byId.set(node.id, node); }
    for (const frame of local.layout.frames) { frame.x += dx; frame.y += dy; scene.layout.frames.push(frame); }
    for (const scope of local.layout.controlScopes || []) scene.layout.controlScopes.push({ ...scope, x: scope.x + dx, y: scope.y + dy });
    scene.visualEdges.push(...local.visualEdges);
    scene.truncated ||= Boolean(local.truncated);
    scene.expansions.push({ occurrenceId: call.id, ownerNodeId: call.sourceOwnerId, targetId: record.targetId,
      kind: record.kind, bounds: box, entryId: local.rootId, nodeIds: local.visuals.map(node => node.id),
      frameIds: local.layout.frames.map(frame => frame.id), scopeIds: (local.layout.controlScopes || []).map(scope => scope.id) });
    placements.set(record.id, { ...box }); call.bodyExpanded = true;
    return true;
  }
  /** A chooser exposes project implementations first; it never unfolds all possible bodies as a sequence. */
  function showCandidates(call, items, depth, chain) {
    const source = items.filter(item => (item.scope || 'source') === 'source');
    const selected = (source.length ? source : items).slice(0, 6);
    const visuals = selected.map((item, index) => {
      const node = raw(item.id), data = facts(item.id);
      const id = `${call.id}::candidate:${item.id}`;
      return { id, label: item.label || node?.label || item.id, kind: 'candidate', sourceOwnerId: item.id,
        ownerId: call.id, x: 150, y: index * 152 + 70, w: 300, h: 140, line: item.line || data?.line || node?.line,
        file: item.file || node?.file, codeProjectId: node?.codeProjectId, canExpand: depth < maxDepth,
        readTargetId: item.id, readTargetIds: [item.id], definitionId: item.id, definitionLine: item.line || node?.line,
        definitionLabel: 'Метод ↗', complexity: data?.complexity || node?.complexity, health: data?.health || node?.health,
        condition: declarationSummary(data?.documentation) || node?.comment || 'Возможная реализация · выбор при выполнении',
        documentation: data?.documentation, candidateScope: item.scope || 'source', candidateCount: 1 };
    });
    if (!visuals.length) return;
    const local = { rootId: visuals[0].id, visuals, byId: new Map(visuals.map(node => [node.id, node])),
      visualEdges: [], layout: { frames: [], controlScopes: [] }, bounds: { x: 0, y: 0, width: 300, height: visuals.at(-1).y + 70 } };
    if (!attach(call, local, { id: call.id + '::candidates', parentId: call.id, targetId: null, kind: 'candidates' })) return;
    for (const node of visuals) {
      // Choosing an alternative is UI navigation, not another nested method call.
      depths.set(node.id, depth); ancestors.set(node.id, new Set(chain));
      scene.visualEdges.push({ source: call.id, target: node.id, caption: 'Возможная реализация', sequenceKind: 'candidate' });
    }
    call.shownCandidates = selected.length;
    call.expansionNotice = `${selected.length} карточек · всего ${call.candidateCount || items.length} кандидатов; остальные — «Реализации»`;
    // The next source row remains context until a concrete alternative's normal return is established.
    scene.visualEdges = scene.visualEdges.map(edge => edge.source === call.id && edge.sequenceKind === 'next'
      ? { ...edge, sequenceKind: 'context', caption: 'Следующий шаг вызывающего метода' } : edge);
  }
  /** Read one selected body only; source exits determine normal, exception and unknown continuation. */
  function showBody(call, target, depth, chain) {
    if (chain.has(target.id)) { call.expansionNotice = 'Рекурсия: повторное тело не раскрывается.'; return; }
    scene.requestedTargets.add(target.id);
    const data = facts(target.id);
    if (data?.methodFlow?.status !== 'parsed') {
      const state = options.getStatus?.(target.id);
      call.bodyExpanded = true;
      if (state?.status === 'error') {
        call.expansionFailed = true; call.expansionNotice = `${state.message || 'Не удалось прочитать тело.'} Сверните и раскройте снова для повтора.`; return;
      }
      call.expansionPending = !data?.sourceStatus;
      if (!data?.sourceStatus) loadData(target.id);
      call.expansionNotice = data?.sourceStatus ? 'Детали тела недоступны: откройте исходник.' : 'Загружаем детали…'; return;
    }
    const body = buildMethodSequence(graph, target.id, { ...options, rootData: data, expandedIds: new Set(), getNode,
      localReading: true });
    if (!body) return;
    const prefix = `${call.id}::method:${target.id}`;
    const rename = id => id === target.id ? prefix : id.startsWith(target.id + '::') ? prefix + id.slice(target.id.length) : id;
    body.rootId = prefix;
    for (const node of body.visuals) {
      node.id = rename(node.id); if (node.ownerId) node.ownerId = rename(node.ownerId); if (node.blockId) node.blockId = rename(node.blockId);
      if (node.controlId) node.controlId = rename(node.controlId);
      if (node.controlScope) node.controlScope = { ...node.controlScope, continueTargets: (node.controlScope.continueTargets || []).map(rename) };
    }
    for (const frame of body.layout.frames) { frame.id = rename(frame.id); if (frame.parentId) frame.parentId = rename(frame.parentId); }
    body.layout.controlScopes = body.layout.controlScopes.map(scope => ({ ...scope, id: rename(scope.id), continueTargets: (scope.continueTargets || []).map(rename) }));
    body.visualEdges = body.visualEdges.map(edge => ({ ...edge, source: rename(edge.source), target: rename(edge.target) }));
    body.byId = new Map(body.visuals.map(node => [node.id, node]));
    initialEdges.push(...body.visualEdges);
    if (!attach(call, body, { id: prefix, parentId: call.id, targetId: target.id, kind: 'body' })) return;
    const nextChain = new Set([...chain, target.id]);
    for (const node of body.visuals) {
      depths.set(node.id, depth + 1); ancestors.set(node.id, nextChain);
      if (depth + 1 >= maxDepth && node.canExpand) {
        node.canExpand = false; node.expansionNotice = `Предел ${maxDepth} уровней; откройте исходник.`;
      }
    }
    scene.visualEdges.push({ source: call.id, target: prefix, caption: 'Вызвать', sequenceKind: 'call' });
    const continuationOwner = call.kind === 'candidate' ? call.ownerId : call.id;
    const next = initialEdges.filter(edge => edge.source === continuationOwner && edge.sequenceKind === 'next');
    const complete = !body.truncated && !data.methodFlow.truncated && !data.methodFlow.unsupported?.length;
    const sourceCall = call.kind === 'candidate' ? scene.byId.get(call.ownerId) : call;
    const asyncUnknown = sourceCall?.parallel || data.callableFacts?.modifiers?.includes('async') && !sourceCall?.directAwait;
    scene.visualEdges = scene.visualEdges.filter(edge => !(edge.source === continuationOwner && ['next', 'context'].includes(edge.sequenceKind)));
    for (const child of next) if (child.sequenceKind === 'context') child.sequenceKind = 'next';
    const returned = body.visuals.find(node => node.id === prefix + '::return') || body.visuals.find(node => node.simpleRows);
    const exits = returned && (returned.simpleRows || body.visualEdges.some(edge => edge.target === returned.id));
    for (const edge of next) {
      if (complete && exits && !asyncUnknown) scene.visualEdges.push({ ...edge, source: returned.id, caption: 'Вернуться к следующему шагу', sequenceKind: 'return' });
      else if (!complete || asyncUnknown) scene.visualEdges.push({ ...edge, caption: 'Продолжение не установлено', sequenceKind: 'context' });
    }
  }
  const priorVisible = options.visibleRegionIds || new Set(livePlacements.keys()), processed = new Set();
  // Existing visible bodies receive the finite budget first, including their nested calls.
  while (true) {
    const pending = scene.visuals.filter(call => !processed.has(call.id) && call.canExpand
      && (depths.get(call.id) || 0) < maxDepth && (expandedIds.has(call.id)
        || call.kind !== 'candidate' && (call.readTargetIds || []).some(id => expandedIds.has(id))));
    if (!pending.length) break;
    const call = pending.find(item => [...priorVisible].some(id => id.startsWith(item.id + '::'))) || pending[0];
    processed.add(call.id);
    const depth = depths.get(call.id) || 0;
    const items = candidates(call), chain = ancestors.get(call.id) || new Set([rootId]);
    if (items.length > 1) showCandidates(call, items, depth, chain);
    else if (items.length === 1) { const target = raw(items[0].id); if (target) showBody(call, target, depth, chain); }
  }
  scene.sourceEdges = initialEdges.map(edge => ({ ...edge }));
  scene.positions = new Map(scene.visuals.map(node => [node.id, { ...node, cardBounds: { left: node.x - node.w / 2,
    top: node.y - node.h / 2, right: node.x + node.w / 2, bottom: node.y + node.h / 2 } }]));
  scene.layout.placed = scene.visuals; scene.layout.lineBlocks = sequenceLineBlocks(scene.visuals);
  scene.content = scene.visuals.filter(node => node.id !== rootId);
  const rectangles = scene.visuals.map(boxOf).concat(scene.layout.frames.map(frame => ({ x: frame.x, y: frame.y, width: frame.w, height: frame.h })));
  const left = Math.min(scene.baselineBounds.x, ...rectangles.map(box => box.x));
  const top = Math.min(scene.baselineBounds.y, ...rectangles.map(box => box.y));
  scene.bounds = { x: left, y: top, width: Math.max(...rectangles.map(box => box.x + box.width), scene.baselineBounds.x + scene.baselineBounds.width) - left,
    height: Math.max(...rectangles.map(box => box.y + box.height), scene.baselineBounds.y + scene.baselineBounds.height) - top };
  return scene;
}

/** Convert a source card to a footprint without treating enclosing method frames as occupied space. */
function boxOf(node) { return { x: node.x - node.w / 2, y: node.y - node.h / 2, width: node.w, height: node.h }; }
