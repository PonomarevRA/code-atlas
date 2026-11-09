import { placeRightExpansion, progressiveFootprints } from './progressive-placement.js';
import { PROGRESSIVE_LIMITS } from './progressive-method-scene.js';
import { sequenceLineBlocks } from './method-sequence-blocks.js';

/** Place immutable method baselines first, then new disclosures against the complete inline map. */
export function placeInlineMethodDetails(scene, positions) {
  const placements = new Map(), obstacles = [...positions.values()].map(point => point.cardBounds);
  const bodies = [...(scene.methodBodies || [])].filter(([id, body]) => body.detached && positions.has(id));
  bodies.sort(([a, bodyA], [b, bodyB]) => Number(bodyB.visibleRegionIds.has(b + '::inline-body')) - Number(bodyA.visibleRegionIds.has(a + '::inline-body')));
  const visibleBodies = enforceBudget(scene, bodies, positions.size);
  const liveAreas = new Set();
  for (const [id, body] of visibleBodies) {
    liveAreas.add(id + '::inline-body');
    for (const area of body.expansions) liveAreas.add(area.bounds.id);
  }
  const globalRegistry = new Map();
  // Reserve only regions visible on the preceding render. Closed cached addresses are not obstacles.
  for (const [, body] of visibleBodies) for (const [key, record] of body.placements) {
    if (liveAreas.has(key) && body.visibleRegionIds.has(key)) globalRegistry.set(key, record);
  }
  for (const [id, body] of visibleBodies) {
    const anchor = positions.get(id), entry = body.byId.get(body.rootId), local = body.baselineBounds;
    const registry = new Map(globalRegistry);
    if (body.placements.has(id + '::inline-body')) registry.set(id + '::inline-body', body.placements.get(id + '::inline-body'));
    const box = placeRightExpansion({ ...anchor, w: anchor.headerBounds.width },
      { width: local.width, height: local.height, entryY: entry.y - local.y,
        footprints: progressiveFootprints(body.visuals.filter(node => body.baselineIds.has(node.id)), local) }, obstacles, registry,
      { id: id + '::inline-body', parentId: id, targetId: scene.items.find(item => item.id === id).rawId, kind: 'body' });
    body.placements.set(box.id, box);
    globalRegistry.set(box.id, box);
    const placement = { scene: body, x: box.x - local.x, y: box.y - local.y };
    placements.set(id, placement);
    for (const node of body.visuals) if (body.baselineIds.has(node.id)) obstacles.push(footprint(node, placement));
  }
  // Local wrappers never consume persisted global coordinates. Each new area is docked independently here.
  const areaBoxes = [];
  for (const [, placement] of placements) {
    const body = placement.scene;
    for (const area of body.expansions) {
      const registry = new Map(globalRegistry);
      if (body.placements.has(area.bounds.id)) registry.set(area.bounds.id, body.placements.get(area.bounds.id));
      const caller = body.byId.get(area.occurrenceId), entry = body.byId.get(area.entryId);
      const anchor = { ...caller, x: caller.x + placement.x, y: caller.y + placement.y };
      const box = placeRightExpansion(anchor, { width: area.bounds.width, height: area.bounds.height,
        entryY: entry.y - area.bounds.y, footprints: progressiveFootprints(area.nodeIds.map(id => body.byId.get(id)), area.bounds) }, obstacles, registry, { ...area.bounds });
      const dx = box.x - placement.x - area.bounds.x, dy = box.y - placement.y - area.bounds.y;
      for (const id of area.nodeIds) { const node = body.byId.get(id); node.x += dx; node.y += dy; obstacles.push(footprint(node, placement)); }
      for (const frame of body.layout.frames) if (area.frameIds.includes(frame.id)) { frame.x += dx; frame.y += dy; }
      for (const scope of body.layout.controlScopes) if (area.scopeIds.includes(scope.id)) { scope.x += dx; scope.y += dy; }
      areaBoxes.push(box);
      globalRegistry.set(box.id, box);
      body.placements.set(box.id, box); area.bounds = { ...box, x: box.x - placement.x, y: box.y - placement.y };
    }
    // All derived geometry must follow the final card coordinates and the shared budget filter.
    body.layout.lineBlocks = sequenceLineBlocks(body.visuals);
    body.layout.placed = body.visuals;
    body.positions = new Map(body.visuals.map(node => [node.id, { ...node, cardBounds: {
      left: node.x - node.w / 2, top: node.y - node.h / 2, right: node.x + node.w / 2, bottom: node.y + node.h / 2 } }]));
  }
  const baselineBoxes = [...placements].map(([id, placement]) => placement.scene.placements.get(id + '::inline-body'));
  return { placements, rectangles: obstacles.concat(baselineBoxes, areaBoxes) };
}

