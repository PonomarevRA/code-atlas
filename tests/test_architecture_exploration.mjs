/** Compose real session, block, projection, layout and export contracts without a browser. */
import assert from 'node:assert/strict';
import {ArchitectureSession, ArchitectureSessions, boundArchitectureProjection, boundCanonicalArchitecture} from '../web/architecture-session.js';
import {architectureBlocks, architectureBlockId} from '../web/architecture-blocks.js';
import {stableArchitectureGeometry,architecturePositions} from '../web/architecture-layout.js';
import {systemProjection} from '../web/system-architecture.js';
import {solutionArchitecture} from '../web/solution-architecture.js';
import {architectureExport} from '../web/architecture-export.js';

const owner = {id:'repo:p',kind:'project',codeProjectId:'p',originalCodeProjectId:'p',repositoryId:'repo',label:'Project P'};
/** One source-only operation with a public proof and a stable owner-scoped resource. */
function resource(id, kind, resourceKind, operation, selectedOwner = owner.id) {
  const node = {id,kind,resourceKind,ownerId:selectedOwner,label:id,status:'resolved',runtimeSelectionKnown:false,
    proof:{basis:'library-call',sourceKind:'working-tree',runtimeSelectionKnown:false}};
  const edge = {id:'fact:'+id,factId:'fact:'+id,source:operation==='consumes'?id:selectedOwner,target:operation==='consumes'?selectedOwner:id,
    relation:operation,operation,weight:1,status:'resolved',runtimeSelectionKnown:false,
    evidence:[{file:'Service.cs',line:12,confidence:'candidate',sourceHash:'a'.repeat(64)}]};
  return {node,edge};
}
const facts = [resource('orders','kafka','topic','publishes'),resource('inbox','rabbit','queue','consumes'),resource('assets','database','table','reads')];
const data = {architectureVersion:2,snapshotId:'s',sourceKind:'working-tree+snapshot',repositories:[{id:'repo',snapshotId:'s'}],
  nodes:[owner,...facts.map(f=>f.node),{id:'configured-db',kind:'database',label:'Configured DB',host:'db.example'}],
  edges:[...facts.map(f=>f.edge),{source:owner.id,target:'configured-db',relation:'configured-database',weight:1,evidence:[]}],
  coverage:{limited:false},exploration:{visitedNodeIds:['raw'],runtimeSelectionKnown:false}};
const original = JSON.stringify(data);
const blockIds = facts.map(f=>architectureBlockId(owner.id,f.node.kind));
const collapsed = architectureBlocks(data);
assert.equal(collapsed.blocks.size,3);
for (const id of ['orders','inbox','assets']) assert(!collapsed.graph.nodes.some(node=>node.id===id));
assert([...collapsed.blocks.values()].every(block=>block.label.includes('привязка неизвестна')&&!block.host));
assert.equal(JSON.stringify(data),original,'grouping does not change canonical facts or bind candidates to a host');

for (const scale of ['projects','repositories']) {
  const expanded = architectureBlocks(data,new Set(blockIds));
  const projectedData = scale==='repositories'?solutionArchitecture(expanded.graph,{id:'repo',name:'Repo'}):expanded.graph;
  const projection = systemProjection(projectedData,{projectIds:scale==='projects'?['p']:null,primaryRepositoryId:'repo'});
  for (const id of ['orders','inbox','assets']) {
    assert(projection.allNodes.some(node=>node.id===id),`${scale} reveals ${id} through explicit block membership`);
    assert(projection.allEdges.some(edge=>edge.target===id&&edge.relation==='logical-member'));
  }
  const match = systemProjection(projectedData,{query:'orders',projectIds:scale==='projects'?['p']:null,primaryRepositoryId:'repo'});
  assert(match.allNodes.some(node=>node.id===(scale==='projects'?owner.id:'solution:repo')),'child-name search keeps its eligible owner');
  const hiddenOwner = systemProjection(expanded.graph,{projectIds:[],primaryRepositoryId:'repo'});
  assert.equal(hiddenOwner.allNodes.length,0,'membership does not reintroduce an excluded owner');
}
const pinnedChild = architectureBlocks(data,new Set(),new Set(['orders']));
assert(systemProjection(pinnedChild.graph).allNodes.some(node=>node.id==='orders'),'a pinned child stays visible while its block is collapsed');

