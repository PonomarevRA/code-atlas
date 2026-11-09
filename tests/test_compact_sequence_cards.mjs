import assert from 'node:assert/strict';
import { buildMethodSequence } from '../web/method-sequence.js';
import { sequenceEdgeRouter } from '../web/method-sequence-routing.js';
import { RectangleIndex, cardRectangle } from '../web/rectangle-index.js';
import { curveCrossesCards } from '../web/inline-flow-edges.js';
import { GraphView } from '../web/graph.js';
import { inlineMethodBody } from '../web/inline-method-body.js';
import { inlineFlowLayout } from '../web/inline-flow-layout.js';
import { InlineHierarchyRenderer } from '../web/inline-hierarchy-renderer.js';
import { codeTokens } from '../web/code-text.js';
import { Svg } from './svg_fixture.mjs';
import { compactConditionEntries } from '../web/method-sequence-compact.js';

const choice = { kind: 'branch', controlKind: '?.', label: 'context.Calculation != null', line: 61, column: 1,
  arms: [{ label: 'значение задано', steps: [{ kind: 'value', label: 'Взять значение', expression: 'context.Calculation.Insurance', line: 61 }] },
    { label: 'null', steps: [{ kind: 'value', expression: 'null', line: 62 }] }] };
const root = { id: 'root', label: 'Build()', file: 'C.cs', methodFlow: { status: 'parsed', steps: [choice,
  { kind: 'operation', label: 'Insurance = selected', line: 63 }] }, events: [] };
const graph = { nodes: [root], edges: [] }, before = JSON.stringify(graph);
const scene = buildMethodSequence(graph, 'root');
assert.equal(JSON.stringify(graph), before);
const node = scene.visuals.find(n => n.valueChoices);
assert.equal(node.valueChoices.length, 2); assert.equal(node.h, 128);
assert.ok(!scene.visuals.some(n => n.valueOutcome));
assert.equal(scene.visuals.find(n => n.kind === 'join').w, 36);
const routes = sequenceEdgeRouter(scene), index = new RectangleIndex(scene.positions.values(), cardRectangle);
const paths = scene.visualEdges.filter(e => e.sourcePort != null).map(e => routes(e).d);
assert.equal(paths.length, 2); assert.notEqual(paths[0], paths[1]);
for (const path of paths) assert.ok(path && !curveCrossesCards(path, index));
const filtered = buildMethodSequence(graph, 'root', { controlFilters: { conditions: false } });
assert.ok(filtered.visuals.find(n => n.valueChoices).hideCondition);
const calledArm = { ...root, methodFlow: { status: 'parsed', steps: [{ ...choice, arms: [
  { label: 'истина', steps: [{ kind: 'call', label: 'Load()', line: 61, column: 1, site: { line: 61, column: 1 } }] }, choice.arms[1]] }] } };
assert.ok(!buildMethodSequence({ nodes: [calledArm], edges: [] }, 'root').visuals.some(n => n.valueChoices));

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g'); viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const sourceActions = [], view = new GraphView(svg, () => {}, () => {});
view.pathRootId = 'root'; view.onPathSource = (...args) => sourceActions.push(args); view.displayProvider = 'flow';
for (const provider of ['natural', 'graphistry']) {
  view.groupingProvider = provider; view.renderPathCards(graph.nodes, graph.edges, 'root');
  const symbol = viewport.querySelector('.method-value-choice');
  assert.equal(symbol.querySelectorAll('circle').filter(n => String(n.attributes['data-port']).startsWith('out-')).length, 2);
  symbol.children.find(n => n.attributes['data-value-choice'] === 1).listeners.click({ stopPropagation() {}, preventDefault() {} });
  assert.deepEqual(sourceActions.at(-1), ['root', 62]);
  const continuation = viewport.querySelector('.method-sequence-transition');
  assert.ok(continuation.querySelector('circle')); assert.ok(!continuation.querySelector('rect'));
}
const simple = { id: 'simple', label: '.ToString()', file: 'C.cs', line: 17, events: [],
  methodFlow: { status: 'parsed', steps: [{ kind: 'return', label: 'return $"[{DateFrom}, {DateTo})";', line: 19 }] } };
