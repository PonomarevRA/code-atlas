/** Реальные renderer и toolbar: полная статическая цепочка, камера и кандидатные переходы. */
import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';
import { PathToolbar } from '../web/path-toolbar.js';
import { Svg } from './svg_fixture.mjs';

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };

/** Создаёт изолированную SVG-сцену с настоящим GraphView. */
function scene(provider) {
  const svg = new Svg('svg');
  const viewport = new Svg('g');
  viewport.setAttribute('id', 'viewport');
  svg.append(viewport);
  const selections = [];
  const view = new GraphView(svg, id => selections.push(id), () => {});
  view.setDisplayProvider(provider);
  return { view, viewport, selections };
}

const nodes = ['root', 'contract', 'impl', 'alternative', 'provider', 'work', 'orphan', 'path-entry:http'].map(id => ({
  id, label: id, kind: 'method', file: `src/${id}.cs`,
}));
const evidence = { kind: 'interface-implementation', interfaceType: 'Example.IService', implementationType: 'Example.Service', signature: 'LoadAsync(string)', sourceKind: 'working-tree-ast', file: 'src/Impl.cs', line: 12, ambiguous: true };
const edges = [
  { source: 'path-entry:http', target: 'root', relation: 'calls' },
  { source: 'root', target: 'contract', relation: 'calls', confidence: 'source' },
  { source: 'contract', target: 'impl', relation: 'dispatches_to', confidence: 'INFERRED', source_file: 'src/IService.cs', source_location: 'L7', evidence },
  { source: 'contract', target: 'alternative', relation: 'dispatches_to', confidence: 'candidate' },
  { source: 'impl', target: 'provider', relation: 'calls', confidence: 'source' },
  { source: 'provider', target: 'work', relation: 'calls', confidence: 'source' },
  { source: 'work', target: 'root', relation: 'calls', confidence: 'source' },
  { source: 'root', target: 'missing', relation: 'calls' },
];
const original = JSON.stringify({ nodes, edges });
for (const provider of ['topology', 'flow']) {
  const { view, viewport, selections } = scene(provider);
  assert.equal(view.expandAllPathNodes(), false, 'ordinary map cannot revive stale path data');
  view.pathRootId = 'root';
  const notifications = [];
  view.onPathScopeChange = info => notifications.push(info);
  view.renderPathCards(nodes, edges, 'contract');
  assert.ok(!view.nodeElements.has('impl'), 'ambiguous implementation stays compact initially');
  assert.equal(notifications.at(-1).canExpandAll, true);
  view.pathCollapsedIds.add('provider');
  view.camera = { x: 87, y: -42, scale: .9 };
  const camera = { ...view.camera };
  assert.equal(view.expandAllPathNodes(), true);
  for (const id of ['root', 'contract', 'impl', 'alternative', 'provider', 'work']) {
    assert.ok(view.nodeElements.has(id), `${provider}: missing ${id}`);
    assert.ok(view.pathExpandedIds.has(id));
  }
  assert.ok(!view.pathExpandedIds.has('orphan'));
  assert.ok(!view.pathExpandedIds.has('path-entry:http'));
  assert.ok(!view.nodeElements.has('orphan'));
  assert.equal(view.pathCollapsedIds.size, 0);
  assert.equal(notifications.at(-1).canExpandAll, false);
  assert.deepEqual(view.camera, camera);
  assert.equal(view.selectedId, 'contract');
  assert.deepEqual(selections, [], 'expansion does not select or navigate');
  if (provider === 'flow') {
    assert.equal(view.nodeElements.size, 7, 'cycle reuses each real card, including entry, exactly once');
    assert.equal(viewport.querySelectorAll('.path-reference').length, 0);
    assert.ok(view.pathEdgeElements.some(edge => edge.source === 'work' && edge.target === 'root'), 'cycle returns to the existing root');
  } else assert.ok(viewport.querySelectorAll('.path-reference').length > 0, 'detailed topology keeps finite local references');
  const geometry = JSON.stringify([...view.positions]);
  view.expandAllPathNodes();
  assert.equal(JSON.stringify([...view.positions]), geometry, 'repeated expansion is deterministic');
  view.collapseAllPathNodes();
  assert.ok(!view.nodeElements.has('work'));
  assert.equal(notifications.at(-1).canExpandAll, true);
  view.render(nodes.slice(0, 2), edges.slice(1, 2), null);
  assert.equal(view.expandAllPathNodes(), false, 'old cached path stays inactive');
}