const session = new ArchitectureSession(); session.accept(data);
assert(!session.graph().nodes.some(node=>node.id==='configured-db'),'unvisited configured resources stay sparse');
assert(session.revealOwners([owner.id])); assert(!session.revealOwners([owner.id]));
assert(session.graph().nodes.some(node=>node.id==='configured-db'));
assert(session.visit('raw')); assert(!session.visit('raw'));
session.accept(data); assert.equal(session.graph().edges.filter(edge=>edge.factId==='fact:orders').length,1);
for(let i=1;i<64;i++)assert(session.visit('raw'+i));
assert(!session.visit('overflow')); assert.equal(session.visited.size,64);
const manyNodes=Array.from({length:30},(_,i)=>({id:'n'+i,kind:'http',label:'Node'+i}));
session.accept({...data,nodes:[...data.nodes,...manyNodes]});
for(let i=0;i<24;i++)assert(session.togglePin('n'+i));
assert(!session.togglePin('n24')); assert.equal(session.pinned.size,24);
session.accept({...data,repositories:[{id:'repo',snapshotId:'related-changed'}]});
assert.equal(session.pinned.size,0,'a changed related revision invalidates pins and layout');
assert.equal(session.visited.size,64,'primary raw visits remain valid until the primary context changes');
session.clearExploration(); assert.equal(session.visited.size,0); assert.equal(session.pinned.size,0);
assert(session.graph().nodes.every(node=>node.kind==='project'));

const sessions=new ArchitectureSessions(), first=sessions.get('a');
for(const key of ['b','c','d','e'])sessions.get(key);
assert.equal(sessions.entries.size,4); assert.notEqual(sessions.get('a'),first);

const registry=new Map(), initial={positions:new Map([['anchor',{x:10,y:20,r:50,color:'x'}]]),containers:[]};
stableArchitectureGeometry(initial,registry);
const growing={positions:new Map([['anchor',{x:999,y:888,r:50,color:'x'}],['new',{x:10,y:20,r:50,color:'y'}]]),containers:[]};
const sameMap=growing.positions;
stableArchitectureGeometry(growing,registry);
assert.equal(growing.positions,sameMap,'FlowRenderer must read the same temporary Map after the hook');
assert.deepEqual([growing.positions.get('anchor').x,growing.positions.get('anchor').y],[10,20]);
assert.notDeepEqual([growing.positions.get('new').x,growing.positions.get('new').y],[10,20]);

// A full persistent cache must not make two newly visible nodes overlap after a filter/page change.
const fullRegistry=new Map(Array.from({length:240},(_,i)=>['old'+i,{x:i*200,y:0,width:100,height:100}]));
const changedSubset=new Map([['new1',{x:0,y:0,r:50}],['new2',{x:0,y:0,r:50}]]);
stableArchitectureGeometry(changedSubset,fullRegistry);
assert.equal(fullRegistry.size,240);
const [new1,new2]=[changedSubset.get('new1'),changedSubset.get('new2')];
assert(Math.abs(new1.x-new2.x)>=136||Math.abs(new1.y-new2.y)>=136);
const repeatedSubset=new Map([['new1',{x:0,y:0,r:50}],['new2',{x:0,y:0,r:50}]]);
stableArchitectureGeometry(repeatedSubset,fullRegistry);
assert.deepEqual([...changedSubset.values()],[...repeatedSubset.values()]);
const currentCoordinates=architecturePositions(fullRegistry,new Set(['old0','new1','new2']),changedSubset);
assert.deepEqual(currentCoordinates.new1,{x:new1.x,y:new1.y});
assert.deepEqual(currentCoordinates.new2,{x:new2.x,y:new2.y});
assert.deepEqual(currentCoordinates.old0,{x:0,y:0});
assert.equal(fullRegistry.size,240,'exporting uncached current positions does not grow the persistent cache');

