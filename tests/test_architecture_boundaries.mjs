/** Контракты декомпозиции: исходные свидетельства, ограниченные запросы и семантика отношений. */
import assert from 'node:assert/strict';
import { projectExecutionPath } from '../web/path-graph-projection.js';
import { presentRelation } from '../web/relation-presentation.js';
import { requestJson } from '../web/bounded-request.js';
const nodes = new Map([['root',{id:'root',label:'Root',file:'Root.cs',codeProjectId:'App'}],['impl',{id:'impl',label:'Impl',file:'Impl.cs'}]]);
const data = {rootNodeId:'root',snapshotId:'s',steps:[{nodeId:'root',controlFlow:{branches:[]},events:[{kind:'call',line:4}]},{nodeId:'impl'},{nodeId:'unknown'},{nodeId:'root'}],edges:[
  {source:'root',target:'impl',relation:'dispatches_to',confidence:'candidate',sourceFile:'Root.cs',sourceLine:4,evidence:{why:'DI'}},
  {source:'root',target:'impl',relation:'calls',sourceLine:7},
  {source:'impl',target:'unknown',relation:'calls'},
]};
const original=JSON.stringify(data), projected=projectExecutionPath(data,nodes,{id:'post',httpMethod:'POST',route:'/calculate',evidence:[{file:'Api.cs'}]});
assert.deepEqual(projected.nodes.map(n=>n.id),['path-entry:post','root','impl']);
assert.equal(projected.edges.length,3,'duplicate transitions retained; missing endpoint excluded');
assert.equal(projected.edges[1].confidence,'candidate');
assert.equal(projected.edges[1].source_file,'Root.cs');
assert.equal(projected.edges[1].sourceLine,4);
assert.deepEqual(projected.edges[1].evidence,{why:'DI'});
assert.equal(projected.nodes[1].events,data.steps[0].events);
assert.equal(JSON.stringify(data),original,'projection must not alter pinned input');
for (const edge of [{confidence:'INFERRED'},{graphConfidence:'INFERRED'},{confidence:'inferred'}]) assert.equal(presentRelation({...edge,relation:'references'}).qualifier,'предположение анализа');
assert.equal(presentRelation({relation:'dispatches_to',confidence:'EXACT'}).qualifier,'кандидат');
assert.equal(presentRelation({relation:'calls',confidence:.7}).qualifier,'уверенность: 0.7');
assert.equal(presentRelation({relation:'method'}).group,'ownership');
assert.equal(presentRelation({relation:'has_attribute'}).group,'attributes');
let signal;
await assert.rejects(requestJson('/pending',{}, {timeoutMs:5,fetcher:(_,options)=>{signal=options.signal;return new Promise(()=>{});}}), e=>e.name==='TimeoutError');
assert.equal(signal.aborted,true,'timeout aborts underlying fetch even when transport ignores it');
await assert.rejects(requestJson('/pending-json',{}, {timeoutMs:5,fetcher:async()=>({ok:true,json:()=>new Promise(()=>{})})}),e=>e.name==='TimeoutError');
const cancel=new AbortController();
const pending=requestJson('/cancel',{signal:cancel.signal},{timeoutMs:1000,fetcher:()=>new Promise(()=>{})});
cancel.abort();await assert.rejects(pending,e=>e.name==='AbortError');
await assert.rejects(requestJson('/failure',{}, {fetcher:async()=>({ok:false,status:409,json:async()=>({error:'Снимок изменился'})})}),e=>e.status===409);
assert.deepEqual(await requestJson('/ready',{}, {fetcher:async()=>({ok:true,json:async()=>({snapshotId:'s'})})}),{snapshotId:'s'});
console.log('Architecture boundaries PASS');