/** All local source cards are translated into the common map's obstacle coordinates. */
function footprint(node, offset) {
  return { x: node.x + offset.x - node.w / 2, y: node.y + offset.y - node.h / 2, width: node.w, height: node.h };
}

/** Protect previously visible baselines and disclosures before allocating the shared optional-detail budget. */
function enforceBudget(scene, bodies, headerCount) {
  let cardsLeft = Math.max(0, PROGRESSIVE_LIMITS.cards - headerCount), framesLeft = PROGRESSIVE_LIMITS.frames;
  const accepted = [], selections = new Map(), chosen = new Set();
  /** A baseline is admitted once; a new owner never steals capacity from an existing nested body. */
  function admitBaseline(id, body) {
    const baseline = body.visuals.filter(node => body.baselineIds.has(node.id));
    if (baseline.length > cardsLeft || framesLeft === 0) { markLimit(scene, id); return; }
    cardsLeft -= baseline.length; framesLeft--;
    accepted.push([id, body]); selections.set(body, new Set(baseline.map(node => node.id)));
  }
  /** Parent areas precede descendants; visibility from the last render is independent of cached history. */
  function admitAreas(prior) {
    for (const [id, body] of accepted) for (const area of body.expansions) {
      if (body.visibleRegionIds.has(area.bounds.id) !== prior || chosen.has(area)) continue;
      const kept = selections.get(body);
      if (!kept.has(area.occurrenceId) || area.nodeIds.length > cardsLeft || area.frameIds.length > framesLeft) { markLimit(scene, id); continue; }
      cardsLeft -= area.nodeIds.length; framesLeft -= area.frameIds.length; chosen.add(area);
      for (const nodeId of area.nodeIds) kept.add(nodeId);
    }
  }
  for (const [id, body] of bodies) if (body.visibleRegionIds.has(id + '::inline-body')) admitBaseline(id, body);
  admitAreas(true);
  for (const [id, body] of bodies) if (!body.visibleRegionIds.has(id + '::inline-body')) admitBaseline(id, body);
  admitAreas(false);
  for (const [id, body] of accepted) {
    const kept = selections.get(body), rejected = body.expansions.filter(area => !chosen.has(area));
    body.visuals = body.visuals.filter(node => kept.has(node.id)); body.content = body.visuals;
    body.byId = new Map(body.visuals.map(node => [node.id, node]));
    body.visualEdges = body.visualEdges.filter(edge => (kept.has(edge.source) || edge.source === id) && kept.has(edge.target));
    for (const area of rejected) {
      const caller = body.byId.get(area.occurrenceId);
      if (!caller) continue;
      caller.expansionFailed = true; caller.expansionNotice = 'Общий лимит деталей: сверните часть раскрытий.';
      for (const edge of body.sourceEdges || []) if (edge.source === caller.id && edge.sequenceKind === 'next' && kept.has(edge.target)) {
        body.visualEdges.push({ ...edge, sequenceKind: 'context', caption: 'Детали ограничены; следующий шаг исходника' });
      }
    }
    body.expansions = body.expansions.filter(area => chosen.has(area));
    const frameIds = new Set(body.expansions.flatMap(area => area.frameIds));
    body.layout.frames = body.layout.frames.filter(frame => frame.id === body.rootId || frameIds.has(frame.id));
    body.layout.controlScopes = body.layout.controlScopes.filter(scope => kept.has(scope.id));
  }
  return accepted;
}

/** A display limit is an explicit source-card notice, never a claim that the omitted method is empty. */
function markLimit(scene, id) {
  const owner = scene.items.find(item => item.id === id);
  if (owner) owner.bodyLimit = 'Лимит деталей: сверните часть методов.';
  scene.detailLimited = true;
}
