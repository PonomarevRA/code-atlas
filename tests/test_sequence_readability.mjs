import assert from 'node:assert/strict';
import { buildMethodSequence } from '../web/method-sequence.js';
import { sequencePresentation } from '../web/sequence-presentation.js';
import { wrapDiagramText } from '../web/diagram-text.js';
import { renderMethodSequence, appendSequenceRow } from '../web/method-sequence-renderer.js';
import { sequenceRowGeometry, sequenceRowHeight } from '../web/sequence-card-layout.js';
import { Svg } from './svg_fixture.mjs';
import { GraphView } from '../web/graph.js';
import { InlineHierarchyRenderer } from '../web/inline-hierarchy-renderer.js';
import { inlineFlowLayout } from '../web/inline-flow-layout.js';

// Bounds and source whitespace remain explicit even when the visible excerpt is shortened.
for (const source of ['result = null;', 'var result = Read(input);', 'value = "a  b";', 'first();\nsecond();']) {
  const wrapped = wrapDiagramText(source, 24, 4);
  assert.equal(wrapped.truncated, false);
  assert.equal(wrapped.lines.join(''), source.replaceAll('\n', ''));
  assert.ok(wrapped.lines.every(line => line.length <= 24));
}
assert.deepEqual(wrapDiagramText('candidate.Security.Id > current.Security.Id', 25).lines,
  ['candidate.Security.Id > ', 'current.Security.Id']);
assert.ok(wrapDiagramText('ExtremelyLongIdentifierThatCannotFitAnywhere', 12).truncated);

const root = { id: 'root', label: 'Run()', file: 'C.cs', events: [{ text: 'Run()', line: 1, column: 1, targetNodeIds: ['iface'] }],
  methodFlow: { status: 'parsed', steps: [{ kind: 'call', label: 'Run()', line: 1, column: 1, site: { line: 1, column: 1 } }] } };
const implementations = Array.from({ length: 7 }, (_, index) => ({ id: `impl${index}`, label: `Impl${index}()`, file: 'C.cs',
  methodFlow: { status: 'parsed', steps: [] }, events: [] }));
const fields = Array.from({ length: 4 }, (_, index) => ({ id: `field${index}`, label: `Field${index}`, file: 'C.cs',
  fieldFacts: { kind: 'field', type: 'Dictionary' }, dataFacts: { kind: 'dictionary', entries: Array.from({ length: 5 }, (_, i) => ({ key: String(i), value: String(i) })), totalEntries: 5 } }));
const graph = { nodes: [root, { id: 'iface', label: 'Interface.Run()' }, ...implementations, ...fields], edges: [
  ...implementations.map(target => ({ source: 'iface', target: target.id, relation: 'dispatches_to' })),
  { source: 'root', target: 'field0', relation: 'reads' }, { source: 'root', target: 'field1', relation: 'writes' },
  { source: 'root', target: 'field2', relation: 'reads' }, { source: 'root', target: 'field2', relation: 'writes' },
  { source: 'root', target: 'field3', relation: 'reads' }] };
const original = JSON.stringify(graph), scene = buildMethodSequence(graph, 'root');
assert.equal(JSON.stringify(graph), original);
assert.equal(scene.truncated, false, 'Data/alternative display limits do not claim unknown execution completion');
assert.equal(scene.presentationTruncated, true);
assert.equal(scene.layout.frames[0].dataSection.shown, 3);
assert.equal(scene.layout.frames[0].dataSection.total, 4);
const call = scene.visuals.find(node => node.kind === 'call');
assert.equal(sequencePresentation(call).context, 'Показано 6 из 7 реализаций');
for (const node of scene.visuals.filter(node => node.kind === 'data')) assert.ok(sequencePresentation(node).context.startsWith('Показано 3 из 5 значений.'));
assert.deepEqual(scene.visualEdges.filter(edge => edge.sequenceKind === 'data').map(edge => edge.caption),
  ['Чтение данных', 'Запись данных', 'Чтение / запись данных']);
