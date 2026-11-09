/** Проверяет реальную SVG-отрисовку типов в двух renderer и изоляцию обычной карты кода. */
import assert from 'node:assert/strict';
import {GraphView} from '../web/graph.js';
import {architectureResourceKind, ARCHITECTURE_RESOURCE_TYPES} from '../web/architecture-resource-style.js';
import {Svg} from './svg_fixture.mjs';

globalThis.document = {createElementNS: (_, tag) => new Svg(tag)};
globalThis.ResizeObserver = class {observe() {}};

const kinds = ['database', 'kafka', 'rabbit', 'http'];
assert.equal(architectureResourceKind({kind: 'toString'}), null);
assert.equal(architectureResourceKind({kind: 'package'}), null);
const items = kinds.map(kind => ({id: kind, kind, label: '<img src=x onerror=bad> ' + kind,
  system: true, architecture: true, architectureLevel: 'projects', kindLabel: kind, nodeCount: 1}));
const before = JSON.stringify(items);

for (const provider of ['flow', 'topology']) {
  const svg = new Svg('svg'), viewport = new Svg('g');
  viewport.setAttribute('id', 'viewport'); svg.append(viewport);
  const view = new GraphView(svg, () => {}, () => {});
  view.setDisplayProvider(provider);
  const cluster = {kind: 'kafka', label: 'Kafka <script>bad</script>', x: -800, y: -600,
    width: 100, height: 100, nodeIds: ['kafka']};
  view.renderOverview(items, [], null, null, null, {positionHook: geometry => {
    geometry.architectureClusters = [cluster];
    return geometry;
  }});
  for (const kind of kinds) {
    const group = view.communityElements.get(kind).group;
    assert.equal(group.attributes['data-architecture-kind'], kind);
    const badge = group.querySelector('.architecture-resource-type');
    assert.ok(badge.textContent.includes(ARCHITECTURE_RESOURCE_TYPES[kind].label));
    assert.equal(badge.querySelector('.architecture-resource-icon').attributes['data-resource-icon'], kind);
    assert.equal(group.querySelectorAll('img').length, 0);
    assert.equal(group.querySelectorAll('script').length, 0);
    if (provider === 'topology') {
      const bounds = view.positions.get(kind).cardBounds;
      assert.ok(bounds && bounds.bottom > bounds.top, 'Rectangle has actual routing/fit bounds');
      const badgeY = Number(badge.attributes.transform.match(/,([^,]+)\)/)[1]);
      const titleY = Number(group.querySelector('.architecture-project-label').attributes.y);
      assert.ok(titleY - (badgeY + 18) >= 38, 'Title reserves its font ascent plus a gap below the type glyph');
    } else {
      const badgeY = Number(badge.attributes.transform.match(/,([^,]+)\)/)[1]);
      const countBottom = Math.max(...group.querySelectorAll('.flow-port-label').map(label => Number(label.attributes.y) + 4));
      assert.ok(badgeY >= countBottom + 8, 'Glyph/footer is below the IN/OUT text, including descenders');
    }
  }
  assert.equal(viewport.querySelector('.architecture-cluster-heading').textContent, cluster.label);
  assert.equal(viewport.querySelectorAll('script').length, 0);
  assert.ok(view.geometryBounds().minY <= cluster.y - 20, 'Fit retains cluster heading');
  if (provider === 'flow') {
    const edges = items.slice(1).map((node, index) => ({source: items[index].id, target: node.id}));
    view.renderOverview(items, edges, null, null, null, {positionHook: geometry => {
      let row = 0;
      for (const point of geometry.positions.values()) {point.x = 0; point.y = row++ * 210;}
      geometry.architectureClusters = [cluster];
      return geometry;
    }});
    assert.ok(view.flowBounds.width < 700, 'Fit discards the obsolete horizontal rank extent after cluster placement');
    assert.ok(view.flowBounds.height > 700, 'Fit includes the actual vertical positions and edge gutter');
  }
  view.renderOverview([{...items[0], system: false, id: 'code', nodeCount: 1}], [], null);
  assert.equal(view.communityElements.get('code').group.attributes['data-architecture-kind'], undefined);
  assert.equal(viewport.querySelectorAll('.architecture-resource-cluster').length, 0, 'Clusters are scoped to current system scene');
}
assert.equal(JSON.stringify(items), before, 'Presentation cannot mutate captured graph facts');
console.log('Architecture resource SVG badges, glyphs, safe text, routing bounds and code isolation PASS');
