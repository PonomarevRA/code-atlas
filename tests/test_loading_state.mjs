/** Request feedback follows complete bodies and cancellation, independently of rendered content. */
import assert from 'node:assert/strict';
import {beginLoading, loadingSnapshot, subscribeLoading, withLoading} from '../web/loading-state.js';
import {requestLoadingLabel} from '../web/loading-labels.js';
import {requestJson} from '../web/bounded-request.js';
const deferred = () => {let resolve, reject; const promise = new Promise((yes,no) => {resolve=yes; reject=no;}); return {promise,resolve,reject};};
const seen=[];
const unsubscribe=subscribeLoading(value=>seen.push(value.count));
const first=deferred(),second=deferred();
const a=requestJson('/api/projects/one/graph?secret=not-display',{}, {fetcher:async()=>({ok:true,json:()=>first.promise})});
const b=requestJson('/api/projects/one/source',{}, {fetcher:async()=>({ok:true,json:()=>second.promise})});
assert.equal(loadingSnapshot().count,2);
assert.ok(!JSON.stringify(loadingSnapshot()).includes('secret'));
await Promise.resolve();
assert.equal(loadingSnapshot().count,2,'headers are not completion');
first.resolve({nodes:[]}); await a;
assert.equal(loadingSnapshot().count,1);
second.resolve({text:'source'}); await b;
assert.equal(loadingSnapshot().count,0);
for (const [path,method] of [['/api/jobs/job','GET'],['/api/projects/p/code-health/refresh','GET'],['/api/projects/p/documentation-index','GET'],['/api/projects/p/semantic-enrichment','GET']]) {
  assert.equal(requestLoadingLabel(path,{method}),null);
  const wait=deferred();
  const operation=requestJson(path,{method},{fetcher:async()=>({ok:true,json:()=>wait.promise})});
  assert.equal(loadingSnapshot().count,0);
  wait.resolve({}); await operation;
}
assert.ok(requestLoadingLabel('/api/projects/p/code-health/refresh',{method:'POST'}));
await assert.rejects(requestJson('/api/projects/p/source',{}, {timeoutMs:10,fetcher:()=>new Promise(()=>{})}),error=>error.name==='TimeoutError');
assert.equal(loadingSnapshot().count,0);
const controller=new AbortController();
const cancelled=requestJson('/api/projects/p/source',{signal:controller.signal},{fetcher:()=>new Promise(()=>{})});
assert.equal(loadingSnapshot().count,1); controller.abort();
await assert.rejects(cancelled,error=>error.name==='AbortError');
assert.equal(loadingSnapshot().count,0);
await assert.rejects(requestJson('/api/projects/p/source',{}, {fetcher:async()=>({ok:true,status:200,json:async()=>{throw new SyntaxError();}})}),error=>error.name==='ResponseFormatError');
await assert.rejects(withLoading('Failure',async()=>{throw new Error('failure');}));
assert.equal(loadingSnapshot().count,0);
const releases=Array.from({length:150},(_,i)=>beginLoading('Work '+i));
assert.equal(loadingSnapshot().count,150);
releases[0].update({ru:'Первый',en:'First'});
for(const release of releases) {release();release();release.update('late');}
assert.equal(loadingSnapshot().count,0);
const release=beginLoading({ru:'Русский',en:'English'});
const detached=loadingSnapshot(); detached.label.ru='changed';
assert.equal(loadingSnapshot().label.ru,'Русский');release();
unsubscribe();
assert.deepEqual(seen.slice(0,5),[0,1,2,1,0]);
console.log('Loading lifecycle PASS: body, concurrency, quiet polling, timeout, abort, format, overflow and cleanup');
