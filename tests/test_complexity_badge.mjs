import assert from 'node:assert/strict';
import { Svg } from './svg_fixture.mjs';
import { appendFlowCard } from '../web/flow-card.js';
import { complexityText } from '../web/complexity-badge.js';
import { GraphView } from '../web/graph.js';
import { inlineMethodBody } from '../web/inline-method-body.js';
import { inlineFlowLayout } from '../web/inline-flow-layout.js';
import { InlineHierarchyRenderer } from '../web/inline-hierarchy-renderer.js';
import { projectExecutionPath } from '../web/path-graph-projection.js';

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const metrics = { version: 'local-structure-v1', status: 'complete', score: 10,
  maxNestingDepth: 3, averageNestingDepth: 1.5, branchingCount: 5, loopCount: 2, sourceKind: 'working-tree' };
const card = new Svg('g');
appendFlowCard(card, { width: 260, height: 154, title: 'Build()', complexity: metrics,
  semantic: { id: 'source', fill: '#eee', color: '#333' }, palette: {}, color: '#333' });
const badge = card.querySelector('.code-complexity-badge');
assert.equal(badge.attributes['data-complexity-score'], 10);
assert.equal(badge.querySelector('circle').attributes.fill, '#b13e43');
assert.ok(badge.attributes['aria-label'].includes('максимум 3, средняя 1.5'));
assert.equal(card.querySelector('.flow-card-name').attributes.x, -86); // Badge and title own separate space.
assert.ok(complexityText({ ...metrics, status: 'partial', averageNestingDepth: null }).includes('Не менее 10'));
assert.ok(complexityText({ score: null }).includes('неизвестна'));

const root = { id: 'root', label: 'Build()', file: 'C.cs', line: 1, complexity: metrics, events: [],
  methodFlow: { status: 'parsed', steps: [{ kind: 'return', label: 'return 1;', line: 2 }] } };
const svg = new Svg('svg'), viewport = new Svg('g'); viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const view = new GraphView(svg, () => {}, () => {}); view.pathRootId = 'root';
for (const grouping of ['natural', 'graphistry']) {
  view.groupingProvider = grouping; view.renderPathCards([root], [], 'root');
  assert.equal(viewport.querySelector('.code-complexity-badge').attributes['data-complexity-score'], 10);
  assert.ok(viewport.querySelector('.method-simple-card'));
}
const body = inlineMethodBody(root, { data: root }, { nodes: [root], edges: [] });
const owner = { id: 'root', rawId: 'root', kind: 'raw', node: root, label: root.label, children: [], expanded: true };
const hierarchy = { items: [owner], roots: ['root'], edges: [], methodBodies: new Map([['root', body]]) };
new InlineHierarchyRenderer(view).render(hierarchy, inlineFlowLayout(hierarchy));
assert.equal(viewport.querySelectorAll('.code-complexity-badge').length, 2, 'The retained raw header and detached body entry both expose the same method score');
for (const badge of viewport.querySelectorAll('.code-complexity-badge')) assert.equal(badge.attributes['data-complexity-score'], 10);
const projected = projectExecutionPath({ rootNodeId: 'root', steps: [{ nodeId: 'root', complexity: { ...metrics, score: 4 } }], edges: [] }, new Map([['root', root]]));
assert.equal(projected.nodes[0].complexity.score, 4);
assert.equal(root.complexity.score, 10);
for (const provider of ['flow', 'topology']) {
  view.displayProvider = provider; view.render([root], [], 'root');
  assert.equal(viewport.querySelector('.code-complexity-badge').attributes['data-complexity-score'], 10);
}
console.log('Shared badge, title reservation, partial/unknown metrics, Natural/Flow and inline source provenance PASS');
