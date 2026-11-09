/** Java metadata, raw navigation and bounded evidence remain legible without evaluating source. */
import assert from 'node:assert/strict';
import {appendTestEntryDetails} from '../web/test-entry-details.js';
import {createIngressController} from '../web/ingress-controller.js';
class Element {
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.value='';}
 append(...items){this.children.push(...items);} replaceChildren(...items){this.children=items;}
 addEventListener(name,fn){this.listeners[name]=fn;} setAttribute(name,value){this[name]=value;}
 set innerHTML(_){throw Error('Unsafe HTML');} scrollIntoView(){}
}
const elements=new Map();
globalThis.document={createElement:tag=>new Element(tag),getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);}};
const all=node=>[node,...node.children.flatMap(all)];
const text=node=>all(node).map(x=>x.textContent||'').join(' ');
const root=new Element(),locations=[],targets=[];
const entry={id:'synthetic',kind:'test',label:'Проверка ClientId',handlerNodeIds:['raw-test'],framework:'junit5',line:12,
 metadata:[{name:'io.qameta.allure.Feature',values:['<img src=x onerror=bad()>']}],
 annotations:[{name:'MethodSource',scope:'declaration',text:'@MethodSource("cases")',file:'Test.java',line:10,ownerNodeId:'raw-test'}],
 dataSources:[{label:'cases',status:'unresolved',reason:'ambiguous-provider',nodeIds:['raw-provider'],declaredRowCount:100,rowsTruncated:true,rows:Array.from({length:30},(_,i)=>({expression:`Arguments.of(${i})`,file:'private bookkeeping'}))}]};
appendTestEntryDetails(root,entry,{onNode:id=>targets.push(id),onSource:(...x)=>locations.push(x)});
assert.match(text(root),/Feature: <img src=x onerror=bad\(\)>/);
assert.ok(text(root).indexOf('Данные и варианты') < text(root).indexOf('Feature:'));
assert.match(text(root),/показано 20 из 100/);assert.match(text(root),/Список сокращён/);assert.match(text(root),/ambiguous-provider/);
assert.doesNotMatch(text(root),/private bookkeeping/);assert.match(text(root),/Результат запуска неизвестен/);
all(root).find(x=>x.textContent==='Открыть связанный метод').listeners.click();assert.deepEqual(targets,['raw-provider']);
all(root).find(x=>x.textContent==='Строка 10').listeners.click();assert.deepEqual(locations,[['raw-test',10]]);
const pending=[];const state={project:{id:'p'},graph:{metadata:{snapshotId:'s'},codeProjects:[]},nodes:new Map([['raw-test',{id:'raw-test',label:'test',line:12}]]),answerRevision:0,ingress:{status:'ready',data:{entries:[]}}};
const controller=createIngressController({state,view:{},draw(){},controls(){},filteredNodes:()=>[...state.nodes.values()],selectNode(){},analyzePath:id=>targets.push(id),onSource:(...x)=>locations.push(x),request:()=>new Promise((resolve,reject)=>pending.push({resolve,reject}))});
const first=controller.select({...entry,detailsAvailable:true});pending[0].resolve({truncated:true,node:{id:'raw-test'}});await first;
const panel=document.getElementById('selection');
assert.match(text(panel),/Схема и исходник остаются доступны/);assert.doesNotMatch(text(panel),/Подготовка не обнаружена/);
all(panel).find(x=>x.textContent==='Открыть исходник теста').listeners.click();assert.deepEqual(locations.at(-1),['raw-test',12]);
all(panel).find(x=>x.textContent?.startsWith('Путь от входа:')).listeners.click();assert.equal(targets.at(-1),'raw-test');
const stale=controller.select({...entry,detailsAvailable:true});state.graph.metadata.snapshotId='other';pending[1].reject(Error('stale failure'));await stale;assert.doesNotMatch(text(panel),/stale failure/);
state.graph.metadata.snapshotId='s';const success=controller.select({...entry,detailsAvailable:true});pending[2].resolve({node:{testFacts:entry}});await success;assert.match(text(panel),/Feature: <img/);
console.log('Test details metadata, bounded rows, raw IDs, oversized fallback and stale response PASS');
// Captured tests become usable before transport scanning, without hiding HTTP/Kafka/Rabbit.
const stagePending=[],stageRequests=[];
const stagedState={...state,graph:{metadata:{snapshotId:'stage-s',testDiscovery:{coverage:{testEntries:1}}},codeProjects:[]},answerRevision:0,ingress:{revision:0,status:'idle',data:null}};
const staged=createIngressController({state:stagedState,view:{},draw(){},controls(){},filteredNodes:()=>[...state.nodes.values()],selectNode(){},request:url=>{stageRequests.push(url);return new Promise((resolve,reject)=>stagePending.push({resolve,reject}));}});
const loading=staged.load();
assert.match(stageRequests[0],/capturedOnly=true/);
stagePending[0].resolve({snapshotId:'stage-s',entries:[entry],testDiscovery:{status:'complete'},coverage:{scope:'captured-only'}});
await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(stagedState.ingress.status,'ready');assert.equal(stagedState.ingress.infrastructureStatus,'pending');
assert.equal(stagedState.ingress.data.entries[0].id,'synthetic');assert.doesNotMatch(stageRequests[1],/capturedOnly/);
assert.equal(document.getElementById('ingress-catalog-search').disabled,false);
assert.match(text(document.getElementById('ingress-catalog-types')),/Kafka · …/);
stagePending[1].resolve({snapshotId:'stage-s',entries:[{...entry,label:'less precise'},{id:'http',kind:'http',label:'GET /api',handlerNodeIds:[]},{id:'rabbit',kind:'rabbit',label:'queue',handlerNodeIds:[]}]});await loading;
assert.equal(stagedState.ingress.infrastructureStatus,'ready');assert.equal(stagedState.ingress.data.entries.length,3);assert.equal(stagedState.ingress.data.entries.find(item=>item.id==='synthetic').label,entry.label);
const failed=staged.load();stagePending[2].resolve({snapshotId:'stage-s',entries:[entry]});await new Promise(resolve=>setTimeout(resolve,0));stagePending[3].reject(Error('transport timeout'));await failed;
assert.equal(stagedState.ingress.status,'ready');assert.equal(stagedState.ingress.infrastructureStatus,'error');assert.equal(stagedState.ingress.data.entries.length,1);
assert.equal(document.getElementById('ingress-catalog-retry').hidden,false);assert.match(document.getElementById('ingress-catalog-status').textContent,/transport timeout/);
console.log('Captured-first catalogue, full transport merge, unknown counts and partial failure retry PASS');
