/** Natural сохраняет факты, читаемые области и устойчивость раскрытия реального GraphView. */
import assert from 'node:assert/strict';
import { naturalLayout } from '../web/natural-layout.js';
import { GraphView } from '../web/graph.js';
import { navigationSnapshot, readRoute, writeRoute } from '../web/navigation-history.js';
import { Svg } from './svg_fixture.mjs';
import { assertClear } from './curve_geometry.mjs';

/** Полные прямоугольники должны быть отделены, а не только центры карточек. */
function assertSeparated(positions) {
  const points = [...positions.values()];
  for (let i = 0; i < points.length; i++) for (let j = i + 1; j < points.length; j++) {
    const a = points[i], b = points[j];
    assert.ok(Math.abs(a.x - b.x) >= (a.width + b.width) / 2 || Math.abs(a.y - b.y) >= (a.height + b.height) / 2);
  }
}
const nodes = [{ id: 'root', label: 'Controller' }, { id: 'a', label: 'Service', w: 400, h: 180 },
  { id: 'b', label: 'Mapper' }, { id: 'c', label: 'Rules' }, { id: 'd', label: 'Contract' }, { id: 'alone', label: 'Isolate' }];
const edges = [{ source: 'root', target: 'a', sourceLine: 5 }, { source: 'root', target: 'b', sourceLine: 7 },
  { source: 'a', target: 'c' }, { source: 'b', target: 'd' }, { source: 'c', target: 'a' },
  { source: 'c', target: 'd', confidence: 'candidate' }, { source: 'd', target: 'd' }];
const facts = JSON.stringify({ nodes, edges });
const layout = naturalLayout(nodes, edges, { rootId: 'root' });
assertSeparated(layout.positions);
assert.deepEqual(naturalLayout([...nodes].reverse(), [...edges].reverse(), { rootId: 'root' }).positions, layout.positions);
assert.ok(layout.positions.get('root').x < layout.positions.get('a').x);
assert.equal(layout.positions.get('a').cycleId, layout.positions.get('c').cycleId);
assert.equal(naturalLayout([], []).positions.size, 0);
const fan = [{ id: 'hub' }], links = [];
for (let i = 0; i < 320; i++) { fan.push({ id: `leaf-${i}` }); links.push({ source: 'hub', target: `leaf-${i}` }); }
const large = naturalLayout(fan, links, { rootId: 'hub' });
assertSeparated(large.positions);
assert.ok(large.bounds.height < 17000, 'fanout folds into six columns');
const chain = [], chainEdges = [];
for (let i = 0; i < 2000; i++) { chain.push({ id: `step-${i}` }); if (i) chainEdges.push({ source: `step-${i - 1}`, target: `step-${i}` }); }
assert.equal(naturalLayout(chain, chainEdges, { rootId: 'step-0' }).positions.size, 2000);

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g');
viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const selected = [], view = new GraphView(svg, id => selected.push(id), () => {});
view.groupingProvider = 'natural';
view.setDisplayProvider('flow'); view.render(nodes, edges, null);
assert.equal(viewport.querySelectorAll('.flow-edge').length, edges.length);
assert.equal(viewport.querySelectorAll('.flow-card').length, nodes.length);
for (const path of viewport.querySelectorAll('.flow-edge')) assertClear(path.attributes.d, [...view.positions.values()].map(point => ({ ...point, w: point.width, h: point.height })));
view.nodeElements.get('a').group.listeners.click(); assert.deepEqual(selected, ['a']);
view.setSearchMatches(['a']); assert.ok(view.nodeElements.get('a').group.querySelector('.regex-ring'));
view.setDisplayProvider('topology'); view.render(nodes, edges, null);
assert.equal(view.positions.size, nodes.length); assert.equal(view.compartments.length, 0);
assert.equal(JSON.stringify({ nodes, edges }), facts);

const pathNodes = [{ id: 'root', label: 'Root', controlFlow: { branches: [
  { kind: 'if', line: 5, arms: [{ id: 'yes', kind: 'true' }] },
  { kind: 'foreach', line: 8, arms: [{ id: 'body', kind: 'body' }] },
] } }, { id: 'a', label: 'Service' }, { id: 'b', label: 'Mapper' }, { id: 'c', label: 'Rules' }];
const pathEdges = [{ source: 'root', target: 'a', sourceLine: 6, branchIds: ['yes'], confidence: 'candidate' },
  { source: 'root', target: 'b', sourceLine: 9, branchIds: ['body'] }, { source: 'a', target: 'c' }];
view.pathRootId = 'root'; view.pathReading = true; view.pathSnapshotId = 'natural-one';
view.renderPathCards(pathNodes, pathEdges, 'root');
const reserved = view.entryPathLayout, positions = new Map(view.positions);
view.camera = { x: 17, y: 25, scale: .8 };
const camera = { ...view.camera };
view.setPathControlFilters({ conditions: false, loops: false });
view.togglePathNode('a');
assert.notEqual(view.entryPathLayout, reserved, 'expansion rebuilds only the visible tree');
for (const [id, point] of positions) if (view.positions.has(id)) assert.deepEqual(view.positions.get(id), point);
assert.deepEqual(view.camera, camera);
for (const path of viewport.querySelectorAll('.path-edge')) assertClear(path.attributes.d, [...view.positions.values()]);
view.setDisplayProvider('flow'); view.groupingProvider = 'graphistry'; view.renderPathCards(pathNodes, pathEdges, 'root');
assert.notEqual(view.entryPathLayout, reserved, 'changing the provider invalidates reserved layout');

const url = writeRoute({ repo: 'repo', algorithm: 'natural', path: 'root', display: 'flow' }, 'http://127.0.0.1:8765/');
assert.equal(readRoute(url).algorithm, 'natural');
assert.equal(navigationSnapshot({ route: readRoute(url) }).filters.algorithm, 'natural', 'a shared URL restores the provider');
console.log('Natural bounded groups, branches, renderer, stable path and navigation PASS');
