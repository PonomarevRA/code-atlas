/** Проверяет добавочный слой входов без выдуманных обработчиков и изменения архитектуры. */
import assert from 'node:assert/strict';
import {ingressOverlay,ingressLabel} from '../web/ingress.js';
const architecture={groups:[{id:'project-a',targetProjectId:'A'},{id:'project-b',targetProjectId:'B'}],nodes:[{id:'a'},{id:'b'}],membership:new Map([['a','project-a'],['a2','project-a'],['b','project-b']])};
const entries=[
{id:'http',kind:'http',label:'GET /a',codeProjectId:'A',handlerNodeIds:['a','a2'],evidence:[{file:'A.cs',line:3}]},
{id:'rabbit',kind:'rabbit',label:'Queue',codeProjectId:'B',handlerNodeIds:['b'],evidence:[{file:'B.cs',line:4}]},
{id:'unknown',kind:'kafka',label:'Unknown handler',codeProjectId:'A',handlerNodeIds:[],evidence:[{file:'A.cs',line:5}]},
{id:'outside',kind:'http',label:'Outside',codeProjectId:'C',handlerNodeIds:['hidden'],evidence:[]},
];
const before=JSON.stringify(entries),membership=[...architecture.membership];
const result=ingressOverlay(entries,architecture);
assert.equal(result.total,3);assert.deepEqual(result.entries.find(entry=>entry.id==='http').targets,['project-a']);
assert.deepEqual(result.entries.find(entry=>entry.id==='unknown').targets,[]);
assert.equal(ingressOverlay(entries,architecture,{kind:'rabbit'}).total,1);
const page=ingressOverlay(entries,{...architecture,groups:[architecture.groups[1]]});
assert.deepEqual(page.entries.map(entry=>entry.id),['rabbit']);
const symbols={...architecture,groups:[],nodes:[{id:'a'}],membership:new Map([['a','a'],['a2','a2']])};
const symbolOverlay=ingressOverlay(entries,symbols,{level:'symbols',projectId:'A',file:'A.cs'});
assert.deepEqual(symbolOverlay.entries.find(entry=>entry.id==='http').targets,['a']);
assert.deepEqual(symbolOverlay.entries.find(entry=>entry.id==='unknown').targets,[]);
const many=Array.from({length:25},(_,index)=>({...entries[0],id:`entry${index}`,label:`Entry ${index}`}));
const firstPage=ingressOverlay(many,architecture);
assert.equal(firstPage.pageCount,5);assert.equal(firstPage.entries.length,6);
const seen=new Set();
for(let page=0;page<firstPage.pageCount;page++){
  const current=ingressOverlay(many,architecture,{page});
  assert.equal(current.page,page);assert.ok(current.entries.length<=6);
  for(const entry of current.entries){assert.ok(!seen.has(entry.id));seen.add(entry.id);}
}
assert.equal(ingressOverlay(many,architecture,{pageSize:100}).entries.length,6);
assert.equal(ingressOverlay(many,architecture,{page:100}).page,4);
const getLabel=ingressLabel({kind:'http',httpMethod:'GET',route:'/api/orders/search'});
assert.ok(getLabel.startsWith('GET '));assert.ok(getLabel.endsWith('orders/search'));
assert.notEqual(getLabel,ingressLabel({kind:'http',httpMethod:'POST',route:'/api/orders/search'}));
assert.notEqual(getLabel,ingressLabel({kind:'http',httpMethod:'GET',route:'/api/orders/details'}));
const topic='long.topic.prefix.with.an.order.events.tail';
assert.ok(ingressLabel({kind:'kafka',topic}).endsWith(topic.slice(-26)));
assert.equal(seen.size,25);
assert.equal(JSON.stringify(entries),before);assert.deepEqual([...architecture.membership],membership);
console.log('Ingress overlay filtering and identity PASS');
