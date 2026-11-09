import assert from 'node:assert/strict';
import {buildMethodSequence} from '../web/method-sequence.js';
import {GraphView} from '../web/graph.js';
import {Svg} from './svg_fixture.mjs';
import {sequenceCurve, sequenceCaption} from '../web/method-sequence-renderer.js';
import {compactLinearCells, internalBlockEdge} from '../web/method-sequence-blocks.js';
const call=(label,line,column=1)=>({kind:'call',label,site:{line,column},line,column});
const root={id:'root',label:'Root()',file:'C.cs',methodFlow:{status:'parsed',steps:[call('Child()',1),call('Child()',2),{kind:'return',label:'return result',line:3,column:1}]},events:[{kind:'call',text:'Child()',line:1,column:1,targetNodeIds:['child']},{kind:'call',text:'Child()',line:2,column:1,targetNodeIds:['child']}]};
const child={id:'child',label:'Child()',file:'C.cs',methodFlow:{status:'parsed',steps:[{kind:'return',label:'return 1',line:5,column:1}]},events:[]};
const graph={nodes:[root,child],edges:[{source:'root',target:'child',relation:'calls'}]},original=JSON.stringify(graph);
const scene=buildMethodSequence(graph,'root',{expandedIds:new Set(['root::inline-call:1:1'])});
assert.equal(JSON.stringify(graph),original);
assert.equal(scene.visuals.filter(n=>n.kind==='call').length,2);
assert.equal(scene.visuals.find(n=>n.id==='root::inline-call:1:1').stepNumber,'1');
assert.equal(scene.visuals.find(n=>n.id==='root::inline-call:2:1').stepNumber,'2');
assert.equal(scene.visuals.find(n=>n.id==='root::inline-call:2:1').bodyExpanded,false);
const resume=scene.visuals.find(n=>n.kind==='resume');assert.ok(resume);
assert.ok(scene.visualEdges.some(e=>e.target===resume.id&&e.sequenceKind==='return'));
assert.ok(scene.visualEdges.some(e=>e.source===resume.id&&e.target==='root::inline-call:2:1'));
const nested=scene.layout.frames.find(f=>f.depth===1);assert.ok(nested);
for(const n of scene.visuals.filter(n=>n.ownerId===nested.id)) assert.ok(n.x>=nested.x&&n.x<=nested.x+nested.w);
for(const point of scene.positions.values()) assert.ok([point.x,point.y,point.w,point.h].every(Number.isFinite));

const work=call('Work()',12);
const loop={...root,methodFlow:{status:'parsed',steps:[{kind:'loop',controlKind:'for',label:'for',line:10,column:1,condition:[],body:[{kind:'continue',label:'continue',line:11,column:1}],update:[work]},call('After()',20)]},events:[{text:'Work()',line:12,column:1,targetNodeIds:[]},{text:'After()',line:20,column:1,targetNodeIds:[]}]};
const loopScene=buildMethodSequence({nodes:[loop],edges:[]},'root');
assert.ok(loopScene.visualEdges.some(e=>e.sequenceKind==='continue'&&e.target==='root::inline-call:12:1'));
assert.ok(loopScene.visualEdges.some(e=>e.sequenceKind==='loop'));
assert.ok(loopScene.visualEdges.some(e=>e.source.includes('::join')&&e.target==='root::inline-call:20:1'));
const branch={...root,methodFlow:{status:'parsed',steps:[{kind:'branch',controlKind:'if',label:'condition',line:1,column:1,arms:[{label:'истина',steps:[{kind:'return',label:'return',line:2,column:1}]},{label:'иначе',steps:[{kind:'throw',label:'throw',line:3,column:1}]}]},call('Unreachable()',4)]},events:[]};
const branchScene=buildMethodSequence({nodes:[branch],edges:[]},'root');
assert.ok(!branchScene.visuals.some(n=>String(n.label).includes('Unreachable')));
assert.ok(branchScene.visualEdges.some(e=>e.sequenceKind==='throw'));

