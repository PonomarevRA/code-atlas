/** Временная потеря связи не освобождает занятый worker и не применяет поздний статус. */
import assert from 'node:assert/strict';
import {monitorJob} from '../web/job-monitor.js';
let active='job',reads=0;const delays=[],states=[],errors=[];
await monitorJob({id:'job'},{
 isCurrent:id=>active===id,
 read:async()=>{reads++;if(reads===1)throw new Error('offline');return{status:reads===2?'running':'completed'};},
 onTransientError:(error,delay)=>{assert.equal(active,'job');errors.push(error.message);},
 onState:status=>{states.push(status.status);if(status.status==='completed')active=null;},
 wait:async delay=>{delays.push(delay);assert.equal(active,'job');}
});
assert.equal(reads,3);assert.deepEqual(errors,['offline']);assert.deepEqual(states,['running','completed']);
assert.deepEqual(delays,[3000,1500]);assert.equal(active,null);
let oldCallbacks=0;
await monitorJob({id:'old'},{
 isCurrent:id=>active===id,
 read:async()=>{throw new Error('Must not fetch cancelled job');},
 onState:()=>oldCallbacks++,onTransientError:()=>oldCallbacks++
});assert.equal(oldCallbacks,0);
active='new';const backoff=[];
await monitorJob({id:'new'},{
 isCurrent:id=>active===id,read:async()=>{throw new Error('offline');},
 onState:()=>assert.fail('No terminal state received'),onTransientError:()=>assert.equal(active,'new'),
 wait:async delay=>{backoff.push(delay);if(backoff.length===6)active=null;}
});
assert.deepEqual(backoff,[3000,6000,12000,15000,15000,15000]);
console.log('Job monitor transient retry and bounded backoff PASS');
