import assert from 'node:assert/strict';
import {MapCleanupState,cleanupMapKey,cleanupMapURL} from '../web/map-cleanup-state.js';
import {mapCleanupLabels} from '../web/map-cleanup-labels.js';
import {MapCleanupController} from '../web/map-cleanup.js';

/**
 * Minimal DOM fixture; markup parsing is forbidden so server strings must stay literal.
 */
class Element {
  constructor(tag,document){this.tag=tag;this.ownerDocument=document;this.children=[];this.listeners={};this.attributes={};this.disabled=false;this.hidden=false;this._text='';}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
  set textContent(value){this._text=String(value);this.children=[];}
  set innerHTML(value){throw new Error('Unsafe markup');}
  get isConnected(){return Boolean(this.parent?.isConnected||this===this.ownerDocument.body);}
  append(...items){for(const item of items){item.remove();item.parent=this;this.children.push(item);}}
  insertBefore(item,next){item.parent=this;this.children.splice(this.children.indexOf(next),0,item);}
  replaceChildren(...items){this._text='';for(const child of this.children)child.parent=null;this.children=[];this.append(...items);}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);this.parent=null;}
  setAttribute(key,value){this.attributes[key]=String(value);if(key==='id')this.id=String(value);}
  removeAttribute(key){delete this.attributes[key];}
  addEventListener(name,callback){(this.listeners[name]||=[]).push(callback);}
  removeEventListener(name,callback){this.listeners[name]=(this.listeners[name]||[]).filter(fn=>fn!==callback);}
  dispatch(name){for(const fn of this.listeners[name]||[])fn({preventDefault(){}});}
  focus(){if(!this.disabled)this.ownerDocument.activeElement=this;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  querySelectorAll(selector){const result=[];for(const child of this.children){const match=selector.startsWith('#')?child.id===selector.slice(1):selector.startsWith('.')?child.className===selector.slice(1):child.tag===selector;if(match)result.push(child);result.push(...child.querySelectorAll(selector));}return result;}
  showModal(){this.open=true;}
  close(){this.open=false;queueMicrotask(()=>this.dispatch('close'));}
}
/**
 * Owns a document and deferred transport for one independent storage fixture.
 * @param {string} locale Declared host locale; RU by default, EN exercised for recovery text.
 * @returns {object} Controller, safe synthetic DOM and explicitly resolvable pending requests.
 */
function fixture(locale='ru',receiptStorage=undefined) {
  const document={createElement(tag){return new Element(tag,this);},createTextNode(text){const node=new Element('#text',this);node.textContent=text;return node;}};
  document.body=new Element('body',document);document.activeElement=document.body;
  const root=document.createElement('section'),wrapper=document.createElement('div');wrapper.className='table-scroll';
  for(const [tag,id] of [['button','reload-storage'],['p','storage-status']]){const node=document.createElement(tag);node.id=id;root.append(node);}
  const table=document.createElement('tbody');table.id='storage-maps';wrapper.append(table);root.append(wrapper);document.body.append(root);
  const requests=[];const controller=new MapCleanupController(root,{localeContext:{locale,availableLocales:['ru','en'],fallbackLocale:'ru'},receiptStorage,fetcher:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))});
  return {root,document,controller,requests};
}
const map=(id,title,logicalBytes=100)=>({id,title,url:'/data.html?mapId='+id,status:'completed',builtAt:'2026-10-20T12:00:00Z',sizeBytes:900,freshness:{status:'not-checked'},cleanup:{available:true,logicalBytes,analysisVersion:null,versionStatus:'unknown'}});
const catalog={maps:[map('same','<img src=x onerror=evil>'),{...map('blocked','No snapshots'),cleanup:{available:false,reason:'No clearable snapshots'}}],codeMaps:[{...map('same','Code'),url:'javascript:evil()'}],totalMapBytes:2700};
const codeKey=cleanupMapKey({kind:'code',id:'same'}),dataKey=cleanupMapKey({kind:'data',id:'same'});
const response=(request,value,status=200)=>request.resolve({ok:status<400,status,json:async()=>value});
/**
 * Creates one finite preview/operation receipt without touching real map storage.
 * @param {Array} maps Explicit synthetic kind/id pairs, never user files.
 * @param {object} extra Overrides a status/progress field to exercise one server transition.
 * @returns {object} Same-schema receipt with a future preview deadline.
 */
