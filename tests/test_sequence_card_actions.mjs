import assert from 'node:assert/strict';
import { Svg } from './svg_fixture.mjs';
import { GraphView } from '../web/graph.js';
import { buildMethodSequence } from '../web/method-sequence.js';
import { buildProgressiveMethodScene } from '../web/progressive-method-scene.js';
import { sequenceInspectionId } from '../web/sequence-card-interactions.js';
import { isolateCallChain } from '../web/call-isolation.js';
import { appendValueChoice, appendLoopUpdate, appendLoopControl } from '../web/sequence-card-symbols.js';

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };
const child = { id:'child',label:'LoadAsync()',file:'Loader.cs',line:30,methodFlow:{status:'parsed',steps:[{kind:'return',label:'return data;',line:31,column:1}]} };
const leaf = { id:'leaf',label:'ReadAsync()',file:'Data.cs' };
const root = { id:'root',label:'Root()',file:'Root.cs',line:1,
  methodFlow:{status:'parsed',steps:[{kind:'loop',controlKind:'for',label:'for (int i = 0; i < active.Length; i++)',line:10,column:1,
    condition:[],update:[{kind:'operation',label:'i++',line:10,column:45}],body:[
      {kind:'call',label:'LoadAsync()',line:11,column:1,site:{line:11,column:1}},
      {kind:'branch',controlKind:'if',label:'stop',line:12,column:1,arms:[
        {label:'истина',steps:[{kind:'break',label:'break;',line:13,column:1}]},
        {label:'иначе',steps:[{kind:'continue',label:'continue;',line:14,column:1}]}]}]}]},
  events:[{text:'LoadAsync()',callee:'LoadAsync',line:11,column:1,targetNodeIds:['child']}] };
const graph={nodes:[root,child,leaf],edges:[{source:'root',target:'child',relation:'calls'},{source:'child',target:'leaf',relation:'calls'}]};
const original=JSON.stringify(graph), scene=buildMethodSequence(graph,'root');
const update=scene.visuals.find(n=>n.compactLoopUpdate), loop=scene.visuals.find(n=>n.kind==='loop');
assert.equal(update.label,'i++');assert.equal(update.h,56);assert.equal(loop.h,112);
assert.ok(scene.visualEdges.some(e=>e.sequenceKind==='continue'&&e.target===update.id));
assert.ok(scene.visualEdges.some(e=>e.source===update.id&&e.target===loop.id&&e.sequenceKind==='loop'));
assert.ok(scene.visualEdges.some(e=>e.sequenceKind==='break'&&e.target===loop.id+'::join'));
assert.equal(JSON.stringify(graph),original,'Compact updates preserve the captured graph');
const belowLoop=scene.byId.get(loop.id+'::join');
assert.equal(belowLoop.x,loop.x);assert.ok(belowLoop.y>update.y+update.h/2,'Loop exit sits below its body and update');
for(const centered of [false,true]) {
 const group=new Svg('g');appendLoopUpdate(group,update,centered);
 const input=group.querySelectorAll('circle').find(n=>n.attributes['data-port']==='in');
 assert.equal(input.attributes.cx,centered?0:update.w/2);assert.equal(input.attributes.cy,centered?-update.h/2:0);
 const guardGroup=new Svg('g');appendLoopControl(guardGroup,loop,centered);
 const repeat=guardGroup.querySelectorAll('circle').find(n=>n.attributes['data-port']==='repeat');
 const exit=guardGroup.querySelectorAll('circle').find(n=>n.attributes['data-port']==='exit');
 assert.equal(repeat.attributes.cx-exit.attributes.cx,loop.w/4);assert.equal(repeat.attributes.cy,exit.attributes.cy);
 const valueGroup=new Svg('g'),values={w:300,h:128,label:'choice',condition:'item != null',line:12,sourceOwnerId:'root',
  valueChoices:[{label:'задано',expression:'item.Value',line:12},{label:'null',expression:'null',line:12}]};
 appendValueChoice({bindPathAction(){}},valueGroup,values,centered);
 const divider=valueGroup.querySelector('.method-value-divider');assert.ok(divider);
 assert.equal(divider.attributes.y1,divider.attributes.y2);
 const outputs=valueGroup.querySelectorAll('circle').filter(n=>String(n.attributes['data-port']).startsWith('out-'));
 assert.ok(divider.attributes.y1>outputs[0].attributes.cy && divider.attributes.y1<outputs[1].attributes.cy);
}