globalThis.document={createElementNS:(_,tag)=>new Svg(tag)};globalThis.ResizeObserver=class{observe(){}};
const svg=new Svg('svg'),viewport=new Svg('g');viewport.setAttribute('id','viewport');svg.append(viewport);
const view=new GraphView(svg,()=>{},()=>{});view.displayProvider='flow';view.pathReading=true;view.pathRootId='root';view.pathSnapshotId='s';
view.renderPathCards(graph.nodes,graph.edges,'root');
assert.ok(viewport.querySelector('.method-sequence-frame'));
assert.ok(Object.values(view.geometryBounds()).every(Number.isFinite));
view.fit();assert.ok(Object.values(view.camera).every(Number.isFinite));
const before=JSON.stringify(view.pathSequenceScene.visuals.map(n=>[n.id,n.stepNumber]));
view.groupingProvider='natural';view.renderPathCards(graph.nodes,graph.edges,'root');
assert.equal(JSON.stringify(view.pathSequenceScene.visuals.map(n=>[n.id,n.stepNumber])),before);
const action=viewport.querySelector('.path-card-expand');const camera={...view.camera};
action.listeners.click({stopPropagation(){}});
assert.ok(view.pathExpandedIds.has('root::inline-call:1:1'));
assert.equal(view.camera.scale,camera.scale);
assert.ok(viewport.querySelectorAll('text').some(n=>String(n.textContent).includes('Вернуться')));
console.log('Method sequence: occurrences, normal returns, branch exits, loops, provider parity and real GraphView PASS');

// A child which always throws has no normal return port and must not imply a following caller step.
const throwing={...child,methodFlow:{status:'parsed',steps:[{kind:'throw',line:5,column:1,label:'throw failure'}]}};
const thrown=buildMethodSequence({nodes:[root,throwing],edges:graph.edges},'root',{expandedIds:new Set(['root::inline-call:1:1'])});
assert.ok(!thrown.visuals.some(n=>n.id==='root::inline-call:2:1'));
assert.ok(!thrown.visualEdges.some(e=>e.sequenceKind==='return'&&e.target.endsWith('::resume')));
// Missing descriptions stay loadable and cannot masquerade as empty completed method bodies.
const requested=[];
const lazy=buildMethodSequence({nodes:[root,{...child,methodFlow:undefined}],edges:graph.edges},'root',{
 expandedIds:new Set(['root::inline-call:1:1']),getData:id=>id==='root'?root:undefined,loadData:id=>requested.push(id)});
assert.deepEqual(requested,['child']);assert.ok(lazy.visuals.find(n=>n.kind==='call').canExpand);
assert.ok(lazy.visuals.some(n=>n.kind==='unknown'));
assert.ok(!lazy.visualEdges.some(e=>e.sequenceKind==='return'&&e.target.endsWith('::resume')));
// Controls can be hidden without losing arm ownership or the nearest-loop continuation.
const hidden=buildMethodSequence({nodes:[loop],edges:[]},'root',{controlFilters:{loops:false}});
assert.ok(!hidden.visuals.some(n=>n.kind==='loop'));assert.ok(hidden.visualEdges.some(e=>e.sequenceKind==='loop'));
const doReturn={...branch,methodFlow:{status:'parsed',steps:[{kind:'loop',controlKind:'do',line:1,column:1,body:[{kind:'return',line:2,column:1,label:'return'}],condition:[],update:[]},call('Unreachable()',3)]}};
assert.ok(!buildMethodSequence({nodes:[doReturn],edges:[]},'root').visuals.some(n=>n.label?.includes('Unreachable')));
// Chained calls share a source start, but end coordinates distinguish their occurrences.
const chain={...root,methodFlow:{status:'parsed',steps:[call('A()',1),{...call('A().B()',1),site:{line:1,column:1,endLine:1,endColumn:9}}]},
 events:[{text:'A()',line:1,column:1,endLine:1,endColumn:4,targetNodeIds:[]},{text:'A().B()',line:1,column:1,endLine:1,endColumn:9,targetNodeIds:[]}]};
