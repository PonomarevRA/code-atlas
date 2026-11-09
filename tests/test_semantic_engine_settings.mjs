/** Real settings controller and event handlers with a small inert DOM fixture. */
import assert from 'node:assert/strict';
import {createSemanticEngineSettings, engineStatusText, isSemanticOnlyMod} from '../web/semantic-engine-settings.js';

class Element {
  constructor(tag) { this.tagName=tag;this.children=[];this.listeners={};this.dataset={};this.attributes={};this.style={};this.textContent='';this.value='';this.disabled=false; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(name,value) { this.attributes[name]=value; }
  addEventListener(name,callback) { this.listeners[name]=callback; }
  fire(name,event={}) { this.listeners[name]?.({preventDefault(){},...event}); }
}
const document={createElement:tag=>new Element(tag)};
function all(node,predicate) { const found=[];const pending=[node];while(pending.length){const current=pending.pop();if(predicate(current))found.push(current);pending.push(...current.children);}return found; }
function one(node,predicate) { const value=all(node,predicate)[0];assert.ok(value);return value; }
const mod={id:'semantic-typescript',analysisMode:'auto',manifest:{analysis:{semanticProviders:[{strategy:'typescript-local-v1'}]}}};
const preferences={version:1,mods:{},fields:{'typescript-local-v1':['enginePath','nodePath']},defaultMode:'automatic',agentRequired:false};
const project={id:'project/one',name:'<img src=x onerror=alert(1)>',snapshotId:'snapshot1'};
const tick=()=>new Promise(resolve=>setImmediate(resolve));

/** Setup never probes tools; paths and project metadata are ordinary bounded HTTP reads. */
async function setup(overrides={}) {
  const calls=[];let notified=0;
  const request=async(url,options)=>{calls.push(['get',url,options]);if(url==='/api/semantic-engines/settings')return structuredClone(preferences);if(url==='/api/projects')return {projects:[project]};return {updatedAt:1,providers:[]};};
  const post=async(url,body,options)=>{calls.push(['post',url,body,options]);return {paths:body.paths||{},mode:Object.keys(body.paths||{}).length?'explicit':'automatic'};};
  const controller=createSemanticEngineSettings({request,post,document,notify:()=>notified++,wait:async()=>{},...overrides});
  await controller.load();const panel=controller.render(mod);return {controller,panel,calls,notified:()=>notified};
}
const initial=await setup();
assert.equal(initial.calls.length,2);assert.ok(initial.calls.every(item=>!item[1].includes('semantic-enrichment')));
assert.ok(all(initial.panel,node=>node.tagName==='p').some(node=>node.textContent.includes('Агент не нужен')));
assert.equal(one(initial.panel,node=>node.tagName==='summary').textContent,'Движок: автоматический поиск');
const option=one(initial.panel,node=>node.tagName==='option'&&node.value===project.id);
assert.equal(option.textContent,project.name);assert.equal(option.children.length,0);
const engineInput=one(initial.panel,node=>node.dataset.semanticEnginePath==='enginePath');
engineInput.value=' /absolute/typescript.js ';
one(initial.panel,node=>node.tagName==='form').fire('submit');await tick();
assert.deepEqual(initial.calls.at(-1).slice(0,3),['post','/api/mods/semantic-typescript/semantic-engine-settings',{paths:{enginePath:'/absolute/typescript.js'}}]);
assert.equal(initial.notified(),1);assert.equal(one(initial.panel,node=>node.tagName==='summary').textContent,'Движок: выбранные пути');
one(initial.panel,node=>node.dataset.semanticAction==='reset').fire('click');await tick();
assert.deepEqual(initial.calls.at(-1)[2],{paths:{}});assert.equal(engineInput.value,'');
assert.equal(one(initial.panel,node=>node.tagName==='summary').textContent,'Движок: автоматический поиск');
initial.controller.dispose();

/** A failed write keeps the typed override and never reports automatic success. */
const failed=await setup({post:async()=>{throw Error('Укажите абсолютный путь');}});
const invalid=one(failed.panel,node=>node.dataset.semanticEnginePath==='enginePath');invalid.value='relative/path';
one(failed.panel,node=>node.tagName==='form').fire('submit');await tick();
assert.equal(invalid.value,'relative/path');assert.ok(one(failed.panel,node=>node.attributes.role==='status').textContent.includes('абсолютный'));
failed.controller.dispose();

/** Check uses the current published snapshot, no node/path invention, and bounded status-only polling. */
let reads=0;const checkingCalls=[];
const checking=await setup({request:async(url,options)=>{
  if(url==='/api/semantic-engines/settings')return structuredClone(preferences);
  if(url==='/api/projects')return {projects:[project]};
  checkingCalls.push(['get',url,options]);reads++;
  const status=reads===1?'unchecked':reads===2?'checking':'missing';
  return {updatedAt:reads===1?1:2,providers:[{id:'semantic-typescript/typescript',availability:{status},enrichment:'baseline'}],enrichment:'baseline'};
},post:async(url,body,options)=>{checkingCalls.push(['post',url,body,options]);return {providers:[],updatedAt:1};},maxPolls:3});
one(checking.panel,node=>node.dataset.semanticAction==='check').fire('click');await tick();await tick();
const submitted=checkingCalls.find(item=>item[0]==='post');
assert.equal(submitted[1],'/api/projects/project%2Fone/semantic-enrichment');
assert.deepEqual(submitted[2],{snapshotId:'snapshot1',nodeIds:[],force:true});
assert.equal(reads,3);assert.ok(checkingCalls.filter(item=>item[0]==='get').every(item=>item[1].endsWith('?snapshotId=snapshot1')));
assert.ok(one(checking.panel,node=>node.attributes.role==='status').textContent.includes('Базовая карта доступна'));
checking.controller.dispose();

/** Disposal fences a detached panel before another poll; there is no background polling loop. */
let release;let statusReads=0;
const disposed=await setup({request:async url=>{
  if(url==='/api/semantic-engines/settings')return structuredClone(preferences);
  if(url==='/api/projects')return {projects:[project]};statusReads++;return {providers:[],updatedAt:1};
},post:async()=>({providers:[],updatedAt:1}),wait:()=>new Promise(resolve=>{release=resolve;})});
one(disposed.panel,node=>node.dataset.semanticAction==='check').fire('click');await tick();
disposed.controller.dispose();release();await tick();assert.equal(statusReads,1);

/** Completed-but-old observations cannot claim fresh discovery or applied analysis. */
assert.ok(engineStatusText({availability:{status:'available'},enrichment:'baseline'}).includes('ещё не применены'));
assert.ok(engineStatusText({availability:{status:'available'},enrichment:'applied'}).includes('неполный'));
assert.ok(engineStatusText({availability:{status:'available'}},true).startsWith('Сохранённое наблюдение'));
assert.equal(isSemanticOnlyMod(mod),true);
assert.equal(isSemanticOnlyMod({...mod,manifest:{analysis:{semanticProviders:[{}],projectRules:[{}]}}}),false);
console.log('Semantic settings automatic/explicit/reset, no-agent checks, bounded polling, safe text and disposal PASS');

/** A perpetually old observation cannot keep a status loop alive after its configured budget. */
let boundedReads=0;
const bounded=await setup({request:async url=>{
  if(url==='/api/semantic-engines/settings')return structuredClone(preferences);
  if(url==='/api/projects')return {projects:[project]};boundedReads++;return {providers:[],updatedAt:1};
},post:async()=>({providers:[],updatedAt:1}),maxPolls:2});
one(bounded.panel,node=>node.dataset.semanticAction==='check').fire('click');await tick();await tick();
assert.equal(boundedReads,3);assert.ok(one(bounded.panel,node=>node.attributes.role==='status').textContent.includes('ещё не завершилась'));
bounded.controller.dispose();

/** A total client deadline restores controls even when cancellation arrives between polls. */
const deadline=await setup({checkTimeoutMs:5,pollMs:20,wait:milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds)),
  post:async()=>({providers:[],updatedAt:1})});
