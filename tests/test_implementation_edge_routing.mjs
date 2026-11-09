/** Проверяет реальные стрелки renderer: sibling controls не должны перекрывать промежуточные карточки. */
import assert from 'node:assert/strict';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';
import { assertClear } from './curve_geometry.mjs';

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg');
const viewport = new Svg('g');
viewport.setAttribute('id', 'viewport');
svg.append(viewport);
const selected = [];
const view = new GraphView(svg, id => selected.push(id), () => {});
view.setDisplayProvider('flow');
view.pathRootId = 'root';
const branches = [];
for (let index = 0; index < 28; index++) branches.push({ kind: index % 2 ? 'if' : 'foreach', line: 10 + index * 2,
  condition: `item${index}.Enabled`, arms: [{ id: `arm-${index}`, kind: index % 2 ? 'true' : 'body' }] });
branches.push({ kind: 'switch', line: 80, condition: 'product.Kind', arms: ['Broker', 'TrustManagement', 'Insurance', 'StructuredProducts', '_']
  .map(label => ({ id: `product-${label}`, kind: 'case', label })) });
view.renderPathCards([
  { id: 'root', label: '.Calculate()', file: 'Root.cs', controlFlow: { branches } },
  { id: 'callee', label: '.Calculate()', file: 'ActualCalculator.cs' },
], [{ source: 'root', target: 'callee', relation: 'dispatches_to', confidence: 'candidate', branchIds: ['arm-22'] }], 'root');
view.expandAllPathNodes();

/** Плавные кривые обходят внутреннюю область любой карточки, включая endpoints. */
function assertRoutesClear() {
  const cards = [...view.positions.values()];
  const paths = viewport.querySelectorAll('.implementation-flow-edge');
  assert.ok(paths.length > 28);
  for (const path of paths) {
    const d = path.attributes.d;
    assert.ok(d, 'every semantic edge needs a visible route');
    assertClear(d, cards);
    assert.ok(path.querySelector('title'), 'source/candidate evidence stays accessible');
  }
  for (const label of viewport.querySelectorAll('.path-edge-label')) {
    const visibleText = label.textContent.replace(label.querySelector('title')?.textContent || '', '');
    const x = Number(label.attributes.x), y = Number(label.attributes.y), width = visibleText.length * 7;
    for (const card of cards) assert.ok(x + width / 2 <= card.x - card.w / 2 || x - width / 2 >= card.x + card.w / 2
      || y + 3 <= card.y - card.h / 2 || y - 12 >= card.y + card.h / 2, `caption overlaps ${card.id}`);
  }
  const labels = viewport.querySelectorAll('.path-edge-label');
  for (let index = 0; index < labels.length; index++) {
    for (let other = index + 1; other < labels.length; other++) {
      const a = labels[index], b = labels[other];
      const visibleA = a.textContent.replace(a.querySelector('title')?.textContent || '', '');
      const visibleB = b.textContent.replace(b.querySelector('title')?.textContent || '', '');
      assert.ok(Math.abs(Number(a.attributes.x) - Number(b.attributes.x)) >= (visibleA.length + visibleB.length) * 3.5
        || Math.abs(Number(a.attributes.y) - Number(b.attributes.y)) >= 13, 'separate outgoing captions must not overlap');
    }
  }
}
assertRoutesClear();
const stub = viewport.querySelectorAll('.path-edge-label').find(label => label.textContent.replace(label.querySelector('title')?.textContent || '', '') === '5 ветвей · исходник');
assert.ok(stub, 'uncovered switch arms share one bounded source link');
for (const arm of ['Broker', 'TrustManagement', 'Insurance', 'StructuredProducts', '_']) assert.ok(stub.querySelector('title').textContent.includes(arm), 'all original arm captions stay accessible');
const reference = viewport.querySelectorAll('.path-reference').find(group => group.attributes['data-focus-id'] === 'callee');
assert.ok(reference);
for (const scale of [.5, 2]) {
  view.camera = { x: 30, y: 45, scale };
  reference.listeners.click({ stopPropagation() {} });
  assert.equal(selected.at(-1), 'callee');
  assert.equal(view.camera.scale, scale);
  const point = view.positions.get('callee');
  assert.equal(point.x * scale + view.camera.x, (svg.clientWidth || 960) / 2);
  reference.listeners.keydown({ key: 'Enter', preventDefault() {}, stopPropagation() {} });
  assert.equal(view.camera.scale, scale);
}
view.fitPathInitial();
assert.ok(view.camera.scale >= .75, 'initial view retains readable zoom even with a tall root body');
console.log('Implementation edge routing, captions, candidate evidence and reference zoom PASS');