chain.methodFlow.steps[0].site={line:1,column:1,endLine:1,endColumn:4};
const chained=buildMethodSequence({nodes:[chain],edges:[]},'root');assert.equal(new Set(chained.visuals.map(n=>n.id)).size,chained.visuals.length);
// Presentation limits are visible and do not invent normal completion beyond omitted steps.
const big={...root,methodFlow:{status:'parsed',steps:Array.from({length:500},(_,i)=>call('Work()',i+1))},events:[]};
const bounded=buildMethodSequence({nodes:[big],edges:[]},'root');assert.ok(bounded.truncated);assert.ok(bounded.visuals.length<=320);
assert.ok(!bounded.visualEdges.some(e=>e.target==='root::return'));
console.log('Incomplete bodies, lazy facts, hidden controls, chained sites and budgets PASS');

// Structural transitions have compact geometry and never open a fictitious source line.
const withAwait={...root,methodFlow:{status:'parsed',steps:[call('Child()',1),{kind:'await',label:'Дождаться результата',line:2,column:1},{kind:'return',label:'return result',line:3,column:1}]}};
const compactScene=buildMethodSequence({nodes:[withAwait,child],edges:graph.edges},'root');
assert.equal(compactScene.visuals.find(n=>n.kind==='await').h,54);
assert.ok(compactScene.visuals.find(n=>n.kind==='await').compactRow);
assert.ok(!branchScene.visuals.some(n=>n.kind==='empty'));
const sourceActions=[];view.onPathSource=(...args)=>sourceActions.push(args);view.renderPathCards(graph.nodes,graph.edges,'root');
const definition=view.viewport.querySelectorAll('text').find(n=>n.attributes['data-definition-id']==='child');
assert.ok(definition);definition.listeners.click({stopPropagation(){},preventDefault(){}});
assert.equal(sourceActions.at(-1)[0],'child','definition opens the callee, not caller');
const emptyBranch={...branch,methodFlow:{status:'parsed',steps:[{
 kind:'branch',controlKind:'if',line:1,column:1,label:'condition',
 arms:[{label:'истина',steps:[]},{label:'иначе',steps:[]}]
}]}};
view.renderPathCards([emptyBranch],[],'root');
const structural=view.viewport.querySelectorAll('.method-sequence-transition').filter(n=>['empty','join'].includes(n.attributes['data-transition-kind']));
assert.equal(structural.length,1);assert.ok(structural.every(n=>n.attributes.role==='img'&&!n.listeners.click));
assert.ok(structural.every(n=>!n.querySelector('.flow-card-note')));
const emptyScene=view.pathSequenceScene;
assert.ok(!emptyScene.visuals.some(n=>n.kind==='empty'));
const skipEdges=emptyScene.visualEdges.filter(e=>e.sequenceKind==='branch');
assert.equal(skipEdges.length,1);
assert.match(skipEdges[0].caption,/истина.*иначе/);
assert.ok(skipEdges[0].target.includes('::join'));
console.log('Compact await, branch junctions and exact definition links PASS');

// A short action and omitted else share two labelled ports; expanded calls retain their bypass corridor.
const choice={...emptyBranch,methodFlow:{status:'parsed',steps:[{...emptyBranch.methodFlow.steps[0],
 arms:[{label:'истина',steps:[{kind:'operation',line:2,column:1,label:'result = item'}]},
 {label:'иначе',steps:[],emptyReason:'skip'}]},call('After()',3)]},events:[{text:'After()',line:3,column:1}]};
const choiceScene=buildMethodSequence({nodes:[choice],edges:[]},'root');
const choiceNode=choiceScene.visuals.find(n=>n.kind==='branch');
assert.deepEqual(choiceNode.valueChoices.map(n=>n.expression),['result = item','Перейти дальше']);
assert.ok(choiceScene.visualEdges.some(e=>e.source===choiceNode.id&&e.target.includes('::join')&&e.sourcePort===1));
assert.ok(choiceScene.visualEdges.some(e=>e.source.includes('::join')&&e.target==='root::inline-call:3:1'));
const expandedChoice={...choice,methodFlow:{status:'parsed',steps:[{...choice.methodFlow.steps[0],arms:[
 {label:'истина',steps:[call('Child()',1)]},{label:'иначе',steps:[],emptyReason:'skip'}]}]},events:root.events};
