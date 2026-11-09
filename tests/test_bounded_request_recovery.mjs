/** HTTP failures retain their status even when an intermediary returns HTML or null. */
import assert from 'node:assert/strict';
import {requestJson} from '../web/bounded-request.js';

for (const payload of [null, [], 'unavailable']) {
  await assert.rejects(requestJson('/failure', {}, {
    fetcher: async () => ({ok:false, status:503, json:async () => payload}),
  }), error => error.status === 503 && error.message.includes('503'));
}
await assert.rejects(requestJson('/gateway', {}, {
  fetcher: async () => ({ok:false, status:502, json:async () => {throw new SyntaxError('private HTML response');}}),
}), error => error.status === 502 && !error.message.includes('private HTML'));
await assert.rejects(requestJson('/invalid-success', {}, {
  fetcher: async () => ({ok:true, status:200, json:async () => {throw new SyntaxError('invalid JSON');}}),
}), error => error.name === 'ResponseFormatError' && error.status === 200);
await assert.rejects(requestJson('/detail', {}, {
  fetcher: async () => ({ok:false, status:409, json:async () => ({detail:'Карта обновилась'})}),
}), error => error.status === 409 && error.message === 'Карта обновилась');
console.log('Bounded request recovery PASS');
for (const jobId of ['a'.repeat(32),'invalid',42]) {
  await assert.rejects(requestJson('/repair',{}, {
    fetcher:async()=>({ok:false,status:409,json:async()=>({error:'Восстановление карты',code:'SNAPSHOT_REBUILD_STARTED',jobId})}),
  }), error=>error.code==='SNAPSHOT_REBUILD_STARTED' && error.jobId===(jobId==='a'.repeat(32)?jobId:undefined));
}