function plan(maps,extra={}) {return {schemaVersion:'atlas-map-cleanup-v1',planId:'plan1',expiresAt:new Date(Date.now()+600000).toISOString(),status:'ready',maps:maps.map(item=>({...item,title:item.kind+' map',builtAt:'2026-10-20T12:00:00Z',logicalBytes:100,snapshotCount:2})),totals:{mapCount:maps.length,sizeBytes:maps.length*100},preserved:['sources','views'],warnings:[],...extra};}
async function load(f,data=catalog){const promise=f.controller.load(true);response(f.requests.at(-1),data);await promise;}
async function preview(f){const promise=f.controller.preview();response(f.requests.at(-1),plan(f.controller.state.selection().maps));await promise;}

const labels=mapCleanupLabels({locale:'de',availableLocales:['ru','en'],fallbackLocale:'en'});
for(const unsafe of ['javascript:evil()','//evil.invalid/','/\\evil.invalid','/api/technical/open-storage','https://evil.invalid/data.html'])assert.equal(cleanupMapURL(unsafe),null);
assert.equal(cleanupMapURL('/data.html?mapId=one'),'/data.html?mapId=one');
assert.equal(labels.locale,'en');assert.equal(labels.text('cancel'),'Cancel');assert.equal(labels.bytes(null),'unknown');assert.equal(labels.date('invalid'),'—');
const state=new MapCleanupState();state.setCatalog(catalog);assert.equal(state.selection().count,0);
assert(state.choose(codeKey,true));assert(state.choose(dataKey,true));assert.equal(state.selection().count,2);assert.equal(state.selection().bytes,200);
assert.notEqual(codeKey,dataKey);assert.equal(state.choose('unknown',true),false);
for(const invalid of [{...plan(state.selection().maps),maps:[{kind:'code',id:'substitution'}]}, {...plan(state.selection().maps),expiresAt:'bad'}, {...plan(state.selection().maps),planId:''}])assert.throws(()=>state.accept(invalid,state.selection().maps),/INVALID_CLEANUP_PLAN/);
const many={maps:Array.from({length:51},(_,i)=>map(String(i),'Map'+i)),codeMaps:[]};state.selected.clear();state.setCatalog(many);
for(const row of state.rows.values())state.choose(cleanupMapKey(row),true);
assert.equal(state.selection().count,50,'selection quota never evicts earlier choices');
state.setCatalog({maps:[map('0','Map0',null)],codeMaps:[]});assert.equal(state.selection().unknown,1);assert.equal(state.selection().count,1);

