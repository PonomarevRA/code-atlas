/** Проверяет реальные карточки топологии, разделение групп и стабильные пользовательские действия. */
import assert from 'node:assert/strict';
import { groupedNodeGeometry } from '../web/topology-groups.js';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';

/** Прямоугольники могут иметь общую границу, но не положительную площадь пересечения. */
function noOverlap(boxes) {
  for (let first = 0; first < boxes.length; first++) for (let second = first + 1; second < boxes.length; second++) {
    const a = boxes[first], b = boxes[second];
    assert.ok(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top, `${a.id} overlaps ${b.id}`);
  }
}
/** Заголовок секции расположен выше карточек; все карточки остаются внутри своей секции. */
function assertGeometry(geometry) {
  const positions = geometry instanceof Map ? geometry : geometry.positions;
  const sections = geometry.compartments;
  noOverlap([...positions.values()].map(point => ({ ...point.cardBounds, id: point.id })));
  noOverlap(sections.map(box => ({ id: box.id, left: box.x, top: box.y, right: box.x + box.width, bottom: box.y + box.height })));
  for (const point of positions.values()) {
    const box = sections.find(section => point.cardBounds.left >= section.x && point.cardBounds.right <= section.x + section.width && point.cardBounds.top >= section.y + 54 && point.cardBounds.bottom <= section.y + section.height);
    assert.ok(box, `No owning section for ${point.id}`);
  }
}
const projects = [{ id: 'api', label: 'API' }, { id: 'calc', label: 'Calculator' }];
const communities = [{ id: 'entry', label: 'HTTP API' }, { id: 'math', label: 'Расчёт стоимости' }];
const nodes = Array.from({ length: 90 }, (_, index) => ({ id: `n${index}`, label: index % 3 ? `Calculate${index}` : 'RepeatedLongCalculateTotalAsyncWithArguments', kind: 'method', community: index < 20 ? 'entry' : 'math', codeProjectId: index < 20 ? 'api' : 'calc', file: index < 20 ? `Controllers/CostChange${index % 3}.cs` : `Domain/Calculator${index % 6}.cs`, line: index + 1, changedFile: index === 0 }));
const edges = nodes.map((node, index) => ({ source: node.id, target: nodes[(index + 1) % nodes.length].id, relation: index === 20 ? 'dispatches_to' : 'calls', confidence: index === 20 ? 'candidate' : 'EXTRACTED', sourceLine: index + 1 }));
edges.push({ source: 'n0', target: 'n0', relation: 'calls' });
const before = JSON.stringify({ nodes, edges, projects, communities });
const plain = groupedNodeGeometry(nodes, communities, edges, null, null, projects);
assertGeometry(plain);
const projectGeometry = groupedNodeGeometry(nodes, communities, edges, projects, null, projects);
assertGeometry(projectGeometry);
assert.equal(projectGeometry.positions.size, nodes.length);
assert.equal(projectGeometry.containers.length, 2);
noOverlap(projectGeometry.containers.map(box => ({ id: box.id, left: box.x, top: box.y, right: box.x + box.width, bottom: box.y + box.height })));
const permuted = groupedNodeGeometry([...nodes].reverse(), communities, [...edges].reverse(), null, null, projects);
assert.deepEqual([...permuted], [...plain], 'Input order does not change placement');
assert.equal(groupedNodeGeometry([]).size, 0);
assert.equal(JSON.stringify({ nodes, edges, projects, communities }), before);

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g'); viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const selected = [];
const view = new GraphView(svg, id => selected.push(id), () => {});
view.colorProjects = projects;
view.render(nodes, edges, null, communities, projects);
assertGeometry({ positions: view.positions, compartments: view.compartments });
assert.deepEqual(view.routingBounds, view.geometryBounds(), 'Routing includes current group headers on the first render');
assert.equal(view.nodeElements.size, nodes.length);
assert.equal(view.edgeElements.length, edges.length, 'Every raw fact, including recursion, is rendered');
for (const node of nodes) {
  const point = view.positions.get(node.id), group = view.nodeElements.get(node.id).group;
  const shell = group.querySelector('.topology-card-shell');
  assert.equal(shell.attributes.width, point.cardBounds.right - point.cardBounds.left);
  assert.equal(shell.attributes.height, point.cardBounds.bottom - point.cardBounds.top);
  assert.ok(group.querySelector('.topology-card-location'));
  assert.equal(group.attributes.tabindex, 0);
}
for (const item of view.edgeElements) {
  const target = view.positions.get(item.path.attributes['data-target']).cardBounds;
  assert.ok(Math.abs(item.target.x - target.left) < .001 || Math.abs(item.target.x - target.right) < .001 || Math.abs(item.target.y - target.top) < .001 || Math.abs(item.target.y - target.bottom) < .001, 'Arrow ends on actual card boundary');
}
const card = view.nodeElements.get('n0').group;
card.listeners.click();
card.listeners.keydown({ key: 'Enter', preventDefault() {} });
assert.deepEqual(selected, ['n0', 'n0']);
view.camera = { x: 123, y: -456, scale: .8 };
const camera = { ...view.camera }, positions = view.positions, edgePaths = view.edgeElements.map(item => item.path);
view.setSelected('n0'); view.setSearchMatches(['n0']); view.setBundled(false); view.setBundled(true);
assert.equal(card.querySelector('.topology-card-shell').attributes['stroke-width'], 2.5);
assert.equal(card.querySelector('.regex-ring').tag, 'rect');
assert.equal(view.positions, positions);
assert.deepEqual(view.camera, camera);
assert.deepEqual(view.edgeElements.map(item => item.path), edgePaths);
view.setSelected('n1');
assert.equal(card.querySelector('.topology-card-shell').attributes['stroke-width'], 1.2);
assert.ok(card.querySelector('.regex-ring'));
assert.ok(card.querySelector('.change-ring'));
view.setDisplayProvider('flow'); view.render(nodes.slice(0, 4), edges.slice(0, 3), null);
assert.equal(view.compartments.length, 0, 'Provider switch clears old topology bounds');
view.setDisplayProvider('topology'); view.render(nodes.slice(0, 4), edges.slice(0, 3), 'n0');
assert.equal(view.nodeElements.size, 4);
assertGeometry({ positions: view.positions, compartments: view.compartments });
assert.deepEqual(view.routingBounds, view.geometryBounds(), 'Small map routing ignores previous large compartments');
view.renderOverview([{ id: 'only', label: 'Overview', nodeCount: 2 }], [], null);
assert.equal(view.compartments.length, 0);
assert.deepEqual(view.routingBounds, view.geometryBounds(), 'Overview routing clears raw card group extents');
assert.equal(JSON.stringify({ nodes, edges, projects, communities }), before);
console.log('Topology groups actual cards, bounds, directions, raw IDs, stable selection and provider switching PASS');