const compact = buildMethodSequence({ nodes: [simple], edges: [] }, 'simple');
assert.equal(compact.visuals.length, 1); assert.ok(compact.visuals[0].simpleRows);
view.pathRootId = 'simple'; view.renderPathCards([simple], [], 'simple');
assert.ok(viewport.querySelector('.method-simple-card'));
assert.equal(viewport.querySelectorAll('.method-sequence-frame').length, 0);
const body = inlineMethodBody(simple, { data: simple }, { nodes: [simple], edges: [] });
view.onInlineSource = (...args) => sourceActions.push(args);
const owner = { id: 'simple', rawId: 'simple', kind: 'raw', node: simple, label: simple.label, children: [], expanded: true };
const hierarchy = { items: [owner], roots: ['simple'], edges: [], methodBodies: new Map([['simple', body]]) };
new InlineHierarchyRenderer(view).render(hierarchy, inlineFlowLayout(hierarchy));
assert.equal(viewport.querySelectorAll('.method-simple-card').length, 1);
assert.equal(viewport.querySelectorAll('.inline-hierarchy-frame').length, 0);
viewport.querySelector('.method-simple-card').children.find(n => n.attributes['aria-label']?.includes('Исходник строки 19')).listeners.click({ stopPropagation() {}, preventDefault() {} });
assert.deepEqual(sourceActions.at(-1), ['simple', 19]);
assert.equal(codeTokens('return "<script>  x </script>" ?? 42;').map(t => t.text).join(''), 'return "<script>  x </script>" ?? 42;');
assert.ok(viewport.querySelectorAll('tspan').some(n => n.attributes['data-token-kind'] === 'string'));
assert.ok(!viewport.querySelector('script'));

const entry = { id: 'path-entry:http', label: '/calculate', httpMethod: 'POST' };
view.pathRootId = 'root'; view.renderPathCards([entry, root], [{ source: entry.id, target: root.id }], 'root');
assert.ok(viewport.querySelector('.method-sequence-entry'));
console.log('Compact two-value cards, distinct ports, circular joins, simple methods, entry style and safe code colours PASS');

// The source's short-circuit evaluation and reuse test become one guard, not two checks.
const evaluation = { kind: 'branch', controlKind: '&&', label: 'request.IsDay', line: 46, column: 13,
  arms: [{ label: 'если левая часть истинна', steps: [{ kind: 'value', line: 46, expression: 'input.LiveBrokerReturns is not null' }] },
    { label: 'левая часть ложна; правая не вычисляется', steps: [], emptyReason: 'skip' }] };
const guard = { kind: 'branch', controlKind: 'if', line: 46, column: 9,
  label: 'request.IsDay && input.LiveBrokerReturns is not null', conditionResult: 'reuse',
  conditionExpression: 'request.IsDay && input.LiveBrokerReturns is not null',
  arms: [{ label: 'истина', steps: [{ kind: 'operation', line: 47, label: 'input = input with { Rows = [] }' }] },
    { label: 'иначе', steps: [], emptyReason: 'skip' }] };
const guarded = { id: 'guarded', label: 'Build()', file: 'C.cs', events: [],
  methodFlow: { status: 'parsed', steps: [evaluation, guard, { kind: 'operation', line: 48, label: 'result = input' }] } };
