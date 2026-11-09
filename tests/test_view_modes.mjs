/** Раздел интерфейса отделён от масштаба систем: значения фильтров не теряются. */
import assert from 'node:assert/strict';
import {VIEW_MODES,normalizeViewMode,isSystemMode,systemScale,presentViewMode} from '../web/view-modes.js';
assert.deepEqual(VIEW_MODES.map(item=>item.id),['architecture','system','groups']);
assert.equal(normalizeViewMode('solution'),'system');assert.equal(normalizeViewMode('unknown'),'architecture');
assert.equal(isSystemMode('solution'),true);assert.equal(isSystemMode('groups'),false);
assert.equal(systemScale('system',''),'repositories');assert.equal(systemScale('solution','projects'),'repositories');assert.equal(systemScale('system','projects'),'projects');
const controls={code:[{value:'search remains',hidden:false}],groups:[{value:'louvain',hidden:false}],architecture:[{value:'all',hidden:false}]};
const root={querySelectorAll:selector=>controls[selector.match(/data-(.*)-tools/)[1]]};
presentViewMode('system',root);assert.ok(Object.values(controls).flat().every(item=>item.hidden));
presentViewMode('architecture',root);assert.equal(controls.code[0].hidden,false);assert.equal(controls.architecture[0].hidden,false);assert.equal(controls.groups[0].hidden,true);
presentViewMode('groups',root);assert.equal(controls.groups[0].hidden,false);assert.equal(controls.architecture[0].hidden,true);
assert.equal(controls.code[0].value,'search remains');assert.equal(controls.groups[0].value,'louvain');assert.equal(controls.architecture[0].value,'all');
console.log('View modes preserve inactive controls PASS');

// Реальная навигация проекта→файла→символа вызывает raw action только на последнем уровне.
const {createCodeNavigation}=await import('../web/code-navigation-controller.js');
const {GraphModel}=await import('../web/graph-model.js');
class Element{
 constructor(){this.children=[];this.listeners={};this.value='';this.checked=false;this.dataset={};}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 addEventListener(name,callback){this.listeners[name]=callback;}
}
const elements=new Map();const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
globalThis.document={getElementById:get,createElement:()=>new Element()};
get('architecture-preset').value='dependencies';
const graph={nodes:[{id:'raw',label:'Handler',kind:'method',file:'src/A.cs',codeProjectId:'p'}],edges:[],codeProjects:[{id:'p',label:'Project'}]};
const state={graph,architecture:{level:'projects',projectId:null,file:null,page:0},ingress:{page:4},selected:'old',expandedId:'old',focused:true,answerRevision:0,listLimit:50,codeProjectIds:['p']};
const selected=[],renders=[],docs=[];let draws=0;
const navigation=createCodeNavigation({state,view:{render:(...args)=>renders.push(args),renderOverview:(...args)=>renders.push(args),fit:()=>{}},model:new GraphModel(graph),filteredNodes:()=>graph.nodes,renderIngress:()=>{},renderContextExpansion:()=>{},refreshRegexSearch:()=>{},selectNode:id=>selected.push(id),draw:()=>draws++,controls:()=>{},onDocumentation:value=>docs.push(value)});
navigation.render(false);
const projectId=state.architectureGroups[0].id;
navigation.open(projectId);
assert.equal(state.architecture.level,'files');assert.equal(state.selected,null);assert.equal(state.expandedId,null);assert.equal(state.ingress.page,0);assert.equal(draws,1);assert.deepEqual(selected,[]);
navigation.render(false);navigation.open(state.architectureGroups[0].id);
assert.equal(state.architecture.level,'symbols');assert.equal(state.architecture.file,'src/A.cs');assert.deepEqual(state.codeProjectIds,['p']);
navigation.render(false);get('results').children[0].listeners.click();assert.deepEqual(selected,['raw']);
assert.equal(get('architecture-preset').value,'dependencies');
console.log('Code drilldown keeps synthetic aggregates out of raw actions PASS');
