import assert from 'node:assert/strict';
import { buildMethodSequence } from '../web/method-sequence.js';
import { buildProgressiveMethodScene } from '../web/progressive-method-scene.js';
import { normalizeProgressivePlacements, placeRightExpansion } from '../web/progressive-placement.js';
import { GraphView } from '../web/graph.js';
import { InlineHierarchyRenderer } from '../web/inline-hierarchy-renderer.js';
import { inlineFlowLayout } from '../web/inline-flow-layout.js';
import { inlineMethodBody } from '../web/inline-method-body.js';
import { navigationSnapshot, resolveNavigation } from '../web/navigation-history.js';
import { Svg } from './svg_fixture.mjs';
import { sequenceLineBlocks } from '../web/method-sequence-blocks.js';
import { createMapBlocks } from '../web/map-blocks.js';
import { revealProgressiveDetails } from '../web/progressive-navigation.js';

/** Calls have exact source occurrences even when they share the same raw target. */
const call = (line, target = 'Child()') => ({ kind: 'call', label: target, line, column: 1, site: { line, column: 1 } });
const root = { id: 'root', label: 'Root()', file: 'C.cs', events: [
  { kind: 'call', text: 'Child()', line: 10, column: 1, targetNodeIds: ['child'] },
  { kind: 'call', text: 'Child()', line: 11, column: 1, targetNodeIds: ['child'] }],
  methodFlow: { status: 'parsed', steps: [call(10), call(11), { kind: 'return', label: 'return result;', line: 12, column: 1 }] } };
const child = { id: 'child', label: 'Child()', file: 'C.cs', events: [], methodFlow: { status: 'parsed', steps: [
  { kind: 'operation', label: 'result = input;', line: 20, column: 1 },
  { kind: 'branch', controlKind: 'if', label: 'ready', line: 21, column: 1, arms: [
    { label: 'истина', steps: [{ kind: 'operation', label: 'result = corrected;', line: 22, column: 1 }] },
    { label: 'иначе', steps: [], emptyReason: 'skip' }] },
  { kind: 'return', label: 'return result;', line: 23, column: 1 }] } };
const graph = { nodes: [root, child], edges: [{ source: 'root', target: 'child', relation: 'calls' }] };
const graphBefore = JSON.stringify(graph), firstId = 'root::inline-call:10:1', secondId = 'root::inline-call:11:1';

/** Expansion may change status/edges, but cannot change old coordinates or source-line block membership. */
function assertRetained(before, after) {
  for (const node of before.visuals) {
    const current = after.byId.get(node.id);
    assert.ok(current, `Retain visible occurrence ${node.id}`);
    const currentPoint = after.positions.get(node.id), oldPoint = before.positions.get(node.id);
    for (const field of ['x', 'y', 'w', 'h']) assert.equal(currentPoint[field], oldPoint[field], `Retain world ${field} on ${node.id}`);
    for (const field of ['blockId', 'rowIndex', 'rowCount', 'compactRow']) assert.equal(current[field], node[field], `Retain ${field} on ${node.id}`);
  }
}
const rawBaseline = buildMethodSequence(graph, 'root');
const baseline = buildProgressiveMethodScene(graph, 'root');
assertRetained(rawBaseline, baseline);
const placements = new Map();
const opened = buildProgressiveMethodScene(graph, 'root', { expandedIds: new Set([firstId]), placements });
assertRetained(baseline, opened);
assert.ok(opened.baselineBounds);
const newCards = opened.visuals.filter(node => !baseline.byId.has(node.id));
assert.ok(newCards.some(node => node.sourceOwnerId === 'child'));
const callerPoint = opened.positions.get(firstId);
assert.ok(newCards.every(node => opened.positions.get(node.id).x - opened.positions.get(node.id).w / 2 > callerPoint.x + callerPoint.w / 2),
  'New method cards lie strictly right of their clicked occurrence');
assert.ok(placements.size > 0);
assert.ok(!opened.visualEdges.some(edge => edge.source === firstId && edge.target === secondId && edge.sequenceKind === 'next'),
  'Visible child body replaces the direct execution shortcut');
assert.ok(opened.visualEdges.some(edge => edge.sequenceKind === 'return' && edge.target === secondId),
  'Normal callee completion returns to the exact next caller occurrence');

// An additional call uses free right-hand space without moving a previous overlay or base block.
const twice = buildProgressiveMethodScene(graph, 'root', { expandedIds: new Set([firstId, secondId]), placements });
assertRetained(opened, twice);
assert.equal(JSON.stringify(graph), graphBefore);

// Old and new cards must remain disjoint, including an overlay next to an occupied right column.
for (let first = 0; first < twice.visuals.length; first++) {
  for (let second = first + 1; second < twice.visuals.length; second++) {
    const a = twice.positions.get(twice.visuals[first].id), b = twice.positions.get(twice.visuals[second].id);
    const separate = a.x + a.w / 2 <= b.x - b.w / 2 + 1e-6 || b.x + b.w / 2 <= a.x - a.w / 2 + 1e-6
      || a.y + a.h / 2 <= b.y - b.h / 2 + 1e-6 || b.y + b.h / 2 <= a.y - a.h / 2 + 1e-6;
    assert.ok(separate, `Cards ${twice.visuals[first].id} and ${twice.visuals[second].id} do not overlap`);
  }
}
const restoredPlacements = new Map(normalizeProgressivePlacements(placements).map(record => [record.id, record]));
const restored = buildProgressiveMethodScene(graph, 'root', { expandedIds: new Set([secondId, firstId]), placements: restoredPlacements });
assertRetained(twice, restored);
const collapsed = buildProgressiveMethodScene(graph, 'root', { placements });
assertRetained(baseline, collapsed);
assert.equal(collapsed.visuals.length, baseline.visuals.length);
assertRetained(twice, buildProgressiveMethodScene(graph, 'root', { expandedIds: new Set([firstId, secondId]), placements }));

// A loading description cannot become a placeholder body that changes the caller or neighboring rows.
const pendingGraph = { nodes: [root, { ...child, methodFlow: undefined }], edges: graph.edges }, requested = [];
const pendingBase = buildProgressiveMethodScene(pendingGraph, 'root', { getData: id => id === 'root' ? root : undefined });
const pending = buildProgressiveMethodScene(pendingGraph, 'root', { expandedIds: new Set([firstId]), placements: new Map(),
  getData: id => id === 'root' ? root : undefined, loadData: id => requested.push(id) });
assert.deepEqual(requested, ['child']);
assertRetained(pendingBase, pending);
assert.equal(pending.visuals.length, pendingBase.visuals.length, 'Loading adds caller status, not an empty method frame');
const resolved = buildProgressiveMethodScene(pendingGraph, 'root', { expandedIds: new Set([firstId]), placements: new Map(),
  getData: id => id === 'child' ? child : root });
assertRetained(pendingBase, resolved);
assert.ok(resolved.visuals.some(node => node.sourceOwnerId === 'child'));

// A resolved description may belong to a catalogue target outside the bounded returned path.
const outsideRoot = { ...root, events: [{ ...root.events[0], targetNodeIds: ['external-child'] }],
  methodFlow: { status: 'parsed', steps: [call(10)] } };
const outsideNode = { ...child, id: 'external-child', label: 'External.Child()' };
const outsideScene = buildProgressiveMethodScene({ nodes: [outsideRoot], edges: [] }, 'root', {
  expandedIds: new Set([firstId]), placements: new Map(),
  getNode: id => id === 'external-child' ? outsideNode : id === 'root' ? outsideRoot : undefined,
  getData: id => id === 'external-child' ? outsideNode : id === 'root' ? outsideRoot : undefined,
  implementationCatalog: { iface: { total: 1, truncated: false, items: [{ id: 'external-child', label: 'External.Child()', scope: 'source' }] } } });
assert.ok(outsideScene.visuals.some(node => node.sourceOwnerId === 'external-child'),
  'A known full-snapshot target can open despite absence from the bounded path nodes');

