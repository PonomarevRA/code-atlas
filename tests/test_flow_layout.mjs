/** Проверяет направленную карточную раскладку, SCC и геометрию портов без браузерных зависимостей. */
import assert from 'node:assert/strict';
import {flowLayout,flowEdgePath,flowEdgeKey} from '../web/flow-layout.js';
const nodes=['A','B','C','D','loop','isolate'].map(id=>({id,label:id}));
const edges=[{source:'A',target:'B'},{source:'A',target:'B'},{source:'B',target:'C'},{source:'C',target:'B'},{source:'C',target:'D'},{source:'loop',target:'loop'}];
const original=JSON.stringify({nodes,edges}),layout=flowLayout(nodes,edges);
/** Проверяет непересечение карточек и включение каждой в общий fit bbox. */
function validCards(result,count){
 assert.equal(result.positions.size,count);
 const boxes=[...result.positions.values()],bounds=result.bounds;
 for(const box of boxes){
  for(const name of ['x','y','width','height'])assert.ok(Number.isFinite(box[name]),name);
  assert.ok(box.width>0&&box.height>0);
  assert.ok(box.x-box.width/2>=bounds.x-1e-6);assert.ok(box.x+box.width/2<=bounds.x+bounds.width+1e-6);
  assert.ok(box.y-box.height/2>=bounds.y-1e-6);assert.ok(box.y+box.height/2<=bounds.y+bounds.height+1e-6);
 }
 for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){
  const a=boxes[i],b=boxes[j];
  assert.ok(Math.abs(a.x-b.x)>=(a.width+b.width)/2||Math.abs(a.y-b.y)>=(a.height+b.height)/2,'Overlapping cards');
 }
}
validCards(layout,nodes.length);
assert.equal(layout.positions.get('B').cycleId,layout.positions.get('C').cycleId);
assert.ok(layout.positions.get('B').cycleId);assert.ok(layout.positions.get('loop').cycleId);
assert.notEqual(layout.positions.get('A').cycleId,layout.positions.get('B').cycleId);
assert.ok(layout.positions.get('A').x<layout.positions.get('B').x);
assert.ok(layout.positions.get('C').x<layout.positions.get('D').x);
assert.equal(layout.edgeKinds.get(flowEdgeKey(edges[0])),'forward');
assert.equal(layout.edgeKinds.get(flowEdgeKey(edges[3])),'back');
assert.equal(layout.edgeKinds.get(flowEdgeKey(edges[5])),'self');
assert.deepEqual(layout.cycles.map(c=>[...c.nodeIds].sort()).sort(),[['B','C'],['loop']].sort());
for(const [index,edge] of edges.entries()){
 const source=layout.positions.get(edge.source),target=layout.positions.get(edge.target);
 const path=flowEdgePath(source,target,index),values=path.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);
 assert.ok(values.every(Number.isFinite));
 assert.deepEqual(values.slice(0,2),[source.x+source.width/2,source.y]);
 assert.deepEqual(values.slice(-2),[target.x-target.width/2,target.y]);
 assert.equal(flowEdgePath(source,target,index),path);
 // Cubic control-point hull is a conservative bound of the whole curve.
 for(let i=0;i<values.length;i+=2){
  assert.ok(values[i]>=layout.bounds.x&&values[i]<=layout.bounds.x+layout.bounds.width);
  assert.ok(values[i+1]>=layout.bounds.y&&values[i+1]<=layout.bounds.y+layout.bounds.height);
 }
}
const reversed=flowLayout([...nodes].reverse(),[...edges].reverse());
assert.deepEqual([...layout.positions].sort(),[...reversed.positions].sort());
assert.deepEqual(layout.bounds,reversed.bounds);
assert.equal(JSON.stringify({nodes,edges}),original);
const large=Array.from({length:320},(_,i)=>({id:`n${String(i).padStart(3,'0')}`,label:`Node ${i}`}));
const largeEdges=large.slice(1).map((node,i)=>({source:large[i].id,target:node.id}));
for(let i=0;i<large.length;i+=20)largeEdges.push({source:large[Math.min(i+9,319)].id,target:large[i].id});
const started=performance.now(),largeResult=flowLayout(large,largeEdges);
validCards(largeResult,320);
assert.deepEqual([...largeResult.positions].sort(),[...flowLayout([...large].reverse(),[...largeEdges].reverse()).positions].sort());
assert.ok(performance.now()-started<5000,'320-card layout exceeds generous regression budget');
validCards(flowLayout([{id:'one'}],[]),1);
assert.equal(flowLayout([],[]).positions.size,0);
console.log('Flow layout SCC, direction, bounds and deterministic 320 cards PASS');

// Независимый oracle: взаимная достижимость, а не повтор алгоритма Косарайю.
let seed=42;
const random=()=>{seed=(1664525*seed+1013904223)>>>0;return seed/2**32;};
for(let sample=0;sample<10;sample++){
 const items=Array.from({length:12},(_,i)=>({id:`r${i}`})),facts=[];
 for(const source of items)for(const target of items)if(random()<.12)facts.push({source:source.id,target:target.id});
 const reach=new Map();
 for(const node of items){
  const seen=new Set(),pending=[node.id];
  while(pending.length){const id=pending.pop();for(const edge of facts)if(edge.source===id&&!seen.has(edge.target)){seen.add(edge.target);pending.push(edge.target);}}
  reach.set(node.id,seen);
 }
 const result=flowLayout(items,facts);validCards(result,items.length);
 for(const a of items)for(const b of items){
  const sameCycle=Boolean(result.positions.get(a.id).cycleId)&&result.positions.get(a.id).cycleId===result.positions.get(b.id).cycleId;
  assert.equal(sameCycle,reach.get(a.id).has(b.id)&&reach.get(b.id).has(a.id));
 }
}
const isolates=flowLayout(large,[]);validCards(isolates,320);
assert.ok(isolates.bounds.width/isolates.bounds.height<4,'Disconnected cards should use shelves, not a single endless row');
console.log('Flow independent reachability oracle and isolate packing PASS');