const guardedGraph = { nodes: [guarded], edges: [] }, original = JSON.stringify(guardedGraph);
const guardedScene = buildMethodSequence(guardedGraph, 'guarded');
const guardNode = guardedScene.visuals.find(n => n.guardGeometry);
assert.ok(guardNode.compactEvaluation); assert.ok(guardNode.h < 180);
assert.match(guardNode.id, /::step:2:46:9$/);
assert.match(guardedScene.visuals.find(n => n.line === 48).id, /::step:3:/);
assert.equal(guardedScene.visuals.filter(n => n.line === 46).length, 1);
assert.equal(JSON.stringify(guardedGraph), original, 'the raw facts, including short-circuit arms, remain intact');
const guardedRouter = sequenceEdgeRouter(guardedScene);
const guardedIndex = new RectangleIndex(guardedScene.positions.values(), cardRectangle);
const guardedPaths = guardedScene.visualEdges.filter(e => e.source === guardNode.id).map(e => guardedRouter(e).d);
assert.equal(guardedPaths.length, 2); assert.notEqual(...guardedPaths);
for (const path of guardedPaths) assert.ok(!curveCrossesCards(path, guardedIndex));
for (const provider of ['natural', 'graphistry']) {
  view.pathRootId = 'guarded'; view.groupingProvider = provider; view.renderPathCards([guarded], [], 'guarded');
  const symbol = viewport.querySelector('.method-compact-guard');
  assert.ok(symbol); assert.equal(symbol.querySelectorAll('.method-value-divider').length, 2);
  symbol.children.find(n => n.attributes['data-value-choice'] === 0).listeners.click({ stopPropagation() {}, preventDefault() {} });
  assert.deepEqual(sourceActions.at(-1), ['guarded', 47]);
  assert.ok(symbol.querySelectorAll('text').some(n => n.textContent === 'Слева направо · короткое замыкание'));
  const source = symbol.querySelector('.path-card-source');
  assert.equal(source.attributes.y, guardNode.h - 10, 'source action has its own footer, separate from rows');
}
const unsafeEvaluation = { ...evaluation, arms: [{ ...evaluation.arms[0], steps: [{ kind: 'call', label: 'Read()', line: 46 }] }, evaluation.arms[1]] };
assert.equal(compactConditionEntries([unsafeEvaluation, guard]).length, 2, 'RHS calls remain explicit');
assert.equal(compactConditionEntries([evaluation, { ...guard, line: 50 }]).length, 2);
assert.equal(compactConditionEntries([evaluation, guard], false).length, 2);
const unknownArm = { ...evaluation, arms: [{ label: 'истина', steps: [], emptyReason: 'not-extracted' }, evaluation.arms[1]] };
assert.equal(compactConditionEntries([unknownArm, guard]).length, 2);
for (const kind of ['call', 'return', 'throw', 'break', 'continue', 'unknown']) {
  const complexGuard = { ...guard, arms: [{ ...guard.arms[0], steps: [{ kind, label: 'Action', line: 47 }] }, guard.arms[1]] };
  const complex = { ...guarded, methodFlow: { status: 'parsed', steps: [complexGuard] } };
  assert.ok(!buildMethodSequence({ nodes: [complex], edges: [] }, 'guarded').visuals.some(n => n.guardGeometry));
}
for (const limits of [{ truncated: true }, { unsupported: ['pattern'] }, { status: 'unsupported' }]) {
  const limitedGuard = { ...guarded, methodFlow: { ...guarded.methodFlow, ...limits } };
  assert.ok(!buildMethodSequence({ nodes: [limitedGuard], edges: [] }, 'guarded').visuals.some(n => n.guardGeometry));
}
assert.ok(!buildMethodSequence(guardedGraph, 'guarded', { controlFilters: { conditions: false } }).visuals.some(n => n.guardGeometry));
const orGuard = { ...guard, label: 'left || right', conditionExpression: 'left || right' };
assert.equal(compactConditionEntries([{ ...evaluation, controlKind: '||' }, orGuard])[0].step.label, 'left || right');
const inlineGuard = inlineMethodBody(guarded, { data: guarded }, guardedGraph);
const guardOwner = { ...owner, id: 'guarded', rawId: 'guarded', node: guarded, label: guarded.label };
const guardHierarchy = { items: [guardOwner], roots: ['guarded'], edges: [], methodBodies: new Map([['guarded', inlineGuard]]) };
new InlineHierarchyRenderer(view).render(guardHierarchy, inlineFlowLayout(guardHierarchy));
const inlineSymbol = viewport.querySelector('.method-compact-guard');
assert.ok(inlineSymbol);
inlineSymbol.children.find(n => n.attributes['data-value-choice'] === 0).listeners.click({ stopPropagation() {}, preventDefault() {} });
assert.deepEqual(sourceActions.at(-1), ['guarded', 47]);
console.log('Compact short-circuit guards, exact action sources, stable occurrence IDs and safe exclusions PASS');

// The same nullable expression is evaluated once and assigned once in one display card.
const assigned = { kind: 'operation', line: 61, column: 1, label: 'Insurance = context.Calculation?.Insurance' };
const coalesce = { kind: 'branch', controlKind: '??', line: 61, column: 1,
  label: 'context.Calculation?.Insurance', arms: [
    { label: 'null', steps: [{ kind: 'value', expression: '[]', line: 61 }] },
    { label: 'значение', steps: [{ kind: 'value', expression: 'context.Calculation?.Insurance', reuseResult: true, line: 61 }] }] };
const selection = { ...choice, arms: [{ ...choice.arms[0], steps: [{ ...choice.arms[0].steps[0], reuseResult: true }] },
  { ...choice.arms[1], steps: [{ ...choice.arms[1].steps[0], line: 61 }] }] };