const f=fixture();await load(f);const c=f.controller;
assert.equal(c.state.selection().count,0);assert.equal(c.previewButton.disabled,true);
assert.equal(c.rows.get(dataKey).link.textContent,'<img src=x onerror=evil>');assert.equal(c.rows.get(codeKey).link.attributes.href,undefined);
assert.equal(c.rows.get(cleanupMapKey({kind:'data',id:'blocked'})).checkbox.disabled,true);
assert.match(c.rows.get(codeKey).cells[4].textContent,/неизвестна/);
c.state.choose(codeKey,true);c.state.choose(dataKey,true);c.sync();const retained=c.rows.get(codeKey).row;
assert.match(c.summary.textContent,/2\/50/);assert(!c.summary.textContent.includes('900'));
const refresh=c.load();assert.equal(c.rows.get(codeKey).row,retained);response(f.requests.at(-1),{error:'fail'},500);await refresh;
assert.equal(c.rows.get(codeKey).row,retained);assert.equal(c.state.selection().count,2);
const failedPreview=c.preview();assert.equal(c.rows.get(codeKey).checkbox.disabled,true);response(f.requests.at(-1),{error:'busy',code:'CLEANUP_BUSY'},409);await failedPreview;
assert.equal(c.state.selection().count,2);assert.equal(c.state.plan,null);assert.match(c.notice.textContent,/занята/);
c.previewButton.focus();await preview(f);assert.equal(c.dialog.open,true);assert.equal(f.document.activeElement,c.cancel);
assert.equal(c.state.confirmed,false);assert.match(c.dialog.textContent,/не имеет отмены/);assert.match(c.dialog.textContent,/сохранённые виды/);
c.closeDialog();await Promise.resolve();assert.equal(c.dialog.open,false);assert.equal(c.state.plan,null);assert.equal(c.state.selection().count,2);assert.equal(f.document.activeElement,c.previewButton);
await preview(f);const target=c.state.plan;
const execute=c.execute(),count=f.requests.length;await c.execute();assert.equal(f.requests.length,count,'duplicate submit is ignored');
assert.equal(f.requests.at(-1).url,'/api/technical/map-cleanup/plan1/execute');assert.deepEqual(JSON.parse(f.requests.at(-1).options.body),{confirm:true});
f.requests.at(-1).reject(new TypeError('Lost acknowledgement'));await execute;
assert.equal(c.state.phase,'uncertain');assert.equal(c.dialog.open,false,'lost acknowledgement exposes in-flow check outside modal');assert(!c.notice.textContent.includes('0 MiB'),'readyplan0bytes is not an observed deletion result');assert.equal(c.state.plan,target);assert.equal(c.retryButton.hidden,true);assert.equal(c.checkButton.hidden,false);
await c.execute();assert.equal(f.requests.length,count,'uncertain reply cannot immediately repeat execute');
const check=c.check();assert.equal(f.requests.at(-1).url,'/api/technical/map-cleanup/plan1');response(f.requests.at(-1),target);await check;
assert.equal(c.state.phase,'retry');assert.equal(c.retryButton.hidden,false);
const retry=c.execute();response(f.requests.at(-1),{...target,status:'partial',mapsCleared:true,canRetry:true,pendingPurgeCount:1});await retry;
assert.equal(c.state.selection().count,0);assert.equal(c.rows.get(codeKey).row,retained);assert.equal(c.state.rows.get(codeKey).status,'not-built');
assert.equal(c.state.rows.get(codeKey).sizeBytes,null,'preserved view bytes are not guessed as zero');
assert.equal(c.rows.get(codeKey).checkbox.disabled,true);assert.equal(c.state.rows.size,3,'code and data registrations remain');
// A retry of committed own quarantine must not expire like an unexecuted preview.
c.state.plan={...c.state.plan,expiresAt:'2000-01-01T00:00:00Z'};
const purge=c.execute();response(f.requests.at(-1),{...c.state.plan,status:'completed',mapsCleared:true,canRetry:false,pendingPurgeCount:0});await purge;
assert.match(c.notice.textContent,/Снимки очищены/);assert.equal(c.state.phase,'idle');
response(f.requests.at(-1),{maps:catalog.maps.map(item=>({...item,cleanup:{available:false},status:'not-built'})),codeMaps:[{...catalog.codeMaps[0],cleanup:{available:false},status:'not-built'}]});await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(c.state.rows.size,3);c.dispose();

