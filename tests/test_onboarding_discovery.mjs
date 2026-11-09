import assert from 'node:assert/strict';
import {GraphModel} from '../web/graph-model.js';
import {projectSearchAliases, hasHttpEntryEvidence} from '../web/discovery-text.js';
import {rankNavigationMatches} from '../web/navigation-search.js';
import {projectIngressCatalog} from '../web/ingress-catalog.js';
const nodes=[{id:'source',label:'PifInputLoader',comment:'Читает данные ПИФ.',file:'Loader.cs'},
 {id:'reader',label:'ReadPifAsync',file:'Reader.cs'}, {id:'unrelated',label:'Orders',comment:'ПИФ'},
 {id:'entry',label:'GetOrders',attributeFacts:{groups:[{scope:'declaration',kind:'method_declaration',attributes:[{name:'HttpPost'}]}]}},
 {id:'type',label:'Orders',attributeFacts:{groups:[{scope:'containing-type',kind:'class_declaration',attributes:[{name:'HttpPost'}]}]}}];
const graph={nodes,edges:[{source:'source',target:'reader',relation:'calls'}]};
const original=JSON.stringify(graph),model=new GraphModel(graph),filtered=model.filter();
assert.equal(model.searchAliases.get('пиф'),'pif');
assert.deepEqual(model.search('ПИФ').map(n=>n.id),['source','reader','unrelated']);
assert.deepEqual(projectSearchAliases([nodes[2]]),new Map(),'comment alone cannot invent an alias');
assert.equal(model.filter(),filtered,'discovery must leave filter cache unchanged');
assert.equal(JSON.stringify(graph),original);
assert.equal(hasHttpEntryEvidence(nodes[3]),true);
assert.equal(hasHttpEntryEvidence(nodes[4]),false);
assert.equal(hasHttpEntryEvidence({attributeFacts:{groups:[{scope:'parameter',kind:'parameter',attributes:[{name:'HttpPost'}]}]}}),false);
assert.deepEqual(rankNavigationMatches('Orders',[nodes[4],nodes[3]]).map(n=>n.id),['entry','type']);
const entries=[{id:'http',kind:'http',route:'/pif',handlerNodeIds:['source']},
 {id:'kafka',kind:'kafka',topic:'orders',handlerNodeIds:['entry']}];
const result=projectIngressCatalog(entries,{kind:'kafka',query:'ПИФ',nodes:new Map(nodes.map(n=>[n.id,n])),aliases:model.searchAliases});
assert.equal(result.total,0,'selected protocol must not silently switch');
assert.deepEqual(result.matchingCounts,{http:1,mcp:0,kafka:0,rabbit:0,test:0});
assert.equal(projectIngressCatalog(entries,{kind:'http',query:'ПИФ',nodes:new Map(nodes.map(n=>[n.id,n])),aliases:model.searchAliases}).total,1);
model.setGraph({nodes:[nodes[1]],edges:[]});
assert.equal(model.searchAliases.size,0,'new snapshot invalidates derived aliases');
assert.equal(model.search('ПИФ').length,0);
console.log('Onboarding source-backed alias, scope, neutral filtering and protocol alternatives PASS');

assert.deepEqual(rankNavigationMatches('ПИФ', [
 {id:'file',label:'PifInputLoader.cs',kind:'file'},
 {id:'field',label:'Pif',kind:'field'},
 {id:'method',label:'.LoadAsync()',file:'PifInputLoader.cs',kind:'symbol',callableFacts:{kind:'method',sourceKind:'snapshot-captured-source'}},
], {aliases:new Map([['пиф','pif']])}).map(n=>n.id), ['method','field','file']);
console.log('Source-backed abbreviation ranking prioritizes callable declarations PASS');