one(deadline.panel,node=>node.dataset.semanticAction==='check').fire('click');
await new Promise(resolve=>setTimeout(resolve,40));
assert.ok(one(deadline.panel,node=>node.attributes.role==='status').textContent.includes('повторите запрос позже'));
assert.equal(one(deadline.panel,node=>node.dataset.semanticAction==='check').disabled,false);
deadline.controller.dispose();

/** Off and missing-map guards apply before a click can issue engine requests. */
const off=await setup();
const offPanel=off.controller.render({...mod,analysisMode:'off'});
assert.equal(one(offPanel,node=>node.dataset.semanticAction==='check').disabled,true);
one(offPanel,node=>node.dataset.semanticAction==='check').fire('click');await tick();
assert.equal(off.calls.length,2);off.controller.dispose();
const noMap=await setup({request:async url=>url==='/api/semantic-engines/settings'?structuredClone(preferences):{projects:[]}});
assert.equal(one(noMap.panel,node=>node.dataset.semanticAction==='check').disabled,true);
assert.equal(one(noMap.panel,node=>node.dataset.semanticAction==='save').disabled,false);noMap.controller.dispose();

/** A per-field automatic reset preserves other saved overrides and unsaved drafts. */
let savedPaths={enginePath:'/engine.js',nodePath:'/saved/node'};
const fieldCalls=[];
const fieldReset=await setup({request:async url=>url==='/api/semantic-engines/settings'
  ?{...structuredClone(preferences),mods:{[mod.id]:{...savedPaths}}}:{projects:[project]},
  post:async(url,body)=>{fieldCalls.push(body);if(body.mode==='patch'){
    for(const [name,value]of Object.entries(body.paths)){if(value)savedPaths[name]=value;else delete savedPaths[name];}
  }else savedPaths={...body.paths};return {paths:{...savedPaths},mode:Object.keys(savedPaths).length?'explicit':'automatic'};}});
