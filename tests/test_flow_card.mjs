import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';
import { InlineHierarchyRenderer } from '../web/inline-hierarchy-renderer.js';
import { inlineFlowLayout } from '../web/inline-flow-layout.js';
import { inlineBaseBounds } from '../web/inline-space.js';
import { Svg } from './svg_fixture.mjs';

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g'); viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const view = new GraphView(svg, () => {}, () => {});
view.colorProjects = [{ id: 'p', label: 'Project' }];
const owner = { id: 'file', label: 'Folder/Calculator.cs', displayLabel: 'Calculator.cs', codeProjectId: 'p', nodeCount: 2, architecture: true, kind: 'file' };
view.setDisplayProvider('flow'); view.renderOverview([owner], [], null);
const ordinary = view.communityElements.get('file').group;
const baseline = inlineBaseBounds(view.positions);
/** Compare visual contracts, including their local coordinates; callbacks and identities stay renderer-owned. */
function surface(group) {
  return group.children.filter(child => ['flow-card-body', 'flow-card-header', 'code-role-stripe', 'flow-card-name', 'flow-card-context', 'flow-card-role', 'flow-card-note', 'flow-port'].some(name => child.classList.contains(name)))
    .map(child => ({ tag: child.tag, text: child.textContent, attrs: Object.fromEntries(Object.entries(child.attributes).filter(([key]) => key !== 'class')) }));
}
const before = surface(ordinary);
const scene = { roots: ['file'], membership: new Map(), edges: [], items: [
  { id: 'file', kind: 'file', label: owner.label, file: owner.label, node: owner, totalMembers: 2, children: ['method'], expanded: true, visibleChildren: 1, totalChildren: 1 },
  { id: 'method', rawId: 'method', kind: 'raw', label: '.Calculate()', node: { id: 'method', label: '.Calculate()', codeProjectId: 'p' }, parentId: 'file', children: [] },
] };
new InlineHierarchyRenderer(view).render(scene, inlineFlowLayout(scene, baseline));
assert.deepEqual(surface(view.communityElements.get('file').group), before, 'Expansion retains the exact ordinary Flow surface');
assert.equal(viewport.querySelectorAll('.inline-hierarchy-edge').length, 0, 'Containment does not fabricate a call');
assert.equal(viewport.querySelectorAll('.inline-hierarchy-containment').length, 0, 'A visible owner frame replaces redundant containment arrows');
assert.equal(viewport.querySelectorAll('.inline-hierarchy-frame').length, 1);
assert.ok(view.nodeElements.get('method').group.classList.contains('atlas-flow-card'));
assert.ok(view.geometryBounds().minX <= baseline.get('file').x - 160, 'Fit includes external Flow loop channels');
console.log('Shared Flow card exact appearance, identity and explicit containment PASS');
