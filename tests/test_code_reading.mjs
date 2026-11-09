import assert from 'node:assert/strict';
import { workspaceDisplayProvider } from '../web/display-providers.js';
import { normalizeRoute } from '../web/navigation-history.js';
import { codeReadingMetrics, appendCodeReadingCard } from '../web/code-reading-card.js';
import { nodeCardLayout } from '../web/node-card-layout.js';
import { appendNodeActions } from '../web/node-actions.js';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
assert.equal(workspaceDisplayProvider('architecture', 'topology').id, 'flow');
assert.equal(workspaceDisplayProvider('system', 'topology').id, 'topology');
assert.equal(workspaceDisplayProvider('groups', 'topology').id, 'topology');
for (const level of ['projects', 'files', 'symbols']) {
  const route = normalizeRoute({ repo: 'repo', view: 'architecture', display: 'topology', level, project: 'p', file: 'Service.cs' });
  assert.equal(route.display, 'flow'); assert.equal(route.level, level);
}
const method = { id: 'method', label: 'CalculateAsync(request)', file: 'Service.cs', line: 17,
  memberFacts: { kind: 'method', visibility: 'public' },
  documentation: { summary: 'Рассчитывает стоимость выбранных активов с сохранением исходных дат.' },
  typeContext: { documentation: { summary: 'Класс расчёта портфеля.' } },
  complexity: { score: 7, status: 'complete' } };
const project = { id: 'project', label: 'Portfolio', architectureLevel: 'projects', nodeCount: 120 };
const file = { id: 'file', label: 'Service.cs', architectureLevel: 'files', nodeCount: 12 };
const original = JSON.stringify([method, project, file]);
const metrics = codeReadingMetrics(method);
assert.ok(metrics.context.startsWith('Рассчитывает'), 'Purpose precedes technical visibility metadata');
assert.ok(metrics.context.includes('О классе: Класс расчёта портфеля.'));
assert.equal(codeReadingMetrics(project).context, 'Проект · 120 символов');
assert.equal(codeReadingMetrics({ ...project, nodeCount: 1 }).context, 'Проект · 1 символ');
assert.equal(codeReadingMetrics({ ...project, nodeCount: 22 }).context, 'Проект · 22 символа');
assert.ok(codeReadingMetrics(file).context.startsWith('Файл · 12'));
assert.equal(codeReadingMetrics({ id: 'unknown' }).context, 'Символ · описание не добавлено');
assert.equal(codeReadingMetrics({ id: 'class', _callable_class: true }).kind, 'Класс');
assert.equal(codeReadingMetrics({ id: 'interface', typeFacts: { kind: 'interface' } }).kind, 'Интерфейс');
const surface = { semantic: { id: 'source', color: '#5f7d70', fill: '#e8f2eb' }, palette: { fill: '#e8f2eb' }, color: '#5f7d70' };
const group = new Svg('g');
appendCodeReadingCard(group, method, { ...surface, width: metrics.width, height: metrics.height });
assert.equal(group.attributes['data-reading-design'], 'entry-path');
assert.equal(group.querySelector('.flow-card-header').attributes.height, 52);
assert.equal(group.querySelector('.flow-card-name').attributes.style, 'font-size:14px');
assert.equal(group.querySelectorAll('.flow-port-label').length, 0);
const noteY = Number(group.querySelector('.flow-card-note').attributes.y);
assert.ok(group.querySelectorAll('.flow-card-context').every(row => Number(row.attributes.y) < noteY));
assert.ok(group.querySelectorAll('title').some(title => title.textContent.includes('не порядок исполнения')));
const selected = [], opened = [], toggled = [], sourced = [];
const actions = appendNodeActions(group, { id: method.id, enter: id => opened.push(id), onToggle: id => toggled.push(id),
  reading: { left: -metrics.width / 2, top: -metrics.height / 2, width: metrics.width, height: metrics.height, source: id => sourced.push(id) } });
for (const [name, target] of Object.entries(actions)) {
  assert.ok(target?.querySelector('text'), `${name} is labelled`);
  assert.equal(target.querySelectorAll('circle').length, 0);
  target.listeners.click({ stopPropagation() {}, preventDefault() {} });
}
assert.deepEqual(opened, ['method']); assert.deepEqual(toggled, ['method']); assert.deepEqual(sourced, ['method']);
for (const natural of [false, true]) {
  const nodes = [project, file, method], edges = [{ source: 'project', target: 'file', relation: 'references' },
    { source: 'file', target: 'method', relation: 'references' }];
  const layout = nodeCardLayout(nodes, edges, { natural, reading: true });
  for (const node of nodes) assert.equal(layout.positions.get(node.id).height, codeReadingMetrics(node).height);
  const svg = new Svg('svg'), viewport = new Svg('g'); viewport.setAttribute('id', 'viewport'); svg.append(viewport);
  const view = new GraphView(svg, id => selected.push(id), () => {});
  view.codeReading = true; view.groupingProvider = natural ? 'natural' : 'graphistry'; view.setDisplayProvider('flow');
  view.render(nodes, edges, method.id);
  assert.equal(viewport.querySelectorAll('.flow-card').length, 3);
  for (const node of nodes) assert.equal(view.nodeElements.get(node.id).group.attributes['data-reading-design'], 'entry-path');
  assert.equal(viewport.querySelectorAll('.flow-edge').length, edges.length);
  view.nodeElements.get(method.id).group.listeners.click();
  view.fitCodeInitial();
  assert.equal(view.camera.scale, .9);
  const selectedPoint = view.positions.get(method.id);
  assert.equal(view.camera.x + (selectedPoint.x - selectedPoint.width / 2) * view.camera.scale, 24);
  const geometry = JSON.stringify([...view.positions]);
  view.fit(); assert.equal(JSON.stringify([...view.positions]), geometry, 'Overview changes only the camera');
}
assert.deepEqual(selected, ['method', 'method']);
const systems = [{ id: 'system', system: true, label: 'Database' }];
assert.deepEqual(nodeCardLayout(systems, [], { reading: true }), nodeCardLayout(systems, [], { reading: false }));
assert.equal(JSON.stringify([method, project, file]), original);
console.log('Unified Code reading: legacy routes, shared card geometry, purpose/owner, footer actions, raw IDs and system isolation PASS');
