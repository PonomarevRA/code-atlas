/** A selected health mode cannot accidentally display a legacy complexity number. */
import assert from 'node:assert/strict';
import {Svg} from './svg_fixture.mjs';
import {appendComplexityBadge} from '../web/complexity-badge.js';
let mode='health';
globalThis.document={createElementNS:(_,tag)=>new Svg(tag),getElementById:()=>({value:mode})};
const metrics={score:14,status:'complete',version:'v1',maxNestingDepth:3,branchingCount:11,loopCount:0};
const group=new Svg('g');
appendComplexityBadge(group,metrics,10,20);
assert.equal(group.querySelector('.code-health-badge').attributes['data-health-score'],'');
assert.match(group.textContent,/неизвестно/);
const healthy=new Svg('g');
appendComplexityBadge(healthy,metrics,10,20,{score:95,documentationScore:20,status:'complete',version:'atlas-health-v1',verdict:'PASS'});
assert.equal(healthy.querySelector('circle').attributes.fill,'#327f70');
mode='documentation';
const docs=new Svg('g');
appendComplexityBadge(docs,metrics,10,20,{score:95,documentationScore:20,status:'complete',version:'atlas-health-v1'});
assert.equal(docs.querySelector('circle').attributes.fill,'#b13e43');
assert.equal(docs.querySelector('.code-health-badge').attributes['data-health-metric'],'documentation');
mode='complexity';
const legacy=new Svg('g'); appendComplexityBadge(legacy,metrics,10,20);
assert.equal(legacy.querySelector('.code-complexity-badge').attributes['data-complexity-score'],14);
console.log('PASS shared H/D/complexity markers and legacy uncertainty');

// Embedded/saved diagrams without the selector must retain complexity even when health facts exist.
globalThis.document.getElementById=()=>null;
const standalone=new Svg('g');
appendComplexityBadge(standalone,metrics,10,20,{score:95,documentationScore:20,status:'complete'});
assert.equal(standalone.querySelector('.code-complexity-badge').attributes['data-complexity-score'],14);
assert.ok(!standalone.querySelector('.code-health-badge'));
assert.equal(standalone.querySelector('circle').attributes.fill,'#b13e43');
console.log('PASS default structural complexity and explicit metric identities');
