/** Выбранный raw метод, его caller/callee и ссылки должны описывать одну и ту же связь. */
import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';
import { pathDirection, pathRelations, pathToNode } from '../web/path-relations.js';
import { Svg } from './svg_fixture.mjs';
import { PathToolbar } from '../web/path-toolbar.js';

const nodes = [
  { id: 'root', label: '.Calculate()', file: 'RootController.cs', controlFlow: { branches: [{ kind: 'if', line: 4, condition: 'valid', arms: [{ id: 'valid', kind: 'true' }] }] } },
  { id: 'iface', label: '.Calculate()', file: 'ICalculator.cs' },
  { id: 'impl', label: '.Calculate()', file: 'RealCalculator.cs' },
  { id: 'shared', label: '.Resolve()', file: 'Shared.cs' },
];
const edges = [
  { source: 'root', target: 'iface', relation: 'calls', confidence: 'source', branchIds: ['valid'], sourceLine: 5 },
  { source: 'iface', target: 'impl', relation: 'dispatches_to', confidence: 'candidate' },
  { source: 'impl', target: 'shared', relation: 'calls', confidence: 'source', sourceLine: 20 },
  { source: 'impl', target: 'shared', relation: 'calls', confidence: 'source', sourceLine: 22 },
  { source: 'shared', target: 'root', relation: 'calls', confidence: 'source' },
];
const untouched = JSON.stringify({ nodes, edges });
assert.deepEqual(pathToNode({ nodes, edges }, 'root', 'shared'), ['root', 'iface', 'impl', 'shared']);
assert.deepEqual(pathToNode({ nodes, edges }, 'root', 'absent'), []);
assert.equal(pathDirection({ x: 0, y: 0 }, { x: 100, y: -100 }).arrow, '↗');

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g');
viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const selected = [], changes = [];
const view = new GraphView(svg, id => selected.push(id), () => {});
view.onPathRelationsChange = info => changes.push(info);
view.setDisplayProvider('flow'); view.pathRootId = 'root';
view.renderPathCards(nodes, edges, 'root');
view.togglePathNode('impl'); // Единственный dispatch первоначально раскрывается автоматически; вручную скрываем его тело.
assert.equal(changes.at(-1).selected.label, 'RootController.Calculate()');
assert.equal(changes.at(-1).incoming[0].id, 'shared');
assert.equal(changes.at(-1).incoming[0].visible, false, 'collapsed caller is described honestly');
const localRootEdge = view.pathEdgeElements.find(item => item.source === 'root' && item.target === 'iface');
assert.ok(localRootEdge.path.classList.contains('path-related-outgoing'), 'branch retains its raw caller');
assert.ok(view.pathFrameElements.get('iface').classList.contains('path-related-outgoing'));
view.setSelected('iface');
assert.ok(view.pathFrameElements.get('root').classList.contains('path-related-incoming'));
assert.ok(view.pathFrameElements.get('impl').classList.contains('path-related-outgoing'));
assert.equal(changes.at(-1).outgoing[0].candidate, true);
assert.ok(localRootEdge.path.classList.contains('path-related-incoming'));
view.setSelected('impl');
assert.equal(changes.at(-1).outgoing[0].visible, false);
assert.equal(changes.at(-1).outgoing[0].edges.length, 2, 'repeat calls preserve both source lines');
view.camera = { x: 20, y: 30, scale: .5 };
const old = view.positions.get('impl');
view.togglePathNode('impl');
const current = view.positions.get('impl');
assert.equal(view.camera.scale, .5);
assert.equal(old.x * .5 + 20, current.x * .5 + view.camera.x, 'expansion preserves caller anchor');
assert.equal(changes.at(-1).revealOwner.id, 'impl');
assert.ok(changes.at(-1).revealed.some(item => item.id === 'shared' && item.visible));
view.togglePathNode('shared');
assert.ok(viewport.querySelectorAll('.path-reference').some(group => group.attributes['data-focus-id'] === 'root'), 'cycle remains a reference');
view.collapseAllPathNodes();
view.togglePathNode('impl');
assert.equal(view.positions.has('shared'), false);
view.followPathNode('shared');
assert.equal(selected.at(-1), 'shared');
assert.equal(view.camera.scale, .5);
assert.equal(view.positions.has('shared'), true, 'explicit hidden relation reveals a finite ancestor chain');
assert.equal(changes.at(-1).selected.id, 'shared');
assert.ok(!view.pathFrameElements.get('root').classList.contains('path-related-selected'), 'stale selected accent is cleared');
assert.equal(JSON.stringify({ nodes, edges }), untouched);
const model = pathRelations({ nodes, edges }, 'root::branch:4:0', view.positions, view.pathMethodNumbers);
assert.equal(model.selected.id, 'root', 'synthetic controls never become raw callers');
const withEntry = pathRelations({ nodes: [...nodes, { id: 'path-entry:http', label: 'POST /calculate' }],
  edges: [...edges, { source: 'path-entry:http', target: 'root', relation: 'вход' }] }, 'root', view.positions, view.pathMethodNumbers);
