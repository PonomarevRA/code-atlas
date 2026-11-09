/** Прямой контракт workspace-controller: pinned проекция, реальная карта, Back и отмена await. */
import assert from 'node:assert/strict';
import { createWorkspaceNavigation } from '../web/workspace-navigation.js';
import { projectExecutionPath } from '../web/path-graph-projection.js';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';
import { navigationSnapshot, pathDetailToken } from '../web/navigation-history.js';
const rawNodes = new Map([['a',{id:'a',label:'.Calculate()',file:'a.cs',codeProjectId:'p'}],['b',{id:'b',label:'.Calculate()',file:'b.cs',codeProjectId:'p'}]]);
const input = { rootNodeId:'a', steps:[{nodeId:'a',controlFlow:{branches:[]},events:[]},{nodeId:'b'},{nodeId:'b'},{nodeId:'absent'}], edges:[{source:'a',target:'b',relation:'calls',sourceLine:4,sourceFile:'a.cs',evidence:{kind:'typed-interface-call'}},{source:'a',target:'b',relation:'calls',sourceLine:5},{source:'a',target:'absent'}] };
const before = JSON.stringify(input);
const projected = projectExecutionPath(input,rawNodes,{id:'http',route:'/calculate',httpMethod:'POST'});
assert.deepEqual(projected.nodes.map(n=>n.id),['path-entry:http','a','b']);
assert.equal(projected.edges.length,3);
assert.equal(projected.edges[1].source_file,'a.cs');
assert.equal(projected.edges[1].evidence.kind,'typed-interface-call');
assert.deepEqual(projected.edges.slice(1).map(e=>e.sourceLine),[4,5]);
assert.equal(JSON.stringify(input),before);
const elements = new Map();
const values = {'view-mode':'architecture','display-provider':'topology','system-scale':'repositories','architecture-preset':'all','project-blocks':'','search':'','kind':'','relation':'','system-search':'','system-systems':'','system-packages':'','solution-kind':'all','discovery-mode':'code','navigation-search':'Calculator','semantic-query':'','ingress-catalog-search':'','semantic-kind':'all','navigation-search-scope':'all','navigation-search-results':'','algorithm':'expert'};
for(const [id,value] of Object.entries(values)) elements.set(id,{value,checked:true,hidden:id==='navigation-search-results',options:[{value},{value:''}],replaceChildren(){}});
elements.set('answer',{replaceChildren(){}}); elements.set('selection',{replaceChildren(){}});
globalThis.document={getElementById:id=>elements.get(id),createElementNS:(_,tag)=>new Svg(tag)};
globalThis.ResizeObserver=class { observe() {} };
const state={project:{id:'repo'},projects:[{id:'repo'},{id:'other'}],graph:{metadata:{snapshotId:'snap'},nodes:[...rawNodes.values()]}, architecture:{level:'projects',projectId:null,file:null,page:0},selected:'a',community:null,inlineExpanded:new Set(),isolatedBlock:null,selectedBlock:null,pathOnGraph:null,codeProjectIds:new Set(),algorithm:'expert',algorithms:[{id:'expert'}],ingress:{data:null},focused:false,loading:false};
const calls=[];
/** Фиксирует обращения к оболочке, чтобы обнаружить поздний draw отменённого перехода. */
const record = name => (...args)=>{calls.push(name);};
const svg = new Svg('svg'), viewport = new Svg('g');
viewport.setAttribute('id','viewport'); svg.append(viewport);
const view = new GraphView(svg,record('map.select'),()=>{});
view.camera={x:12,y:24,scale:.9};
const viewers={wikiViewer:{documentId:null,nodeId:null,offset:0,close:record('wiki.close')},pathViewer:{entry:null,close:record('path.close')},factsPopup:{close:record('facts.close')},documentationViewer:{select:record('documentation.select')},systemView:{page:0},mapBlocks:{restoreSelection:record('block.restore')}};
const search={searchNavigation:{cancel:record('search.cancel')},navigationSearch:{refresh:record('search.refresh')},semanticSearch:{invalidateSearch:record('semantic.invalidate')}};
const transient={inert:true,sourceOverride:'old source'};
/** Освобождает временный просмотр до загрузки адресуемой карты. */
function closeTransientStudy() { calls.push('study.close'); transient.inert=false; transient.sourceOverride=null; }
const actions={closeTransientStudy,updateDisplayProvider:record('provider'),updateDiscovery:record('discovery'),openProject:async()=>{},draw:record('draw'),filteredNodes:()=>state.graph.nodes,displayedAggregate:()=>({communities:[]}),renderCodeProjects:record('projects'),renderSelection:record('selection'),loadIngress:async()=>{},paintPathGraph:record('path.paint'),controls:record('controls'),status:record('status')};

