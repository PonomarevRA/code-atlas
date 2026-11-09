import assert from 'node:assert/strict';
import { InlineMethodFacts, inlineMethodBody } from '../web/inline-method-body.js';
import { inlineFlowLayout } from '../web/inline-flow-layout.js';
import { InlineHierarchyRenderer } from '../web/inline-hierarchy-renderer.js';
import { Svg } from './svg_fixture.mjs';
import { GraphView } from '../web/graph.js';
import { curveCrossesCards } from '../web/inline-flow-edges.js';
import { RectangleIndex, cardRectangle } from '../web/rectangle-index.js';

const root={id:'ctor',label:'C()',kind:'constructor',file:'C.cs',codeProjectId:'p',callableFacts:{kind:'constructor'}};
const run={id:'run',label:'Run()',kind:'method',file:'C.cs',codeProjectId:'p'};
const field={id:'codes',label:'ClassCodePriorities',kind:'field',file:'C.cs',line:4,staticData:{kind:'dictionary',entries:[{key:'BoardCode.TQBR',value:'4',line:4}],initializer:'new()',truncated:false}};
const graph={nodes:[root,run,field],edges:[{source:'run',target:'codes',relation:'reads'}]};
const data={nodeId:'ctor',snapshotId:'s',sourceStatus:'parsed',events:[{kind:'call',callee:'Run',text:'Run()',line:3,column:4,branchIds:['yes'],arguments:[{expression:'input'}],targetNodeIds:['run']}],controlFlow:{branches:[{kind:'if',line:2,header:'input != null',arms:[{id:'yes',kind:'true'},{id:'no',kind:'false'}]}],terminals:[]}};
const runData={nodeId:'run',snapshotId:'s',sourceStatus:'parsed',events:[],controlFlow:{branches:[],terminals:[{kind:'return',text:'return codes[code]',line:5}]}};
const initial=JSON.stringify(graph), original=JSON.stringify(data);
const expandedIds=new Set(['ctor::inline-call:3:4']);
let requested=[];
const body=inlineMethodBody(root,{data},graph,{expandedIds,getFacts:id=>({data:runData}),loadFacts:id=>requested.push(id)});
assert.deepEqual(requested,['run']);
assert.ok(body.visuals.some(n=>n.label==='ClassCodePriorities'));
assert.ok(body.visuals.some(n=>n.staticValue&&n.condition==='4'));
assert.ok(body.visualEdges.some(e=>e.caption==='истина'));
assert.ok(body.content.some(n=>n.label==='иначе'),'empty alternative remains visible');
assert.ok(body.visuals.find(n=>n.id==='ctor::inline-call:3:4').bodyExpanded);
assert.equal(JSON.stringify(graph),initial);assert.equal(JSON.stringify(data),original);
const providerData={...data,callableFacts:{controlFlowSupported:false},methodFlow:{status:'parsed',steps:[],unsupported:[],truncated:false}};
const providerBody=inlineMethodBody(root,{data:providerData},graph,{expandedIds:new Set(),getFacts:()=>null,loadFacts:()=>{}});
assert.ok(!providerBody.content.some(n=>n.kind==='source-status'),'current parsed tree supersedes stale language capabilities');
assert.equal(providerBody.detached,true,'all current providers enter the same progressive method renderer');
const item={id:root.id,rawId:root.id,node:root,label:root.label,kind:'raw',children:[],expanded:true,enter:{kind:'raw'},totalChildren:0,memberIds:['ctor']};
const scene={items:[item],roots:['ctor'],edges:[],methodBodies:new Map([['ctor',body]])};
const layout=inlineFlowLayout(scene,new Map([['ctor',{x:17,y:29,width:260,height:154}]]));
assert.equal(layout.positions.get('ctor').headerBounds.x,17);assert.equal(layout.positions.get('ctor').headerBounds.y,29);
const placed=layout.methodBodies.get('ctor');
for(const node of body.content){const b=layout.positions.get('ctor').cardBounds;assert.ok(node.x+placed.x-node.w/2>=b.x);assert.ok(node.y+placed.y+node.h/2<=b.y+b.height);}
globalThis.document={createElementNS:(ns,tag)=>new Svg(tag)};globalThis.ResizeObserver=class{observe(){}};const svg=new Svg('svg');const viewport=new Svg('g');viewport.setAttribute('id','viewport');svg.append(viewport);const view=new GraphView(svg,()=>{},()=>{});const toggled=[],sources=[];
view.onToggleBlock=id=>toggled.push(id);view.onInlineSource=(...args)=>sources.push(args);view.canReadBlock=()=>true;
view.inlineHierarchyRenderer=new InlineHierarchyRenderer(view);
view.inlineSceneActive=true;
view.inlineHierarchyRenderer.render(scene,layout);
assert.ok(view.viewport.querySelectorAll('text').some(n=>String(n.textContent).includes('Конструктор')));
assert.ok(view.viewport.querySelector('.inline-method-control'));
assert.ok(Object.values(view.geometryBounds()).every(Number.isFinite),'fit includes derived body cards');
const nested=view.viewport.querySelector('.path-card-expand');nested.listeners.click({stopPropagation(){}});
assert.deepEqual(toggled,['ctor::inline-call:3:4']);
const selected=[];view.onSequenceSelect=(id,occurrenceId)=>{selected.push(id);view.setSelected(id,occurrenceId);};
const controlCard=view.viewport.querySelector('.inline-method-control');
controlCard.listeners.click({stopPropagation(){}});
assert.deepEqual(selected,['ctor']);assert.deepEqual(sources,[],'Card selects details rather than opening source');
assert.equal(controlCard.attributes['aria-pressed'],'true');
assert.equal(view.viewport.querySelectorAll('.selected').length,1,'Inline source steps sharing the same method have one selected card');
view.setSelected('ctor');
assert.equal(view.viewport.querySelectorAll('.selected').length,1,'Inspector symbol selection selects its owner rather than every inline step');
assert.equal(controlCard.attributes['aria-pressed'],'false');