const assignedRoot = { ...root, methodFlow: { status: 'parsed', steps: [selection, coalesce,
  { ...assigned, label: assigned.label + ' ?? []' }, { kind: 'operation', label: 'After()', line: 62 }] } };
const assignedGraph = { nodes: [assignedRoot], edges: [] }, assignedBefore = JSON.stringify(assignedGraph);
const assignedScene = buildMethodSequence(assignedGraph, 'root', { localReading: true });
const assignedNode = assignedScene.visuals.find(n => n.assignmentSelection);
assert.ok(assignedNode); assert.equal(assignedScene.visuals.filter(n => n.line === 61).length, 1);
assert.equal(assignedNode.label, 'Insurance = context.Calculation?.Insurance ?? []');
assert.deepEqual(assignedNode.guardGeometry.heading, ['Insurance =']);
assert.ok(assignedNode.h <= 120, 'A short binding and two single-line outcomes fit a compact card');
assert.ok(!assignedScene.visuals.some(n => n.kind === 'join'), 'No synthetic continuation bubble for a pure member assignment');
assert.match(assignedNode.id, /::step:3:61:1$/, 'Keep the assignment original occurrence index');
assert.deepEqual(assignedNode.valueChoices.map(c => c.expression), ['context.Calculation.Insurance', '[]']);
assert.equal(JSON.stringify(assignedGraph), assignedBefore, 'Display folding must not rewrite captured evaluation facts');
const assignedRouter = sequenceEdgeRouter(assignedScene), assignedIndex = new RectangleIndex(assignedScene.positions.values(), cardRectangle);
const assignedPaths = assignedScene.visualEdges.filter(e => e.source === assignedNode.id).map(e => assignedRouter(e).d);
assert.equal(assignedPaths.length, 2); assert.notEqual(...assignedPaths);
for (const path of assignedPaths) assert.ok(!curveCrossesCards(path, assignedIndex));
const nextAssignedStep = assignedScene.visuals.find(n => n.line === 62);
for (const path of assignedPaths) {
  const coordinates = path.match(/-?\d+(?:\.\d+)?/g).map(Number);
  assert.deepEqual(coordinates.slice(-2), [nextAssignedStep.x, nextAssignedStep.y - nextAssignedStep.h / 2]);
}
for (const provider of ['natural', 'graphistry']) {
  view.pathRootId = 'root'; view.groupingProvider = provider; view.renderPathCards([assignedRoot], [], 'root');
  const symbol = viewport.querySelector('.method-value-assignment'); assert.ok(symbol);
  assert.equal(symbol.querySelectorAll('circle').filter(n => String(n.attributes['data-port']).startsWith('out-')).length, 2);
  const input = symbol.querySelectorAll('circle').find(n => n.attributes['data-port'] === 'in');
  assert.equal(input.attributes.cy, 0); assert.equal(input.attributes.cx, assignedNode.w / 2);
  symbol.children.find(n => n.attributes['data-value-choice'] === 1).listeners.click({ stopPropagation() {}, preventDefault() {} });
  assert.deepEqual(sourceActions.at(-1), ['root', 61]);
}
const oneSelector = compactConditionEntries([selection, assigned]);
assert.equal(oneSelector.length, 1); assert.ok(oneSelector[0].step.assignmentSelection);
const secondAccess = { ...selection, label: 'context.Calculation?.Total != null', arms: [
  { label: 'значение', steps: [{ kind: 'value', expression: 'context.Calculation.Total?.Amount', reuseResult: true, line: 61 }] }, selection.arms[1]] };
const firstAccess = { ...selection, arms: [{ ...selection.arms[0], steps: [{ kind: 'value', expression: 'context.Calculation.Total', reuseResult: true, line: 61 }] }, selection.arms[1]] };
const nestedAssignment = { ...assigned, label: 'Amount = context.Calculation?.Total?.Amount' };
assert.equal(compactConditionEntries([firstAccess, secondAccess, nestedAssignment]).length, 1);
assert.equal(compactConditionEntries([firstAccess, firstAccess, nestedAssignment]).length, 3, 'Duplicate unrelated evaluations must not fold');
for (const steps of [[selection, { ...assigned, line: 62 }], [selection, { ...assigned, label: 'Insurance = other?.Insurance' }],
  [selection, { ...assigned, kind: 'return' }], [selection, coalesce, { ...assigned, label: assigned.label + ' ?? Load()' }],
  [{ ...selection, arms: [{ ...selection.arms[0], steps: [{ kind: 'call', label: 'Get()', line: 61 }] }, selection.arms[1]] }, assigned]]) {
  assert.ok(!compactConditionEntries(steps).some(e => e.step.assignmentSelection));
}
assert.equal(compactConditionEntries([selection, assigned], false).length, 2);
const assignedBody = inlineMethodBody(assignedRoot, { data: assignedRoot }, assignedGraph);
const assignedOwner = { ...owner, id: 'root', rawId: 'root', node: assignedRoot, label: assignedRoot.label };
const assignedHierarchy = { items: [assignedOwner], roots: ['root'], edges: [], methodBodies: new Map([['root', assignedBody]]) };
new InlineHierarchyRenderer(view).render(assignedHierarchy, inlineFlowLayout(assignedHierarchy));
assert.ok(viewport.querySelector('.method-value-assignment'));
console.log('Nullable member assignments: one source card, two outcomes, chain validation, source navigation and provider parity PASS');