const expandedChoiceScene=buildMethodSequence({nodes:[expandedChoice,child],edges:[]},'root',{
 expandedIds:new Set(['root::inline-call:1:1'])});
const expandedBypass=expandedChoiceScene.visualEdges.find(e=>e.bypass);
const expandedCorridor=expandedChoiceScene.positions.get(expandedBypass.source).y+expandedBypass.bypassOffset;
const childFrame=expandedChoiceScene.layout.frames.find(f=>f.depth===1);
assert.ok(expandedCorridor>childFrame.y+childFrame.h,'skip routes below an expanded callee frame');
const bypassStart=expandedChoiceScene.positions.get(expandedBypass.source),bypassEnd=expandedChoiceScene.positions.get(expandedBypass.target);
assert.ok(sequenceCaption(bypassStart,bypassEnd,expandedBypass.caption,expandedBypass).y>childFrame.y+childFrame.h);
const skipCoordinates=sequenceCurve(bypassStart,bypassEnd,expandedBypass).match(/-?\d+(?:\.\d+)?/g).map(Number);
assert.deepEqual(skipCoordinates.slice(0,2),[bypassStart.x,bypassStart.y+bypassStart.h/2]);
assert.deepEqual(skipCoordinates.slice(-2),[bypassEnd.x,bypassEnd.y-bypassEnd.h/2]);
assert.equal(bypassStart.x,bypassEnd.x,'A skipped arm follows the downward reading spine');
assert.deepEqual(choiceScene.visuals.filter(n=>n.stepNumber).map(n=>n.stepNumber),['1','2']);
assert.ok(scene.visuals.filter(n=>n.stepNumber).every(n=>/^\d+$/.test(n.stepNumber)));
assert.equal(scene.visuals.find(n=>n.id===nested.id).simpleRows[0].stepNumber,'1');

// Value outcomes are source actions, not empty junctions or second calls.
const nullable={...emptyBranch,methodFlow:{status:'parsed',steps:[{...emptyBranch.methodFlow.steps[0],controlKind:'?.',label:'result != null',
 arms:[{label:'значение задано',steps:[{kind:'value',line:7,column:8,label:'Взять значение',expression:'result.Security',reuseResult:true}]},
 {label:'null',steps:[{kind:'value',line:7,column:8,label:'Значение',expression:'null'}]}]}]}};
const nullableScene=buildMethodSequence({nodes:[nullable],edges:[]},'root');
assert.deepEqual(nullableScene.visuals.find(n=>n.valueChoices).valueChoices.map(n=>n.expression),['result.Security','null']);
assert.ok(nullableScene.visuals.find(n=>n.valueChoices).h===128);
view.renderPathCards([nullable],[],'root');
const valueGroup=view.nodeElements.get(view.pathSequenceScene.visuals.find(n=>n.valueChoices).id).group;
valueGroup.children.find(n=>n.attributes['data-value-choice']===0).listeners.click({stopPropagation(){},preventDefault(){}});
assert.deepEqual(sourceActions.at(-1),['root',7]);
assert.ok(valueGroup.querySelectorAll('text').some(n=>String(n.textContent).includes('result.Security')));
assert.equal(valueGroup.querySelectorAll('circle').filter(n=>String(n.attributes['data-port']).startsWith('out-')).length,2);
assert.ok(!valueGroup.querySelectorAll('text').some(n=>/^Шаг /.test(String(n.textContent))));

