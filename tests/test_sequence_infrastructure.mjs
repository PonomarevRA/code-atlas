import assert from 'node:assert/strict';
import {projectExecutionPath} from '../web/path-graph-projection.js';
import {buildMethodSequence} from '../web/method-sequence.js';
import {infrastructureCard} from '../web/sequence-infrastructure.js';

const fact={id:'infra:1',kind:'kafka',resourceKind:'topic',operation:'publishes',name:'assets',status:'configured',
  file:'Sender.cs',line:3,provider:'infrastructure-v1',basis:'library-call',configurationBindingKnown:true,
  configurationCandidates:[{name:'assets',file:'appsettings.json',line:2,bindingFile:'Registration.cs',bindingLine:5}]};
const method={nodeId:'root',file:'Sender.cs',line:1,kind:'method',sourceStatus:'parsed',events:[],
  methodFlow:{status:'parsed',steps:[{kind:'call',label:'producer.Send()',line:3,column:1}]},infrastructure:[fact]};
const original={id:'root',label:'Send()',file:'Sender.cs'};
const graph=projectExecutionPath({rootNodeId:'root',steps:[method],edges:[]},new Map([['root',original]]));
assert.equal(graph.nodes[0].infrastructure[0],fact);
assert.equal(original.infrastructure,undefined);
const scene=buildMethodSequence(graph,'root');
const resource=scene.visuals.find(n=>n.kind==='external-resource');
assert.ok(resource);
assert.equal(resource.sourceOwnerId,'root');
assert.equal(resource.line,3);
assert.equal(resource.infrastructureFact,fact);
assert.ok(resource.infrastructureEvidence.includes('Registration.cs:5'));
const link=scene.visualEdges.find(e=>e.target===resource.id);
assert.equal(link.sequenceKind,'data');
assert.ok(link.caption.includes('статическая'));
const presentation=infrastructureCard({...fact,status:'unknown',name:null,configurationBindingKnown:false});
assert.ok(presentation.label.includes('?'));
assert.ok(presentation.context.includes('привязка не установлена'));
assert.ok(presentation.evidence.includes('runtime-использование неизвестны'));
const many=Array.from({length:16},(_,i)=>({...fact,id:`infra:${i}`}));
const limited=buildMethodSequence({nodes:[{...original,...method,id:'root',infrastructure:many,infrastructureTotal:42}],edges:[]},'root');
assert.equal(limited.visuals.filter(n=>n.kind==='external-resource').length,12);
const frame=limited.layout.frames.find(f=>f.id==='root') || limited.layout.frames[0];
assert.equal(frame.infrastructureSection.total,42);
assert.equal(frame.infrastructureSection.shown,12);
for(const card of limited.visuals.filter(n=>n.kind==='external-resource')) assert.ok(card.y+card.h/2<=frame.y+frame.h);
console.log('Infrastructure projection, owner/source actions, non-control edges and explicit limits PASS');
