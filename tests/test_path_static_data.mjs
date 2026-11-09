import assert from 'node:assert/strict';
import { buildPathScope } from '../web/path-scope.js';
import { buildPathScene } from '../web/path-scene.js';
import { projectExecutionPath, pathReadingTitle } from '../web/path-graph-projection.js';
import { implementationBlock, executableBlock } from '../web/map-blocks.js';

const table = { id:'table', label:'Priorities', staticData:{kind:'dictionary',entries:[
  {key:'Board.A',value:'1',line:8},{key:'Board.B',value:'2',line:9}],line:7} };
const constant = { id:'constant',label:'Suffix',staticData:{kind:'constant',initializer:'"TOM"',entries:[],line:4} };
const method = { id:'method',label:'.Read()',_callable:true };
const nodes = [method,table,constant];
const edges = [{source:'method',target:'table',relation:'reads'}, {source:'table',target:'constant',relation:'reads'}];
const before = JSON.stringify({nodes,edges});
const collapsed = buildPathScope(nodes,edges,{rootId:'method'});
assert.equal(collapsed.stats.get('table').canExpand,true);
assert.equal(collapsed.nodes.find(n=>n.id==='table').staticData,undefined);
for (const natural of [false,true]) {
  const scope = buildPathScope(nodes,edges,{rootId:'method',expandedIds:['table','constant']});
  const scene = buildPathScene(scope,{flowPath:true,flatFlow:true,reading:true,natural,controlFilters:{conditions:false,loops:false}});
  const selector = scene.visuals.find(n=>n.dataSelection);
  assert.equal(selector.kind,'dictionary');
  assert.deepEqual(scene.visuals.filter(n=>n.staticValue).map(n=>n.condition),['1','2','"TOM"']);
  assert.equal(scene.visualEdges.filter(e=>e.source===selector.id).length,2);
  const rectangles = [...scene.positions.values()];
  assert(rectangles.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
  for (let i=0;i<rectangles.length;i++) for (let j=i+1;j<rectangles.length;j++) {
    const a=rectangles[i],b=rectangles[j];
    assert(Math.abs(a.x-b.x)>=(a.w+b.w)/2 || Math.abs(a.y-b.y)>=(a.h+b.h)/2,'data alternatives cannot overlap');
  }
}
assert.equal(JSON.stringify({nodes,edges}),before);
assert(implementationBlock({_callable_class:true}));
assert(!executableBlock({_callable_class:true,_callable:true}),'type is not a method entry');
assert(implementationBlock(table));
const projected=projectExecutionPath({rootNodeId:'method',steps:nodes.map(n=>({nodeId:n.id,staticData:n.staticData})),edges},new Map(nodes.map(n=>[n.id,n])));
assert.equal(projected.edges[0].caption,'читает данные');
assert.equal(projected.nodes[1].staticData,table.staticData);
assert.equal(pathReadingTitle({rootKind:'class'}),'Методы класса');
assert.equal(pathReadingTitle({rootKind:'field'}),'Статические данные');
const writing = {id:'factory',label:'.Create()',events:[{kind:'data-write',dataKey:'codes[key]',dataValue:'[tom, small]',line:3,column:5,branchIds:['loop:body']}],
  controlFlow:{branches:[{kind:'foreach',line:2,header:'foreach(var key in values)',arms:[{id:'loop:body',kind:'body'}]}]}};
const writeScope=buildPathScope([writing],[],{rootId:'factory'});
const writeScene=buildPathScene(writeScope,{flowPath:true,flatFlow:true,reading:true});
const write=writeScene.visuals.find(n=>n.label==='codes[key]');
assert.equal(write.condition,'[tom, small]');
const link=writeScene.visualEdges.find(e=>e.target===write.id);
assert.equal(writeScene.byId.get(link.source).kind,'foreach','dynamic writes remain inside the iteration');
console.log('Static data alternatives, constants, projections, limits and both layouts PASS');
