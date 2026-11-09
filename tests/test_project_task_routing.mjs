/** Execute the actual overview-to-existing-discovery bridge without booting the browser application. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../web/app.js',import.meta.url),'utf8');
const block=source.slice(source.indexOf('async function startProjectTask('),source.indexOf('const codeNavigation ='));
assert(block.includes('async function startProjectTask('));
function deferred(){let resolve;const promise=new Promise(yes=>{resolve=yes;});return {promise,resolve};}
function harness(){
  const state={project:{id:'a'},graph:{metadata:{snapshotId:'sid'}},nodes:new Map([['raw',{id:'raw',project:'source'}]])},fields=new Map(),calls=[];
  const $=id=>{if(!fields.has(id))fields.set(id,{value:'',focus(){calls.push('focus:'+id);},click(){calls.push('click:'+id);}});return fields.get(id);};
  const host={state,$,wikiViewer:{close(){}},pathViewer:{close(){}},clearComparison(){},selectedContext:()=>({projectId:state.project.id,snapshotId:state.graph.metadata.snapshotId}),
    syncProjectOverview(){calls.push('task.sync');},showImpactLinks(){calls.push('impact.links');},navigationHistory:{checkpoint(){calls.push('checkpoint');},commit(){calls.push('commit');}},
    ensureSearchGraph:async()=>{},projectOverview:{setOpen(value){calls.push('overview:'+value);}},
    updateDisplayProvider(){calls.push('display');},clearFilters(){calls.push('clearFilters');},codeProjectId:node=>node.project,
    navigateArchitecture:route=>calls.push(route),selectSemanticNode:id=>calls.push('node:'+id),
    semanticSearch:{invalidateSearch(){calls.push('invalidate');}},updateDiscovery:value=>calls.push('discovery:'+value),
    ingressController:{catalog:{open(){calls.push('catalog.open');}}},status:message=>calls.push('status:'+message)};
  const names=Object.keys(host),start=new Function(...names,block+';return startProjectTask;')(...names.map(name=>host[name]));
  return {state,$,calls,start,host};
}
const overview={projectId:'a',snapshotId:'sid'};
for(const [action,mode,kind,focus] of [['understand','meaning','documentation','semantic-query'],['impact','code',null,'navigation-search'],['entrypoints','entries',null,null]]){
  const h=harness();await h.start(action,undefined,overview);assert.equal(h.$('discovery-mode').value,mode);
  if(kind)assert.equal(h.$('semantic-kind').value,kind);
  if(focus)assert(h.calls.includes('focus:'+focus));else assert(h.calls.includes('catalog.open'));
  assert(h.calls.includes('discovery:true'));if(action==='impact')assert.deepEqual(h.state.discoveryTask,{kind:'impact',projectId:'a',snapshotId:'sid'});
  assert(!h.calls.some(value=>String(value).includes('rebuild')),'task starts never build maps/models automatically');
}
{
  const h=harness();await h.start('impact',null,{projectId:'other',snapshotId:'sid'});assert.deepEqual(h.calls,[]);
  await h.start('impact',null,{projectId:'a',snapshotId:'old'});assert.deepEqual(h.calls,[]);
  await h.start('rebuild',null,overview);assert.deepEqual(h.calls,['click:rebuild'],'only explicit rebuild forwards the native action');
}
{
  const h=harness();h.state.pathOnGraph={rootNodeId:'old-path'};h.state.isolatedRoot='old';await h.start('area',{title:'Core',path:'src/core',nodeIds:['raw']},overview);
  assert.equal(h.$('search').value,'src/core');assert.equal(h.state.pathOnGraph,null);assert.equal(h.state.isolatedRoot,null);assert(h.calls.some(value=>value?.level==='files'&&value.projectId==='source'));
}
// A lazy raw-graph request may ignore abort; scope check still fences the later navigation.
{
  const pending=deferred(),h=harness();h.host.ensureSearchGraph=()=>pending.promise;
  const names=Object.keys(h.host),start=new Function(...names,block+';return startProjectTask;')(...names.map(name=>h.host[name]));
  const loading=start('area',{path:'src',nodeIds:['raw']},overview);h.state.project={id:'b'};pending.resolve();await loading;
  assert(!h.calls.includes('display'));assert.equal(h.$('search').value,'');
}
console.log('Project task routing reuses meaning/search/catalogue, explicit rebuild and scope-fenced source areas PASS');

{
  const h=harness();h.state.selected='raw';await h.start('impact',null,overview);
  assert(h.calls.includes('impact.links'),'an already selected symbol opens the actual existing relation inspector');
}