const consuming = sequencePresentation({ label: 'A() && B()', conditionResult: 'reuse', conditionExpression: 'A() && B()', decision: true });
assert.equal(consuming.title, 'Результат условия');
assert.equal(consuming.context, 'A() && B()');

// Hidden or unloaded callees cannot prove the known next caller operation unreachable.
const caller = { ...root, methodFlow: { status: 'parsed', steps: [...root.methodFlow.steps,
  { kind: 'operation', label: 'After();', line: 2, column: 1 }] } };
const throwing = implementations.map((node, index) => ({ ...node, methodFlow: { status: 'parsed', steps: [
  { kind: index < 6 ? 'throw' : 'return', label: index < 6 ? 'throw error;' : 'return 1;', line: 3, column: 1 }] } }));
const capped = buildMethodSequence({ nodes: [caller, { id: 'iface' }, ...throwing], edges: graph.edges.filter(e => e.relation === 'dispatches_to') }, 'root',
  { expandedIds: new Set(['root::inline-call:1:1']) });
assert.ok(capped.visuals.some(node => node.label === 'After();'));
assert.ok(capped.visuals.find(node => node.kind === 'resume').uncertain);
assert.ok(capped.visualEdges.some(edge => edge.sequenceKind === 'unknown'));
const direct = { ...caller, events: [{ ...caller.events[0], targetNodeIds: ['missing'] }] };
const unloaded = buildMethodSequence({ nodes: [direct, { id: 'missing', label: 'Missing()' }], edges: [] }, 'root',
  { expandedIds: new Set(['root::inline-call:1:1']), getData: id => id === 'root' ? direct : undefined });
assert.ok(unloaded.visuals.some(node => node.label === 'After();'));
assert.ok(!unloaded.visualEdges.some(edge => edge.sequenceKind === 'return' && edge.target.endsWith('::resume')));

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const viewport = new Svg('g'), svg = new Svg('svg');
viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const view = new GraphView(svg, () => {}, () => {});
renderMethodSequence(view, scene, 'root');
assert.ok(viewport.querySelector('.method-sequence-frame-label').textContent.includes('Run()'));
assert.ok(viewport.querySelector('.method-sequence-data-heading').textContent.includes('3 из 4'));
assert.ok(viewport.querySelectorAll('text').some(text => String(text.textContent).includes('6 из 7')));

// Count notices and complete captured source must survive compaction and inline-root exclusion.
const declaration = 'var meaningfulResult = Interface.Run(inputWithLongButMeaningfulName);';
const rowCaller = { ...caller, methodFlow: { ...caller.methodFlow, steps: [{ ...caller.methodFlow.steps[0], sourceStatement: declaration }, caller.methodFlow.steps[1]] } };
const rows = buildMethodSequence({ nodes: [rowCaller, ...graph.nodes.slice(1)], edges: graph.edges }, 'root');
assert.ok(rows.visuals.find(node => node.kind === 'call').compactRow);
renderMethodSequence(view, rows, 'root');
assert.ok(viewport.querySelectorAll('text').some(text => String(text.textContent).includes('6 из 7')));
assert.ok(viewport.querySelectorAll('title').some(title => String(title.textContent).includes(declaration)));
const owner = { id: 'root', rawId: 'root', node: rowCaller, label: 'Run()', kind: 'raw', expanded: true,
  children: [], memberIds: ['root'], enter: { kind: 'raw' }, totalChildren: 0 };
