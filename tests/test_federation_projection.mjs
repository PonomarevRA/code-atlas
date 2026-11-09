/** Федерация фильтрует primary-проекты отдельно и привязывает запрос к набору related IDs. */
import assert from 'node:assert/strict';
import {systemProjection,SystemArchitectureView} from '../web/system-architecture.js';
const data={snapshotId:'s',nodes:[{id:'ns-one',kind:'project',label:'Service',repositoryId:'one',codeProjectId:'same',originalCodeProjectId:'same'},{id:'ns-two',kind:'project',label:'Service',repositoryId:'two',codeProjectId:'same',originalCodeProjectId:'same'},{id:'shared',kind:'http',label:'Remote',repositoryIds:['one','two']}],edges:[{source:'ns-one',target:'shared',relation:'configured-http',weight:1,evidence:[]},{source:'ns-two',target:'shared',relation:'configured-http',weight:1,evidence:[]}],repositories:[{id:'one',snapshotId:'s'},{id:'two',snapshotId:'s-two'}]};
const hiddenPrimary=systemProjection(data,{projectIds:[],primaryRepositoryId:'one'});
assert.deepEqual(new Set(hiddenPrimary.nodes.map(node=>node.id)),new Set(['ns-two','shared']));
assert.equal(hiddenPrimary.edges.length,1);
assert.equal(hiddenPrimary.edges[0].source,'ns-two');
assert.equal(systemProjection(data,{projectIds:['same'],primaryRepositoryId:'one'}).nodes.length,3);
class Element{
 constructor(){this.children=[];this.listeners={};this.value='';}
 addEventListener(name,callback){this.listeners[name]=callback;}
 replaceChildren(...nodes){this.children=nodes;}
 append(...nodes){this.children.push(...nodes);}
 setAttribute(name,value){this[name]=value;}
 scrollIntoView(){}
}
const elements=new Map();globalThis.document={createElement:()=>new Element(),getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);}};
const element=id=>document.getElementById(id);
element('view-mode').value='system';element('system-scale').value='projects';element('system-systems').checked=true;
let related=['two'];const requests=[],opened=[];
const state={project:{id:'one'},graph:{metadata:{snapshotId:'s'},codeProjects:[]},codeProjectIds:null,answerRevision:0};
const controller=new SystemArchitectureView({state,view:{renderOverview:()=>{},fit:()=>{}},controls:()=>{},openProjectCode:(...ids)=>opened.push(ids),getRelatedIds:()=>related,fetcher:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}))});
const first=controller.load();assert.equal(requests[0].options.method,'POST');assert.deepEqual(JSON.parse(requests[0].options.body),{snapshotId:'s',relatedProjectIds:['two'],visitedNodeIds:[]});
related=[];const second=controller.load();assert.equal(requests[1].options.method,'POST');assert.deepEqual(JSON.parse(requests[1].options.body),{snapshotId:'s',relatedProjectIds:[],visitedNodeIds:[]});
requests[1].resolve({ok:true,json:async()=>({...data,nodes:[],edges:[]})});await second;
requests[0].resolve({ok:true,json:async()=>data});await first;assert.equal(controller.data.nodes.length,0);
related=['two'];const third=controller.load();requests[2].resolve({ok:true,json:async()=>data});await third;
controller.select('ns-two');element('selection').children.find(node=>node.textContent==='Открыть код проекта').listeners.click();
assert.deepEqual(opened,[['same','two']]);
assert.equal(state.selected,null);
controller.openProjectCode=async()=>{throw new Error('offline refresh failure');};
controller.select('ns-two');
await element('selection').children.find(node=>node.textContent==='Открыть код проекта').listeners.click();
assert.equal(state.project.id,'one');
const allText=node=>[node.textContent||'',...node.children.map(allText)].join(' ');
assert.ok(allText(element('selection')).includes('offline refresh failure'));
let rejectDrill;
controller.openProjectCode=()=>new Promise((resolve,reject)=>{rejectDrill=reject;});
controller.select('ns-two');
const delayed=element('selection').children.find(node=>node.textContent==='Открыть код проекта').listeners.click();
controller.select('shared');rejectDrill(new Error('STALE_DRILL_ERROR'));await delayed;
assert.ok(!allText(element('selection')).includes('STALE_DRILL_ERROR'));
console.log('Federation projection namespaces, scoped filters, POST context and explicit drilldown PASS');