const topics=Array.from({length:240},(_,i)=>resource('topic-'+i,'kafka','topic','publishes'));
const large={...data,nodes:[owner,...topics.map(f=>f.node)],edges:topics.map(f=>f.edge),coverage:{complete:true,limited:false}};
const bounded=boundCanonicalArchitecture(large,new Set([architectureBlockId(owner.id,'kafka')]));
assert.equal(bounded.nodes.length,240); assert(bounded.nodes.some(node=>node.id===owner.id));
assert.equal(bounded.edges.length,239); assert.equal(bounded.sceneLimits.omittedNodes,1);
assert.equal(bounded.coverage.limited,true); assert.equal(bounded.coverage.complete,false);
const archive=JSON.parse(architectureExport(bounded,{format:'json',projectId:'repo',snapshotId:'s'}).text);
assert.equal(archive.coverage.limited,true); assert.equal(archive.coverage.complete,false);
assert.equal(archive.edges.length,239); assert(archive.edges.every(edge=>archive.nodes.some(node=>node.id===edge.source)&&archive.nodes.some(node=>node.id===edge.target)));

const pageInput={allNodes:[owner,...Array.from({length:200},(_,i)=>({id:'v'+i,kind:'http',label:'V'+i}))],allEdges:[],page:0};
const firstPage=boundArchitectureProjection(pageInput,new Set(['v199']));
const secondPage=boundArchitectureProjection({...pageInput,page:1},new Set(['v199']));
assert.equal(firstPage.allNodes.length,201); assert.equal(firstPage.pageCount,2);
assert(firstPage.nodes.some(node=>node.id==='v199')&&secondPage.nodes.some(node=>node.id==='v199'),'pins remain on every ordinary page');
assert(firstPage.nodes.some(node=>node.id===owner.id)&&secondPage.nodes.some(node=>node.id===owner.id),'core owners remain on every ordinary page');
assert(firstPage.nodes.length<=160&&secondPage.nodes.length<=160);
for(const ownerCount of [160,240]) {
  const crowded={allNodes:[...Array.from({length:ownerCount},(_,i)=>({id:'owner'+i,kind:'project',label:'Owner '+i})),{id:'pin',kind:'http',label:'Pinned'}],allEdges:[],page:0};
  const first=boundArchitectureProjection(crowded,new Set(['pin']));
  assert(first.allNodes.some(node=>node.id==='pin'),'explicit pins outrank ordinary core owners at the global cap');
  for(let page=0;page<first.pageCount;page++)assert(boundArchitectureProjection({...crowded,page},new Set(['pin'])).nodes.some(node=>node.id==='pin'),'explicit pins remain visible on every crowded page');
}

console.log('Architecture sessions, sparse groups, Kafka/Rabbit/DB disclosure, eligible-owner closure, stable placement, caps and canonical export PASS');

// Native semantic export reports concise format tradeoffs in its own inline status, never a canvas overlay.
const {bindArchitectureExport}=await import('../web/graph-export.js');
let exportClick,selectedFormat='json',downloaded=0;
const exportButton={disabled:false,addEventListener(event,fn){assert.equal(event,'click');exportClick=fn;}};
const exportStatus={hidden:true,textContent:''};
const exportDocument={defaultView:{URL:{createObjectURL(){return 'blob:fixture';},revokeObjectURL(){}},setTimeout(){}},
 body:{append(){}},createElement(){return {click(){downloaded++;},remove(){}};}};
bindArchitectureExport({button:exportButton,status:exportStatus,format:()=>selectedFormat,
 getModel:()=>({graph:bounded,options:{projectId:'repo',snapshotId:'s'}}),document:exportDocument});
exportClick();assert.equal(downloaded,1);assert.equal(exportButton.disabled,false);
assert.match(exportStatus.textContent,/сохранена в Atlas JSON/);assert.match(exportStatus.textContent,/пропущено 1 узлов/);
assert(!exportStatus.textContent.includes('allowlist'));
selectedFormat='structurizr';exportClick();assert.equal(downloaded,2);
assert.match(exportStatus.textContent,/Раскладка будет пересчитана/);assert(exportStatus.textContent.length<260);
console.log('Inline semantic download feedback and bounded partial notice PASS');
