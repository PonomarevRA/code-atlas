/** Окружение использует только исходные ID и направленные факты текущего фильтра. */
import assert from 'node:assert/strict';
import {expansionNeighborhood} from '../web/expansion.js';
const graph={nodes:[{id:'a'},{id:'b'},{id:'c'},{id:'hidden'},{id:'alone'}],edges:[
 {source:'a',target:'b',relation:'calls'}, {source:'a',target:'b',relation:'calls'},
 {source:'b',target:'a',relation:'references'}, {source:'c',target:'a',relation:'calls'},
 {source:'b',target:'c',relation:'calls'}, {source:'a',target:'hidden',relation:'calls'},
 {source:'a',target:'a',relation:'calls'}]};
const before=JSON.stringify(graph),allowed=graph.nodes.filter(n=>n.id!=='hidden');
const result=expansionNeighborhood(graph,'a',{nodes:allowed});
assert.deepEqual(result.nodes.map(n=>n.id),['a','b','c']);
assert.equal(result.totalNeighbors,2);assert.equal(result.totalEdges,6);
assert.equal(result.omittedNodes,0);assert.equal(result.omittedEdges,0);
assert.deepEqual(result.edges.filter(e=>e.source==='a'&&e.target==='b'),graph.edges.slice(0,2));
assert.ok(result.edges.some(e=>e.source==='b'&&e.target==='a'));
assert.deepEqual(expansionNeighborhood(graph,'alone').nodes,[graph.nodes[4]]);
assert.equal(expansionNeighborhood(graph,'hidden',{nodes:allowed}),null);
const capped=expansionNeighborhood(graph,'a',{nodes:allowed,maxNodes:2,maxEdges:2});
assert.deepEqual(capped.nodes.map(n=>n.id),['a','b']);assert.equal(capped.omittedNodes,1);
assert.equal(capped.totalEdges,4);assert.equal(capped.omittedEdges,2);assert.equal(capped.edges.length,2);
assert.equal(expansionNeighborhood(graph,'a',{relation:'references'}).edges.length,1);
assert.equal(expansionNeighborhood(graph,'a',{relations:['references']}).edges.length,1);
assert.deepEqual(expansionNeighborhood({...graph,nodes:[...graph.nodes].reverse(),edges:[...graph.edges].reverse()},'a',{nodes:[...allowed].reverse()}),result);
assert.equal(JSON.stringify(graph),before);
const large={nodes:Array.from({length:50},(_,i)=>({id:String(i).padStart(2,'0')})),edges:[]};
for(const source of large.nodes)for(const target of large.nodes)large.edges.push({source:source.id,target:target.id,relation:'calls'});
const limited=expansionNeighborhood(large,'00');
assert.equal(limited.nodes.length,25);assert.equal(limited.edges.length,400);assert.equal(limited.totalNeighbors,49);
assert.equal(limited.omittedNodes,25);assert.equal(limited.totalEdges,625);assert.equal(limited.omittedEdges,225);
console.log('Expansion raw facts, filters, direction and limits PASS');
