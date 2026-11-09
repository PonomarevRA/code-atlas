import assert from 'node:assert/strict';
import { Svg } from './svg_fixture.mjs';
import { renderReadability } from '../web/health-readability.js';
import { appendHealthBadge, nodeHealthText } from '../web/health-badge.js';

globalThis.document = {createElement: tag => new Svg(tag), createElementNS: (_,tag) => new Svg(tag)};
const profile = {version:'atlas-readability-v1',status:'complete',score:88,verdict:'REVIEW',
  penalties:[{rule:'nesting',observed:3,free:2,penalty:12}],
  halstead:{version:'atlas-halstead-v1',status:'complete',volume:8,difficulty:1,effort:8},
  unverifiedDimensions:['design','naming','correctness']};
const health = {readabilityScore:88,readability:profile,verdict:'PASS',version:'atlas-health-v2'};
const host = new Svg('div');
renderReadability(host,health);
assert.match(host.textContent,/88 \/ 100/);
assert.match(host.textContent,/3, ориентир 2, штраф 12/);
assert.match(host.textContent,/не означает секунды/);
assert.match(host.textContent,/Смысловое ревью не выполнено/);
assert.match(host.textContent,/смысл имён/);
assert.match(host.textContent,/корректность/);

const badge = new Svg('g');
appendHealthBadge(badge, health,0,0,'readability');
assert.equal(badge.querySelector('circle').attributes.fill,'#986718');
assert.match(nodeHealthText(health,'readability'),/atlas-readability-v1/);
assert.match(nodeHealthText({...health,status:'partial'},'readability'),/Статус: complete/);
assert.ok(!nodeHealthText(health,'readability').includes('Политика: atlas-health-v2'));
const unknown = new Svg('g');
appendHealthBadge(unknown,{readabilityScore:100},0,0,'readability');
assert.equal(unknown.querySelector('.code-health-badge').attributes['data-health-score'],'');
assert.equal(unknown.querySelector('circle').attributes.fill,'#7b8784');
const legacy = new Svg('div');renderReadability(legacy,{});
assert.match(legacy.textContent,/не измерена в этом снимке/);
const untrusted = new Svg('div');
renderReadability(untrusted,{readability:{...profile,unverifiedDimensions:['<img src=x onerror=alert(1)>']}});
assert.ok(untrusted.textContent.includes('<img src=x onerror=alert(1)>'));
assert.equal(untrusted.querySelector('img'),undefined);
console.log('Readability: explainable penalties, R identity, legacy unknown, review limits and safe DOM PASS');
