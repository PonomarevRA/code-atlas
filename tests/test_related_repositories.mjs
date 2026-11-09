/** Выбор связанных индексов принадлежит primary; завершение задания не переключает открытый репозиторий. */
import assert from 'node:assert/strict';
import {readRelatedIds,RelatedRepositories} from '../web/related-repositories.js';
class Element {
 constructor(){this.children=[];this.listeners={};this.value='';}
 get options(){return this.children;}
 addEventListener(name,callback){this.listeners[name]=callback;}
 replaceChildren(...nodes){this.children=nodes;}
 append(...nodes){this.children.push(...nodes);}
 setAttribute(name,value){this[name]=value;}
}
const elements=new Map();
globalThis.document={createElement:()=>new Element(),getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);}};
globalThis.Option=class extends Element {constructor(text,value){super();this.textContent=text;this.value=value;}};
const element=id=>document.getElementById(id);
const saved=new Map();const storage={getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value)};
saved.set('code-atlas-related-repositories:one',JSON.stringify(['one','two','two',3]));
assert.deepEqual(readRelatedIds(storage,'one'),['two']);
saved.set('code-atlas-related-repositories:broken','{');assert.deepEqual(readRelatedIds(storage,'broken'),[]);
const state={project:{id:'one'},projects:[{id:'one',name:'One',status:'completed'},{id:'two',name:'Two',status:'completed'}],job:null,submitting:false};
let changed=0,resolveBuild;
const calls=[];
const controller=new RelatedRepositories({state,storage,onChange:()=>changed++,controls:()=>{},wait:async()=>{},fetcher:async(url,options)=>{
 calls.push({url,options});
 if(options?.method==='POST')return new Promise(resolve=>{resolveBuild=resolve;});
 if(url==='/api/projects')return {ok:true,json:async()=>({projects:[...state.projects,{id:'third',name:'Third',status:'completed'}]})};
 if(url==='/api/jobs/job')return {ok:true,json:async()=>({id:'job',status:'completed'})};
 throw new Error(`Unexpected request ${url}`);
}});
controller.render();assert.deepEqual(controller.getSelected(),['two']);assert.equal(element('related-summary').textContent,'Связанные репозитории (1)');
controller.attach('one');controller.attach('two');assert.deepEqual(controller.getSelected(),['two']);
state.project={id:'two'};controller.resetForPrimary();assert.deepEqual(controller.getSelected(),[]);assert.equal(element('related-summary').textContent,'Связанные репозитории (0)');
controller.attach('one');assert.deepEqual(controller.getSelected(),['one']);
state.project={id:'one'};controller.resetForPrimary();assert.deepEqual(controller.getSelected(),['two']);
element('related-path').value='/local/third';
const building=controller.indexAndAttach();assert.equal(controller.busy,true);assert.equal(state.submitting,true);
assert.equal(JSON.parse(calls[0].options.body).path,'/local/third');
state.project={id:'two'};controller.resetForPrimary();
resolveBuild({ok:true,json:async()=>({project:{id:'third'},job:{id:'job',status:'queued'}})});await building;
assert.equal(state.project.id,'two');assert.equal(element('projects').value,'two');
assert.deepEqual(controller.getSelected('one'),['two','third']);assert.deepEqual(controller.getSelected('two'),['one']);
assert.equal(state.job,null);assert.equal(controller.busy,false);assert.equal(state.submitting,false);
controller.save('two',Array.from({length:8},(_,i)=>`repo-${i}`));controller.attach('ninth');assert.equal(controller.getSelected().length,8);
assert.ok(element('related-status').textContent.includes('8'));
console.log('Related repositories preferences, caps and job completion keep primary identity PASS');
