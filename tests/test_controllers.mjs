/** Контроллеры отклоняют поздние ответы и не превращают overlay ID в source/MCP selection. */
import assert from 'node:assert/strict';
import {createIngressController} from '../web/ingress-controller.js';
import {createGitController} from '../web/git-controller.js';
class Element{
 constructor(){this.children=[];this.listeners={};this.value='';this.checked=false;}
 setAttribute(name,value){this[name]=value;}
 scrollIntoView(){}
 addEventListener(event,handler){this.listeners[event]=handler;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 set innerHTML(value){throw new Error('Unsafe HTML');}
}
const elements=new Map();
globalThis.document={getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:()=>new Element()};
const element=id=>document.getElementById(id);
const state={project:{id:'p'},graph:{metadata:{snapshotId:'s'}},nodes:new Map([['handler',{id:'handler',label:'Handle'}]]),ingress:{revision:0},architecture:{level:'projects'},selected:'old',answerRevision:0,compareRevision:0};
const requests=[];globalThis.fetch=(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}));
let draws=0,controls=0;const selected=[];
const ingress=createIngressController({state,view:{},draw:()=>draws++,controls:()=>controls++,filteredNodes:()=>[...state.nodes.values()],selectNode:id=>selected.push(id)});
const first=ingress.load();state.graph.metadata.snapshotId='new';const second=ingress.load();
assert.equal(ingress.load(),second);
assert.ok(requests[0].options.signal.aborted);
requests[1].resolve({ok:true,json:async()=>({snapshotId:'new',entries:[],warning:'fresh'})});await second;
requests[0].resolve({ok:true,json:async()=>({snapshotId:'s',entries:[],warning:'old'})});await first;
assert.equal(state.ingress.data.warning,'fresh');assert.equal(draws,1);assert.equal(state.ingress.status,'ready');
state.graph.metadata.snapshotId='retry';
const failedLoad=ingress.load();assert.equal(state.ingress.status,'loading');
assert.ok(element('ingress-catalog-types').children.every(button=>button.textContent.includes('…')));
requests[2].resolve({ok:false,status:503,json:async()=>({error:'temporary scan failure'})});await failedLoad;
assert.equal(state.ingress.status,'error');assert.equal(element('ingress-catalog-retry').hidden,false);
assert.equal(element('ingress-catalog-list').children.length,0);assert.equal(element('ingress-catalog-search').disabled,false);
const retried=element('ingress-catalog-retry').listeners.click();assert.equal(state.ingress.status,'loading');
requests[3].resolve({ok:true,json:async()=>({snapshotId:'retry',entries:[]})});await retried;
state.graph.metadata.snapshotId='new';
ingress.catalog.render();assert.equal(state.ingress.status,'ready');
assert.equal(element('ingress-catalog-retry').hidden,true);assert.equal(element('ingress-catalog-search').disabled,false);
assert.ok(element('ingress-catalog-list').children[0].textContent.includes('в проверенных источниках не найдено'));
assert.ok(element('ingress-catalog-types').children.some(button=>button.textContent==='Kafka · 0'));
const entry={id:'synthetic',kind:'http',label:'GET /orders',handlerNodeIds:['handler'],evidence:[{file:'Orders.cs',line:3}]};
ingress.select(entry);assert.equal(state.selected,null);assert.equal(controls,1);assert.deepEqual(selected,[]);
const button=element('selection').children.find(child=>child.textContent?.startsWith('Открыть обработчик:'));
assert.ok(button);assert.equal(button.disabled,false);button.listeners.click();assert.deepEqual(selected,['handler']);
const pending=[];let filters=0;
const git=createGitController({state,api:(...args)=>new Promise(resolve=>pending.push({resolve,args})),applyFilters:()=>filters++,controls:()=>controls++});
element('base-ref').value='HEAD~1';element('target-ref').value='HEAD';element('compare-mode').value='direct';
const comparing=element('compare-form').listeners.submit({preventDefault(){}});
assert.equal(state.comparing,true);git.clear(false);
pending[0].resolve({snapshotId:'new',files:[]});await comparing;
assert.equal(state.comparison,null);assert.equal(state.comparing,false);assert.equal(filters,0);
const newer=element('compare-form').listeners.submit({preventDefault(){}});
pending[1].resolve({snapshotId:'stale',files:[]});await newer;
assert.equal(state.comparison,null);assert.ok(element('comparison-output').children[0].textContent.includes('Индекс изменился'));
console.log('Controllers stale response and real handler navigation PASS');

const {GraphModel}=await import('../web/graph-model.js');
const {createNavigationSearch}=await import('../web/navigation-search.js');
const navigationGraph={nodes:Array.from({length:25},(_,index)=>({id:`raw${index}`,label:`Order ${index}`,file:'Orders.cs',codeProjectId:'api'})),edges:[]};
const model=new GraphModel(navigationGraph),visible=model.filter(),navigationSelected=[];
const beforeNavigation=JSON.stringify(navigationGraph);
createNavigationSearch({model,getNodes:()=>visible,getProjects:()=>[{id:'api',label:'API'}],onSelect:id=>navigationSelected.push(id)});
element('navigation-search').value='orders';element('navigation-search').listeners.input();
assert.equal(element('navigation-results').children.length,20);
assert.equal(model.filter(),visible);assert.equal(JSON.stringify(navigationGraph),beforeNavigation);
assert.equal(element('navigation-more').hidden,false);element('navigation-more').listeners.click();
assert.equal(element('navigation-results').children.length,25);assert.equal(element('navigation-more').hidden,true);
element('navigation-results').children[24].listeners.click();assert.deepEqual(navigationSelected,['raw24']);
element('navigation-search').value='no matching symbol';element('navigation-search').listeners.input();
assert.equal(element('navigation-results').children.length,0);
assert.equal(model.filter(),visible);
console.log('Navigation search preserves graph and opens real IDs PASS');

const {edgeHighlighter}=await import('../web/highlight.js');
const changes=[];
const lines=Array.from({length:1000},(_,index)=>({source:`s${index}`,target:`t${index}`,line:{classList:{toggle:(name,on)=>changes.push([index,name,on])}}}));
const highlight=edgeHighlighter(lines,'active');highlight('s10');highlight('s11');
assert.deepEqual(changes,[[10,'active',true],[10,'active',false],[11,'active',true]]);
highlight(null);assert.deepEqual(changes.at(-1),[11,'active',false]);
console.log('Indexed hover updates incident edges only PASS');
