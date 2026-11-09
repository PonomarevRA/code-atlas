/** Каталог охватывает репозиторий независимо от шести элементов overlay и фильтров карты. */
import assert from 'node:assert/strict';
import {projectIngressCatalog} from '../web/ingress-catalog.js';
const nodes=new Map([
 ['handler',{id:'handler',label:'Импорт OrdersHandler',file:'Application/Consumer.cs'}],
 ['controller',{id:'controller',label:'GetOrders',file:'Api/Controller.cs'}]
]);
const entries=[];
for(let i=0;i<8;i++)entries.push({id:`k${i}`,kind:'kafka',label:`KAFKA orders.${i}`,topic:`orders.${i}`,codeProjectId:'app',handlerNodeIds:['handler'],evidence:[{file:'Config.cs',line:i+1}]});
for(let i=0;i<7;i++)entries.push({id:`h${i}`,kind:'http',label:`GET /orders/${i}`,route:`/orders/${i}`,httpMethod:'GET',codeProjectId:'api',handlerNodeIds:['controller']});
for(let i=0;i<3;i++)entries.push({id:`r${i}`,kind:'rabbit',label:`Queue ${i}`,queue:`payments.${i}`,codeProjectId:'app',handlerNodeIds:i?[]:['not-in-snapshot']});
const options={nodes,projects:[{id:'app',label:'Application'},{id:'api',label:'API'}],availableIds:new Set(['controller'])};
const before=JSON.stringify(entries),nodeBefore=JSON.stringify([...nodes]);
const first=projectIngressCatalog(entries,options);
assert.deepEqual(first.counts,{http:7,mcp:0,kafka:8,rabbit:3,test:0});assert.equal(first.total,18);assert.equal(first.pageCount,3);
const ids=new Set();
for(let page=0;page<first.pageCount;page++){
 const result=projectIngressCatalog(entries,{...options,page});assert.ok(result.entries.length<=6);
 for(const entry of result.entries){assert.ok(!ids.has(entry.id));ids.add(entry.id);}
}
assert.equal(ids.size,18);
const kafka=projectIngressCatalog(entries,{...options,kind:'kafka'});
assert.equal(kafka.total,8);assert.deepEqual(kafka.counts,first.counts);
assert.equal(kafka.entries[0].projectLabel,'Application');
assert.equal(kafka.entries[0].handlers[0].node.id,'handler');assert.equal(kafka.entries[0].handlers[0].available,false);
assert.equal(projectIngressCatalog(entries,{...options,query:'ORDERS CONSUMER.CS'}).total,8);
assert.equal(projectIngressCatalog(entries,{...options,query:'ИМПОРТ orders.2'}).total,1);
assert.equal(projectIngressCatalog(entries,{...options,query:'GETORDERS'}).total,7);
assert.equal(projectIngressCatalog(entries,{...options,query:'PAYMENTS.2'}).total,1);
assert.equal(projectIngressCatalog(entries,{...options,query:'orders does-not-exist'}).total,0);
assert.equal(projectIngressCatalog(entries,{...options,availableIds:new Set()}).total,18);
assert.deepEqual(projectIngressCatalog(entries,{...options,kind:'rabbit'}).entries[0].handlers,[]);
assert.equal(projectIngressCatalog(entries,{...options,page:1000}).page,2);
assert.equal(projectIngressCatalog(entries,{...options,page:-5}).page,0);
assert.equal(projectIngressCatalog([...entries,...entries],{...options,pageSize:999}).entries.length,20);
assert.equal(JSON.stringify(entries),before);assert.equal(JSON.stringify([...nodes]),nodeBefore);
assert.equal(projectIngressCatalog([],{...options}).total,0);
console.log('Repository ingress catalog completeness, search and original handler IDs PASS');