const nav=createWorkspaceNavigation({state,view,viewers,search,actions});
const saved=nav.capture();
assert.equal(saved.searchContext.codeQuery,'Calculator');
assert.deepEqual(saved.camera,{x:12,y:24,scale:.9});
view.camera={x:1,y:2,scale:.4};
await nav.restore(saved,{signal:new AbortController().signal,isCurrent:()=>true});
assert.deepEqual(view.camera,{x:12,y:24,scale:.9});
assert.equal(calls[0],'study.close');
assert.deepEqual(transient,{inert:false,sourceOverride:null});
assert.ok(calls.includes('selection')&&calls.includes('controls'));
// Реальный GraphView получает два paint: базовый снимок, затем восстановленные раскрытия.
const pathData={rootNodeId:'a',snapshotId:'snap',steps:[{nodeId:'a',controlFlow:{branches:[]}},{nodeId:'b'}],edges:[{source:'a',target:'b',relation:'calls'},{source:'b',target:'a',relation:'calls'}]};
const pathPaints=[];
actions.paintPathGraph=data=>{
  const {nodes,edges}=projectExecutionPath(data,rawNodes);
  view.pathRootId=data.rootNodeId; view.pathSnapshotId=data.snapshotId;
  view.renderPathCards(nodes,edges,state.selected);
  pathPaints.push(view.pathExpandedIds.size);
};
globalThis.fetch=async (url,options)=>{
  calls.push('fetch.path');
  assert.deepEqual(transient,{inert:false,sourceOverride:null});
  assert.equal(url,'/api/projects/repo/execution-path');
  assert.equal(JSON.parse(options.body).snapshotId,'snap');
  assert.equal(JSON.parse(options.body).maxNodes,2000);
  return {ok:true,json:async()=>pathData};
};
const pathNav=createWorkspaceNavigation({state,view,viewers,search,actions});
const savedPath=navigationSnapshot({...saved,route:{...saved.route,path:'a',detail:pathDetailToken(['b'],[])},pathExpanded:['b'],pathCollapsed:[]});
const pathRestoreStart=calls.length;
transient.inert=true; transient.sourceOverride='open modal';
await pathNav.restore(savedPath,{signal:new AbortController().signal,isCurrent:()=>true});
assert.equal(calls[pathRestoreStart],'study.close');
assert.ok(calls.slice(pathRestoreStart).indexOf('study.close')<calls.slice(pathRestoreStart).indexOf('fetch.path'));
assert.deepEqual(pathPaints,[0,1]);
assert.deepEqual([...view.pathExpandedIds],['b']);
assert.ok(view.pathSceneActive);
assert.deepEqual(view.camera,saved.camera);
assert.equal(state.pathOnGraph,pathData);
const documentedPath=navigationSnapshot({...savedPath,route:{...savedPath.route,reading:'documentation'}});
await pathNav.restore(documentedPath,{signal:new AbortController().signal,isCurrent:()=>true});
assert.equal(state.methodPresentation,'documentation');
assert.equal(pathNav.capture().route.reading,'documentation');
await pathNav.restore(savedPath,{signal:new AbortController().signal,isCurrent:()=>true});
assert.equal(state.methodPresentation,'diagram');

let current=true, finish;
actions.openProject=(_id,_preserve,options)=>{ calls.push('project.open'); assert.deepEqual(transient,{inert:true,sourceOverride:'other modal'}); assert.equal(options.signal instanceof AbortSignal,true); return new Promise(resolve=>{finish=resolve;}); };
// A separate controller receives its dependency once, callbacks themselves retain access to current state.
const cancelNav=createWorkspaceNavigation({state,view,viewers,search,actions});
const changed=navigationSnapshot({...saved,route:{...saved.route,repo:'other'}});
const projectRestoreStart=calls.length;
transient.inert=true; transient.sourceOverride='other modal';
const pending=cancelNav.restore(changed,{signal:new AbortController().signal,isCurrent:()=>current});
assert.equal(calls[projectRestoreStart],'project.open');
assert.equal(calls.slice(projectRestoreStart).includes('study.close'),false,'the committed reader remains open during a pending project request');
const drawBefore=calls.filter(c=>c==='draw').length;
current=false; finish(); await pending;
assert.equal(calls.filter(c=>c==='draw').length,drawBefore,'obsolete async restoration must not redraw');
assert.deepEqual(transient,{inert:true,sourceOverride:'other modal'},'a cancelled project change retains the committed reader');
// A failed current load also stops before applying target controls or node IDs.
actions.openProject=async()=>false;
const failedNav=createWorkspaceNavigation({state,view,viewers,search,actions});
const selectionBefore=state.selected, algorithmBefore=state.algorithm, projectBefore=state.project;
await failedNav.restore(changed,{signal:new AbortController().signal,isCurrent:()=>true});
assert.equal(state.project,projectBefore);
assert.equal(state.selected,selectionBefore);
assert.equal(state.algorithm,algorithmBefore);
assert.deepEqual(transient,{inert:true,sourceOverride:'other modal'});
// Даже transport без AbortSignal не может нарисовать путь после следующего Back.
let pathCurrent=true, finishResponse;
globalThis.fetch=()=>new Promise(resolve=>{finishResponse=resolve;});
const lateNav=createWorkspaceNavigation({state,view,viewers,search,actions});
const paintsBeforeLate=pathPaints.length;
const late=lateNav.restore(savedPath,{signal:new AbortController().signal,isCurrent:()=>pathCurrent});
pathCurrent=false;
finishResponse({ok:true,json:async()=>pathData});
await late;
assert.equal(pathPaints.length,paintsBeforeLate);
assert.equal(state.pathOnGraph,null);

console.log('Workspace navigation capture, real path restoration, camera and obsolete async cancellation PASS');