// A fully parsed throw-only body has no ordinary return shortcut to the next source step.
const throwing = { ...child, methodFlow: { status: 'parsed', steps: [{ kind: 'throw', label: 'throw error;', line: 20, column: 1 }] } };
const thrown = buildProgressiveMethodScene({ nodes: [root, throwing], edges: graph.edges }, 'root', { expandedIds: new Set([firstId]), placements: new Map() });
assertRetained(baseline, thrown);
assert.ok(!thrown.visualEdges.some(edge => edge.target === secondId && ['next', 'return'].includes(edge.sequenceKind)),
  'Throw-only completion does not invent a normal transition to the next caller step');
assert.ok(thrown.visualEdges.some(edge => edge.sequenceKind === 'throw'));
assert.ok(twice.visuals.length <= 320 && twice.layout.frames.length <= 24);
for (const frame of twice.layout.frames) {
  assert.ok(frame.x >= twice.bounds.x && frame.y >= twice.bounds.y
    && frame.x + frame.w <= twice.bounds.x + twice.bounds.width + 1e-6
    && frame.y + frame.h <= twice.bounds.y + twice.bounds.height + 1e-6,
  `Fit/export bounds contain frame ${frame.id}: frame=${JSON.stringify({ x: frame.x, y: frame.y, width: frame.w, height: frame.h })}, scene=${JSON.stringify(twice.bounds)}`);
}

// Occupied space advances to another RIGHT column, preserving the clicked vertical alignment.
const secondNewCards = twice.visuals.filter(node => !opened.byId.has(node.id));
const firstHeader = newCards.find(node => node.kind === 'method'), secondHeader = secondNewCards.find(node => node.kind === 'method');
assert.ok(firstHeader && secondHeader);
assert.equal(opened.positions.get(firstHeader.id).y, opened.positions.get(firstId).y);
assert.equal(twice.positions.get(secondHeader.id).y, twice.positions.get(secondId).y);
assert.ok(twice.positions.get(secondHeader.id).x > opened.positions.get(firstHeader.id).x,
  'An occupied right column causes another right column, never a downward jump');

// Alternatives initially expose compact headers, not six complete bodies.
const implementations = Array.from({ length: 7 }, (_, index) => ({ ...child, id: `impl${index}`, label: `Impl${index}()` }));
const multiRoot = { ...root, events: [{ ...root.events[0], targetNodeIds: ['iface'] }],
  methodFlow: { status: 'parsed', steps: [call(10), { kind: 'return', label: 'return result;', line: 12, column: 1 }] } };
const multiGraph = { nodes: [multiRoot, { id: 'iface', label: 'Interface.Child()' }, ...implementations],
  edges: implementations.map(target => ({ source: 'iface', target: target.id, relation: 'dispatches_to' })) };
const catalog = { iface: { total: 7, truncated: false, items: implementations.map(target => ({ id: target.id, label: target.label, scope: 'source' })) } };
const multiPlacements = new Map();
const multiBase = buildProgressiveMethodScene(multiGraph, 'root', { implementationCatalog: catalog });
const choices = buildProgressiveMethodScene(multiGraph, 'root', { expandedIds: new Set([firstId]), implementationCatalog: catalog, placements: multiPlacements });
assertRetained(multiBase, choices);
const headers = choices.visuals.filter(node => node.kind === 'candidate');
assert.equal(headers.length, 6);
assert.ok(headers.every(node => node.id === `${firstId}::candidate:${node.readTargetId}` && node.definitionId === node.readTargetId && node.canExpand));
assert.ok(!choices.visuals.some(node => node.kind === 'operation' && node.sourceOwnerId.startsWith('impl')));
const rawExpandedAlternatives = buildProgressiveMethodScene(multiGraph, 'root', {
  expandedIds: new Set([firstId, ...implementations.map(node => node.id)]), implementationCatalog: catalog, placements: new Map() });
assert.ok(!rawExpandedAlternatives.visuals.some(node => node.kind === 'operation' && node.sourceOwnerId.startsWith('impl')),
  'Legacy raw-target expansion flags cannot automatically fan out all implementation bodies');
const selectedHeader = headers[0];
const selected = buildProgressiveMethodScene(multiGraph, 'root', { expandedIds: new Set([firstId, selectedHeader.id]),
  implementationCatalog: catalog, placements: multiPlacements });
assertRetained(choices, selected);
assert.ok(selected.byId.get(selectedHeader.id).bodyExpanded);
assert.deepEqual([...new Set(selected.visuals.filter(node => node.kind === 'operation' && node.sourceOwnerId.startsWith('impl')).map(node => node.sourceOwnerId))], [selectedHeader.readTargetId]);
const selectedBody = selected.visuals.find(node => node.kind === 'method' && node.sourceOwnerId === selectedHeader.readTargetId);
assert.ok(selected.positions.get(selectedBody.id).x - selected.positions.get(selectedBody.id).w / 2
  > selected.positions.get(selectedHeader.id).x + selected.positions.get(selectedHeader.id).w / 2);
const originalContinuation = multiBase.visualEdges.find(edge => edge.source === firstId && edge.sequenceKind === 'next').target;
assert.ok(selected.visualEdges.some(edge => edge.sequenceKind === 'return' && edge.target === originalContinuation
  && selected.byId.get(edge.source)?.sourceOwnerId === selectedHeader.readTargetId),
  'The selected alternative returns to the original caller continuation');

// Placement history admits bounded addresses only; malformed geometry cannot move a restored map.
const valid = { id: 'body-address', parentId: firstId, targetId: 'child', kind: 'body', x: 400, y: 100, width: 300, height: 200 };
assert.deepEqual(normalizeProgressivePlacements([{ ...valid, source: 'not-history', html: '<script>' }]), [valid]);
assert.deepEqual(normalizeProgressivePlacements(new Map([[valid.id, valid]])), [valid]);
assert.deepEqual(normalizeProgressivePlacements([{ ...valid, footprints: [null] }]), [valid]);
// A long enclosing method background must not reserve its empty right half for deeper disclosures.
const sparseRegistry = new Map([['outer', { ...valid, id: 'outer', width: 1200,
  footprints: [{ x: 0, y: 0, width: 300, height: 200 }] }]]);
const sparseAnchor = { x: 550, y: 150, w: 300 };
const sparseLocal = { width: 300, height: 200, entryY: 50,
  footprints: [{ x: 0, y: 0, width: 300, height: 200 }] };
const sparseRecord = { id: 'outer::child', parentId: 'outer::call', targetId: 'child', kind: 'body' };
const closeBody = placeRightExpansion(sparseAnchor, sparseLocal, [], sparseRegistry, sparseRecord);
assert.equal(closeBody.x, 770, 'Nested body starts beside the clicked card rather than beyond the enclosing frame');
assert.equal(closeBody.y, 100);
assert.deepEqual(placeRightExpansion(sparseAnchor, sparseLocal, [], sparseRegistry, sparseRecord), closeBody,
  'Reopening the same body retains its aligned position');
for (const invalid of [{ ...valid, x: Infinity }, { ...valid, width: -1 }, { ...valid, height: 1000001 },
  { ...valid, x: 10000001 }, { ...valid, id: 'x'.repeat(12001) }, { ...valid, kind: 'untrusted' }]) {
  assert.deepEqual(normalizeProgressivePlacements([invalid]), []);
}
assert.equal(normalizeProgressivePlacements(Array.from({ length: 60 }, (_, index) => ({ ...valid, id: `body${index}` }))).length, 48);

// Stress all open occurrences: budgets apply to the combined diagram, not independently per detached body.
const crowdedRoot = { ...root, events: Array.from({ length: 60 }, (_, index) => ({ kind: 'call', text: 'Child()', line: index + 1, column: 1, targetNodeIds: ['child'] })),
  methodFlow: { status: 'parsed', steps: Array.from({ length: 60 }, (_, index) => call(index + 1)) } };
const crowded = buildProgressiveMethodScene({ nodes: [crowdedRoot, child], edges: graph.edges }, 'root', {
  expandedIds: new Set(crowdedRoot.events.map(event => `root::inline-call:${event.line}:1`)), placements: new Map() });