// Unrepresented source and truncated value arms cannot pretend that they are empty no-op paths.
for(const flow of [{status:'parsed',steps:[{...emptyBranch.methodFlow.steps[0],arms:[{label:'истина',steps:[],emptyReason:'not-extracted',line:9}]}]},
 {status:'parsed',truncated:true,steps:[{...emptyBranch.methodFlow.steps[0],arms:[{label:'истина',steps:[]}]}]}]) {
 const uncertainScene=buildMethodSequence({nodes:[{...emptyBranch,methodFlow:flow}],edges:[]},'root');
 assert.ok(uncertainScene.visuals.some(n=>n.label==='Действия не извлечены'));
 assert.ok(!uncertainScene.visualEdges.some(e=>e.caption?.includes('→ дальше')));
}
// Empty loop bodies still iterate; do-while enters the test and for-update remains reachable.
for(const controlKind of ['for','while','do','foreach']) {
 const emptyLoop={...loop,methodFlow:{status:'parsed',steps:[{kind:'loop',controlKind,label:controlKind,line:10,column:1,body:[],bodyEmptyReason:'skip',condition:[],update:controlKind==='for'?[work]:[]},call('After()',20)]}};
 const emptyLoopScene=buildMethodSequence({nodes:[emptyLoop],edges:[]},'root');
 assert.ok(!emptyLoopScene.visuals.some(n=>n.kind==='empty'));
 assert.ok(emptyLoopScene.visualEdges.some(e=>e.sequenceKind==='loop'));
 assert.ok(emptyLoopScene.visualEdges.some(e=>e.source.includes('::join')&&e.target==='root::inline-call:20:1'));
}
view.groupingProvider='natural';view.renderPathCards([nullable],[],'root');
const naturalValues=JSON.stringify(view.pathSequenceScene.visuals.map(n=>[n.id,n.stepNumber,n.sourceExpression]));
view.groupingProvider='graphistry';view.renderPathCards([nullable],[],'root');
assert.equal(JSON.stringify(view.pathSequenceScene.visuals.map(n=>[n.id,n.stepNumber,n.sourceExpression])),naturalValues);
console.log('Direct skips, value source actions, flat method numbers, empty loops and provider parity PASS');

// Exclusive actions become rows in one outline; branches and expanded methods retain their boundaries.
assert.equal(compactScene.layout.lineBlocks.length,1);
const actionRows=compactScene.visuals.filter(n=>n.compactRow);
assert.equal(actionRows.length,3);
assert.equal(new Set(actionRows.map(n=>n.blockId)).size,1);
assert.deepEqual(actionRows.map(n=>n.kind),['call','await','return']);
assert.ok(actionRows.every(n=>n.x===actionRows[0].x));
assert.ok(actionRows[1].y>actionRows[0].y&&actionRows[2].y>actionRows[1].y);
const internalEdges=compactScene.visualEdges.filter(e=>internalBlockEdge(compactScene.byId.get(e.source),compactScene.byId.get(e.target),e));
assert.equal(internalEdges.length,2,'facts preserve internal next transitions');
view.pathExpandedIds.clear();view.renderPathCards([withAwait,child],graph.edges,'root');
assert.equal(view.viewport.querySelectorAll('.method-sequence-code-block').length,1);
assert.equal(view.viewport.querySelectorAll('.method-sequence-row').length,3);
assert.equal(view.viewport.querySelectorAll('.method-sequence-edge').length,compactScene.visualEdges.length-2);
assert.ok(!view.viewport.querySelectorAll('text').some(n=>/^Шаг /.test(String(n.textContent))));
const awaitRow=view.nodeElements.get(view.pathSequenceScene.visuals.find(n=>n.kind==='await').id).group;
assert.ok(awaitRow.querySelectorAll('rect').some(n=>n.attributes.fill==='#e7f1f7'));
const selectedRows=[];view.onSequenceSelect=id=>selectedRows.push(id);
awaitRow.listeners.click({stopPropagation(){},preventDefault(){}});assert.deepEqual(selectedRows,['root']);
awaitRow.querySelector('.path-card-source').listeners.click({stopPropagation(){}});assert.deepEqual(sourceActions.at(-1),['root',2]);
const rowDefinition=view.viewport.querySelectorAll('text').find(n=>n.attributes['data-definition-id']==='child');
rowDefinition.listeners.click({stopPropagation(){},preventDefault(){}});assert.equal(sourceActions.at(-1)[0],'child');
const cell=id=>({cards:[{id,kind:'operation',w:260,h:154}],frames:[],entry:id,exits:[id],w:260,h:154});
const forkEdges=[{source:'a',target:'b',sequenceKind:'next'},{source:'a',target:'outside',sequenceKind:'branch'}];
assert.equal(compactLinearCells([cell('a'),cell('b')],forkEdges).length,2,'an external fork prevents grouping');
const fanIn=[{source:'a',target:'b',sequenceKind:'next'},{source:'outside',target:'b',sequenceKind:'next'}];
assert.equal(compactLinearCells([cell('a'),cell('b')],fanIn).length,2,'an external entry prevents grouping');
assert.ok(scene.visuals.find(n=>n.id==='root::inline-call:1:1').bodyExpanded);
assert.ok(!scene.visuals.find(n=>n.id==='root::inline-call:1:1').compactRow);
assert.ok(!choiceScene.visuals.find(n=>n.kind==='branch').compactRow);
const literalRows={...withAwait,methodFlow:{status:'parsed',steps:[call('Child()',1),{kind:'return',label:'return "a  b";',line:2,column:1}]}};
view.renderPathCards([literalRows,child],graph.edges,'root');
assert.ok(view.viewport.querySelectorAll('text').some(n=>n.textContent==='return "a  b";'&&n.attributes['xml:space']==='preserve'));
console.log('Source-like code blocks, exclusive chains, row actions and preserved transitions PASS');

