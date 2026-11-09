/** Системная проекция не смешивается с raw-symbol действиями или областями фильтров. */
import assert from 'node:assert/strict';
import {systemProjection,SystemArchitectureView} from '../web/system-architecture.js';
const data={snapshotId:'s',architectureVersion:2,nodes:[
 {id:'p1',codeProjectId:'p1',kind:'project',label:'API'},
 {id:'p2',codeProjectId:'p2',kind:'project',label:'Worker'},
 {id:'http',kind:'http',label:'Remote',host:'remote.example'},
 {id:'package',kind:'package',label:'Example.Client'}
],edges:[{source:'p1',target:'p2',relation:'project-reference',weight:1,evidence:[]},{source:'p1',target:'http',relation:'configured-http',weight:1,evidence:[]},{source:'p2',target:'http',relation:'configured-http',weight:1,evidence:[]},{source:'p1',target:'package',relation:'package-reference',weight:1,evidence:[]},{source:'p1',target:'p1',relation:'calls',weight:1,evidence:[]}]};
const before=JSON.stringify(data);
assert.deepEqual(new Set(systemProjection(data).nodes.map(node=>node.id)),new Set(['p1','p2','http']));
assert.equal(systemProjection(data).edges.length,3);
assert.equal(systemProjection(data,{packages:true}).nodes.length,4);
const scoped=systemProjection(data,{projectIds:['p1']});
assert.deepEqual(new Set(scoped.nodes.map(node=>node.id)),new Set(['p1','http']));
assert.equal(scoped.edges.length,1);assert.equal(scoped.edges[0].source,'p1');
assert.equal(systemProjection(data,{projectIds:[]}).nodes.length,0);
assert.equal(systemProjection(data,{systems:false}).nodes.length,2);
assert.deepEqual(new Set(systemProjection(data,{query:'REMOTE EXAMPLE'}).nodes.map(node=>node.id)),new Set(['p1','p2','http']));
assert.deepEqual(new Set(systemProjection(data,{query:'REMOTE EXAMPLE',projectIds:['p1']}).nodes.map(node=>node.id)),new Set(['p1','http']));
assert.equal(systemProjection(data,{query:'Client'}).nodes.length,0);
const paged=systemProjection(data,{pageSize:1});
assert.equal(paged.totalNodes,3);assert.equal(paged.totalEdges,3);assert.equal(paged.pageCount,3);
assert.equal(paged.edges.length,0);assert.equal(JSON.stringify(data),before);
class Element {
 constructor(){this.children=[];this.listeners={};this.value='';this.checked=false;}
 addEventListener(name,callback){this.listeners[name]=callback;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 setAttribute(name,value){this[name]=value;}
 scrollIntoView(){}
 set innerHTML(value){throw new Error('Unsafe HTML');}
}
const elements=new Map();
globalThis.document={createElement:()=>new Element(),getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);}};
const element=id=>document.getElementById(id);
element('view-mode').value='system';element('system-scale').value='projects';element('system-systems').checked=true;
const state={project:{id:'repository'},graph:{metadata:{snapshotId:'s'},codeProjects:[]},selected:'raw-method',expandedId:'raw-method',answerRevision:0,codeProjectIds:null};
const renders=[],requests=[],opened=[];
const view={renderOverview:(...args)=>renders.push(args),fit:()=>{},colorProjects:[]};
let controls=0;
const controller=new SystemArchitectureView({state,view,controls:()=>{controls++;assert.equal(state.selected,null);},openProjectCode:id=>opened.push(id),fetcher:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}))});
const load=controller.load();
assert.equal(requests[0].url,'/api/projects/repository/system-architecture');
assert.equal(requests[0].options.method,'POST');
assert.deepEqual(JSON.parse(requests[0].options.body),{snapshotId:'s',relatedProjectIds:[],visitedNodeIds:[]});
requests[0].resolve({ok:true,json:async()=>data});await load;
assert.ok(renders.at(-1)[0].every(node=>node.system===true));
assert.deepEqual(controller.projection.nodes.map(node=>node.id),['p1','p2']);
controller.select('p1');controller.select('http');assert.equal(state.selected,null);assert.equal(state.expandedId,null);assert.equal(controls,2);
assert.ok(!element('selection').children.some(node=>node.textContent==='Открыть код проекта'));
controller.select('p1');element('selection').children.find(node=>node.textContent==='Открыть код проекта').listeners.click();assert.deepEqual(opened,['p1']);
controller.selectEdge(data.edges[0]);assert.equal(state.selected,null);
controller.select('http');element('system-search').value='nothing-matches';controller.render(false);
assert.equal(controller.selectedNodeId,null);assert.ok(!element('selection').children.some(node=>node.textContent==='Remote'));
element('system-search').value='';controller.render(false);controller.select('p1');
element('system-packages').checked=true;controller.render(false);assert.equal(controller.selectedNodeId,null);
element('system-packages').checked=false;controller.render(false);controller.select('p1');
controller.render(false);assert.equal(controller.selectedNodeId,'p1');
// Масштаб репозитория переиспользует индекс, но очищает несовместимый выбор/страницу.
const retainedData=controller.data,requestCount=requests.length;
state.codeProjectIds=['p1'];element('system-search').value='Remote';controller.page=3;
element('system-scale').value='repositories';element('system-scale').listeners.change();
assert.equal(controller.data,retainedData);assert.equal(requests.length,requestCount);
assert.equal(controller.page,0);assert.equal(controller.selectedNodeId,null);
assert.equal(element('system-search').value,'Remote');assert.deepEqual(state.codeProjectIds,['p1']);
assert.deepEqual(new Set(controller.projection.nodes.map(node=>node.id)),new Set(['solution:repository','http']));
assert.equal(controller.projection.edges[0].weight,1); // p2 исключён ДО сворачивания.
controller.select('solution:repository');assert.equal(state.selected,null);
assert.equal(controller.projection.nodes.find(node=>node.id==='solution:repository').memberProjects.length,1);
element('system-scale').value='projects';element('system-scale').listeners.change();
assert.equal(requests.length,requestCount);assert.equal(controller.selectedNodeId,null);
assert.deepEqual(new Set(controller.projection.nodes.map(node=>node.id)),new Set(['p1','http']));
state.codeProjectIds=null;element('system-search').value='';
const stale=controller.load();state.graph.metadata.snapshotId='s2';controller.reset();
assert.ok(requests[1].options.signal.aborted);
requests[1].resolve({ok:true,json:async()=>data});await stale;assert.equal(controller.data,null);
const mismatch=controller.load();requests[2].resolve({ok:true,json:async()=>data});await mismatch;
assert.equal(controller.status,'error');assert.equal(element('system-retry').hidden,false);
const evidencePanel=controller.evidence({...data.edges[0],evidenceTruncated:true,evidence:Array.from({length:12},(_,line)=>({file:'Service.cs',line:line+1,confidence:'graph'}))});
const panelText=node=>[node.textContent||'',...node.children.map(panelText)].join(' ');
assert.match(panelText(evidencePanel),/огранич|не все|сокращ/i);
console.log('System projection totals, directed facts, synthetic selection and stale requests PASS');
// Оба реальных renderer сохраняют системные IDs/направления и не создают raw-symbol map.
const {GraphView}=await import('../web/graph.js');
const {Svg}=await import('./svg_fixture.mjs');
globalThis.document.createElementNS=(_,tag)=>{const item=new Svg(tag);item.classList.add=name=>item.classList.toggle(name,true);return item;};
globalThis.ResizeObserver=class{observe(){}};
const svg=new Svg('svg'),viewport=new Svg('g');viewport.setAttribute('id','viewport');svg.append(viewport);
const symbols=[],systems=[];
const graphView=new GraphView(svg,id=>symbols.push(id),id=>systems.push(id));
const visible=systemProjection(data);
const adapted=visible.nodes.map(node=>({...node,system:true,kindLabel:node.kind,architecture:true,architectureLevel:'projects'}));
for(const provider of ['topology','flow']){
 graphView.setDisplayProvider(provider);graphView.renderOverview(adapted,visible.edges.map(edge=>({...edge,system:true})),null);
 assert.equal(graphView.nodeElements.size,0);assert.equal(graphView.communityElements.size,3);
 const group=graphView.communityElements.get('http').group;
 group.listeners.click();assert.equal(systems.at(-1),'http');
 const paths=viewport.querySelectorAll(provider==='flow'?'.flow-edge':'.community-edge');
 assert.equal(paths.length,3);assert.ok(paths.every(path=>path.attributes['data-source']!==path.attributes['data-target']));
// Actual rendering uses the same temporary positions Map in both renderers, then releases the hook.
 const {stableArchitectureGeometry}=await import('../web/architecture-layout.js');
 const registry=new Map();let hooked=0;
 const hook=geometry=>{hooked++;return stableArchitectureGeometry(geometry,registry);};
 const block={...adapted[0],resourceBlock:true,expanded:true,children:[{id:'child'}]};
 graphView.renderOverview([block,...adapted.slice(1)],visible.edges,null,null,null,{positionHook:hook});
 const anchor={x:graphView.positions.get(block.id).x,y:graphView.positions.get(block.id).y};
 graphView.renderOverview([block,...adapted.slice(1),{...adapted[2],id:'new-resource'}],visible.edges,null,null,null,{positionHook:hook});
 assert.deepEqual({x:graphView.positions.get(block.id).x,y:graphView.positions.get(block.id).y},anchor);
 assert.equal(graphView.communityElements.get(block.id).group.attributes['aria-expanded'],'true');
 assert.equal(graphView.communityElements.get(block.id).group.attributes['data-node-id'],block.id);
 const fullRegistry=new Map(Array.from({length:240},(_,i)=>['previous'+i,{x:i*200,y:0,width:500,height:200}]));
 graphView.renderOverview([{...block,id:'new1'},{...block,id:'new2'}],[],null,null,null,{positionHook:geometry=>stableArchitectureGeometry(geometry,fullRegistry)});
 const [a,b]=[graphView.positions.get('new1'),graphView.positions.get('new2')];
 assert.notDeepEqual([a.x,a.y],[b.x,b.y],'actual renderer subset transition keeps new nodes apart with a full cache');
 const prior=hooked;graphView.renderOverview(adapted,visible.edges,null);
 assert.equal(hooked,prior,'positionHook is not retained by ordinary Code/flow renders');
}
assert.deepEqual(symbols,[]);
console.log('System architecture renders both providers without raw-symbol callbacks PASS');