assert.ok(crowded.visuals.length <= 320); assert.ok(crowded.layout.frames.length <= 24);
assert.ok(crowded.truncated || crowded.presentationTruncated, 'Visible scene limits cannot silently discard requested bodies');

// Recursive expansion depth is carried through detached bodies instead of resetting for each standalone build.
const chain = Array.from({ length: 5 }, (_, index) => ({ id: `chain${index}`, label: `Chain${index}()`, file: 'C.cs',
  events: index < 4 ? [{ kind: 'call', text: `Chain${index + 1}()`, line: 30, column: 1, targetNodeIds: [`chain${index + 1}`] }] : [],
  methodFlow: { status: 'parsed', steps: index < 4 ? [call(30, `Chain${index + 1}()`), { kind: 'return', label: 'return result;', line: 31, column: 1 }]
    : [{ kind: 'return', label: 'return 1;', line: 31, column: 1 }] } }));
const chainRoot = { ...root, events: [{ ...root.events[0], targetNodeIds: ['chain0'] }],
  methodFlow: { status: 'parsed', steps: [call(10)] } };
const chainGraph = { nodes: [chainRoot, ...chain], edges: [] }, chainOpen = new Set([firstId]), chainPlacements = new Map();
let chainScene;
for (let depth = 0; depth < 4; depth++) {
  chainScene = buildProgressiveMethodScene(chainGraph, 'root', { expandedIds: chainOpen, placements: chainPlacements, maxDepth: 3 });
  const next = chainScene.visuals.find(node => node.kind === 'call' && node.sourceOwnerId === `chain${depth}`);
  if (next) chainOpen.add(next.id);
}
chainScene = buildProgressiveMethodScene(chainGraph, 'root', { expandedIds: chainOpen, placements: chainPlacements, maxDepth: 3 });
assert.ok(!chainScene.visuals.some(node => node.sourceOwnerId === 'chain3'), 'Fourth callee body is not opened beyond depth three');
assert.ok(chainScene.layout.frames.length <= 24);

// A call inside a detached body returns to that body's next row, not only to the outer baseline.
const grandchild = { ...child, id: 'grandchild', label: 'Grandchild()', events: [],
  methodFlow: { status: 'parsed', steps: [{ kind: 'return', label: 'return 1;', line: 40, column: 1 }] } };
const nestedChild = { ...child, events: [{ kind: 'call', text: 'Grandchild()', line: 30, column: 1, targetNodeIds: ['grandchild'] }],
  methodFlow: { status: 'parsed', steps: [call(30, 'Grandchild()'), { kind: 'return', label: 'return result;', line: 31, column: 1 }] } };
const nestedGraph = { nodes: [root, nestedChild, grandchild], edges: graph.edges }, nestedPlacements = new Map();
const outerOnly = buildProgressiveMethodScene(nestedGraph, 'root', { expandedIds: new Set([firstId]), placements: nestedPlacements });
const nestedCall = outerOnly.visuals.find(node => node.kind === 'call' && node.sourceOwnerId === 'child');
const nestedNext = outerOnly.visualEdges.find(edge => edge.source === nestedCall.id && edge.sequenceKind === 'next').target;
const nestedOpen = buildProgressiveMethodScene(nestedGraph, 'root', { expandedIds: new Set([firstId, nestedCall.id]), placements: nestedPlacements });
assertRetained(outerOnly, nestedOpen);
assert.ok(!nestedOpen.visualEdges.some(edge => edge.source === nestedCall.id && edge.target === nestedNext && edge.sequenceKind === 'next'));
assert.ok(nestedOpen.visualEdges.some(edge => edge.sequenceKind === 'return' && edge.target === nestedNext),
  'Grandchild returns to its immediate caller continuation');

// Awaited calls can return to their await row; fire-and-forget/parallel completion remains unknown.
const asyncChild = { ...child, callableFacts: { modifiers: ['async'] } };
const asyncRoot = { ...root, events: [{ ...root.events[0], awaited: true }], methodFlow: { status: 'parsed', steps: [
  { ...call(10), directAwait: true }, { kind: 'await', label: 'Дождаться результата', line: 10, column: 1 },
  { kind: 'return', label: 'return result;', line: 12, column: 1 }] } };
const asyncGraph = { nodes: [asyncRoot, asyncChild], edges: graph.edges };
const asyncBase = buildProgressiveMethodScene(asyncGraph, 'root');
const awaitNode = asyncBase.visuals.find(node => node.kind === 'await');
const awaited = buildProgressiveMethodScene(asyncGraph, 'root', { expandedIds: new Set([firstId]), placements: new Map() });
assertRetained(asyncBase, awaited);
assert.ok(awaited.visualEdges.some(edge => edge.sequenceKind === 'return' && edge.target === awaitNode.id),
  'A direct await retains its known normal completion continuation');
const nonAwaited = buildProgressiveMethodScene({ nodes: [root, asyncChild], edges: graph.edges }, 'root', {
  expandedIds: new Set([firstId]), placements: new Map() });
assert.ok(!nonAwaited.visualEdges.some(edge => edge.sequenceKind === 'return' && edge.target === secondId));
assert.ok(nonAwaited.visualEdges.some(edge => edge.sequenceKind === 'context' && edge.source === firstId && edge.target === secondId));
// A closed disclosure's cached box is not a reservation: a newly visible peer keeps its place.
const collisionPlacements = new Map();
const collisionA = buildProgressiveMethodScene(graph, 'root', { expandedIds: new Set([firstId]), placements: collisionPlacements });
buildProgressiveMethodScene(graph, 'root', { placements: collisionPlacements });
const collisionB = buildProgressiveMethodScene(graph, 'root', { expandedIds: new Set([secondId]), placements: collisionPlacements });
const collisionAB = buildProgressiveMethodScene(graph, 'root', { expandedIds: new Set([firstId, secondId]), placements: collisionPlacements });
assertRetained(collisionB, collisionAB);
const oldA = collisionA.expansions.find(item => item.occurrenceId === firstId);
const newA = collisionAB.expansions.find(item => item.occurrenceId === firstId);
assert.ok(collisionAB.positions.get(newA.entryId).x > collisionA.positions.get(oldA.entryId).x,
  'Reopened A reallocates right when B occupies its former box');