const complex={...root,methodFlow:{status:'parsed',steps:[{...root.methodFlow.steps[0],
  update:[{kind:'call',label:'LoadAsync()',line:11,column:1,site:{line:11,column:1}}]}]}};
assert.ok(!buildMethodSequence({nodes:[complex,child],edges:graph.edges},'root').visuals.some(n=>n.compactLoopUpdate),
  'Calls in an update keep their full expansion and source actions');
assert.equal(sequenceInspectionId({kind:'call',sourceOwnerId:'root',definitionId:'iface',readTargetIds:['child','leaf']}),'iface');

for(const provider of ['natural','graphistry']) {
  const svg=new Svg('svg'), viewport=new Svg('g');viewport.setAttribute('id','viewport');svg.append(viewport);
  const selections=[],sources=[],view=new GraphView(svg,id=>selections.push(id),()=>{});
  view.groupingProvider=provider;view.displayProvider='flow';view.pathRootId='root';view.pathSnapshotId='s';
  view.onPathSource=(...args)=>sources.push(args);
  view.onSequenceSelect=(id,occurrenceId)=>{selections.push(id);view.setSelected(id,occurrenceId);};
  view.renderPathCards(graph.nodes,graph.edges,'root');
  const selectedCards=()=>[...view.nodeElements].filter(([,item])=>item.group.classList.contains('selected')).map(([id])=>id);
  assert.deepEqual(selectedCards(),['root'],'Selecting the method must not select every source step');
  const initial=[...view.positions].map(([id,p])=>[id,p.x,p.y]),camera={...view.camera};
  const call=view.pathSequenceScene.visuals.find(n=>n.kind==='call');
  const card=view.nodeElements.get(call.id).group;
  card.listeners.click({stopPropagation(){}}); assert.equal(selections.at(-1),'child');assert.deepEqual(sources,[]);
  assert.deepEqual(selectedCards(),[call.id]);
  card.listeners.keydown({key:'Enter',stopPropagation(){},preventDefault(){}});assert.equal(selections.at(-1),'child');
  assert.deepEqual(view.camera,camera);assert.deepEqual([...view.positions].map(([id,p])=>[id,p.x,p.y]),initial);
  const isolation=isolateCallChain(graph,selections.at(-1));
  assert.deepEqual([...isolation.allNodeIds],['child','leaf'],'Inspector selection anchors the callee chain');
  card.querySelector('.path-card-source').listeners.click({stopPropagation(){}});assert.deepEqual(sources.at(-1),['child',30]);
  assert.ok(view.viewport.querySelector('.method-loop-update'));
  const updateCard=view.nodeElements.get(update.id).group;
  updateCard.listeners.click({stopPropagation(){}});assert.equal(selections.at(-1),'root');
  assert.deepEqual(selectedCards(),[update.id],'A source step keeps the real inspector target but selects only its own occurrence');
  assert.equal(updateCard.attributes['aria-pressed'],'true');
  view.setSelected('root');assert.deepEqual(selectedCards(),['root'],'External symbol selection clears the step occurrence');
  view.setSelected(null);assert.deepEqual(selectedCards(),[]);
  updateCard.querySelector('.path-card-source').listeners.click({stopPropagation(){}});assert.deepEqual(sources.at(-1),['root',10]);
}

const iface={id:'iface',label:'ILoader.LoadAsync()',file:'ILoader.cs'};
const variants={...root,events:[{...root.events[0],targetNodeIds:['iface']}]};
const dispatchGraph={nodes:[variants,iface,child,leaf],edges:[{source:'root',target:'iface',relation:'calls'},
  {source:'iface',target:'child',relation:'dispatches_to'},{source:'iface',target:'leaf',relation:'dispatches_to'}]};
const choice=buildProgressiveMethodScene(dispatchGraph,'root',{expandedIds:new Set(['root::inline-call:11:1'])});
for(const candidate of choice.visuals.filter(n=>n.kind==='candidate')) {
  assert.equal(sequenceInspectionId(candidate),candidate.readTargetId);
  assert.ok(dispatchGraph.nodes.some(n=>n.id===sequenceInspectionId(candidate)),'Candidate inspection never uses a derived occurrence ID');
}
console.log('Sequence card selection, exact source actions, compact loop updates and isolation PASS');