const {createIngressCatalog}=await import('../web/ingress-catalog.js');
/** Минимальный DOM запрещает HTML интерпретацию названий топиков/обработчиков. */
class Element{
 constructor(){this.children=[];this.listeners={};this.value='';}
 addEventListener(event,listener){this.listeners[event]=listener;}
 setAttribute(name,value){this[name]=value;}
 replaceChildren(...nodes){this.children=nodes;}
 append(...nodes){this.children.push(...nodes);}
 scrollIntoView(){}
 set innerHTML(value){throw new Error('Unsafe HTML');}
}
const elements=new Map();globalThis.document={getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:()=>new Element()};
const element=id=>document.getElementById(id),selected=[],evidence=[],analyzed=[],described=[];
const state={ingress:{status:'idle',data:null},nodes,graph:{codeProjects:options.projects}};
const catalog=createIngressCatalog({describeNode:id=>described.push(id),analyzePath:id=>analyzed.push(id),state,filteredNodes:()=>[nodes.get('controller')],selectNode:id=>selected.push(id),selectEvidence:entry=>evidence.push(entry.id),retry:()=>{}});
catalog.render();assert.ok(element('ingress-catalog-types').children.every(button=>button.textContent.includes('…')));
state.ingress={status:'ready',data:{entries}};catalog.render();
element('ingress-catalog-types').children.find(button=>button.textContent.startsWith('Kafka ·')).listeners.click();
assert.ok(element('ingress-catalog-types').children.some(button=>button.textContent==='Kafka · 8'));
assert.equal(element('ingress-catalog-list').children.length,8);
const kafkaCard=element('ingress-catalog-list').children[0];
assert.equal(kafkaCard.children.find(child=>child.textContent==='Открыть обработчик').disabled,true);
assert.equal(kafkaCard.children.find(child=>child.textContent==='Путь выполнения').disabled,true);
assert.ok(kafkaCard.children.some(child=>child.textContent?.includes('исключён фильтрами')));
kafkaCard.children.find(child=>child.textContent==='Подробнее о входе').listeners.click();assert.equal(evidence[0],'k0');
assert.deepEqual(selected,[],'Evidence must not select synthetic entry ID');
element('ingress-catalog-types').children.find(button=>button.textContent.startsWith('HTTP API')).listeners.click();
const handlerButton=element('ingress-catalog-list').children[0].children.find(child=>child.textContent==='Открыть обработчик');
assert.equal(handlerButton.disabled,false);handlerButton.listeners.click();assert.deepEqual(selected,['controller']);
const pathButton=element('ingress-catalog-list').children[0].children.find(child=>child.textContent==='Путь выполнения');
assert.equal(pathButton.disabled,false);pathButton.listeners.click();assert.deepEqual(analyzed,['controller']);
const descriptionButton=element('ingress-catalog-list').children[0].children.find(child=>child.textContent==='Описание узла');
assert.equal(descriptionButton.disabled,false);descriptionButton.listeners.click();assert.deepEqual(described,['controller']);
element('ingress-catalog-search').value='no such route';element('ingress-catalog-search').listeners.input();
assert.ok(element('ingress-catalog-list').children[0].textContent.includes('не найдено'));
assert.ok(element('ingress-catalog-types').children.some(button=>button.textContent==='Kafka · 8'));
catalog.reset();assert.equal(element('ingress-catalog-search').value,'');catalog.render();
console.log('Ingress catalog idle/empty states, evidence and disabled filter actions PASS');

for(const label of ['Kafka','HTTP API','RabbitMQ']){
 element('ingress-catalog-types').children.find(button=>button.textContent.startsWith(`${label} ·`)).listeners.click();
 const cards=element('ingress-catalog-list').children;
 assert.ok(cards.length>0);
 for(const card of cards)assert.ok(card.children.some(child=>child.className==='pill'&&child.textContent===label));
}
console.log('Every catalog card displays its entry type PASS');

// A query remains intact when the reader explicitly chooses another matching protocol.
element('ingress-catalog-types').children.find(button=>button.textContent.startsWith('Kafka ·')).listeners.click();
element('ingress-catalog-search').value='GetOrders';element('ingress-catalog-search').listeners.input();
assert.ok(element('ingress-catalog-types').children.find(button=>button.textContent.startsWith('Kafka ·')).className==='selected');
const alternative=element('ingress-catalog-list').children.find(button=>button.textContent==='Посмотреть HTTP API · 7');
assert.ok(alternative);alternative.listeners.click();
assert.equal(element('ingress-catalog-search').value,'GetOrders');
assert.equal(element('ingress-catalog-list').children.length,7);
assert.ok(element('ingress-catalog-types').children.find(button=>button.textContent.startsWith('HTTP API ·')).className==='selected');
console.log('Explicit matching protocol switch retains query PASS');

// Test-only projects start with their testcases; a deliberate protocol switch survives redraw.
state.project={id:'java'};
state.ingress.data.entries=[...entries,{id:'t1',kind:'test',label:'Проверка ClientId',framework:'junit5',tags:['smoke'],metadata:[{name:'Feature',values:['Лимиты']}],handlerNodeIds:['controller']}];
catalog.render();
assert.ok(element('ingress-catalog-types').children.find(x=>x.textContent==='Тесты · 1').className==='selected');
assert.ok(element('ingress-catalog-list').children.some(x=>x.className==='test-study-hint'));
assert.ok(element('ingress-catalog-list').children.find(x=>x.className==='ingress-catalog-card').children.some(x=>x.textContent==='Схема от теста'));
element('ingress-catalog-search').value='Лимиты smoke';element('ingress-catalog-search').listeners.input();assert.match(element('ingress-catalog-status').textContent,/1 результатов/);
element('ingress-catalog-search').value='';element('ingress-catalog-search').listeners.input();
element('ingress-catalog-types').children.find(x=>x.textContent==='Kafka · 8').listeners.click();catalog.render();
assert.equal(element('ingress-catalog-types').children.find(x=>x.textContent==='Kafka · 8').className,'selected');
console.log('Test default, declared metadata search and explicit protocol retention PASS');

// MCP operations use the same catalogue contract and never become HTTP routes.
const mcpEntry={id:'mcp-search',kind:'mcp',label:'search_nodes',operationName:'search_nodes',mcpType:'tool',handlerNodeIds:['controller']};
const mcpCatalog=projectIngressCatalog([mcpEntry],{kind:'mcp',query:'tool search_nodes',nodes});
assert.equal(mcpCatalog.counts.mcp,1);assert.equal(mcpCatalog.total,1);
assert.equal(mcpCatalog.entries[0].handlers[0].node.id,'controller');
const httpOnly=projectIngressCatalog([mcpEntry],{kind:'http',query:'search_nodes',nodes});
assert.equal(httpOnly.total,0);assert.equal(httpOnly.matchingCounts.mcp,1);
console.log('MCP operation metadata, handler identity and explicit protocol filter PASS');