// Directed isolation retains outgoing dependencies and never adds incoming owners to an external leaf.
element('system-scale').value='projects';element('system-search').value='';state.codeProjectIds=null;
controller.session.accept({...data,snapshotId:'s2'});controller.session.revealOwners(['p1','p2']);
controller.data=controller.session.data;controller.key=controller.contextKey();controller.status='ready';
state.isolatedRoot='p2';controller.render(false);
assert.deepEqual(new Set(controller.projection.nodes.map(node=>node.id)),new Set(['p2','http']));
assert.equal(controller.projection.edges.length,1);
state.isolatedRoot='http';controller.render(false);
assert.deepEqual(controller.projection.nodes.map(node=>node.id),['http']);
assert.equal(controller.projection.edges.length,0);
state.isolatedRoot='missing';controller.render(false);
assert.equal(state.isolatedRoot,null);
state.isolatedRoot=null;controller.render(false);
assert.ok(controller.projection.nodes.length>1);
console.log('System directed isolation and exit PASS');

// Repository isolation bypasses saved project/kind filters until explicitly cleared.
element('system-scale').value='repositories';element('solution-kind').value='database';state.codeProjectIds=['p2'];
state.isolatedRoot='solution:repository';controller.render(false);
assert.ok(controller.projection.nodes.some(node=>node.id==='http'));
assert.ok(controller.projection.edges.some(edge=>edge.target==='http'));
state.isolatedRoot=null;controller.render(false);
assert.ok(!controller.projection.nodes.some(node=>node.id==='http'));
console.log('Repository isolation bypasses hidden project/kind filters PASS');

