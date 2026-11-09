/** Обычная карта наследует чтение пути; комментарии, циклы и сравнение остаются исходными фактами. */
import assert from 'node:assert/strict';
import { nodeCardLayout } from '../web/node-card-layout.js';
import { cardDocumentation } from '../web/card-documentation.js';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';

const nodes = [{ id: 'z-entry', label: 'Entry' }, { id: 'a-interface', label: 'Interface' },
  { id: 'impl-a', label: 'A', comment: 'Обрабатывает входные данные и возвращает рассчитанную стоимость.', commentScope: 'declaration' },
  { id: 'impl-b', label: 'B', comment: 'Реализация для структурных продуктов.', commentScope: 'containing-type' },
  { id: 'other-entry' }, { id: 'a-other-callee' }];
const edges = [{ source: 'z-entry', target: 'a-interface', relation: 'calls' },
  { source: 'a-interface', target: 'impl-a', relation: 'dispatches_to' },
  { source: 'a-interface', target: 'impl-b', relation: 'dispatches_to' },
  { source: 'impl-a', target: 'impl-a', relation: 'calls' },
  { source: 'other-entry', target: 'a-other-callee', relation: 'calls' }];
const original = JSON.stringify({ nodes, edges });
for (const natural of [false, true]) {
  const layout = nodeCardLayout(nodes, edges, { natural }), at = id => layout.positions.get(id);
  assert.ok(at('z-entry').x < at('a-interface').x && at('a-interface').x < at('impl-a').x);
  assert.ok(at('other-entry').x < at('a-other-callee').x, 'Disconnected chains start at callers rather than alphabetically first callees');
  assert.equal(at('impl-a').x, at('impl-b').x, 'Implementations share a vertical comparison column');
  assert.ok(at('impl-a').y < at('impl-b').y);
  assert.ok(at('impl-a').height > at('z-entry').height, 'Captured comments reserve real card space');
  assert.ok(at('impl-a').cycleId, 'Self-recursion retains its static cycle marker');
  const cards = [...layout.positions.values()];
  for (let i = 0; i < cards.length; i++) for (let j = i + 1; j < cards.length; j++) {
    const a = cards[i], b = cards[j];
    assert.ok(Math.abs(a.x - b.x) >= (a.width + b.width) / 2 || Math.abs(a.y - b.y) >= (a.height + b.height) / 2);
  }
  assert.deepEqual(nodeCardLayout([...nodes].reverse(), [...edges].reverse(), { natural }), layout);
}
assert.equal(JSON.stringify({ nodes, edges }), original);
assert.equal(cardDocumentation(nodes[3]).text, 'О классе: Реализация для структурных продуктов.');
assert.equal(cardDocumentation({ comment: 'No ownership facts' }).text, '', 'Unscoped comments cannot become method documentation');
assert.equal(cardDocumentation({ documentation: { summary: 'Own method' }, comment: 'Old snapshot', commentScope: 'declaration' }).text, 'Own method');
globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g'); viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const view = new GraphView(svg, () => {}, () => {});
view.setDisplayProvider('flow'); view.render(nodes, edges);
const group = view.nodeElements.get('impl-a').group;
assert.ok(group.querySelectorAll('.flow-card-description').length);
const title = group.querySelector('.flow-card-description').querySelector('title').textContent;
assert.equal(title, nodes[2].comment);
const descriptionRows = group.querySelectorAll('.flow-card-description');
const note = group.querySelector('.flow-card-note');
assert.ok(descriptionRows.every(row => Number(row.attributes.y) < Number(note.attributes.y)), 'Description rows fit above the footer');
assert.equal(viewport.querySelectorAll('.flow-edge').length, edges.length);
console.log('Entry-based node cards, source comments, comparison columns and stable geometry PASS');
