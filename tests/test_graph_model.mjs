/** Индексы и ограниченные кэши должны сохранять семантику прежнего полного прохода. */
import assert from 'node:assert/strict';
import {GraphModel} from '../web/graph-model.js';
import {filterNodes,aggregateCommunities} from '../web/filters.js';
import {architectureGraph} from '../web/architecture.js';
const graph={nodes:[
 {id:'a',label:'Order Load',file:'App/Orders.cs',kind:'method',codeProjectId:'App',community:'c'},
 {id:'b',label:'Order Save',file:'App/Orders.cs',kind:'method',codeProjectId:'App',community:'c'},
 {id:'c',label:'Gateway',file:'Api/Gateway.cs',kind:'class',codeProjectId:'Api',community:'d'},
 {id:'hidden-id',label:'External',kind:'external',community:'e'}
],edges:[
 {source:'a',target:'b',relation:'calls'}, {source:'a',target:'b',relation:'calls'},
 {source:'b',target:'a',relation:'references'}, {source:'c',target:'a',relation:'calls'},
 {source:'a',target:'a',relation:'calls'}, {source:'hidden-id',target:'c',relation:'defines'}
],codeProjects:[{id:'App',label:'Application'},{id:'Api',label:'API'}]};
const original=JSON.stringify(graph),model=new GraphModel(graph);
for(const keywords of ['', 'order', 'order orders.cs','nomatch'])
for(const kind of ['', 'method'])for(const relation of ['', 'calls'])
for(const codeProjectIds of [null,[],['App']])for(const neighborOf of [null,'a'])
for(const changedNodeIds of [null,[],['a','b']]){
 const options={keywords,kind,relation,codeProjectIds,neighborOf,changedNodeIds};
 const source=changedNodeIds===null?graph:{...graph,nodes:graph.nodes.filter(n=>changedNodeIds.includes(n.id))};
 const expected=filterNodes(source,options),actual=model.filter(options);
 assert.deepEqual(actual,expected);assert.equal(model.filter(options),actual);
}
const all=model.filter();
assert.deepEqual(model.incident('a',all),graph.edges.filter(e=>e.source==='a'||e.target==='a'));
assert.equal(model.incident('a',all).length,5,'Self-loop once; repeated edge preserved');
assert.deepEqual(model.incident('a',graph.nodes.slice(0,2),'calls'),[graph.edges[0],graph.edges[1],graph.edges[4]]);
assert.deepEqual(model.search('order orders.cs'),graph.nodes.slice(0,2));
assert.deepEqual(model.search('hidden-id'),[],'Navigation searches names/paths, not internal identifiers');
assert.deepEqual(model.search(''),[]);
assert.equal(model.filter(),all,'Navigation search leaves filter cache unchanged');
assert.deepEqual(model.aggregate(all).communities,aggregateCommunities(graph,all).communities);
const options={level:'files',projectId:'App'};
assert.deepEqual(model.architecture(all,options),architectureGraph(graph,all,options));
assert.equal(model.architecture(all,options),model.architecture(all,options));
assert.equal(JSON.stringify(graph),original);
model.setGraph({nodes:[{id:'a',label:'New',file:'new.cs'}],edges:[]});
assert.equal(model.nodes.get('a').label,'New');assert.deepEqual(model.search('order'),[]);
assert.equal(model.filter().length,1);assert.deepEqual(model.incident('a',model.filter()),[]);
console.log('Graph model indexed semantic parity PASS');