/** The SVG fixture exercises production renderers and real click handlers without a browser layout engine. */
function makeView(provider) {
  const svg = new Svg('svg'), viewport = new Svg('g');
  viewport.setAttribute('id', 'viewport'); svg.append(viewport);
  const view = new GraphView(svg, () => {}, () => {});
  view.pathRootId = 'root'; view.pathProjectId = 'p'; view.pathSnapshotId = 's';
  view.displayProvider = 'flow'; view.groupingProvider = provider;
  return view;
}
/** Use the renderer's disclosure action, preserving source navigation as a separate contract. */
function expand(view, id) {
  const action = view.nodeElements.get(id)?.group.querySelector('.path-card-expand');
  assert.ok(action, `Visible expand action for ${id}`);
  action.listeners.click({ stopPropagation() {}, preventDefault() {} });
}
globalThis.document = { createElementNS: (_namespace, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
for (const provider of ['natural', 'graphistry']) {
  const view = makeView(provider);
  view.renderPathCards(graph.nodes, graph.edges, 'root');
  const before = view.pathSequenceScene, scale = view.camera.scale;
  expand(view, firstId);
  assertRetained(before, view.pathSequenceScene);
  assert.ok(view.pathExpandedIds.has(firstId));
  assert.equal(view.groupingProvider, provider); assert.equal(view.camera.scale, scale);
  assert.ok(view.pathMethodPlacements.size > 0);
  const afterFirst = view.pathSequenceScene;
  expand(view, secondId); assertRetained(afterFirst, view.pathSequenceScene);

  // Selection swaps only alternatives at this call site, retaining disclosures at other calls.
  const alternatives = makeView(provider);
  alternatives.pathImplementationCatalog = catalog;
  alternatives.renderPathCards(multiGraph.nodes, multiGraph.edges, 'root');
  expand(alternatives, firstId);
  const candidateIds = alternatives.pathSequenceScene.visuals.filter(node => node.kind === 'candidate').map(node => node.id);
  alternatives.pathExpandedIds.add('unrelated-call');
  expand(alternatives, candidateIds[0]); expand(alternatives, candidateIds[1]);
  assert.ok(!alternatives.pathExpandedIds.has(candidateIds[0]));
  assert.ok(alternatives.pathExpandedIds.has(candidateIds[1]));
  assert.ok(alternatives.pathExpandedIds.has('unrelated-call'));

  // A short inline body has a new derived entry; neither outer header nor neighboring root resizes.
  const owner = { id: 'root', rawId: 'root', node: root, kind: 'raw', label: 'Root()', expanded: true,
    children: [], totalChildren: 0, memberIds: ['root'], enter: { kind: 'raw' } };
  const neighbor = { ...owner, id: 'neighbor', rawId: 'neighbor', node: { id: 'neighbor', label: 'Neighbor()' }, label: 'Neighbor()' };
  const hierarchy = { roots: ['root', 'neighbor'], items: [owner, neighbor], edges: [], methodBodies: new Map() };
  const anchors = new Map([['root', { x: 0, y: 0, width: 260, height: 154 }],
    ['neighbor', { x: 400, y: 0, width: 260, height: 154 }]]);
  const closedLayout = inlineFlowLayout(hierarchy, anchors);
  const inlinePlacements = new Map();
  const body = inlineMethodBody(root, { status: 'ready', data: root }, graph, { placements: inlinePlacements });
  assert.equal(body.rootId, 'root::body-entry'); assert.ok(body.detached);
  const openedHierarchy = { ...hierarchy, methodBodies: new Map([['root', body]]) };
  const layout = inlineFlowLayout(openedHierarchy, anchors);
  for (const id of hierarchy.roots) assert.deepEqual(layout.positions.get(id), closedLayout.positions.get(id));
  const placement = layout.methodBodies.get('root'), entry = body.byId.get(body.rootId), anchor = layout.positions.get('root');
  assert.ok(entry.x + placement.x - entry.w / 2 > anchor.headerBounds.x + anchor.headerBounds.width);
  assert.ok(inlinePlacements.has('root::inline-body'));
  const inlineView = makeView(provider), sourceCalls = [], toggles = [];
  inlineView.onInlineSource = (id, line) => sourceCalls.push({ id, line });
  inlineView.onToggleBlock = id => toggles.push(id);
  new InlineHierarchyRenderer(inlineView).render(openedHierarchy, layout);
  assert.deepEqual(inlineView.positions.get('root').headerBounds.width, 260);
  assert.ok(inlineView.nodeElements.has('root::body-entry'));
  assert.ok(inlineView.inlineDetailPositions.has('neighbor') && inlineView.inlineDetailPositions.has(firstId),
    'Routing sees outer headers and disclosed source cards together');
  expand(inlineView, firstId); assert.deepEqual(toggles, [firstId]);
  const selectedInline=[]; inlineView.onSequenceSelect=id=>selectedInline.push(id);
  inlineView.nodeElements.get(firstId).group.listeners.click({ stopPropagation() {} });
  assert.deepEqual(selectedInline,['child']); assert.deepEqual(sourceCalls,[]);
  inlineView.nodeElements.get(firstId).group.querySelector('.path-card-source').listeners.click({ stopPropagation() {} });
  assert.deepEqual(sourceCalls,[{ id:'child',line:child.line }],'Explicit definition opens the called method without inventing its declaration line');
  const oldInlinePoints = new Map(inlineView.positions);
  const childBody = inlineMethodBody(root, { status: 'ready', data: root }, graph, {
    placements: inlinePlacements, expandedIds: new Set([firstId]),
    getFacts: id => id === 'child' ? { status: 'ready', data: child } : undefined });
  const nestedHierarchy = { ...hierarchy, methodBodies: new Map([['root', childBody]]) };
  const nestedLayout = inlineFlowLayout(nestedHierarchy, anchors);
  new InlineHierarchyRenderer(inlineView).render(nestedHierarchy, nestedLayout);
  for (const [id, oldPoint] of oldInlinePoints) {
    const point = inlineView.positions.get(id);
    for (const field of ['x', 'y']) assert.equal(point[field], oldPoint[field], `Nested inline disclosure retains ${id} ${field}`);
  }
  assert.ok(childBody.content.some(node => node.sourceOwnerId === 'child'));
  const definition = inlineView.nodeElements.get(firstId).group.querySelector('.path-card-source');
  assert.equal(definition.attributes['data-definition-id'], 'child');
  definition.listeners.click({ stopPropagation() {}, preventDefault() {} });
  assert.equal(sourceCalls.at(-1).id, 'child', 'Definition opens the real callee rather than a presentation occurrence');
}

// Placement envelopes contain bounded addresses only, and cannot survive a changed source snapshot.
const history = navigationSnapshot({ route: { repo: 'p', view: 'architecture', path: 'root' }, snapshotId: 's',
  methodPlacements: placements, inlineMethodPlacements: [{ ...valid, source: 'never persist source text' }] });
assert.deepEqual(history.methodPlacements, normalizeProgressivePlacements(placements));
assert.deepEqual(history.inlineMethodPlacements, [valid]);
const staleHistory = resolveNavigation(history, { projects: [{ id: 'p' }], nodes: graph.nodes, snapshotId: 'fresh' });
assert.deepEqual(staleHistory.snapshot.methodPlacements, []);
assert.deepEqual(staleHistory.snapshot.inlineMethodPlacements, []);

// A fetcher ignoring AbortSignal still cannot append old facts after the graph's context changes.
const pendingRequests = [];
globalThis.fetch = (url, options) => new Promise(resolve => pendingRequests.push({ url, options, resolve }));
const asyncView = makeView('natural');
asyncView.getSourceNode = id => id === 'external-child' ? { ...outsideNode, methodFlow: undefined } : outsideRoot;
asyncView.renderPathCards([outsideRoot], [], 'root');
const asyncBaseline = asyncView.pathSequenceScene;
expand(asyncView, firstId);
assert.equal(pendingRequests.length, 1);
assert.deepEqual(JSON.parse(pendingRequests[0].options.body), { nodeId: 'external-child', snapshotId: 's' });
assert.ok(pendingRequests[0].url.includes('/projects/p/'));
assertRetained(asyncBaseline, asyncView.pathSequenceScene);
asyncView.pathSnapshotId = 'fresh';
asyncView.renderPathCards([outsideRoot], [], 'root');
assert.ok(pendingRequests[0].options.signal.aborted);
pendingRequests[0].resolve({ ok: true, json: async () => ({ ...outsideNode, nodeId: 'external-child', snapshotId: 's' }) });
await new Promise(resolve => setImmediate(resolve));
assert.ok(!asyncView.pathSequenceScene.visuals.some(node => node.sourceOwnerId === 'external-child'));
assert.equal(asyncView.pathExpandedIds.size, 0);

// An in-snapshot description for a full-snapshot target opens at the original source occurrence.
const readyView = makeView('graphistry');
readyView.getSourceNode = asyncView.getSourceNode;
readyView.renderPathCards([outsideRoot], [], 'root');
const readyBaseline = readyView.pathSequenceScene;
expand(readyView, firstId);
const readyRequest = pendingRequests.at(-1);
assert.deepEqual(JSON.parse(readyRequest.options.body), { nodeId: 'external-child', snapshotId: 's' });
readyRequest.resolve({ ok: true, json: async () => ({ ...outsideNode, nodeId: 'external-child', snapshotId: 's' }) });
await new Promise(resolve => setImmediate(resolve));
assertRetained(readyBaseline, readyView.pathSequenceScene);
assert.ok(readyView.pathSequenceScene.visuals.some(node => node.sourceOwnerId === 'external-child'));
assert.equal(pendingRequests.length, 2, 'Redraw reuses pinned facts instead of fetching repeatedly');

// A failed pinned request reports failure rather than pretending to remain loading forever.
const retryView = makeView('natural'); retryView.getSourceNode = asyncView.getSourceNode;
retryView.renderPathCards([outsideRoot], [], 'root'); expand(retryView, firstId);
pendingRequests.at(-1).resolve({ ok: false, status: 503, json: async () => ({ error: 'Service unavailable' }) });
await new Promise(resolve => setImmediate(resolve));
const failedCall = retryView.pathSequenceScene.byId.get(firstId);
assert.ok(failedCall.expansionFailed); assert.ok(!failedCall.expansionPending);
assert.match(failedCall.expansionNotice, /Service unavailable/);
expand(retryView, firstId); expand(retryView, firstId);
pendingRequests.at(-1).resolve({ ok: true, json: async () => ({ ...outsideNode, nodeId: 'external-child', snapshotId: 's' }) });
await new Promise(resolve => setImmediate(resolve));
assert.ok(retryView.pathSequenceScene.visuals.some(node => node.sourceOwnerId === 'external-child'));
assert.ok(!retryView.pathSequenceScene.byId.get(firstId).expansionFailed);

// Opening and closing distinct occurrences must not exhaust the 32 active-description slots.
const sequentialRoot = { ...root, events: Array.from({ length: 35 }, (_, index) => ({ ...root.events[0],
  line: index + 1, targetNodeIds: [`sequential${index}`] })), methodFlow: { status: 'parsed',
  steps: Array.from({ length: 35 }, (_, index) => call(index + 1)) } };
const sequentialView = makeView('natural');
sequentialView.getSourceNode = id => ({ ...child, id, methodFlow: undefined });
sequentialView.renderPathCards([sequentialRoot], [], 'root');
const requestsBeforeSequential = pendingRequests.length;
for (let index = 0; index < 35; index++) {
  const occurrence = `root::inline-call:${index + 1}:1`;
  expand(sequentialView, occurrence);
  const request = pendingRequests.at(-1), id = `sequential${index}`;
  assert.deepEqual(JSON.parse(request.options.body), { nodeId: id, snapshotId: 's' });
  request.resolve({ ok: true, json: async () => ({ ...child, id, nodeId: id, snapshotId: 's' }) });
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(sequentialView.pathSequenceScene.visuals.some(node => node.sourceOwnerId === id));
  expand(sequentialView, occurrence);
  assert.equal(sequentialView.pathMethodFacts.entries.size, 0, 'Collapsed descriptions free active cache slots');
}
assert.equal(pendingRequests.length - requestsBeforeSequential, 35);

// Candidate completion inherits await and parallel semantics from the original call occurrence.
for (const parallel of [false, true]) {
  const candidateRoot = { ...asyncRoot, events: [{ ...asyncRoot.events[0], targetNodeIds: ['iface'] }],
    methodFlow: { ...asyncRoot.methodFlow, steps: [{ ...asyncRoot.methodFlow.steps[0], parallel }, ...asyncRoot.methodFlow.steps.slice(1)] } };
  const candidateGraph = { ...multiGraph, nodes: [candidateRoot, multiGraph.nodes[1],
    ...implementations.map(node => ({ ...node, callableFacts: { modifiers: ['async'] } }))] };
  const chosenId = `${firstId}::candidate:impl0`;
  const completed = buildProgressiveMethodScene(candidateGraph, 'root', {
    expandedIds: new Set([firstId, chosenId]), implementationCatalog: catalog, placements: new Map() });
  const awaitId = completed.visuals.find(node => node.kind === 'await').id;
  assert.equal(completed.visualEdges.some(edge => edge.sequenceKind === 'return' && edge.target === awaitId), !parallel);
  if (parallel) assert.ok(completed.visualEdges.some(edge => edge.sequenceKind === 'context' && edge.source === firstId && edge.target === awaitId));
}

/** Build a new immutable set of inline bodies sharing persisted global placement addresses. */
function inlineOwners(methods, globalPlacements, opened = new Set(), visibleRegionIds) {
  const dataGraph = { nodes: [...methods, child], edges: [] };
  const items = methods.map(node => ({ id: node.id, rawId: node.id, node, kind: 'raw', label: node.label,
    expanded: true, children: [], totalChildren: 0, memberIds: [node.id], enter: { kind: 'raw' } }));
  return { roots: methods.map(node => node.id), items, edges: [], methodBodies: new Map(methods.map(node => [node.id,
    inlineMethodBody(node, { status: 'ready', data: node }, dataGraph, { placements: globalPlacements,
      expandedIds: opened, visibleRegionIds, getFacts: id => id === 'child' ? { status: 'ready', data: child } : undefined })])) };
}
const peerRoot = { ...root, id: 'peer', label: 'Peer()' }, peerCall = 'peer::inline-call:10:1';
const sharedPlacements = new Map(), twoAnchors = new Map([['root', { x: 0, y: 0, width: 260, height: 154 }],
  ['peer', { x: 400, y: 0, width: 260, height: 154 }]]);
const oldOwners = inlineOwners([root, peerRoot], sharedPlacements, new Set([peerCall]));
const oldOwnerLayout = inlineFlowLayout(oldOwners, twoAnchors);
const ownerView = makeView('natural'); new InlineHierarchyRenderer(ownerView).render(oldOwners, oldOwnerLayout);
const beforeGrowth = new Map(ownerView.positions);
const grownOwners = inlineOwners([root, peerRoot], sharedPlacements, new Set([firstId, peerCall]));
const grownLayout = inlineFlowLayout(grownOwners, twoAnchors);
new InlineHierarchyRenderer(ownerView).render(grownOwners, grownLayout);
for (const [id, oldPoint] of beforeGrowth) {
  const point = ownerView.positions.get(id);
  assert.ok(point, `Growth keeps peer occurrence ${id}`);
  for (const field of ['x', 'y']) assert.equal(point[field], oldPoint[field], `Growth of A preserves old A/B ${id} ${field}`);
}
assert.ok(grownOwners.methodBodies.get('root').visualEdges.some(edge => edge.source === 'root'
  && edge.target === 'root::body-entry' && edge.sequenceKind === 'disclosure' && edge.relation === 'source-body'));

// Optional inline detail has a single whole-view budget and explicit continuation when a body area is rejected.
const manyMethods = Array.from({ length: 16 }, (_, index) => ({ ...root, id: `owner${index}`, label: `Owner${index}()` }));
const budgetPlacements = new Map();
const previousBudget = inlineOwners(manyMethods.slice(0, 2), budgetPlacements);
const previousBudgetLayout = inlineFlowLayout(previousBudget);
const crowdedInline = inlineOwners([...manyMethods.slice(2), ...manyMethods.slice(0, 2)], budgetPlacements,
  new Set(manyMethods.map(node => `${node.id}::inline-call:10:1`)));
const crowdedLayout = inlineFlowLayout(crowdedInline);
const visibleBodies = [...crowdedLayout.methodBodies.values()].map(placement => placement.scene);
assert.ok(crowdedLayout.positions.size + visibleBodies.reduce((count, body) => count + body.visuals.length, 0) <= 320);
assert.ok(visibleBodies.reduce((count, body) => count + body.layout.frames.length, 0) <= 24);
assert.ok(crowdedInline.detailLimited);
for (const id of ['owner0', 'owner1']) assert.ok(crowdedLayout.methodBodies.has(id), 'Previously visible bodies win before newly requested bodies');
assert.ok(previousBudgetLayout.methodBodies.size === 2);
let rejectedCallCount = 0;
for (const body of visibleBodies) for (const node of body.visuals) if (node.expansionFailed) {
  rejectedCallCount++;
  const sourceNext = body.sourceEdges.find(edge => edge.source === node.id && edge.sequenceKind === 'next');
  assert.ok(sourceNext, 'Rejected area still knows the immediate source continuation');
  assert.ok(body.visualEdges.some(edge => edge.source === node.id && edge.target === sourceNext.target && edge.sequenceKind === 'context'),
    'Rejected area restores a labelled source continuation rather than dropping the execution path');
}
assert.ok(rejectedCallCount > 0, 'Shared frame budget actually rejects some requested nested bodies');
for (const body of visibleBodies) {
  assert.deepEqual(body.layout.lineBlocks, sequenceLineBlocks(body.visuals), 'Rejected-area outlines contain visible source rows only');
  assert.deepEqual([...body.positions.keys()], body.visuals.map(node => node.id));
  assert.deepEqual(body.layout.placed.map(node => node.id), body.visuals.map(node => node.id));
}

// Exceed the card budget independently of the frame limit: each body has many source rows but one frame.
const rowHeavyMethods = Array.from({ length: 6 }, (_, ownerIndex) => ({ id: `rows${ownerIndex}`, label: `Rows${ownerIndex}()`,
  file: 'C.cs', events: [], methodFlow: { status: 'parsed', steps: Array.from({ length: 80 }, (_, row) => ({
    kind: 'operation', label: `result += value${row};`, line: row + 1, column: 1 })) } }));
const rowPlacements = new Map();
const allRowsRequested = inlineOwners(rowHeavyMethods, rowPlacements);
const requestedRowCount = allRowsRequested.items.length + [...allRowsRequested.methodBodies.values()].reduce((count, body) => count + body.visuals.length, 0);
assert.ok(requestedRowCount > 320, 'This fixture actually exceeds the whole-view card budget');
const rowLayout = inlineFlowLayout(allRowsRequested);
const acceptedRowCount = rowLayout.positions.size + [...rowLayout.methodBodies.values()].reduce((count, placement) => count + placement.scene.visuals.length, 0);
assert.ok(acceptedRowCount <= 320); assert.ok(allRowsRequested.detailLimited);
assert.ok(rowLayout.methodBodies.size < 6, 'Over-budget rows cannot silently bypass the shared limit');
assert.ok(allRowsRequested.items.some(item => item.bodyLimit), 'Hidden details have a visible owner notice');

/** Runtime visibility describes this render only; saved closed geometry is not a visibility claim. */
function regionIds(layout) {
  return new Set([...layout.methodBodies].flatMap(([id, placement]) => [id + '::inline-body',
    ...placement.scene.expansions.map(area => area.bounds.id)]));
}
/** Capture final world outlines independently from renderer translations. */
function worldBlocks(layout) {
  return new Map([...layout.methodBodies.values()].flatMap(placement => placement.scene.layout.lineBlocks.map(block => [block.id,
    { ...block, x: block.x + placement.x, y: block.y + placement.y }])));
}

// A's existing child is reserved before a newly opened B baseline; source rows and their outlines stay together.
const stableAreas = new Map();
const onlyA = inlineOwners([root, peerRoot], stableAreas, new Set([firstId]), new Set());
onlyA.methodBodies.delete('peer');
const onlyALayout = inlineFlowLayout(onlyA, twoAnchors);
const onlyAView = makeView('natural'); new InlineHierarchyRenderer(onlyAView).render(onlyA, onlyALayout);
const oldAPoints = new Map(onlyAView.positions), oldABlocks = worldBlocks(onlyALayout);
const plusB = inlineOwners([root, peerRoot], stableAreas, new Set([firstId]), regionIds(onlyALayout));
const plusBLayout = inlineFlowLayout(plusB, twoAnchors);
new InlineHierarchyRenderer(onlyAView).render(plusB, plusBLayout);
for (const [id, point] of oldAPoints) {
  for (const field of ['x', 'y']) assert.equal(onlyAView.positions.get(id)[field], point[field], `Opening B retains A/Child ${id} ${field}`);
}
const currentBlocks = worldBlocks(plusBLayout);
for (const [id, block] of oldABlocks) assert.deepEqual(currentBlocks.get(id), block, `Opening B retains world outline ${id}`);
for (const placement of plusBLayout.methodBodies.values()) {
  const body = placement.scene;
  assert.deepEqual(body.layout.lineBlocks, sequenceLineBlocks(body.visuals), 'Outlines derive from final translated cards');
  assert.deepEqual(body.layout.placed.map(node => node.id), body.visuals.map(node => node.id));
  assert.equal(body.positions.size, body.visuals.length);
  for (const node of body.visuals) {
    assert.equal(body.positions.get(node.id).x, node.x); assert.equal(body.positions.get(node.id).y, node.y);
  }
}

// At the card limit, a new baseline cannot evict an already visible nested method.
const heavyA = { ...rowHeavyMethods[0], id: 'heavyA', events: [{ ...root.events[0], line: 1, targetNodeIds: ['heavyChild'] }],
  methodFlow: { status: 'parsed', steps: [call(1), ...rowHeavyMethods[0].methodFlow.steps.slice(1, 79)] } };
const heavyChild = { ...rowHeavyMethods[0], id: 'heavyChild', methodFlow: { status: 'parsed',
  steps: Array.from({ length: 100 }, (_, row) => ({ kind: 'operation', label: `result += value${row};`, line: row + 100, column: 1 })) } };
const heavyB = { ...rowHeavyMethods[0], id: 'heavyB', methodFlow: { status: 'parsed',
  steps: Array.from({ length: 140 }, (_, row) => ({ kind: 'operation', label: `result += value${row};`, line: row + 1, column: 1 })) } };
const heavyGraph = { nodes: [heavyA, heavyB, heavyChild], edges: [] }, heavyMap = new Map(), heavyCallId = 'heavyA::inline-call:1:1';
/** Explicitly enumerate both outer owners even when only A has its optional body open. */
function heavyHierarchy(withB, visible) {
  const hierarchy = inlineOwners([heavyA, heavyB], heavyMap, new Set(), visible);
  hierarchy.methodBodies.set('heavyA', inlineMethodBody(heavyA, { status: 'ready', data: heavyA }, heavyGraph, {
    placements: heavyMap, visibleRegionIds: visible, expandedIds: new Set([heavyCallId]),
    getFacts: id => id === 'heavyChild' ? { status: 'ready', data: heavyChild } : undefined }));
  if (!withB) hierarchy.methodBodies.delete('heavyB');
  return hierarchy;
}
const heavyClosed = heavyHierarchy(false, new Set()), heavyBefore = inlineFlowLayout(heavyClosed);
assert.equal(heavyBefore.methodBodies.get('heavyA').scene.baselineIds.size, 81);
assert.equal(heavyBefore.methodBodies.get('heavyA').scene.expansions[0].nodeIds.length, 102);
const heavyAfterScene = heavyHierarchy(true, regionIds(heavyBefore)), heavyAfter = inlineFlowLayout(heavyAfterScene);
assert.ok(!heavyAfter.methodBodies.has('heavyB'), 'New B142 is rejected while A81 plus existing Child102 stay');
assert.equal(heavyAfter.methodBodies.get('heavyA').scene.expansions.length, 1);
assert.equal(heavyAfter.methodBodies.get('heavyA').scene.visuals.length, 183);
assert.ok(heavyAfterScene.items.find(item => item.id === 'heavyB').bodyLimit);
for (const [id, block] of worldBlocks(heavyBefore)) assert.deepEqual(worldBlocks(heavyAfter).get(id), block);

// Path rendering prioritizes the 23 current child bodies before an earlier newly clicked source occurrence.
const twentyFourRoot = { ...sequentialRoot, events: sequentialRoot.events.slice(0, 24).map(event => ({ ...event, targetNodeIds: ['child'] })),
  methodFlow: { status: 'parsed', steps: Array.from({ length: 24 }, (_, index) => call(index + 1)) } };
const cappedView = makeView('natural'); cappedView.renderPathCards([twentyFourRoot, child], [], 'root');
for (let index = 2; index <= 24; index++) expand(cappedView, `root::inline-call:${index}:1`);
const cappedBefore = cappedView.pathSequenceScene;
assert.equal(cappedBefore.layout.frames.length, 24);
expand(cappedView, 'root::inline-call:1:1');
assertRetained(cappedBefore, cappedView.pathSequenceScene);
assert.equal(cappedView.pathSequenceScene.expansions.length, 23);
assert.ok(cappedView.pathSequenceScene.expansions.some(area => area.occurrenceId === 'root::inline-call:24:1'));
assert.ok(cappedView.pathSequenceScene.byId.get('root::inline-call:1:1').expansionNotice);

// Closed cached A is not ghost-visible: B takes the released lane and reopened A moves around live B.
const ghostView = makeView('natural'); ghostView.renderPathCards(graph.nodes, graph.edges, 'root');
expand(ghostView, firstId); const ghostA = ghostView.pathSequenceScene.expansions[0];
const ghostAX = ghostView.positions.get(ghostA.entryId).x;
expand(ghostView, firstId); assert.equal(ghostView.pathVisibleRegions.size, 0);
expand(ghostView, secondId); const ghostB = ghostView.pathSequenceScene;
assert.equal(ghostView.pathVisibleRegions.size, 1);
expand(ghostView, firstId); assertRetained(ghostB, ghostView.pathSequenceScene);
assert.ok(ghostView.positions.get(ghostA.entryId).x > ghostAX);

const ghostInlinePlacements = new Map();
const ghostInlineA = inlineOwners([root, peerRoot], ghostInlinePlacements, new Set(), new Set());
ghostInlineA.methodBodies.delete('peer');
inlineFlowLayout(ghostInlineA, twoAnchors);
const ghostInlineB = inlineOwners([root, peerRoot], ghostInlinePlacements, new Set(), new Set());
ghostInlineB.methodBodies.delete('root');
const ghostInlineBLayout = inlineFlowLayout(ghostInlineB, twoAnchors);
const ghostInlineView = makeView('natural'); new InlineHierarchyRenderer(ghostInlineView).render(ghostInlineB, ghostInlineBLayout);
const ghostInlineBefore = new Map(ghostInlineView.positions);
const ghostInlineBoth = inlineOwners([root, peerRoot], ghostInlinePlacements, new Set(), regionIds(ghostInlineBLayout));
const ghostInlineBothLayout = inlineFlowLayout(ghostInlineBoth, twoAnchors);
new InlineHierarchyRenderer(ghostInlineView).render(ghostInlineBoth, ghostInlineBothLayout);
for (const [id, oldPoint] of ghostInlineBefore) {
  for (const field of ['x', 'y']) assert.equal(ghostInlineView.positions.get(id)[field], oldPoint[field], `Inline reopen A preserves live B ${id} ${field}`);
}

// Choosing an implementation does not spend one of the three method nesting levels.
const depthImpl = { ...chain[0], id: 'impl0' }, depthGraph = { ...multiGraph,
  nodes: [multiRoot, multiGraph.nodes[1], depthImpl, ...implementations.slice(1), ...chain.slice(1)] };
const depthOpen = new Set([firstId, `${firstId}::candidate:impl0`]);
let candidateDepthScene;
for (const ownerId of ['impl0', 'chain1']) {
  candidateDepthScene = buildProgressiveMethodScene(depthGraph, 'root', { expandedIds: depthOpen, implementationCatalog: catalog, maxDepth: 3 });
  const nextCall = candidateDepthScene.visuals.find(node => node.kind === 'call' && node.sourceOwnerId === ownerId);
  assert.ok(nextCall?.canExpand, `Candidate choice permits nesting inside ${ownerId}`); depthOpen.add(nextCall.id);
}
candidateDepthScene = buildProgressiveMethodScene(depthGraph, 'root', { expandedIds: depthOpen, implementationCatalog: catalog, maxDepth: 3 });
assert.ok(candidateDepthScene.visuals.some(node => node.sourceOwnerId === 'chain2'));
assert.ok(!candidateDepthScene.visuals.some(node => node.sourceOwnerId === 'chain3'));

// The product default admits twelve real method levels equally for a direct target and an implementation choice.
const sixLevels = Array.from({ length: 13 }, (_, depth) => ({ id: `level${depth}`, label: `Level${depth}()`, file: 'C.cs',
  events: depth < 12 ? [{ kind: 'call', text: `Level${depth + 1}()`, line: 30, column: 1, targetNodeIds: [`level${depth + 1}`] }] : [],
  methodFlow: { status: 'parsed', steps: depth < 12 ? [call(30, `Level${depth + 1}()`),
    { kind: 'return', label: 'return result;', line: 31, column: 1 }] : [{ kind: 'return', label: 'return 1;', line: 31, column: 1 }] } }));
for (const chooseImplementation of [false, true]) {
  const start = { ...root, events: [{ ...root.events[0], targetNodeIds: [chooseImplementation ? 'iface' : 'level0'] }],
    methodFlow: { status: 'parsed', steps: [call(10), { kind: 'return', label: 'return result;', line: 12, column: 1 }] } };
  const sixGraph = { nodes: [start, ...sixLevels, { id: 'iface', label: 'ILevel.Run()' }, { ...child, id: 'alternative' }],
    edges: chooseImplementation ? [{ source: 'iface', target: 'level0', relation: 'dispatches_to' },
      { source: 'iface', target: 'alternative', relation: 'dispatches_to' }] : [] };
  const sixCatalog = chooseImplementation ? { iface: { total: 2, items: ['level0', 'alternative'].map(id => ({ id, scope: 'source' })) } } : undefined;
  const sixOpen = new Set([firstId]);
  if (chooseImplementation) sixOpen.add(`${firstId}::candidate:level0`);
  const sixPlacements = new Map();
  let sixScene;
  for (let depth = 0; depth < 12; depth++) {
    sixScene = buildProgressiveMethodScene(sixGraph, 'root', { expandedIds: sixOpen, placements: sixPlacements, implementationCatalog: sixCatalog });
    const nextCall = sixScene.visuals.find(node => node.kind === 'call' && node.sourceOwnerId === `level${depth}`);
    assert.ok(nextCall, `Default exposes method level ${depth + 1}`);
    assert.equal(nextCall.canExpand, depth < 11, 'Twelfth method is visible but cannot unfold a thirteenth method');
    sixOpen.add(nextCall.id);
  }
  for (const maxDepth of [undefined, 99]) {
    sixScene = buildProgressiveMethodScene(sixGraph, 'root', { expandedIds: sixOpen, placements: sixPlacements,
      implementationCatalog: sixCatalog, maxDepth });
    for (let depth = 0; depth < 12; depth++) assert.ok(sixScene.visuals.some(node => node.sourceOwnerId === `level${depth}`));
    assert.ok(!sixScene.visuals.some(node => node.sourceOwnerId === 'level12'), 'Default and oversized requests remain bounded at twelve method levels');
    assert.ok(sixScene.visuals.length <= 320 && sixScene.layout.frames.length <= 24);
  }
}

// The controller's eight body/fetch slots preserve current visible owners before a new earlier source owner.
const controllerControls = new Map();
for (const [id, value] of [['view-mode', 'architecture'], ['relation', ''], ['architecture-preset', 'all'], ['project-blocks', '']]) {
  controllerControls.set(id, { value, checked: true });
}
globalThis.document.getElementById = id => controllerControls.get(id);
const controllerMethods = Array.from({ length: 9 }, (_, index) => ({ id: `slot${index}`, label: `Slot${index}()`,
  kind: 'method', file: 'C.cs', events: [], methodFlow: { status: 'parsed', steps: [
    { kind: 'operation', label: 'result = value;', line: 1, column: 1 },
    { kind: 'return', label: 'return result;', line: 2, column: 1 }] } }));
const slotView = makeView('natural'), slotRequests = [];
const slotState = { project: { id: 'p' }, graph: { nodes: controllerMethods, edges: [], metadata: { snapshotId: 's' } },
  nodes: new Map(controllerMethods.map(node => [node.id, node])), algorithm: 'natural', selected: null };
const slotGeometry = () => new Map(controllerMethods.map((node, index) => [node.id, { x: index * 400, y: 0, width: 260, height: 154 }]));
const slotDraw = () => slotView.prepareInline(controllerMethods, false, slotGeometry);
createMapBlocks({ state: slotState, view: slotView, filteredNodes: () => controllerMethods, communityId: () => 'group',
  draw: slotDraw, selectNode() {}, navigate() {}, openCommunity() {}, analyzePath() {}, isolate() {}, controls() {},
  checkpoint() {}, commit() {}, reveal() {}, fetcher: async (_url, options) => {
    const address = JSON.parse(options.body); slotRequests.push(address);
    return { ok: true, json: async () => ({ ...slotState.nodes.get(address.nodeId), nodeId: address.nodeId, snapshotId: 's' }) };
  } });
for (let index = 1; index <= 8; index++) slotState.inlineExpanded.add(`slot${index}`);
slotDraw(); await new Promise(resolve => setImmediate(resolve));
assert.equal(slotRequests.length, 8);
const oldSlotPoints = new Map(slotView.positions);
for (let index = 1; index <= 8; index++) assert.ok(slotView.nodeElements.has(`slot${index}::body-entry`));
slotState.inlineExpanded.add('slot0'); slotDraw();
assert.equal(slotRequests.length, 8, 'A new early owner cannot take an existing owner fetch slot');
for (let index = 1; index <= 8; index++) {
  const id = `slot${index}::body-entry`;
  assert.ok(slotView.nodeElements.has(id), `Controller retains previously disclosed ${id}`);
  for (const field of ['x', 'y']) assert.equal(slotView.positions.get(id)[field], oldSlotPoints.get(id)[field]);
}

// Reveal all five short implementation headers at the current zoom; camera movement cannot alter diagram geometry.
const fiveGraph = { ...multiGraph, nodes: multiGraph.nodes.slice(0, 7), edges: multiGraph.edges.slice(0, 5) };
const fiveCatalog = { iface: { total: 5, items: catalog.iface.items.slice(0, 5) } };
for (const inline of [false, true]) {
  const chooserView = makeView('natural'); chooserView.svg.clientWidth = 2400;
  if (inline) {
    const body = inlineMethodBody(multiRoot, { status: 'ready', data: multiRoot }, fiveGraph,
      { placements: new Map(), expandedIds: new Set([firstId]) });
    const owner = { id: 'root', rawId: 'root', node: multiRoot, kind: 'raw', label: 'Root()', expanded: true,
      children: [], totalChildren: 0, memberIds: ['root'], enter: { kind: 'raw' } };
    const hierarchy = { roots: ['root'], items: [owner], edges: [], methodBodies: new Map([['root', body]]) };
    const layout = inlineFlowLayout(hierarchy);
    new InlineHierarchyRenderer(chooserView).render(hierarchy, layout);
    const placement = layout.methodBodies.get('root'), area = body.expansions.find(item => item.occurrenceId === firstId);
    assert.equal(chooserView.progressiveAreas.get(firstId).y, area.bounds.y + placement.y,
      'Inline chooser reveal uses translated world coordinates');
  } else {
    chooserView.pathImplementationCatalog = fiveCatalog;
    chooserView.renderPathCards(fiveGraph.nodes, fiveGraph.edges, 'root'); expand(chooserView, firstId);
  }
  const candidates = [...chooserView.nodeElements.keys()].filter(id => id.startsWith(`${firstId}::candidate:`));
  assert.equal(candidates.length, 5);
  const geometry = [...chooserView.positions].map(([id, point]) => [id, point.x, point.y, point.w, point.h]);
  for (const automatic of [true, false]) {
    chooserView.camera = { x: 0, y: -140, scale: .7 };
    assert.equal(revealProgressiveDetails(chooserView, firstId, automatic), true);
    assert.equal(chooserView.camera.scale, .7);
    for (const id of candidates) {
      const point = chooserView.positions.get(id);
      const top = (point.y - point.h / 2) * chooserView.camera.scale + chooserView.camera.y;
      const bottom = (point.y + point.h / 2) * chooserView.camera.scale + chooserView.camera.y;
      const left = (point.x - point.w / 2) * chooserView.camera.scale + chooserView.camera.x;
      const right = (point.x + point.w / 2) * chooserView.camera.scale + chooserView.camera.x;
      assert.ok(top >= 32 - 1e-6 && bottom <= chooserView.svg.clientHeight - 32 + 1e-6,
        `Reveal includes every chooser name: ${id}, automatic=${automatic}`);
      assert.ok(left >= 32 - 1e-6 && right <= chooserView.svg.clientWidth - 32 + 1e-6,
        `Reveal includes the full chooser width: ${id}, automatic=${automatic}`);
    }
    assert.deepEqual([...chooserView.positions].map(([id, point]) => [id, point.x, point.y, point.w, point.h]), geometry);
  }
  const area = chooserView.progressiveAreas.get(firstId), entryId = chooserView.progressiveDetails.get(firstId);
  chooserView.progressiveAreas.set(firstId, { ...area, height: 1400 });
  chooserView.camera = { x: 0, y: -200, scale: 1 };
  assert.equal(revealProgressiveDetails(chooserView, firstId), true);
  assert.equal(chooserView.positions.get(entryId).y + chooserView.camera.y, chooserView.svg.clientHeight / 2,
    'Oversized chooser reveals its first header without shrinking the zoom');
  assert.equal(chooserView.camera.scale, 1);
  chooserView.camera = { x: 0, y: -200, scale: 1 };
  assert.equal(revealProgressiveDetails(chooserView, firstId, true), true);
  assert.equal(chooserView.camera.scale, 1);
  const entry = chooserView.positions.get(entryId);
  assert.ok(entry.y - entry.h / 2 + chooserView.camera.y >= 32);
  assert.ok(entry.y + entry.h / 2 + chooserView.camera.y <= chooserView.svg.clientHeight - 32);
}
console.log('Progressive method scene stable world coordinates, compact blocks, right bodies, loading and return semantics PASS');

// A path budget may omit a body, but its snapshot-confirmed call remains navigable.
{
const limitedRoot = {...root,events:root.events.map(event=>({...event,targetNodeIds:[],
  navigationTargetIds:['child'],navigationTargetCount:1,navigationTargetsTruncated:false}))};
const limitedGraph = {nodes:[limitedRoot],edges:[]}, limitedBefore = JSON.stringify(limitedGraph);
const knownPrivate = {id:'child',label:'Child()',file:'C.cs',line:20,memberFacts:{visibility:'private',static:true}};
let loadedChild;
const requested = [];
const outsideOptions = {getNode:id=>id==='child'?knownPrivate:undefined,
  getData:id=>id==='child'?loadedChild:undefined,loadData:id=>requested.push(id)};
const limitedScene = buildProgressiveMethodScene(limitedGraph,'root',outsideOptions);
const limitedCall = limitedScene.byId.get(firstId);
assert.equal(limitedCall.definitionId,'child'); assert.equal(limitedCall.definitionLine,20);
assert.equal(limitedCall.canExpand,true);
assert.equal(limitedScene.visuals.some(node=>node.sourceOwnerId==='child'),false);
assert.deepEqual(requested,[],'Known navigation must not fetch every omitted body');
const loadingOutside = buildProgressiveMethodScene(limitedGraph,'root',{...outsideOptions,expandedIds:new Set([firstId])});
assert.deepEqual(requested,['child']); assert.ok(loadingOutside.byId.get(firstId).expansionPending);
loadedChild = child;
const revealedOutside = buildProgressiveMethodScene(limitedGraph,'root',{...outsideOptions,expandedIds:new Set([firstId])});
assert.ok(revealedOutside.visuals.some(node=>node.sourceOwnerId==='child'));
assertRetained(limitedScene,revealedOutside);
assert.equal(JSON.stringify(limitedGraph),limitedBefore);
const unresolvedRoot = {...limitedRoot,events:limitedRoot.events.map(event=>({...event,navigationTargetIds:[],navigationTargetCount:0}))};
const unresolvedScene = buildProgressiveMethodScene({nodes:[unresolvedRoot],edges:[]},'root',outsideOptions);
assert.equal(unresolvedScene.byId.get(firstId).definitionId,undefined,'A familiar method name is not resolution evidence');
console.log('Out-of-budget private target navigation, lazy disclosure and unresolved-call safety PASS');
}