const hierarchy = { items: [owner], roots: ['root'], edges: [], methodBodies: new Map([['root', rows]]) };
const layout = inlineFlowLayout(hierarchy, new Map([['root', { x: 17, y: 29, width: 260, height: 154 }]]));
new InlineHierarchyRenderer(view).render(hierarchy, layout);
assert.ok(viewport.querySelector('.method-sequence-data-heading').textContent.includes('3 из 4'));
assert.ok(viewport.querySelectorAll('text').some(text => String(text.textContent).includes('6 из 7')));
assert.ok(viewport.querySelectorAll('title').some(title => String(title.textContent).includes(declaration)));
const unknownOwner = { ...owner, node: direct };
const unknownHierarchy = { items: [unknownOwner], roots: ['root'], edges: [], methodBodies: new Map([['root', unloaded]]) };
new InlineHierarchyRenderer(view).render(unknownHierarchy,
  inlineFlowLayout(unknownHierarchy, new Map([['root', { x: 0, y: 0, width: 260, height: 154 }]])));
const unknownPaths = viewport.querySelectorAll('.inline-method-edge').filter(path => path.attributes['data-sequence-kind'] === 'unknown');
assert.ok(unknownPaths.length);
assert.ok(unknownPaths.every(path => path.attributes['stroke-dasharray'] === '5 4'));
console.log('Readable source wrapping, visible frame ownership, limits, reuse and exact data relations PASS');

// Source-only LINQ rows reserve only their visible code and footer, retaining each occurrence.
const sourceRows = ['Concat(needsCashPairs ? CashMovementTypes : [])', 'Distinct()',
  'var loadTypes = selectedTypes.Concat(...)'];
const sourceRoot = {id:'source',label:'LoadOperations()',file:'Loader.cs',methodFlow:{status:'parsed',
  steps:sourceRows.map((label,index)=>({kind:index<2?'call':'operation',label,line:195,column:index+1}))}};
const sourceGraph = {nodes:[sourceRoot],edges:[]}, beforeSource = JSON.stringify(sourceGraph);
const compactSource = buildMethodSequence(sourceGraph,'source',{localReading:true});
const codeRows = compactSource.visuals.filter(node=>node.compactRow);
assert.equal(codeRows.length,3);
assert.ok(codeRows.reduce((height,node)=>height+node.h,0)<=200,'Three plain rows no longer reserve 310 px');
assert.equal(JSON.stringify(sourceGraph),beforeSource);
for (const node of codeRows) for (const centered of [false,true]) {
  const group = new Svg('g'); appendSequenceRow(group,node,centered);
  const text = group.querySelectorAll('text');
  const footer = text.at(-1), lastCode = text.at(-2);
  assert.ok(Number(footer.attributes.y)-Number(lastCode.attributes.y)>=20,'Code stays clear of the source footer');
}
const plainCall = {kind:'call',label:'Distinct()',w:260};
assert.equal(sequenceRowHeight(plainCall),50);
assert.equal(sequenceRowHeight({...plainCall,documentation:{summary:'   '}}),50,'Blank documentation is not visible');
assert.ok(sequenceRowHeight({...plainCall,label:sourceRows[0]})>50,'Two code lines reserve their real height');
for (const extra of [{documentation:{summary:'Оставляет уникальные типы.'}}, {canExpand:true},
  {candidateCount:3,shownCandidates:2}, {expansionNotice:'Предел раскрытия; откройте исходник'}]) {
  assert.ok(sequenceRowHeight({...plainCall,...extra})>=120,'Visible prose, limits and actions retain room');
}
const described = {...plainCall,documentation:{summary:'Оставляет уникальные типы.'}};
const describedGeometry = sequenceRowGeometry(described);
assert.equal(describedGeometry.annotation,'Оставляет уникальные типы.');
assert.ok(describedGeometry.height-10-describedGeometry.annotationY>=24);
const linkedGeometry = sequenceRowGeometry({...plainCall,definitionId:'external'});
assert.ok(linkedGeometry.height-10-linkedGeometry.lineY>=17,'Definition and call-site line have separate footer bands');
assert.ok(linkedGeometry.lineY-20>=20,'The extra action band does not cover the source excerpt');
assert.ok(sequenceRowGeometry({...plainCall,definitionId:'external',complexity:{score:4}}).lineY>=48,
  'The complexity badge remains clear of the elevated source-line footer');
console.log('Content-sized source rows, visible annotation/action reserves and centered rendering PASS');