const draftNode=one(fieldReset.panel,node=>node.dataset.semanticEnginePath==='nodePath');
draftNode.value='/unsaved/node';
one(fieldReset.panel,node=>node.dataset.semanticResetField==='enginePath').fire('click');await tick();
assert.deepEqual(fieldCalls[0],{paths:{enginePath:''},mode:'patch'});
assert.deepEqual(savedPaths,{nodePath:'/saved/node'});assert.equal(draftNode.value,'/unsaved/node');
assert.equal(one(fieldReset.panel,node=>node.dataset.semanticEnginePath==='enginePath').value,'');
assert.equal(one(fieldReset.panel,node=>node.tagName==='summary').textContent,'Движок: выбранные пути');
fieldReset.controller.dispose();

/** Presence discovery is read-only, renders paths as text and fills only an unsaved draft. */
const discoveryCalls=[];
const candidatePath='/tmp/<img src=x onerror=evil>/typescript.js';
const discovery=await setup({request:async(url,options)=>{
  discoveryCalls.push(['get',url,options]);
  if(url==='/api/semantic-engines/settings')return structuredClone(preferences);
  if(url==='/api/projects')return {projects:[project]};
  return {candidates:{enginePath:[candidatePath],nodePath:['/node'],unknown:['/never']},candidateStatus:'presence-only'};
},post:async(url,body)=>{discoveryCalls.push(['post',url,body]);return {paths:body.paths,mode:'explicit'};}});
one(discovery.panel,node=>node.dataset.semanticAction==='discover').fire('click');await tick();
assert.equal(discoveryCalls.at(-1)[1],'/api/mods/semantic-typescript/semantic-engine-diagnostics?projectId=project%2Fone');
assert.equal(discoveryCalls.filter(call=>call[0]==='post').length,0);
const renderedPath=one(discovery.panel,node=>node.tagName==='code'&&node.textContent===candidatePath);
assert.equal(renderedPath.children.length,0);
one(discovery.panel,node=>node.dataset.semanticCandidateField==='enginePath').fire('click');await tick();
assert.equal(one(discovery.panel,node=>node.dataset.semanticEnginePath==='enginePath').value,candidatePath);
assert.equal(discoveryCalls.filter(call=>call[0]==='post').length,0);
one(discovery.panel,node=>node.tagName==='form').fire('submit');await tick();
assert.deepEqual(discoveryCalls.at(-1)[2],{paths:{enginePath:candidatePath}});
assert.ok(all(discovery.panel,node=>node.tagName==='a').some(node=>node.href==='/engine-setup.html'));
discovery.controller.dispose();

