import assert from 'node:assert/strict';
import { buildMethodSequence } from '../web/method-sequence.js';
import { sequenceEdgeRouter, includeRouteBounds } from '../web/method-sequence-routing.js';
import { curveCrossesCards } from '../web/inline-flow-edges.js';
import { RectangleIndex, cardRectangle } from '../web/rectangle-index.js';
import { internalBlockEdge } from '../web/method-sequence-blocks.js';

const operation=(label,line)=>({kind:'operation',label,line,column:1});
const branch=(line,steps)=>({kind:'branch',controlKind:'if',label:'condition',line,column:1,
 arms:[{label:'истина',steps},{label:'иначе',steps:[],emptyReason:'skip'}]});
const loop={kind:'loop',controlKind:'foreach',label:'foreach item',line:1,column:1,condition:[],update:[],body:[
 branch(2,[{kind:'continue',label:'continue;',line:3,column:1}]),
 operation('Work(item)',4),branch(5,[{kind:'break',label:'break;',line:6,column:1}]),
 {kind:'loop',controlKind:'while',label:'while ready',line:7,column:1,condition:[],update:[],body:[
  operation('Read()',8),branch(9,[{kind:'continue',label:'continue;',line:10,column:1}]),operation('Update()',11)]}
]};
const root={id:'root',label:'Run()',file:'C.cs',methodFlow:{status:'parsed',steps:[loop,
 branch(20,[{kind:'return',label:'return 0;',line:21,column:1}]),
 branch(22,[{kind:'return',label:'return 1;',line:23,column:1}]),
 operation('After()',24),{kind:'return',label:'return result;',line:25,column:1}]},events:[]};
const field={id:'field',label:'codes',fieldFacts:{kind:'field',type:'Dictionary'}};
const graph={nodes:[root,field],edges:[{source:'root',target:'field',relation:'reads'}]}, before=JSON.stringify(graph);
const scene=buildMethodSequence(graph,'root');
assert.equal(JSON.stringify(graph),before);
assert.equal(scene.layout.controlScopes.filter(s=>s.loop).length,2);
const route=sequenceEdgeRouter(scene), cards=new RectangleIndex(scene.positions.values(),cardRectangle);
const visible=scene.visualEdges.filter(e=>!internalBlockEdge(scene.byId.get(e.source),scene.byId.get(e.target),e));
let count=0;
for(const edge of visible) {
 const routed=route(edge);
 assert.ok(routed.d,`missing route ${edge.sequenceKind}: ${edge.source} -> ${edge.target}`);
 assert.ok(!curveCrossesCards(routed.d,cards),`route cuts a card: ${edge.sequenceKind}`);
 assert.ok(routed.d.includes('C'),'detours stay smooth');
 if(['return','continue'].includes(edge.sequenceKind)) assert.equal(routed.caption,null);
 if(edge.sequenceKind==='data') assert.ok(routed.data);
 count++;
}
assert.ok(count>20);
assert.ok(Object.values(route.bounds()).every(Number.isFinite));

// Moving an inline scene does not move its lexical loop/return corridors into other rows.
const dx=400,dy=800, moved=new Map([...scene.positions].map(([id,p])=>[id,{...p,x:p.x+dx,y:p.y+dy}]));
const movedRoute=sequenceEdgeRouter(scene,moved,{dx,dy}), movedCards=new RectangleIndex(moved.values(),cardRectangle);
for(const edge of visible) assert.ok(!curveCrossesCards(movedRoute(edge).d,movedCards));

