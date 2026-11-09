import assert from 'node:assert/strict';
import { Svg } from './svg_fixture.mjs';
import { renderNodeHealth } from '../web/node-health-panel.js';
import { renderInspector } from '../web/inspector.js';

const healthPanel=new Svg('div');healthPanel.dataset={};
const selection=new Svg('div');
globalThis.document={createElement:tag=>{
 const node=new Svg(tag);
 Object.defineProperty(node,'className',{set(value){this.setAttribute('class',value);}});
 return node;
},getElementById:id=>id==='selection'?selection:null};
const node={id:'a & b',label:'<unsafe>()',file:'src/a b.cs',health:{score:0,structuralScore:0,
 documentationScore:0,status:'partial',findings:Array.from({length:12},(_,i)=>({message:`Finding ${i}`}))},
 documentationHealth:{documented:false},complexity:{score:0,status:'complete',version:'v1',maxNestingDepth:0,branchingCount:0,loopCount:0}};
renderNodeHealth(healthPanel,node,'repo');
assert.equal(healthPanel.dataset.nodeId,node.id);
assert.equal(healthPanel.querySelector('h3').textContent,'<unsafe>()');
assert.equal(healthPanel.querySelectorAll('dd')[0].textContent,'0 / 100');
assert.equal(healthPanel.querySelectorAll('dd')[3].textContent,'? / 100');
assert.ok(healthPanel.textContent.includes('Описание метода отсутствует'));
assert.ok(healthPanel.textContent.includes('Finding 7'));
assert.ok(!healthPanel.textContent.includes('Finding 8'));
const compact = healthPanel.querySelector('.complexity-summary');
assert.equal(compact.querySelector('.complexity-summary-score').textContent, '0');
assert.equal(compact.querySelector('.complexity-summary-score').attributes['data-color'], '#327f70');
assert.equal(compact.querySelector('.complexity-summary-score').attributes.style, undefined, 'Strict CSP forbids inline style attributes');
assert.equal(compact.querySelector('details').open, undefined, 'The long explanation starts collapsed');
assert.equal(compact.querySelector('summary').attributes.tabindex,'0','The disclosure is keyboard-focusable in WebKit too');
assert.ok(compact.querySelector('details').textContent.includes('Балл = ветвления'));
assert.deepEqual(compact.querySelectorAll('dd').map(item=>item.textContent), ['0','?','0','0']);
renderNodeHealth(healthPanel,{...node,complexity:{...node.complexity,score:16,status:'partial',averageNestingDepth:1.73}},'repo');
assert.equal(healthPanel.querySelector('.complexity-summary-score').textContent,'≥16');
assert.ok(healthPanel.querySelector('.complexity-summary').textContent.includes('Неполный анализ'));
assert.ok(healthPanel.querySelector('.complexity-summary').querySelectorAll('dd').some(item=>item.textContent==='1,73'));
const url=new URL(healthPanel.querySelector('a').href,'http://localhost');
assert.equal(url.searchParams.get('node'),node.id);assert.equal(url.searchParams.get('file'),node.file);
renderInspector({selected:node.id,graph:{}},{nodes:new Map([[node.id,node]]),incident:()=>[]},[],null,()=>{});
assert.ok(!selection.textContent.includes('Finding 0'),'Details no longer duplicates health recommendations');
renderNodeHealth(healthPanel,{id:'next',label:'Other()'},'other-repo');
assert.equal(healthPanel.dataset.nodeId,'next');assert.ok(!healthPanel.textContent.includes('Finding'));
assert.ok(healthPanel.textContent.includes('не оценено'));assert.equal(healthPanel.querySelectorAll('dd').length,0);
assert.equal(healthPanel.querySelector('.complexity-summary-score').textContent,'?');
renderNodeHealth(healthPanel,null,'other-repo');
assert.equal(healthPanel.dataset.nodeId,'');assert.equal(healthPanel.querySelector('a'),undefined);
assert.ok(healthPanel.textContent.includes('Выберите карточку'));
console.log('Selected-card Health: unknown/zero, safe text, bounded findings, scope clearing and separate Details PASS');

renderNodeHealth(healthPanel,{id:'partial',health:{score:null,status:'partial',organizationScore:null,
  components:{flow:100,size:95,dependencies:null},organization:{coverage:{local:{status:'complete'},
  outgoing:{status:'partial',reason:'unresolved-source-dependencies'}}}}},'repo');
assert.match(healthPanel.textContent,/поток 100 \/ 100; размер 95 \/ 100/);
assert.match(healthPanel.textContent,/не разрешены вызовы источника/);
assert.ok(!healthPanel.textContent.includes('PASS'));
renderNodeHealth(healthPanel,{id:'compact',health:{score:null,status:'partial',unknownReasons:['dependencies:unresolved-calls']}},'repo');
assert.match(healthPanel.textContent,/dependencies:unresolved-calls/);