let context={projectId:'p',snapshotId:'s'},changed=[],pending=[];
const cache=new InlineMethodFacts({context:()=>context,changed:id=>changed.push(id),fetcher:(url,options)=>new Promise(resolve=>pending.push({resolve,options}))});
cache.load('ctor');cache.load('ctor');assert.equal(pending.length,1);
context={projectId:'other',snapshotId:'s2'};cache.sync();pending[0].resolve({ok:true,json:async()=>data});
await new Promise(resolve=>setTimeout(resolve,0));assert.deepEqual(changed,[]);assert.equal(cache.get('ctor'),undefined);
cache.load('run');pending[1].resolve({ok:true,json:async()=>({...runData,snapshotId:'s2'})});
await new Promise(resolve=>setTimeout(resolve,0));assert.equal(cache.get('run').status,'ready');assert.deepEqual(changed,['run']);
cache.clear();
console.log('Inline method bodies, constructors, nested calls, dictionaries and stale requests PASS');
const crowded={...data,events:Array.from({length:40},(_,i)=>({kind:'call',callee:'Run',text:'Run()',line:10+i,column:1,branchIds:[],targetNodeIds:['run']})),controlFlow:{branches:[],terminals:[]}};
const crowdedCalls=new Set(crowded.events.slice(0,7).map(e=>`ctor::inline-call:${e.line}:1`));
const manyReturns={...runData,controlFlow:{branches:Array.from({length:20},(_,i)=>({kind:'if',line:100+i,condition:'x > 0',arms:[{id:`yes-${i}`,kind:'true'},{id:`no-${i}`,kind:'false'}]})),terminals:Array.from({length:12},(_,i)=>({kind:'return',text:'return x',line:60+i}))}};
const bounded=inlineMethodBody(root,{data:crowded},graph,{expandedIds:crowdedCalls,getFacts:()=>({data:manyReturns})});
assert.ok(bounded.content.length<=160);
const notice=bounded.content.find(n=>n.id==='ctor::body-limit');
assert.ok(notice,'large body has an explicit limit notice');
assert.ok(!bounded.content.some(n=>n!==notice&&n.x===notice.x&&n.y===notice.y),'limit notice replaces an omitted slot, not a visible card');