// For-continue reaches its update, empty loops retain real back edges, and all paths include endpoints.
for(const controlKind of ['for','do','foreach']) {
 const source={...root,methodFlow:{status:'parsed',steps:[{kind:'loop',controlKind,line:1,column:1,label:controlKind,
  condition:[],update:controlKind==='for'?[operation('i++',4)]:[],body:[{kind:'continue',label:'continue',line:2,column:1}]},
 {kind:'return',label:'return;',line:5,column:1}]}};
 const local=buildMethodSequence({nodes:[source],edges:[]},'root'), router=sequenceEdgeRouter(local), index=new RectangleIndex(local.positions.values(),cardRectangle);
 for(const edge of local.visualEdges) if(!internalBlockEdge(local.byId.get(edge.source),local.byId.get(edge.target),edge)) {
  const d=router(edge).d;
  assert.ok(d);assert.ok(!curveCrossesCards(d,index),controlKind);
  const coordinates=d.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);
  const target=local.byId.get(edge.target),sourceNode=local.byId.get(edge.source);
  if(target.compactLoopUpdate) assert.deepEqual(coordinates.slice(-2),[target.x,target.y-target.h/2],'Update feedback attaches to the visible TOP input');
  if(['loop','continue'].includes(edge.sequenceKind)&&target.kind==='loop') {
   assert.deepEqual(coordinates.slice(-2),[target.x+target.w/4,target.y+target.h/2],'Repeat uses its own bottom-right loop input');
  }
  if(edge.loopExit) {
   assert.equal(target.x,sourceNode.x);assert.ok(target.y>sourceNode.y+sourceNode.h/2);
   assert.deepEqual(coordinates.slice(0,2),[sourceNode.x,sourceNode.y+sourceNode.h/2]);
   assert.deepEqual(coordinates.slice(-2),[target.x,target.y-target.h/2]);
  }
 }
 const translated=new Map([...local.positions].map(([id,p])=>[id,{...p,x:p.x+dx,y:p.y+dy}]));
 const translatedRouter=sequenceEdgeRouter(local,translated,{dx,dy});
 for(const edge of local.visualEdges) if(local.byId.get(edge.target).compactLoopUpdate) {
  const coordinates=translatedRouter(edge).d.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number),target=translated.get(edge.target);
  assert.deepEqual(coordinates.slice(-2),[target.x,target.y-target.h/2]);
 }
}
const expanded=includeRouteBounds({x:0,y:0,width:10,height:10},'M -30 4 C -30 -40 20 -40 20 4');
assert.ok(expanded.x<=-30&&expanded.y<=-40&&expanded.x+expanded.width>=20);
console.log(`Sequence corridors: ${count} obstacle-free edges, nested loops, returns, data, translated inline scenes and bounds PASS`);

// A long true arm starts beside its predicate and merges below, never back up/right.
for (const localReading of [false, true]) {
 const call=(label,line)=>({kind:'call',label,line,column:1,site:{line,column:1}});
 const source={id:'root',label:'Run()',file:'C.cs',events:[],methodFlow:{status:'parsed',steps:[
  branch(1,[call('First()',2),call('Second()',3),call('Third()',4)]),call('After()',5)]}};
 const original=JSON.stringify(source);
 const local=buildMethodSequence({nodes:[source],edges:[]},'root',{localReading});
 assert.equal(JSON.stringify(source),original);
 const predicate=local.visuals.find(n=>n.kind==='branch');
 const join=local.visuals.find(n=>n.kind==='join'&&n.controlId===predicate.id);
 const arm=local.visuals.filter(n=>n.line>=2&&n.line<=4&&!n.transition);
 assert.equal(predicate.y-predicate.h/2,Math.min(...arm.map(n=>n.y-n.h/2)));
 assert.equal(join.x,predicate.x);
 assert.ok(join.y-join.h/2>Math.max(...arm.map(n=>n.y+n.h/2)));
 if(localReading) {
  const after=local.visuals.find(n=>n.line===5);
  assert.equal(after.x,join.x);assert.ok(after.y-after.h/2>join.y+join.h/2);
 }
 const router=sequenceEdgeRouter(local),index=new RectangleIndex(local.positions.values(),cardRectangle);
 for(const edge of local.visualEdges) if(!internalBlockEdge(local.byId.get(edge.source),local.byId.get(edge.target),edge)) {
  const result=router(edge);
  assert.ok(!curveCrossesCards(result.d,index));
  if(edge.bypass) {
   const xy=result.d.match(/-?\d+(?:\.\d+)?/g).map(Number);
   assert.deepEqual(xy.slice(0,2),[predicate.x,predicate.y+predicate.h/2]);
   assert.deepEqual(xy.slice(-2),[join.x,join.y-join.h/2]);
  }
 }
}
console.log('Long branch top alignment, downward merges, unchanged facts and clear skip inputs PASS');