/** A late discovery response cannot mutate a detached panel. */
let finishDiscovery;
const late=await setup({request:async url=>{
  if(url==='/api/semantic-engines/settings')return structuredClone(preferences);
  if(url==='/api/projects')return {projects:[]};
  return new Promise(resolve=>{finishDiscovery=resolve;});
}});
one(late.panel,node=>node.dataset.semanticAction==='discover').fire('click');await tick();
late.controller.dispose();finishDiscovery({candidates:{enginePath:['/late']}});await tick();
assert.equal(all(late.panel,node=>node.dataset.semanticCandidateField).length,0);
console.log('Per-field reset, presence discovery, unsaved candidate fill and browser-agent guide PASS');

/** The real management-page composition mounts controls and avoids baseline rebuild guidance. */
Element.prototype.replaceChildren=function(...nodes){this.children=[...nodes];};
const ids=['mods-status','mods-directory','mods-list','mods-auto','mods-refresh','mods-open','mods-disable','runtime-info','runtime-path','runtime-select','runtime-embedded'];
const roots=new Map(ids.map(id=>[id,new Element(id.includes('path')?'input':id.includes('status')?'p':'button')]));
const pageDocument={createElement:tag=>new Element(tag),getElementById:id=>roots.get(id),
  querySelectorAll:selector=>selector==='button'?[...new Set([...roots.values()].flatMap(root=>all(root,node=>node.tagName==='button')))]:[]};
const pageCalls=[];
globalThis.document=pageDocument;globalThis.window={addEventListener(){}};
globalThis.fetch=async(url,options)=>{
  pageCalls.push([url,options]);let data;
  if(url==='/api/mods')data={directory:'/local/mods',autoAnalysisEnabled:true,mods:[{...mod,status:'auto',autoAnalysisEligible:true,enabled:false,manifest:{...mod.manifest,name:'TypeScript',version:'1',apiVersion:1}}]};
  else if(url==='/api/semantic-engines/settings')data=structuredClone(preferences);
  else if(url==='/api/projects')data={projects:[project]};
  else if(url==='/api/integrations/graphify')data={mode:'embedded',version:'test',adapterVersion:'1'};
  else throw Error('Unexpected management-page request: '+url);
  return {ok:true,json:async()=>data};
};
await import('../web/mods-page.js');
const card=one(roots.get('mods-list'),node=>node.tagName==='article');
assert.ok(all(card,node=>node.className==='semantic-engine-settings').length===1);
assert.ok(all(card,node=>node.tagName==='p').some(node=>node.textContent.includes('перестраивать не нужно')));
assert.ok(!all(card,node=>node.tagName==='p').some(node=>node.textContent.includes('после обновления карты')));
assert.ok(pageCalls.every(([url])=>!url.includes('agent')&&!url.includes('semantic-enrichment')));
console.log('Management-page composition and semantic-only baseline guidance PASS');