const instance={id:'instance',label:'codes',fieldFacts:{kind:'field',type:'Dictionary'},dataFacts:{kind:'dictionary',entries:[{key:'"MOEX"',value:'4'}],initializer:'new()'}};
const ordinary={id:'counter',label:'counter',fieldFacts:{kind:'field',type:'int'}};
const memberGraph={nodes:[run,instance,ordinary],edges:[{source:'run',target:'instance',relation:'reads'},{source:'run',target:'counter',relation:'reads'},{source:'run',target:'counter',relation:'writes'}]};
const members=inlineMethodBody(run,{data:runData},memberGraph);
assert.ok(members.visuals.some(n=>n.staticValue&&n.condition==='4'),'instance dictionary expands without staticData');
assert.equal(members.visuals.filter(n=>n.sourceOwnerId==='counter').length,1,'one field card retains both access edges');
assert.ok(members.visualEdges.some(e=>e.caption==='записывает поле'));
assert.ok(members.visualEdges.some(e=>e.caption==='читает поле'));
// Source-derived sequences use the same compact ports and definition targets inside ordinary class cards.
const sequenceData={...data,methodFlow:{status:'parsed',steps:[
 {kind:'call',label:'Run()',site:{line:3,column:4},line:3,column:4},
 {kind:'await',label:'Дождаться результата',line:4,column:4},
 {kind:'return',label:'return result',line:5,column:4}
]}};
const sequenceRun={...runData,line:42,methodFlow:{status:'parsed',steps:[{kind:'return',label:'return 1',line:43,column:4}]}};
const sequenceBody=inlineMethodBody(root,{data:sequenceData},graph,{expandedIds,getFacts:()=>({data:sequenceRun}),loadFacts:()=>{}});
assert.ok(sequenceBody.sequence);assert.equal(sequenceBody.visuals.find(n=>n.kind==='await').h,54);
const sequenceScene={...scene,methodBodies:new Map([['ctor',sequenceBody]])};
const sequenceLayout=inlineFlowLayout(sequenceScene,new Map([['ctor',{x:17,y:29,width:260,height:154}]]));
new InlineHierarchyRenderer(view).render(sequenceScene,sequenceLayout);
const sequenceDefinition=view.viewport.querySelectorAll('text').find(n=>n.attributes['data-definition-id']==='run');
assert.ok(sequenceDefinition);sequenceDefinition.listeners.click({stopPropagation(){},preventDefault(){}});
assert.deepEqual(sources.at(-1),['run',42]);
const sequencePlacement=sequenceLayout.methodBodies.get('ctor'),sequenceBounds=sequenceLayout.bounds;
assert.deepEqual(sequenceLayout.positions.get('ctor').headerBounds,{x:17,y:29,width:260,height:154});
for(const node of sequenceBody.content) {
 assert.ok(node.y+sequencePlacement.y-node.h/2>=sequenceBounds.y);
 assert.ok(node.y+sequencePlacement.y+node.h/2<=sequenceBounds.y+sequenceBounds.height);
}
console.log('Inline compact transitions and callee definitions PASS');

// The same explicit values and flat numbers remain clickable inside the ordinary class map.
const valueData={...runData,methodFlow:{status:'parsed',steps:[{kind:'branch',controlKind:'?.',label:'result != null',line:44,column:1,
 arms:[{label:'значение задано',steps:[{kind:'value',label:'Взять значение',expression:'result.Security',line:45,column:1}]},
 {label:'null',steps:[{kind:'value',label:'Значение',expression:'null',line:45,column:1}]}]}]}};
