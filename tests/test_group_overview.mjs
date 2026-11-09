/** Обзор сохраняет полное членство, направления и страницы; SVG остаётся читаемым и выбираемым. */
import assert from 'node:assert/strict';
import { groupOverview, GROUP_PAGE_SIZE } from '../web/group-overview-model.js';
import { nodeCardLayout } from '../web/node-card-layout.js';
import { GraphView } from '../web/graph.js';
import { normalizeRoute, writeRoute, readRoute } from '../web/navigation-history.js';
import { Svg } from './svg_fixture.mjs';

const graph = { nodes: [], edges: [], codeProjects: [], communities: [] };
for (let project = 0; project < 245; project++) {
  const owner = `p${String(project).padStart(3, '0')}`;
  graph.codeProjects.push({ id: owner, label: owner });
  for (let group = 0; group < 12; group++) graph.nodes.push({ id: `${owner}-${group}`, label: `method${group}()`,
    community: `g${group}`, file: `${owner}/file${group}.js`, codeProjectId: owner });
}
for (let group = 0; group < 12; group++) graph.communities.push({ id: `g${group}`, label: `Group ${group}` });
graph.edges = [{ source: 'p000-0', target: 'p001-0', relation: 'calls' },
  { source: 'p001-0', target: 'p000-0', relation: 'references' },
  { source: 'p000-0', target: 'p000-1', relation: 'calls' }];
const before = JSON.stringify(graph);
const root = groupOverview(graph, graph.nodes);
assert.equal(root.items.length, GROUP_PAGE_SIZE);
assert.equal(root.total, 245, 'No hidden 240-project cap');
assert.deepEqual(root.items[0].groupSummary, { scope: 'project', files: 12, groups: 12 });
assert.equal(root.edges.length, 2);
assert.equal(groupOverview(graph, graph.nodes, { changedNodeIds: ['p000-0', 'p000-1'] }).items[0].changedNodeCount, 2);
const pagedIds = new Set();
for (let page = 0; page < root.pageCount; page++) {
  for (const item of groupOverview(graph, graph.nodes, { page }).items) pagedIds.add(item.id);
}
assert.equal(pagedIds.size, 245);
const scoped = groupOverview(graph, graph.nodes, { projectId: 'p000', fragments: true });
assert.equal(scoped.total, 12); assert.equal(scoped.nodes.length, 12);
assert.equal(scoped.items[0].groupSummary.files, 1);
assert.equal(scoped.items[0].groupSummary.algorithmLabel, 'Group 0 · p000');
assert.ok(scoped.allItems.some(item => item.originalCommunityId === 'g0' && item.id === JSON.stringify(['p000', 'g0'])));
const unsplit = groupOverview(graph, graph.nodes, { projectId: 'p000', fragments: false });
assert.ok(unsplit.allItems.some(item => item.id === 'g0')); assert.equal(unsplit.items[0].codeProjectId, 'p000');
const filtered = groupOverview(graph, graph.nodes.filter(node => node.codeProjectId === 'p000'), { relation: 'references' });
assert.equal(filtered.total, 1); assert.equal(filtered.items[0].nodeCount, 12); assert.equal(filtered.edges.length, 0);
assert.equal(groupOverview(graph, [], { page: 1000 }).page, 0);
assert.equal(groupOverview(graph, graph.nodes, { page: 1000 }).page, root.pageCount - 1);
const layout = nodeCardLayout(root.items, root.edges);
for (const [id, a] of layout.positions) for (const [otherId, b] of layout.positions) {
  if (id === otherId) continue;
  assert.ok(Math.abs(a.x - b.x) >= (a.width + b.width) / 2 || Math.abs(a.y - b.y) >= (a.height + b.height) / 2);
}
const route = normalizeRoute({ repo: 'repo', view: 'groups', display: 'flow', level: 'files', project: 'p000', page: 1 });
assert.deepEqual(readRoute(writeRoute(route, 'http://localhost/')), route);
globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g'); viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const selected = [], view = new GraphView(svg, () => {}, id => selected.push(id));
view.setDisplayProvider('flow'); view.colorProjects = graph.codeProjects;
view.renderOverview(root.items, root.edges, null);
assert.equal(view.communityElements.size, GROUP_PAGE_SIZE);
const card = view.communityElements.get(root.items[0].id).group;
assert.equal(card.attributes['data-group-summary'], 'project');
assert.ok(card.textContent.includes('12 групп · 12 файлов'));
card.listeners.click(); assert.deepEqual(selected, [root.items[0].id]);
assert.ok(viewport.querySelectorAll('.flow-edge').every(edge => edge.classList.contains('group-summary-edge')));
const positions = JSON.stringify([...view.positions]); view.setSelected(root.items[0].id);
assert.ok(viewport.querySelectorAll('.flow-edge').every(edge => edge.classList.contains('active')));
assert.equal(JSON.stringify([...view.positions]), positions);
assert.equal(JSON.stringify(graph), before);
console.log('Group overview: scoped membership, pagination, routes, board geometry and selection PASS');