// Durable server records recover a page/server restart with GET only, without selecting or executing maps.
const restored=fixture();await load(restored,{...catalog,cleanupStorage:{hasPendingPhysicalData:true}});
assert.match(restored.controller.pendingPhysical.textContent,/ожидающие очистки/);
const history=restored.controller.loadRecovery();const recorded=plan([{kind:'code',id:'same'}],{status:'partial',mapsCleared:true,canRetry:true,pendingPurgeCount:2,removedBytes:null});
response(restored.requests.at(-1),{schemaVersion:'atlas-map-cleanup-v1',operations:[recorded],truncated:false});await history;
assert.equal(restored.controller.recoveryList.children.length,1);assert.equal(restored.controller.state.selection().count,0);
const resume=restored.controller.recover('plan1');response(restored.requests.at(-1),recorded);await resume;
assert.equal(restored.requests.some(request=>request.options.method==='POST'),false,'reload recovery never executes or creates a plan');
assert.equal(restored.controller.state.phase,'retry');assert.match(restored.controller.notice.textContent,/2/);
assert(!restored.controller.notice.textContent.includes('0 MiB'),'unknown removedBytes never becomes zero freed space');
restored.controller.dispose();

// A recovered uncommitted plan needs a fresh visible confirmation, while a newer rebuilt snapshot is preserved.
const recoveredReady=fixture();await load(recoveredReady);const uncommitted=recoveredReady.controller.recover('plan1');
response(recoveredReady.requests.at(-1),plan([{kind:'code',id:'same'}]));await uncommitted;
assert.equal(recoveredReady.controller.state.confirmed,false);assert.equal(recoveredReady.controller.dialog.open,true);assert.equal(recoveredReady.document.activeElement,recoveredReady.controller.cancel);
assert.equal(recoveredReady.controller.state.selection().count,0);recoveredReady.controller.dispose();
const newerState=new MapCleanupState();newerState.setCatalog({maps:[],codeMaps:[{...catalog.codeMaps[0],builtAt:'2026-10-28T12:00:00Z'}]});
newerState.accept(recorded,recorded.maps);assert.equal(newerState.rows.get(codeKey).status,'completed','old quarantine result cannot reset a newly rebuilt map');

const stale=fixture('en');await load(stale);stale.controller.state.choose(codeKey,true);await preview(stale);
const staleExecute=stale.controller.execute();response(stale.requests.at(-1),{error:'changed',code:'CLEANUP_STALE'},409);await staleExecute;
assert.equal(stale.controller.state.plan,null);assert.equal(stale.controller.state.selected.size,1);assert.equal(stale.controller.previewButton.disabled,false);assert.match(stale.controller.notice.textContent,/review the selection again/);
stale.controller.dispose();

// Session persistence stores only the confirmed opaque ID, including a completed operation whose reply was lost.
const saved=new Map();const storage={getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)};
const beforeReload=fixture('ru',storage);await load(beforeReload);beforeReload.controller.state.choose(codeKey,true);await preview(beforeReload);
const lost=beforeReload.controller.execute();beforeReload.requests.at(-1).reject(new TypeError('lost'));await lost;
assert.deepEqual([...saved.entries()],[['atlas-map-cleanup-active-plan','plan1']]);beforeReload.controller.dispose();
const afterReload=fixture('ru',storage);await load(afterReload);const records=afterReload.controller.loadRecovery();response(afterReload.requests.at(-1),{schemaVersion:'atlas-map-cleanup-v1',operations:[],truncated:false});await records;
assert.equal(afterReload.controller.recoveryList.children.length,1,'completed-but-unacknowledged ID remains discoverable after reload');
assert.equal(afterReload.controller.state.phase,'uncertain');assert.equal(afterReload.controller.previewButton.disabled,true);
const offlineRead=afterReload.controller.check();afterReload.requests.at(-1).reject(new TypeError('offline'));await offlineRead;
assert.equal(afterReload.controller.state.phase,'uncertain');assert.equal(saved.size,1,'failed status read preserves the same receipt');
const readLast=afterReload.controller.recover('plan1');response(afterReload.requests.at(-1),plan([{kind:'code',id:'same'}],{status:'completed',mapsCleared:true,canRetry:false,removedBytes:100}));await readLast;
assert.equal(afterReload.requests.some(request=>request.options.method==='POST'),false);assert.equal(saved.size,0);afterReload.controller.dispose();

