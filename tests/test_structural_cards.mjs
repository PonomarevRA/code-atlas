import assert from 'node:assert/strict';
import { architectureGraph } from '../web/architecture.js';
import { nodeCardLayout } from '../web/node-card-layout.js';
import { renderPreviewRows } from '../web/html-preview.js';

const file = 'tools/index.html';
const node = (id, parent, elementIndex, label = id) => ({ id, label, file, codeProjectId: 'p', kind: 'markup',
  htmlFacts: { role: 'block', parentNodeId: parent, elementIndex } });
const html = node('html', 'file', 0), body = node('body', 'html', 1);
const panel = node('panel', 'body', 2, 'section'), label = node('label', 'panel', 3);
const other = { id: 'other', label: 'legacy', file, codeProjectId: 'p', line: 7 };
const graph = { nodes: [label, html, other, panel, body], edges: [], codeProjects: [{ id: 'p' }] };
const options = { level: 'symbols', projectId: 'p', file };
const first = architectureGraph(graph, graph.nodes, { ...options, pageSize: 2 });
assert.deepEqual(first.nodes.map(item => item.id), ['html', 'body']);
const second = architectureGraph(graph, graph.nodes, { ...options, pageSize: 2, page: 1 });
assert.deepEqual(second.nodes.map(item => item.id), ['panel', 'label']);
assert.deepEqual(second.nodes.map(item => item.readingStructure.depth), [2, 3]);
const filtered = architectureGraph(graph, [label], options);
assert.equal(filtered.nodes[0].readingStructure.parentLabel, 'section');
assert.equal(filtered.nodes[0].readingStructure.depth, 3);
const tree = architectureGraph(graph, graph.nodes, options);
const layout = nodeCardLayout(tree.nodes, [], { reading: true, viewportWidth: 1024 });
assert.deepEqual(layout.sections.map(section => section.label), ['Структура HTML', 'Другие символы файла']);
assert.ok(layout.positions.get('label').x > layout.positions.get('panel').x);
assert.ok(layout.positions.get('label').y > layout.positions.get('panel').y);
assert.ok(layout.positions.get('label').guide);
const narrow = nodeCardLayout(tree.nodes, [], { reading: true, viewportWidth: 320 });
for (const point of narrow.positions.values()) assert.ok(24 + (point.x + point.width / 2) * .9 <= 320);

const projectFiles = ['web/z.js', 'tools/a.js', 'web/b.js', 'readme.md'].map((path, index) =>
  ({ id: 'file' + index, label: path, file: path, codeProjectId: 'p' }));
const project = architectureGraph({ nodes: projectFiles, edges: [], codeProjects: [] }, projectFiles, { level: 'files', projectId: 'p' });
assert.deepEqual(project.groups.map(group => group.readingStructure.label), ['Корень проекта', 'tools', 'web', 'web']);
const grouped = nodeCardLayout(project.groups, [], { reading: true, viewportWidth: 800 });
assert.equal(grouped.sections.length, 3);
assert.ok(grouped.positions.get(project.groups[2].id).y > grouped.positions.get(project.groups[1].id).y);

const methods = [{ id: 'x', label: 'Zulu', file: 'code.py', line: 1, codeProjectId: 'p' },
  { id: 'y', label: 'Alpha', file: 'code.py', line: 15, codeProjectId: 'p' }];
const noCalls = architectureGraph({ nodes: methods, edges: [] }, methods, { ...options, file: 'code.py' });
assert.deepEqual(noCalls.nodes.map(item => item.id), ['x', 'y']);
const call = { source: 'x', target: 'y', relation: 'calls' };
const hasCalls = architectureGraph({ nodes: methods, edges: [call] }, methods, { ...options, file: 'code.py' });
assert.ok(hasCalls.nodes.every(item => !item.readingStructure));
assert.equal(hasCalls.edges[0], call);

const longFile = Array.from({length: 251}, (_, i) => ({id: 'n' + i, label: String(999 - i),
  file: 'long.py', line: i + 1, codeProjectId: 'p'}));
const direct = architectureGraph({nodes: longFile, edges: [], codeProjects: []}, longFile,
  {...options, file: 'long.py', targetNodeId: 'n249'});
assert.equal(direct.page, 1);
assert.ok(direct.nodes.some(item => item.id === 'n249'));
const htmlDirect = architectureGraph(graph, graph.nodes, {...options, targetNodeId: 'label', pageSize: 2});
assert.equal(htmlDirect.page, 1);
assert.ok(htmlDirect.nodes.some(item => item.id === 'label'));
const longTree = [html, body, ...Array.from({length: 120}, (_, i) => node('item' + i, 'body', i + 2))];
const longTreePage = architectureGraph({ ...graph, nodes: longTree }, longTree, { ...options, targetNodeId: 'item99' });
assert.equal(longTreePage.page, 2);
assert.ok(longTreePage.nodes.length <= 48 && longTreePage.nodes.some(item => item.id === 'item99'));

const created = [];
globalThis.document = { createElement(tag) {
  const element = {tag, children: [], dataset: {}, append(...nodes) { this.children.push(...nodes); }};
  created.push(element);
  return element;
}};
const host = document.createElement('section');
renderPreviewRows(host, [
  { parent: null, depth: 0, kind: 'container', label: '<img src="https://bad.test/">', style: 'url(https://bad.test)' },
  { parent: 0, depth: 1, kind: 'script', label: 'ignored' },
  { parent: 0, depth: 1, kind: 'control', label: '<script>alert(1)</script>', onclick: 'fetch(1)' },
]);
assert.deepEqual(created.map(item => item.tag), ['section', 'div', 'span', 'div', 'span']);
assert.equal(host.children[0].children[0].textContent, '<img src="https://bad.test/">');
assert.equal(host.children[0].children[1].children[0].textContent, '<script>alert(1)</script>');
assert.equal(host.children[0].style, undefined);
assert.equal(host.children[0].children[1].onclick, undefined);
console.log('PASS folder sections, source ordering, snapshot HTML nesting and passive preview rendering');
