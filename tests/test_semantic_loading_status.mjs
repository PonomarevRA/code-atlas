/** A user-started enrichment stays visible through queued state; closing stops follow-up reads. */
import assert from 'node:assert/strict';
import {renderNodeSemanticStatus} from '../web/node-semantic-status.js';
import {loadingSnapshot} from '../web/loading-state.js';
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.textContent='';}
 append(...children){this.children.push(...children);}
 addEventListener(name,fn){this.listeners[name]=fn;}
}
globalThis.document={createElement:tag=>new Element(tag)};
const controller=new AbortController();let calls=0;
const root=renderNodeSemanticStatus({projectId:'p',snapshotId:'s',nodeId:'n'},null,{
 signal:controller.signal,isCurrent:()=>true,fetcher:async(url,options)=>{
  calls++;return {ok:true,json:async()=>({enrichment:options?.method==='POST'?'queued':'applied',providers:[]})};
 },
});
const action=root.children[2];
const pending=action.listeners.click();
await new Promise(resolve=>setTimeout(resolve,1));assert.equal(loadingSnapshot().count,1);
await pending;assert.equal(calls,2);assert.equal(loadingSnapshot().count,0);assert.equal(action.disabled,false);
const cancelled=action.listeners.click();await new Promise(resolve=>setTimeout(resolve,1));controller.abort();await cancelled;
assert.equal(loadingSnapshot().count,0);assert.equal(calls,3,'abort prevents the delayed GET');
console.log('Semantic loading status PASS: queued-to-applied and cancelled observer cleanup');