const valueBody=inlineMethodBody(root,{data:valueData},graph);
const valueScene={...scene,methodBodies:new Map([['ctor',valueBody]])};
const valueLayout=inlineFlowLayout(valueScene,new Map([['ctor',{x:17,y:29,width:260,height:154}]]));
new InlineHierarchyRenderer(view).render(valueScene,valueLayout);
assert.equal(view.viewport.querySelectorAll('[data-transition-kind="empty"]').length,0);
const valueCards=valueBody.visuals.filter(n=>n.valueChoices);
assert.deepEqual(valueCards[0].valueChoices.map(n=>n.expression),['result.Security','null']);
const valueSvg=view.nodeElements.get(valueCards[0].id).group;
valueSvg.children.find(n=>n.attributes['data-value-choice']===0).listeners.click({stopPropagation(){},preventDefault(){}});
assert.deepEqual(sources.at(-1),['ctor',45]);
assert.ok(valueSvg.querySelectorAll('text').some(n=>String(n.textContent).includes('result.Security')));
assert.equal(valueSvg.attributes['data-step-number'],'1');
console.log('Inline explicit values, source actions and flat numbering PASS');

const rowData={...runData,methodFlow:{status:'parsed',steps:[{kind:'call',label:'Run()',site:{line:3,column:4},line:3,column:4},
 {kind:'operation',label:'result = value',line:4,column:1},{kind:'return',label:'return result;',line:5,column:1}]},events:data.events};
const rowBody=inlineMethodBody(root,{data:rowData},graph,{getFacts:()=>({data:sequenceRun})});
const rowScene={...scene,methodBodies:new Map([['ctor',rowBody]])};
const rowLayout=inlineFlowLayout(rowScene,new Map([['ctor',{x:17,y:29,width:260,height:154}]]));
new InlineHierarchyRenderer(view).render(rowScene,rowLayout);
assert.equal(view.viewport.querySelectorAll('.method-sequence-code-block').length,1);
assert.equal(view.viewport.querySelectorAll('.method-sequence-row').length,3);
const rowNode=rowBody.visuals.find(n=>n.kind==='operation'), rowSvg=view.nodeElements.get(rowNode.id).group;
rowSvg.listeners.click({stopPropagation(){}});assert.equal(selected.at(-1),'ctor');
rowSvg.querySelector('.path-card-source').listeners.click({stopPropagation(){}});assert.deepEqual(sources.at(-1),['ctor',4]);
assert.ok(!view.viewport.querySelectorAll('text').some(n=>/^Шаг /.test(String(n.textContent))));
console.log('Inline source-like rows and precise row navigation PASS');

// An unrelated class dependency must go around the whole expanded method, not through its body.
const outsideScene={items:['a','b','wide'].map(id=>({id,rawId:id,kind:'raw',label:id,node:{id,label:id},children:[],expanded:id==='wide'})),
 roots:['a','b','wide'],edges:[{source:'a',target:'b',relation:'reads'}],membership:new Map()};
const box=(x,y,width,height)=>({x,y,width,height});
const outsideLayout={positions:new Map([
 ['a',{x:130,y:357,headerBounds:box(0,280,260,154),cardBounds:box(0,280,260,154)}],
 ['b',{x:1030,y:357,headerBounds:box(900,280,260,154),cardBounds:box(900,280,260,154)}],
 ['wide',{x:480,y:77,headerBounds:box(350,0,260,154),cardBounds:box(350,0,420,700)}]
]),frames:[],bounds:box(0,0,1160,700)};
new InlineHierarchyRenderer(view).render(outsideScene,outsideLayout);
const outside=view.viewport.querySelector('.inline-hierarchy-edge');
assert.ok(outside.attributes.d);
assert.ok(!curveCrossesCards(outside.attributes.d,new RectangleIndex([{x:560,y:350,w:420,h:700}],cardRectangle)));
assert.equal(outside.attributes['data-relation'],'reads');
assert.equal(outside.attributes.opacity,'.5');
console.log('Outer references avoid full expanded bodies while preserving data evidence PASS');
