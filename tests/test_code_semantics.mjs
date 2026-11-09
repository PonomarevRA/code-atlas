/** Семантические роли используют явные основания и сохраняют неизвестные/смешанные блоки. */
import assert from 'node:assert/strict';
import { CODE_ROLES, classifyCodeNode, codeConnectionCounts, codeRoleSummary } from '../web/code-semantics.js';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';
import { architectureGraph } from '../web/architecture.js';

const examples = [
  [{ kind: 'interface', file: 'Services/Calculator.cs' }, 'contract', 'metadata'],
  [{ role: 'data', file: 'Controllers/DataController.cs' }, 'data', 'metadata'],
  [{ kind: 'method', file: 'src/Controllers/TotalController.cs' }, 'entry', 'convention'],
  [{ kind: 'method', file: 'src/Domain/TotalCalculator.cs' }, 'logic', 'convention'],
  [{ kind: 'class', file: 'src\\Storage\\LimitsRepository.cs' }, 'data', 'convention'],
  [{ kind: 'class', file: 'src/Contracts/TotalRequest.cs' }, 'contract', 'convention'],
  [{ kind: 'method', file: 'src/Clients/QuotesClient.cs' }, 'integration', 'convention'],
  [{ kind: 'class', file: 'tests/Integration/QuotesTests.cs' }, 'test', 'convention'],
  [{ kind: 'class', file: 'src/Logging/TraceSettings.cs' }, 'infrastructure', 'convention'],
  [{ kind: 'method', label: 'CalculateTotalAsync', file: 'Unknown.cs' }, 'unknown', 'unknown'],
  [{ architectureLevel: 'projects', label: 'ExampleService.Contracts' }, 'contract', 'convention'],
];
const before = JSON.stringify(examples);
for (const [node, expected, confidence] of examples) {
  const role = classifyCodeNode(node);
  assert.equal(role.id, expected);
  assert.equal(role.confidence, confidence);
  assert.ok(role.evidence.length > 0);
  assert.equal(role.color, CODE_ROLES.find(item => item.id === expected).color);
}
assert.equal(JSON.stringify(examples), before);
assert.equal(classifyCodeNode({ roleCounts: { logic: 8, data: 2 } }).id, 'logic');
assert.equal(classifyCodeNode({ roleCounts: { logic: 6, data: 4 } }).id, 'unknown');
assert.equal(classifyCodeNode({ roleCounts: { logic: 1, unknown: 99 } }).id, 'unknown');
assert.ok(codeRoleSummary({ logic: 8, unknown: 2 }).includes('Роль не определена: 2'));
const members = [
  { id: 'calculate-a', label: 'CalculateA', file: 'Domain/Calculator.cs', codeProjectId: 'business' },
  { id: 'calculate-b', label: 'CalculateB', file: 'Domain/Calculator.cs', codeProjectId: 'business' },
  { id: 'unknown', label: 'Helper', file: 'Unspecified.cs', codeProjectId: 'business' },
];
const membersBefore = JSON.stringify(members);
const aggregate = architectureGraph({ nodes: members, edges: [], codeProjects: [{ id: 'business', label: 'Example.Component' }] }, members).groups[0];
assert.equal(aggregate.fileCount, 2);
assert.equal(aggregate.roleCounts.logic, 2);
assert.equal(aggregate.roleCounts.unknown, 1);
assert.equal(Object.values(aggregate.roleCounts).reduce((sum, count) => sum + count, 0), aggregate.nodeCount);
assert.equal(JSON.stringify(members), membersBefore);
const counts = codeConnectionCounts([
  { source: 'a', target: 'b', weight: 2.5 }, { source: 'a', target: 'a' },
  { source: 'b', target: 'a', weight: null }, { source: 'a', target: 'b', weight: 0 },
]);
assert.deepEqual(counts.get('a'), { incoming: 2, outgoing: 3.5 });
assert.deepEqual(counts.get('b'), { incoming: 2.5, outgoing: 1 });

// Карточки проектов остаются в рассчитанных границах; ребро касается прямоугольника, а не скрытого кружка.
globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const svg = new Svg('svg'), viewport = new Svg('g');
viewport.setAttribute('id', 'viewport'); svg.append(viewport);
const view = new GraphView(svg, () => {}, () => {});
const groups = [
  { id: 'api', label: 'Example.Api', displayLabel: 'Api', nodeCount: 20, architecture: true, architectureLevel: 'projects', roleCounts: { entry: 20 } },
  { id: 'dto', label: 'Example.Contracts', displayLabel: 'Contracts', nodeCount: 10, architecture: true, architectureLevel: 'projects', roleCounts: { contract: 10 } },
];
view.renderOverview(groups, [{ source: 'api', target: 'dto', weight: 3 }]);
for (const item of groups) {
  const card = view.communityElements.get(item.id).group;
  const point = view.positions.get(item.id);
  assert.equal(card.attributes['data-code-role'], classifyCodeNode(item).id);
  assert.ok(card.querySelector('.code-landscape-card'));
  for (const label of card.querySelectorAll('text')) {
    const y = Number(label.attributes.y);
    assert.ok(y >= -point.r && y <= point.labelBottom);
  }
}
const path = view.edgeElements[0].path;
const values = path.attributes.d.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);
const target = view.positions.get('dto').cardBounds;
const [x, y] = values.slice(-2);
assert.ok(x >= target.left - .001 && x <= target.right + .001);
assert.ok(y >= target.top - .001 && y <= target.bottom + .001);
assert.ok(Math.min(Math.abs(x - target.left), Math.abs(x - target.right), Math.abs(y - target.top), Math.abs(y - target.bottom)) < .001);
view.setBundled(false);
assert.deepEqual(view.edgeElements[0].path.attributes.d.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number).slice(-2), [x, y]);
view.setDisplayProvider('flow');
view.render([{ id: 'calculate', label: 'Calculate', kind: 'method', file: 'Domain/TotalCalculator.cs' }], [], null);
const flow = view.nodeElements.get('calculate').group;
assert.equal(flow.attributes['data-code-role'], 'logic');
assert.equal(flow.querySelector('.flow-card-role').textContent, 'Логика и расчёты');
assert.ok(flow.querySelector('.code-role-stripe'));
console.log('Code semantics evidence, mixed roles, landscape card ports and flow badges PASS');