// Expand-here preserves the source block and attaches cards right of the exact occurrence.
for (const provider of ['natural', 'graphistry']) {
 const diagram=new GraphView(new Svg('svg'),()=>{},()=>{});
 diagram.svg.append(Object.assign(new Svg('g'),{attributes:{id:'viewport'}}));
 diagram.viewport=diagram.svg.querySelector('#viewport');
 diagram.pathRootId='root';diagram.pathSnapshotId='s';diagram.displayProvider='flow';diagram.groupingProvider=provider;
 let reads=0;diagram.onReadCall=()=>reads++;
 diagram.renderPathCards(graph.nodes,graph.edges,'root');
 const occurrence='root::inline-call:1:1',before=diagram.positions.get(occurrence),camera={...diagram.camera};
 const screen={x:before.x*camera.scale+camera.x,y:before.y*camera.scale+camera.y};
 const clicked=diagram.nodeElements.get(occurrence).group.querySelector('.path-card-expand');
 clicked.listeners.click({stopPropagation(){},preventDefault(){}});
 assert.equal(reads,0,'Expand does not switch to code reading');
 assert.ok(diagram.pathExpandedIds.has(occurrence));
 assert.ok(!diagram.pathExpandedIds.has('root::inline-call:2:1'),'Repeated calls expand independently');
 const opened=diagram.pathSequenceScene,call=opened.byId.get(occurrence);
 const callee=opened.byId.get(occurrence+'::method:child');
 assert.ok(callee.x-callee.w/2>call.x+call.w/2,'Callee cards are right of the caller');
 assert.equal(call.blockId,before.blockId);
 assert.equal(call.x,before.x);assert.equal(call.y,before.y);
 assert.equal(callee.y,call.y,'Entry cards face the clicked call');
 assert.equal(call.x*diagram.camera.scale+diagram.camera.x,screen.x,'Clicked x stays anchored');
 assert.equal(call.y*diagram.camera.scale+diagram.camera.y,screen.y,'Clicked y stays anchored');
 assert.equal(diagram.camera.scale,camera.scale);
 diagram.nodeElements.get(occurrence).group.querySelector('.path-card-expand').listeners.click({stopPropagation(){},preventDefault(){}});
 assert.ok(!diagram.pathExpandedIds.has(occurrence));
 assert.ok(!diagram.pathSequenceScene.byId.has(occurrence+'::method:child'));
}
console.log('Right-facing progressive expansion, independent call occurrences and collapse with reader connected PASS');
