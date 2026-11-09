/** Real card renderers and navigation keep source identity when optional mods succeed or fail. */
import assert from 'node:assert/strict';
import {Svg} from './svg_fixture.mjs';
import {InlineHierarchyRenderer} from '../web/inline-hierarchy-renderer.js';
import {inlineFlowLayout} from '../web/inline-flow-layout.js';
import {inlineMethodBody} from '../web/inline-method-body.js';
import {GraphView} from '../web/graph.js';
import {loadMods,modErrors,decorateModCard} from '../web/mod-runtime.js';
globalThis.document={createElementNS:(_,tag)=>new Svg(tag)};
globalThis.ResizeObserver=class{observe(){}};
const fetcher=async()=>({ok:true,json:async()=>({mods:[
 {id:'dependent',enabled:true,fingerprint:'c'.repeat(64),manifest:{requires:['foundation'],frontend:'mod.js'}},
 {id:'broken',enabled:true,fingerprint:'b'.repeat(64),manifest:{frontend:'mod.js',theme:{fill:'#f00'}}},
 {id:'foundation',enabled:true,fingerprint:'a'.repeat(64),manifest:{frontend:'mod.js',theme:{fill:'#ddeeff',radius:12},behavior:{doubleClick:'source'}}}
]})});
const loaded=[],seen=[];let cleanup=0;
await loadMods({fetcher,importer:async url=>({activate(api){loaded.push(api.modId);
 if(api.modId==='broken')throw Error('bad activate');
 if(api.modId==='foundation') {api.onDispose(()=>cleanup++);api.registerCardDecorator(({node,overlay,actions})=>{assert.ok(Object.isFrozen(node));seen.push(node.id);const text=new Svg('text');text.textContent='MOD';overlay.append(text);overlay.addEventListener('click',()=>actions.select());});}
}})});
assert.ok(loaded.indexOf('foundation')<loaded.indexOf('dependent'));
assert.equal(modErrors.length,1);
const svg=new Svg('svg'),viewport=new Svg('g');viewport.setAttribute('id','viewport');svg.append(viewport);
const selected=[],sources=[];const view=new GraphView(svg,id=>selected.push(id),()=>{});
view.onPathSource=(...args)=>sources.push(args);view.getSourceNode=()=>({line:42});
const nodes=[{id:'a',label:'A'},{id:'b',label:'B'}],edges=[{source:'a',target:'b',relation:'calls'}];const before=JSON.stringify({nodes,edges});
for(const provider of ['flow','topology']) {
 view.setDisplayProvider(provider);view.render(nodes,edges,null);
 const group=view.nodeElements.get('a').group;
 const body=group.querySelector(provider==='flow'?'.flow-card-body':'.topology-card-body');assert.equal(body.attributes.fill,'#ddeeff');
 assert.equal(group.querySelectorAll('.atlas-mod-overlay').length,1);
 group.querySelector('.atlas-mod-overlay').listeners.click();
 group.listeners.dblclick({target:group,preventDefault(){},stopPropagation(){}});
}
assert.deepEqual(selected,['a','a']);assert.deepEqual(sources,[['a',42],['a',42]]);assert.equal(JSON.stringify({nodes,edges}),before);
const occurrence=new Svg('g');decorateModCard(view,occurrence,{id:'visual-call',definitionId:'b',line:3},{width:200,height:100});
assert.equal(seen.at(-1),'b');occurrence.listeners.dblclick({target:occurrence,preventDefault(){},stopPropagation(){}});assert.deepEqual(sources.at(-1),['b',42]);
const connector=new Svg('g');decorateModCard(view,connector,{id:'join',transition:true},{width:36,height:36});assert.equal(connector.children.length,0);
await loadMods({fetcher,importer:async()=>({activate(api){api.registerCardDecorator(({actions})=>actions.select());api.registerCardDecorator(async()=>{throw Error('async hook');});}})});
const host=new Svg('g'),body=new Svg('rect');host.append(body);decorateModCard(view,host,{id:'a'},{width:200,height:100});
assert.equal(host.children.length,1);assert.equal(host.children[0],body);assert.ok(modErrors.some(error=>error.includes('event handlers')));assert.ok(modErrors.some(error=>error.includes('synchronous')));
assert.equal(cleanup,1);
const inlineSeen=[];
await loadMods({fetcher:async()=>({ok:true,json:async()=>({mods:[{id:'inline-mod',enabled:true,fingerprint:'d'.repeat(64),manifest:{frontend:'mod.js',theme:{fill:'#ddeeff'},behavior:{doubleClick:'source'}}}]})}),importer:async()=>({activate(api){api.registerCardDecorator(({node,overlay})=>{inlineSeen.push(node.id);overlay.append(new Svg('text'));});}})});
const root={id:'ctor',label:'C()',kind:'constructor',file:'C.cs',line:2};
const graph={nodes:[root],edges:[]};
const bodyFacts={data:{nodeId:'ctor',snapshotId:'s',sourceStatus:'parsed',events:[],controlFlow:{branches:[{kind:'if',line:3,header:'ready',arms:[{id:'yes',kind:'true'},{id:'no',kind:'false'}]}],terminals:[{kind:'return',text:'return 1;',line:4,branchIds:['yes']}]}}};
const bodyScene=inlineMethodBody(root,bodyFacts,graph,{});
const item={id:'ctor',rawId:'ctor',node:root,label:root.label,kind:'raw',children:[],expanded:true,enter:{kind:'raw'},totalChildren:0,memberIds:['ctor']};
const scene={items:[item],roots:['ctor'],edges:[],methodBodies:new Map([['ctor',bodyScene]])};
const layout=inlineFlowLayout(scene,new Map([['ctor',{x:10,y:10,width:260,height:154}]]));
const inlineSources=[];view.onPathSource=null;view.onInlineSource=(...args)=>inlineSources.push(args);view.getSourceNode=()=>root;view.canReadBlock=()=>true;
new InlineHierarchyRenderer(view).render(scene,layout);
assert.ok(inlineSeen.length>1);assert.ok(inlineSeen.every(id=>id==='ctor'));assert.ok(viewport.querySelectorAll('.atlas-mod-overlay').length>1);
view.nodeElements.get('ctor').group.listeners.dblclick({target:viewport,preventDefault(){},stopPropagation(){}});assert.deepEqual(inlineSources,[['ctor',2]]);
let imports=0;await loadMods({safe:true,fetcher,importer:async()=>{imports++;}});assert.equal(imports,0);
view.setDisplayProvider('flow');view.render(nodes,edges,null);assert.equal(viewport.querySelectorAll('.atlas-mod-overlay').length,0);
console.log('Mod renderer themes, actions, dependencies, failures, safe recovery and raw identity PASS');