// Precise finite backend reasons survive transport without exposing arbitrary server messages or paths.
const classified=fixture();await load(classified);classified.controller.state.choose(codeKey,true);
for(const reason of ['unrecognized-snapshot-file','unrecognized-snapshot-directory']){
  const denied=classified.controller.preview();response(classified.requests.at(-1),{error:'private detail must stay hidden',code:'CLEANUP_UNSAFE',reason},409);await denied;
  assert.match(classified.controller.notice.textContent,/неизвестные файлы или папки/);assert(!classified.controller.notice.textContent.includes('private detail'));
  assert.equal(classified.controller.state.selected.size,1);assert.equal(classified.controller.state.plan,null);
}
const unknownDenied=classified.controller.preview();response(classified.requests.at(-1),{error:'private detail',code:'CLEANUP_UNSAFE',reason:'untrusted'},409);await unknownDenied;
assert.match(classified.controller.notice.textContent,/Безопасность очистки не подтверждена/);classified.controller.dispose();

// Metadata-only removal is independent of snapshot selection and always has its own explicit consequence.
const broken={maps:[{...map('missing','Missing snapshot'),cleanup:{available:false,reason:'missing-snapshot',forgetAvailable:true}}],codeMaps:[{...map('same','Code'),cleanup:{available:true,forgetAvailable:true}}]};
const forgetFixture=fixture();await load(forgetFixture,broken);const forget=forgetFixture.controller;
for(const reason of ['unrecognized-snapshot-file','unrecognized-snapshot-directory'])assert.match(forget.errorText({code:'CLEANUP_UNSAFE',reason}),/неизвестные файлы или папки/);
assert.match(forget.errorText({code:'CLEANUP_UNSAFE',reason:'untrusted-reason'}),/Безопасность очистки не подтверждена/);
assert(!forget.errorText({code:'CLEANUP_UNSAFE',reason:'untrusted-reason',message:'/private/secret'}).includes('/private/secret'));