controller.reset();assert.equal(controller.displayData,null);assert.equal(controller.projection,null);
console.log('System reset clears isolated display data PASS');

// A same-scope enrichment keeps the committed scene/export usable through pending and failed requests.
element('system-scale').value='projects';element('solution-kind').value='';state.codeProjectIds=null;
state.graph.metadata.snapshotId='s';controller.reset();
const initial=controller.load();requests.at(-1).resolve({ok:true,json:async()=>data});await initial;
controller.select('p1');
const committed=controller.data;
const refresh=controller.load();
assert.equal(controller.data,committed);assert.ok(controller.projection.nodes.some(node=>node.id==='http'));
assert.match(element('system-status').children[0].textContent,/Дополняем архитектуру/);
assert.equal(controller.exportModel().graph.snapshotId,'s');
requests.at(-1).resolve({ok:false,status:500,json:async()=>({detail:'Fixture enrichment failed'})});await refresh;
assert.equal(controller.status,'error');assert.equal(controller.data,committed);
assert.match(element('system-status').children[0].textContent,/Fixture enrichment failed/);
assert.equal(element('system-retry').hidden,false);
const retry=controller.load();requests.at(-1).resolve({ok:true,json:async()=>data});await retry;
assert.equal(controller.status,'ready');

// Export of a searched collapsed block retains the canonical owner and actual directed resource fact.
const resource={id:'topic',kind:'kafka',resourceKind:'topic',label:'orders',ownerId:'p1',status:'candidate',proof:{sourceKind:'working-tree',runtimeSelectionKnown:false}};
const enriched={...data,nodes:[...data.nodes,resource],edges:[...data.edges,{id:'publish',factId:'publish',source:'p1',target:'topic',relation:'publishes',operation:'produce',evidence:[]}]};
controller.session.accept(enriched);controller.data=enriched;element('system-search').value='orders';controller.render(false);
const model=controller.exportModel();
assert.ok(model.graph.nodes.some(node=>node.id==='p1'));assert.ok(model.graph.nodes.some(node=>node.id==='topic'));
assert.ok(model.graph.edges.some(edge=>edge.factId==='publish'));
const block=controller.projection.nodes.find(node=>node.resourceBlock);
controller.select(block.id);
assert.ok(controller.projection.nodes.some(node=>node.id==='topic'));
const toggle=element('selection').children.find(node=>node['data-architecture-toggle']);
assert.equal(toggle.textContent,'Свернуть ресурсы');assert.equal(toggle['aria-expanded'],'true');
controller.select(block.id);
assert.ok(!controller.projection.nodes.some(node=>node.id==='topic'));
assert.ok(controller.exportModel().graph.nodes.some(node=>node.id==='topic'));
// A pinned block exceeding the canonical budget keeps owner endpoints and marks JSON partial.
element('system-search').value='';
const topics=Array.from({length:240},(_,i)=>({...resource,id:'topic-'+i,label:'orders '+i}));
const large={...data,nodes:[data.nodes[0],...topics],edges:topics.map(node=>({id:'fact-'+node.id,factId:'fact-'+node.id,source:'p1',target:node.id,relation:'publishes',evidence:[]})),coverage:{complete:true}};
controller.session.accept(large);controller.data=large;controller.render(false);
controller.session.togglePin(controller.projection.nodes.find(node=>node.resourceBlock).id);
const capped=controller.exportModel();
assert.equal(capped.graph.nodes.length,240);assert.equal(capped.graph.edges.length,239);
assert.equal(capped.graph.coverage.limited,true);assert.equal(capped.graph.coverage.complete,false);
assert.equal(capped.graph.sceneLimits.omittedNodes,1);
// Cross-snapshot response cannot replace data accepted under the new context.
const staleRefresh=controller.load(),oldRequest=requests.at(-1);
state.graph.metadata.snapshotId='other';controller.reset();
oldRequest.resolve({ok:true,json:async()=>enriched});await staleRefresh;
assert.equal(controller.data,null);assert.equal(oldRequest.options.signal.aborted,true);
console.log('Retained scene pending/error/retry, visible feedback, searched canonical export and block labels PASS');
