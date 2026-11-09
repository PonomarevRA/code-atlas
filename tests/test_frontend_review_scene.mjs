/** Большой путь сохраняет пустые альтернативы, порядок выходов и дублированные raw-ID. */
import assert from 'node:assert/strict';
import { buildPathScene } from '../web/path-scene.js';
import { denseScene } from './frontend_review_fixtures.mjs';

const fixture = denseScene(100);
const source = JSON.stringify(fixture.scope);
const scene = buildPathScene(fixture.scope, fixture.options);
assert.equal(scene.visuals.length, 5300);
assert.equal(scene.stubs.length, 8000);
assert.equal(JSON.stringify(fixture.scope), source, 'scene assembly does not mutate source facts');
assert.deepEqual(scene.stubs.slice(0, 2).map(stub => stub.armId), ['method-0-true-0', 'method-0-false-0']);
assert.deepEqual(scene.visuals.slice(-12).map(node => node.id),
  Array.from({ length: 12 }, (_, index) => `method-99::exit:${100 + index}:${index}`));

const owner = { id: 'method', controlFlow: {
  branches: [{ kind: 'if', line: 1, arms: [{ id: 'yes', kind: 'true' }, { id: 'no', kind: 'false' }] }],
  terminals: [{ kind: 'return', line: 5, text: 'return value;', branchIds: ['yes'] }],
} };
const scope = { nodes: [owner, { id: 'method::branch:1:0::exit:5:0', label: 'existing raw node' }], edges: [], focusId: owner.id };
const duplicate = buildPathScene(scope, { flowPath: true, flatFlow: true, flowPositions: new Map() });
assert.equal(duplicate.visuals.filter(node => node.id === 'method::branch:1:0::exit:5:0').length, 1,
  'existing node suppresses duplicate synthetic terminal exactly as before');
assert.equal(duplicate.stubs.length, 2, 'suppressed terminal does not fabricate a linked arm');

scope.nodes.pop();
const terminal = buildPathScene(scope, { flowPath: true, flatFlow: true, flowPositions: new Map() });
assert.deepEqual(terminal.stubs.map(stub => stub.armId), ['no'], 'terminal marks only its owning arm');
assert.equal(terminal.visualEdges.at(-1).armId, 'yes');
console.log('Frontend review scene contracts PASS');