const missingKey=cleanupMapKey({kind:'data',id:'missing'}),missingRow=forget.rows.get(missingKey).row;
assert.equal(forget.state.selected.size,0);assert.equal(forget.rows.get(missingKey).forget.hidden,false);
assert.match(forget.rows.get(missingKey).reason.textContent,/указанный в записи карты, отсутствует/);
forget.state.choose(codeKey,true);forget.sync();
forget.rows.get(missingKey).forget.focus();const forgetPreview=forget.previewForget(missingKey);
assert.deepEqual(JSON.parse(forgetFixture.requests.at(-1).options.body),{maps:[{kind:'data',id:'missing'}],action:'forget-registration'});
assert.equal(forget.rows.get(missingKey).forget.disabled,true);
const forgetPlan=plan([{kind:'data',id:'missing'}],{action:'forget-registration',registrationRemoved:false,mapsCleared:false,totals:{mapCount:1,sizeBytes:0}});
response(forgetFixture.requests.at(-1),forgetPlan);await forgetPreview;
assert.equal(forgetFixture.document.activeElement,forget.cancel);assert.match(forget.dialogTitle.textContent,/Удалить запись/);
assert.match(forget.consequences.textContent,/только запись/);assert.match(forget.consequences.textContent,/не удаляются/);
assert.match(forget.preserved.textContent,/сохранённые виды остаются доступны/);assert.equal(forget.estimates.hidden,true);
assert.equal(forget.confirm.textContent,'Удалить запись из списка');assert.equal(forget.state.selected.size,1);
forget.closeDialog();await Promise.resolve();assert.equal(forgetFixture.document.activeElement,forget.rows.get(missingKey).forget);
assert.equal(forget.rows.get(missingKey).row,missingRow);assert.equal(forget.state.rows.size,2);
// Reusing the native dialog for a later snapshot action must reset every visible consequence.
await preview(forgetFixture);assert.match(forget.dialogTitle.textContent,/Очистить снимки/);
assert.match(forget.consequences.textContent,/текущие и предыдущие снимки/);assert.equal(forget.estimates.hidden,false);
assert.equal(forget.confirm.textContent,'Очистить снимки — оставить запись');forget.closeDialog();await Promise.resolve();
// A substituted default-clear receipt cannot authorize the separately requested metadata action.
const wrongAction=forget.previewForget(missingKey);response(forgetFixture.requests.at(-1),plan([{kind:'data',id:'missing'}]));await wrongAction;
assert.equal(forget.dialog.open,false);assert.equal(forget.state.plan,null);assert.match(forget.notice.textContent,/некорректный план/);
const ownPreview=forget.previewForget(missingKey);response(forgetFixture.requests.at(-1),forgetPlan);await ownPreview;
const forgetExecute=forget.execute();const oneSubmit=forgetFixture.requests.length;await forget.execute();assert.equal(forgetFixture.requests.length,oneSubmit);
forgetFixture.requests.at(-1).reject(new TypeError('Lost acknowledgement'));await forgetExecute;
assert.equal(forget.dialog.open,false);assert.equal(forget.state.rows.size,2);assert.equal(forget.rows.get(missingKey).row,missingRow);
assert.equal(forget.state.phase,'uncertain');assert.equal(forget.checkButton.hidden,false);
const readForget=forget.check();assert.equal(forgetFixture.requests.at(-1).options.method,undefined);
response(forgetFixture.requests.at(-1),{...forgetPlan,status:'completed',registrationRemoved:true});await readForget;
assert.equal(forget.state.rows.has(missingKey),true,'acknowledgement alone cannot hide a newer registration');
response(forgetFixture.requests.findLast(request=>request.url==='/api/technical/storage'),{...broken,maps:[]});await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(forget.state.rows.has(missingKey),false);assert.equal(forget.rows.has(missingKey),false);
assert.equal(forget.state.rows.has(codeKey),true);assert.equal(forget.state.selected.has(codeKey),true,'unrelated snapshot selection survives row removal');
assert.match(forget.notice.textContent,/по этому плану подтверждено/);assert(!forget.notice.textContent.includes('MiB'));
forget.dispose();

// Plan identity includes action, even when a same-ID status response changes no target identities.
const actionState=new MapCleanupState();actionState.setCatalog(broken);actionState.accept(forgetPlan,forgetPlan.maps,'forget-registration');
for(const invalid of [{...forgetPlan,action:'clear-snapshots'},{...forgetPlan,action:'forget-registration',mapsCleared:true},{...forgetPlan,action:'unexpected'}]) {
  assert.throws(()=>actionState.accept(invalid,forgetPlan.maps,'forget-registration'),/INVALID_CLEANUP_PLAN/);
  assert(actionState.rows.has(missingKey));
}
const clearState=new MapCleanupState();assert.throws(()=>clearState.accept({...plan([{kind:'code',id:'same'}]),registrationRemoved:true},[{kind:'code',id:'same'}]),/INVALID_CLEANUP_PLAN/);
// Completed old forget receipts do not drop a new registration; failed catalog reads retain visible rows.
const newRegistration=fixture();await load(newRegistration,broken);
const oldForgetRead=newRegistration.controller.recover('plan1');response(newRegistration.requests.at(-1),{...forgetPlan,status:'completed',registrationRemoved:true});await oldForgetRead;
assert(newRegistration.controller.state.rows.has(missingKey));
response(newRegistration.requests.findLast(request=>request.url==='/api/technical/storage'),{error:'offline'},500);await new Promise(resolve=>setTimeout(resolve,0));
assert(newRegistration.controller.state.rows.has(missingKey));assert.match(newRegistration.controller.status.textContent,/Показаны прежние строки/);
const currentCatalog=newRegistration.controller.load();response(newRegistration.requests.at(-1),{...broken,maps:[{...broken.maps[0],title:'New registration',builtAt:'2026-10-29T00:00:00Z'}]});await currentCatalog;
assert.equal(newRegistration.controller.rows.get(missingKey).link.textContent,'New registration');newRegistration.controller.dispose();
// Restart recovery learns action from the durable receipt but never automatically executes it.
const recoveredForget=fixture('en');await load(recoveredForget,broken);const getForget=recoveredForget.controller.recover('plan1');
response(recoveredForget.requests.at(-1),forgetPlan);await getForget;
assert.match(recoveredForget.controller.dialogTitle.textContent,/Remove this map entry/);
assert.equal(recoveredForget.document.activeElement,recoveredForget.controller.cancel);
assert.equal(recoveredForget.requests.some(request=>request.options.method==='POST'),false);recoveredForget.controller.dispose();