for (const provider of ['topology', 'flow']) {
  const { view, viewport } = scene(provider);
  view.render(nodes.slice(0, 3), edges.slice(1, 3), null);
  const selector = provider === 'flow' ? '.flow-edge' : '.graph-edge';
  const rendered = viewport.querySelectorAll(selector);
  const call = rendered.find(path => path.attributes['data-source'] === 'root');
  const candidate = rendered.find(path => path.attributes['data-source'] === 'contract');
  assert.equal(call.attributes['stroke-dasharray'], undefined, 'source call keeps normal stroke');
  assert.equal(candidate.attributes['stroke-dasharray'], '5 4');
  assert.ok(candidate.querySelector('title').textContent.includes('src/Impl.cs'));
  assert.ok(candidate.querySelector('title').textContent.includes('src/IService.cs:L7'));
  assert.ok(candidate.querySelector('title').textContent.includes('Example.IService'));
  assert.ok(candidate.querySelector('title').textContent.includes('Несколько статически совместимых реализаций'));
  assert.ok(candidate.querySelector('title').textContent.includes('12'));
  assert.ok(candidate.querySelector('title').textContent.includes('снимка исходников'));
  assert.ok(viewport.querySelectorAll('.path-edge-label').some(label => label.textContent === 'Возможная реализация'));
  const delegate = {
    source: 'contract', target: 'impl', relation: 'dispatches_to', confidence: 'INFERRED',
    evidence: { kind: 'method-group-binding', parameter: 'calculate', receiverType: 'Example.IService', declaringType: 'Example.Service', bindingFile: 'src/Factory.cs', bindingLine: 42, factoryNodeId: 'factory', runtimeSelectionKnown: false },
  };
  view.render(nodes.slice(0, 3), [delegate], null);
  const delegatePath = viewport.querySelectorAll(selector)[0];
  const tooltip = delegatePath.querySelector('title').textContent;
  assert.equal(delegatePath.attributes['stroke-dasharray'], '5 4');
  assert.ok(tooltip.includes('Передача метода в параметр делегата'));
  assert.ok(tooltip.includes('Параметр: calculate'));
  assert.ok(tooltip.includes('Тип, объявляющий метод: Example.Service'));
  assert.ok(tooltip.includes('Файл передачи метода: src/Factory.cs'));
  assert.ok(tooltip.includes('Строка передачи метода: 42'));
  assert.ok(tooltip.includes('выбор при выполнении не установлен'));
  const typedCall = { source: 'root', target: 'contract', relation: 'calls', confidence: 'INFERRED',
    source_file: 'src/Caller.cs', source_location: 'L37', evidence: { kind: 'typed-interface-call', receiverType: 'Example.IService', signature: 'LoadAsync`0(int)' } };
  view.render(nodes.slice(0, 3), [typedCall], null);
  const callPath = viewport.querySelectorAll(selector)[0];
  assert.equal(callPath.attributes['stroke-dasharray'], undefined);
  assert.ok(callPath.querySelector('title').textContent.includes('Вызов через тип интерфейса'));
  assert.ok(callPath.querySelector('title').textContent.includes('Example.IService'));
  assert.ok(callPath.querySelector('title').textContent.includes('src/Caller.cs:L37'));
}

/** Минимальный HTML-фасад проверяет обработчики, native disabled и жизненный цикл toolbar. */
function toolbarRoot() {
  const controls = new Map();
  for (const name of ['expand', 'collapse', 'focus', 'fit', 'current', 'count', 'limits']) {
    const control = new Svg('button');
    control.disabled = false;
    controls.set(name, control);
  }
  return { hidden: true, controls, querySelector: selector => controls.get(selector.match(/"([^"]+)"/)[1]) };
}
const root = toolbarRoot();
let expanded = 0;
const toolbar = new PathToolbar(root, { collapse() {}, focus() {}, fit() {}, expand: () => { expanded += 1; } });
toolbar.render({ total: 6, visible: 2, expanded: 1, canExpandAll: true }, { truncation: { nodes: true } });
assert.equal(root.hidden, false);
assert.equal(root.controls.get('expand').disabled, false);
root.controls.get('expand').listeners.click();
assert.equal(expanded, 1);
assert.ok(root.controls.get('limits').textContent.includes('Часть вызовов'));
toolbar.render({ total: 6, visible: 6, canExpandAll: false }, { truncation: { arguments: true } });
assert.ok(root.controls.get('limits').textContent.includes('аргументы вызовов'));
assert.ok(!root.controls.get('limits').textContent.includes('Часть вызовов'));
toolbar.render({ total: 6, visible: 6, expanded: 6, canExpandAll: false });
assert.equal(root.controls.get('expand').disabled, true);
toolbar.hide();
assert.equal(root.hidden, true);
assert.equal(root.controls.get('expand').disabled, true);
const withoutAction = toolbarRoot();
new PathToolbar(withoutAction, { collapse() {}, focus() {}, fit() {} }).render({ canExpandAll: true });
assert.equal(withoutAction.controls.get('expand').disabled, true);
assert.equal(JSON.stringify({ nodes, edges }), original, 'input graph remains unchanged');
console.log('Full chain expansion and candidate provenance PASS');