assert.ok(!withEntry.incoming.some(item => item.id.startsWith('path-entry:')), 'HTTP entry is not a raw calling method');
const prior = view.selectedId;
view.followPathNode('path-entry:http');
assert.equal(view.selectedId, prior, 'entry navigation cannot desynchronize graph/app selection');
view.pathReveal = { ownerId: 'impl', nodeIds: ['shared'] };
view.setDisplayProvider('topology');
assert.equal(view.pathReveal, null, 'transient reveal notice is cleared on provider change');
view.pathReveal = { ownerId: 'impl', nodeIds: ['shared'] };
view.renderPathCards(nodes, edges, 'root');
assert.equal(view.pathReveal, null, 'selection restore does not retain unrelated reveal provenance');

// HTML control uses native buttons, safe text and actual raw IDs; no inspector/open-page dependency.
class Html extends Svg { set className(value) { this.attributes.class = value; } }
globalThis.document.createElement = tag => new Html(tag);
const root = new Html('section'), controls = new Map();
for (const name of ['collapse', 'focus', 'fit', 'expand', 'relations']) controls.set(name, new Html(name === 'relations' ? 'details' : 'button'));
root.querySelector = selector => controls.get(selector.match(/"(.+)"/)[1]);
const followed = [];
const toolbar = new PathToolbar(root, { collapse() {}, focus() {}, fit() {}, expand() {}, follow: id => followed.push(id) });
toolbar.renderRelations(model);
const links = controls.get('relations').querySelectorAll('.path-relation-link');
assert.ok(links.some(button => button.attributes['data-node-id'] === 'iface'));
const link = links.find(button => button.attributes['data-node-id'] === 'iface');
link.listeners.click();
assert.deepEqual(followed, ['iface']);
assert.ok(link.title.includes(':5'), 'source line stays available');
assert.ok(link.title.includes(model.outgoing[0].direction));
const hiddenSelection = pathRelations({ nodes, edges }, 'shared', new Map([['impl', { x: 10, y: 20 }]]));
assert.equal(hiddenSelection.selected.visible, false);
assert.equal(hiddenSelection.incoming[0].id, 'impl');
assert.equal(hiddenSelection.incoming[0].visible, true, 'visible caller stays visible when the selected leaf is collapsed');
assert.ok(hiddenSelection.incoming[0].direction.includes('выбранный блок скрыт'));
toolbar.renderRelations(hiddenSelection);
assert.ok(controls.get('relations').querySelector('summary').textContent.includes('блок скрыт'));
const restore = controls.get('relations').querySelectorAll('.path-relation-link').find(button => button.attributes['data-node-id'] === 'shared');
assert.ok(restore, 'hidden selected method has an explicit reveal action');
console.log('Path relationships raw ownership, selection, direction, reveal, cycles and scale PASS');