// Combined deletion has separate bulk consent and never silently excludes a zero-snapshot map.
const deleteCatalog={maps:[{...map('empty','No snapshots'),cleanup:{available:false,deleteAvailable:true,forgetAvailable:true,reason:'no-clearable-snapshots'}}],codeMaps:[{...map('same','Code'),cleanup:{available:true,deleteAvailable:true,forgetAvailable:true}}]};
const combined=fixture();await load(combined,deleteCatalog);const d=combined.controller,emptyKey=cleanupMapKey({kind:'data',id:'empty'});
assert.equal(d.deleteButton.disabled,true);assert.equal(d.state.selected.size,0);
d.state.choose(emptyKey,true);d.state.choose(codeKey,true);d.sync();
assert.equal(d.state.selection().count,2);assert.equal(d.state.selection().canClear,false);assert.equal(d.state.selection().canDelete,true);
assert.equal(d.previewButton.disabled,true);assert.equal(d.deleteButton.disabled,false);assert.match(d.bulkHint.textContent,/части выбранных карт/);
assert.equal(d.rows.get(emptyKey).checkbox.disabled,false);
const noClearCount=combined.requests.length;await d.preview();assert.equal(combined.requests.length,noClearCount,'clear cannot silently exclude delete-only selections');
const deletePreview=d.previewDelete();assert.deepEqual(JSON.parse(combined.requests.at(-1).options.body),{maps:[{kind:'data',id:'empty'},{kind:'code',id:'same'}],action:'delete-map'});
const deletePlan=plan(d.state.selection().maps,{action:'delete-map',mapsCleared:false,registrationRemoved:false});
response(combined.requests.at(-1),deletePlan);await deletePreview;
assert.equal(combined.document.activeElement,d.cancel);assert.match(d.dialogTitle.textContent,/Удалить выбранные карты и их снимки/);
assert.match(d.consequences.textContent,/записи выбранных карт.*текущие и предыдущие снимки/);assert.match(d.preserved.textContent,/исходные файлы.*сохранённые виды/);
assert.equal(d.estimates.hidden,false);assert.equal(d.confirm.textContent,'Удалить выбранные карты');
d.closeDialog();await Promise.resolve();assert.equal(d.state.rows.size,2);assert.equal(d.state.selected.size,2);
const againDelete=d.previewDelete();response(combined.requests.at(-1),deletePlan);await againDelete;
const lostDelete=d.execute();const requestCount=combined.requests.length;await d.execute();assert.equal(combined.requests.length,requestCount);
combined.requests.at(-1).reject(new TypeError('lost'));await lostDelete;assert.equal(d.state.phase,'uncertain');assert.equal(d.state.rows.size,2);
const checkDelete=d.check();response(combined.requests.at(-1),{...deletePlan,status:'partial',mapsCleared:true,registrationRemoved:true,canRetry:true,pendingPurgeCount:1,removedBytes:null});await checkDelete;
assert.equal(d.state.phase,'retry');assert.equal(d.state.rows.size,2,'combined acknowledgement cannot hide a newer registration');
assert.equal(d.state.selected.size,2,'selection remains until fresh catalog reconciliation');
assert.equal(d.state.rows.get(codeKey).status,'completed','combined mapsCleared must not reset current rows optimistically');
response(combined.requests.findLast(request=>request.url==='/api/technical/storage'),{maps:[],codeMaps:[]});await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(d.state.rows.size,0);assert.equal(d.state.selected.size,0);assert.match(d.notice.textContent,/Часть подготовленных файлов/);
assert.match(d.retryButton.textContent,/удаление карт/);assert(!d.notice.textContent.includes('MiB'));
const purgeDelete=d.execute();assert.equal(combined.requests.at(-1).url,'/api/technical/map-cleanup/plan1/execute');
response(combined.requests.at(-1),{...deletePlan,status:'completed',mapsCleared:true,registrationRemoved:true,canRetry:false,pendingPurgeCount:0,removedBytes:100});await purgeDelete;
assert.match(d.notice.textContent,/Удаление выбранных карт.*завершено/);d.dispose();
// Unknown delete capability never turns a clear-only legacy selection into destructive authority.
const legacy=fixture();await load(legacy);legacy.controller.state.choose(codeKey,true);legacy.controller.sync();
assert.equal(legacy.controller.previewButton.disabled,false);assert.equal(legacy.controller.deleteButton.disabled,true);
const legacyCount=legacy.requests.length;await legacy.controller.previewDelete();assert.equal(legacy.requests.length,legacyCount);legacy.controller.dispose();
const combinedState=new MapCleanupState();combinedState.setCatalog(deleteCatalog);combinedState.choose(codeKey,true);combinedState.choose(emptyKey,true);
for(const invalid of [{...deletePlan,action:'clear-snapshots'},{...deletePlan,mapsCleared:true,registrationRemoved:false},{...deletePlan,mapsCleared:false,registrationRemoved:true}])assert.throws(()=>combinedState.accept(invalid,deletePlan.maps,'delete-map'),/INVALID_CLEANUP_PLAN/);
combinedState.accept({...deletePlan,status:'completed',mapsCleared:true,registrationRemoved:true},deletePlan.maps,'delete-map');
assert.equal(combinedState.rows.size,2);assert.equal(combinedState.selected.size,2,'old delete receipt leaves all current identities to authoritative catalog');
// Stale combined scope requires a fresh preview while preserving all explicit choices.
const staleDelete=fixture('en');await load(staleDelete,deleteCatalog);staleDelete.controller.state.choose(codeKey,true);staleDelete.controller.state.choose(emptyKey,true);
const staleDeletePreview=staleDelete.controller.previewDelete();response(staleDelete.requests.at(-1),deletePlan);await staleDeletePreview;
const staleDeleteExecute=staleDelete.controller.execute();response(staleDelete.requests.at(-1),{code:'CLEANUP_STALE',error:'changed'},409);await staleDeleteExecute;
assert.equal(staleDelete.controller.state.plan,null);assert.equal(staleDelete.controller.state.selected.size,2);assert.match(staleDelete.controller.notice.textContent,/review deletion/);staleDelete.controller.dispose();

const late=fixture();const old=late.controller.load();const newer=late.controller.load();response(late.requests[1],catalog);await newer;response(late.requests[0],{maps:[],codeMaps:[]});await old;
assert.equal(late.controller.state.rows.size,3,'late ignored abort does not erase current rows');
const pending=late.controller.preview();await pending;late.controller.dispose();
console.log('Map cleanup explicit selection, native confirmation, same-plan lost-ack recovery, caps, stale scope, retained rows action-bound metadata-only removal and RU/EN fallback PASS');
