/** Проверяет направленные факты метаграфа проектов и размещение блоков по связям. */
import assert from 'node:assert/strict';
import { aggregateProjectLinks, projectContainerGeometry } from '../web/project-blocks.js';
const nodes=[{id:'a',codeProjectId:'A',r:12},{id:'b',codeProjectId:'B',r:12},{id:'c',codeProjectId:'C',r:12},{id:'d',codeProjectId:'D',r:12},{id:'missing',r:12}];
const edges=[{source:'a',target:'b',weight:2.5,confidence:.5},{source:'a',target:'b'},{source:'b',target:'a'},{source:'a',target:'a'},{source:'c',target:'missing'},{source:'unknown',target:'a'}];
const before=JSON.stringify({nodes,edges});
const aggregate=aggregateProjectLinks(nodes,edges);
assert.deepEqual(aggregate.totals.get('A'),{incoming:1,outgoing:3.5,internal:1});
assert.deepEqual(aggregate.totals.get('B'),{incoming:3.5,outgoing:1,internal:0});
assert.deepEqual(aggregate.totals.get('D'),{incoming:0,outgoing:0,internal:0});
assert.deepEqual(aggregate.totals.get('__unassigned__'),{incoming:1,outgoing:0,internal:0});
assert.equal(aggregate.links.find(edge=>edge.source==='A'&&edge.target==='B').weight,3.5);
assert.equal(aggregate.links.find(edge=>edge.source==='B'&&edge.target==='A').weight,1);
assert.equal(aggregate.links.length,3);
const invalidWeights=aggregateProjectLinks(nodes,[0,-2,NaN,Infinity].map(weight=>({source:'b',target:'a',weight})));
assert.equal(invalidWeights.links[0].weight,4);
assert.equal(invalidWeights.totals.get('A').incoming,4);
const weightedInternal=aggregateProjectLinks(nodes,[{source:'a',target:'a',weight:2.5}]);
assert.equal(weightedInternal.totals.get('A').internal,2.5);
assert.equal(JSON.stringify({nodes,edges}),before);
// Рендер может отсечь все линии; статистика и метаграф берутся из полного набора фактов.
const fullEdges=Array.from({length:1601},()=>({source:'a',target:'b'}));
fullEdges.push({source:'b',target:'a'});
const uncapped=projectContainerGeometry(nodes,[],[],{nodes,edges:fullEdges});
assert.equal(uncapped.containers.find(box=>box.id==='A').outgoing,1601);
assert.equal(uncapped.containers.find(box=>box.id==='A').incoming,1);
assert.equal(uncapped.containers.find(box=>box.id==='B').incoming,1601);
/** Сравнивает координаты блоков по стабильным идентификаторам. */
function coords(geometry){return geometry.containers.map(box=>[box.id,box.x,box.y]).sort((a,b)=>a[0].localeCompare(b[0]));}
const chain=[{source:'a',target:'b'},{source:'b',target:'c'},{source:'c',target:'d'}];
const layout=projectContainerGeometry(nodes,chain);
assert.deepEqual(coords(layout),coords(projectContainerGeometry([...nodes].reverse(),[...chain].reverse())));
assert.deepEqual(coords(layout),coords(projectContainerGeometry(nodes,chain.map(edge=>({source:edge.target,target:edge.source})))));
const changed=[{source:'a',target:'c'},{source:'c',target:'b'},{source:'b',target:'d'}];
assert.notDeepEqual(coords(layout),coords(projectContainerGeometry(nodes,changed)));
const weighted=chain.concat(Array.from({length:30},()=>({source:'a',target:'b'})));
assert.notDeepEqual(coords(layout),coords(projectContainerGeometry(nodes,weighted)));
for(const box of layout.containers){
  assert.ok([box.x,box.y,box.width,box.height].every(Number.isFinite));
  const own=nodes.filter(node=>(node.codeProjectId??'__unassigned__')===box.id);
  for(const node of own){const point=layout.positions.get(node.id);assert.ok(point.x-point.r>=box.x&&point.x+point.r<=box.x+box.width&&point.y-point.r>=box.y&&point.y+point.r<=box.y+box.height);}
}
for(let first=0;first<layout.containers.length;first++)for(let second=first+1;second<layout.containers.length;second++){
  const a=layout.containers[first],b=layout.containers[second];assert.ok(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y);
}
assert.equal(projectContainerGeometry([],[]).containers.length,0);
assert.equal(projectContainerGeometry([nodes[0]],[]).positions.size,1);
const maximumNodes=Array.from({length:320},(_,index)=>({id:`node${index}`,codeProjectId:`project${index}`,r:7}));
const maximumEdges=maximumNodes.slice(1).map((node,index)=>({source:node.id,target:maximumNodes[index].id}));
const started=performance.now();
const maximum=projectContainerGeometry(maximumNodes,maximumEdges);
assert.equal(maximum.containers.length,320);
for(let first=0;first<maximum.containers.length;first++)for(let second=first+1;second<maximum.containers.length;second++){
  const a=maximum.containers[first],b=maximum.containers[second];assert.ok(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y);
}
console.log(`320 project nodes: ${Math.round(performance.now()-started)}ms`);
console.log('Project metagraph PASS');
