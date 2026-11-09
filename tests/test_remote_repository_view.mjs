/** Получение репозитория не перехватывает новый пользовательский выбор и переживает сетевой сбой. */
import assert from 'node:assert/strict';
import {RemoteRepositoryPanel} from '../web/remote-repositories.js';
class Element{constructor(){this.value='';this.listeners={};this.children=[];}addEventListener(n,f){this.listeners[n]=f;}append(...nodes){this.children.push(...nodes);}}
const elements=new Map();const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
globalThis.document={getElementById:get,createElement:()=>new Element()};
const stored=new Map(),storage={getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)};
let state={revision:1,submitting:false,job:null},postResolve,reads=0;
const calls=[],opened=[];
get('source-mode').value='remote';get('remote-url').value='https://host/repo';get('remote-ref').value='main';
const panel=new RemoteRepositoryPanel({state,storage,controls:()=>{},refreshProjects:async()=>{},openProject:async id=>opened.push(id),wait:async()=>{},fetcher:(url,options)=>{calls.push({url,options});if(options?.method==='POST')return new Promise(resolve=>postResolve=resolve);reads++;return Promise.resolve({ok:true,json:async()=>({id:'j',status:'completed',projectId:'new'})});}});
const active=panel.submit();assert.equal(state.submitting,true);await panel.submit();assert.equal(calls.length,1);
state.revision=2;postResolve({ok:true,json:async()=>({job:{id:'j',status:'queued'}})});await active;
assert.deepEqual(opened,[]);assert.equal(reads,1);assert.equal(state.submitting,false);
assert.equal(stored.size,0);assert.deepEqual(JSON.parse(calls[0].options.body),{url:'https://host/repo',ref:'main'});
// Reload resume: первый сетевой сбой не должен потерять сохранённый ID задачи.
stored.set('code-atlas-remote-job','resumed');let attempts=0;const waits=[];
const resumed=new RemoteRepositoryPanel({state,storage,controls:()=>{},refreshProjects:async()=>{},openProject:async id=>opened.push(id),wait:async delay=>{waits.push(delay);assert.equal(stored.get('code-atlas-remote-job'),'resumed');assert.equal(state.submitting,true);},fetcher:async()=>{attempts++;if(attempts===1)throw new Error('temporary network');return {ok:true,json:async()=>({id:'resumed',status:'completed',projectId:'ready'})};}});
await resumed.resume();assert.equal(attempts,2);assert.equal(waits.length,1);assert.equal(opened.at(-1),'ready');assert.equal(stored.size,0);
console.log('Remote UI pending guard, selection and transient resume PASS');