// Choices at method/loop endings still complete or repeat via both real result ports.
for (const controlKind of [null, 'for', 'while']) {
  const assignmentSteps = assignedRoot.methodFlow.steps.slice(0, 3);
  const steps = controlKind ? [{kind:'loop', controlKind, label:controlKind, line:60,column:1,
    condition:[],update:[],body:assignmentSteps}] : assignmentSteps;
  const source = {...assignedRoot, methodFlow:{status:'parsed',steps}};
  const local = buildMethodSequence({nodes:[source],edges:[]}, 'root', {localReading:true});
  const card = local.visuals.find(n=>n.assignmentSelection), router = sequenceEdgeRouter(local);
  const index = new RectangleIndex(local.positions.values(),cardRectangle);
  const outgoing = local.visualEdges.filter(e=>e.source===card.id);
  assert.deepEqual(outgoing.map(e=>e.sourcePort), [0,1]);
  for(const edge of outgoing) assert.ok(!curveCrossesCards(router(edge).d,index), controlKind || 'implicit method completion');
}

// Consecutive fields have compact spacing, source-linked rows and a visible top input.
const secondSelection = assignedRoot.methodFlow.steps.slice(0,3).map(step=>({ ...step,line:62,
  ...(step.arms ? {arms:step.arms.map(arm=>({...arm,steps:arm.steps.map(value=>({...value,line:62}))}))} : {}) }));
const fieldRunRoot = {...assignedRoot,methodFlow:{status:'parsed',steps:[...assignedRoot.methodFlow.steps.slice(0,3),
  ...secondSelection,{kind:'operation',label:'After()',line:63}]}};
const fieldRunScene = buildMethodSequence({nodes:[fieldRunRoot],edges:[]},'root',{localReading:true});
const fieldCards = fieldRunScene.visuals.filter(n=>n.assignmentSelection);
assert.equal(fieldCards.length,2);
assert.ok(fieldCards[1].y-fieldCards[1].h/2-(fieldCards[0].y+fieldCards[0].h/2)<=32);
const fieldRouter=sequenceEdgeRouter(fieldRunScene),fieldIndex=new RectangleIndex(fieldRunScene.positions.values(),cardRectangle);
for(const edge of fieldRunScene.visualEdges.filter(e=>e.choiceExit)) {
  const path=fieldRouter(edge).d;assert.ok(!curveCrossesCards(path,fieldIndex));
  const target=fieldRunScene.byId.get(edge.target),xy=path.match(/-?\d+(?:\.\d+)?/g).map(Number);
  assert.deepEqual(xy.slice(-2),[target.x,target.y-target.h/2]);
}

// Inline bodies translate the same geometry; value routes must follow those ports.
const translatedPositions = new Map([...fieldRunScene.positions].map(([id, point]) =>
  [id, {...point, x: point.x + 420, y: point.y + 180}]));
const translatedRouter = sequenceEdgeRouter(fieldRunScene, translatedPositions, {dx:420, dy:180});
const translatedIndex = new RectangleIndex(translatedPositions.values(), cardRectangle);
for (const edge of fieldRunScene.visualEdges.filter(edge => edge.choiceExit)) {
  const path = translatedRouter(edge).d;
  assert.ok(!curveCrossesCards(path, translatedIndex));
  const target = translatedPositions.get(edge.target);
  const coordinates = path.match(/-?\d+(?:\.\d+)?/g).map(Number);
  assert.deepEqual(coordinates.slice(-2), [target.x, target.y - target.h / 2]);
}
