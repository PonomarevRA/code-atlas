import assert from 'node:assert/strict';
import { inlineHierarchyLayout } from '../web/inline-hierarchy-layout.js';
import { buildInlineHierarchy } from '../web/inline-hierarchy.js';
const overlap = (a,b) => a.x < b.x+b.width && a.x+a.width > b.x && a.y < b.y+b.height && a.y+a.height > b.y;
const contains = (outer, inner) => outer.x <= inner.x && outer.y <= inner.y && outer.x+outer.width >= inner.x+inner.width && outer.y+outer.height >= inner.y+inner.height;
const node = id => ({id,label:id,kind:'class',file:'Code.cs'});
const descriptor = raw => ({id:raw.id,rawId:raw.id,label:raw.label,kind:'raw',node:raw});
const nodes = [node('owner'),node('neighbor'),...Array.from({length:9},(_,index)=>node('child'+index))];
const graph = {nodes,edges:Array.from({length:9},(_,index)=>({source:'owner',target:'child'+index,relation:'contains'}))};
const scene=buildInlineHierarchy(graph,{roots:[descriptor(nodes[0]),descriptor(nodes[1])],expandedIds:new Set(['owner'])});
const original=JSON.stringify(scene.items);
const layout=inlineHierarchyLayout(scene);
assert.equal(JSON.stringify(scene.items),original);
assert.equal(layout.positions.size,scene.items.length);
const owner=layout.positions.get('owner'), neighbor=layout.positions.get('neighbor');
assert.ok(!overlap(owner.cardBounds,neighbor.cardBounds));
assert.equal(owner.headerBounds.height,112);
assert.equal(owner.x,owner.headerBounds.x+owner.headerBounds.width/2);
assert.equal(owner.y,owner.headerBounds.y+56);
assert.equal(owner.width,owner.cardBounds.width);
assert.ok(owner.cardBounds.height>owner.headerBounds.height);
assert.ok(owner.cardBounds.width<=1020);
assert.equal(new Set(scene.items.filter(item=>item.parentId==='owner').map(item=>layout.positions.get(item.id).cardBounds.y)).size,3,'9 children wrap into3 rows');
for(const child of scene.items.filter(item=>item.parentId==='owner')) {
 const bounds=layout.positions.get(child.id).cardBounds;
 assert.ok(contains(owner.cardBounds,bounds));
 assert.ok(bounds.y>=owner.headerBounds.y+112+20);
 assert.ok(bounds.x>=owner.cardBounds.x+20);
 assert.ok(bounds.x+bounds.width<=owner.cardBounds.x+owner.cardBounds.width-20);
}
const children=scene.items.filter(item=>item.parentId==='owner');
for(let a=0;a<children.length;a++)for(let b=a+1;b<children.length;b++)assert.ok(!overlap(layout.positions.get(children[a].id).cardBounds,layout.positions.get(children[b].id).cardBounds));
assert.equal(layout.frames.length,1);
assert.equal(layout.frames[0].id,'owner');
assert.equal(layout.frames[0].headerBounds,owner.headerBounds);

const chainNodes=Array.from({length:13},(_,index)=>node('n'+index));
const chainGraph={nodes:chainNodes,edges:chainNodes.slice(1).map((child,index)=>({source:chainNodes[index].id,target:child.id,relation:'contains'}))};
const chain=buildInlineHierarchy(chainGraph,{roots:[descriptor(chainNodes[0]),descriptor(node('external'))],rawNodes:[...chainNodes,node('external')],expandedIds:new Set(chainNodes.map(item=>item.id)),maxDepth:12});
const nested=inlineHierarchyLayout(chain);
assert.ok([...nested.positions.values()].every(item=>Number.isFinite(item.x)&&Number.isFinite(item.y)&&item.cardBounds.width<=1020));
for(const item of chain.items) {
 if(!item.parentId)continue;
 const parent=nested.positions.get(item.parentId), child=nested.positions.get(item.id);
 assert.ok(contains(parent.cardBounds,child.cardBounds));
 assert.ok(child.cardBounds.y>=parent.headerBounds.y+112+20);
}
assert.ok(!overlap(nested.positions.get('n0').cardBounds,nested.positions.get('external').cardBounds));

const rootScene={items:Array.from({length:10},(_,index)=>({id:'root'+index,parentId:null,expanded:false,children:[]})),roots:Array.from({length:10},(_,index)=>'root'+index)};
const grid=inlineHierarchyLayout(rootScene);
assert.equal(new Set([...grid.positions.values()].map(item=>item.cardBounds.x)).size,3);
for(const a of rootScene.roots)for(const b of rootScene.roots)if(a!==b)assert.ok(!overlap(grid.positions.get(a).cardBounds,grid.positions.get(b).cardBounds));
const permuted={...scene,roots:[...scene.roots].reverse(),items:[...scene.items].reverse().map(item=>({...item,children:[...item.children].reverse()}))};
const permutation=inlineHierarchyLayout(permuted);
assert.deepEqual([...layout.positions.entries()].sort(),[...permutation.positions.entries()].sort());
assert.deepEqual(layout.bounds,permutation.bounds);
const collapsed=buildInlineHierarchy(graph,{roots:[descriptor(nodes[0]),descriptor(nodes[1])]});
const small=inlineHierarchyLayout(collapsed);
assert.ok(layout.bounds.height>small.bounds.height);
assert.equal(small.frames.length,0);
assert.deepEqual(inlineHierarchyLayout({items:[],roots:[]}).bounds,{x:0,y:0,width:0,height:0});
console.log('Inline hierarchy geometry: PASS');

// External enter circle center headerRight+12, radius12 stays clear of sibling headers and inside parent padding.
for(const a of children){
 const header=layout.positions.get(a.id).headerBounds;
 const hit={x:header.x+header.width,y:header.y+44,width:24,height:24};
 assert.ok(contains(owner.cardBounds,hit));
 for(const b of children)if(a.id!==b.id)assert.ok(!overlap(hit,layout.positions.get(b.id).headerBounds));
}

// Irregular topology stays irregular: expansion reserves space instead of packing a new grid.
const base = new Map([
 ['owner',{x:100,y:100,width:320,height:112}],
 ['neighbor',{x:480,y:100,width:320,height:112}],
 ['below',{x:100,y:260,width:320,height:112}],
 ['distant',{x:-700,y:-500,width:320,height:112}],
]);
const topologyScene = {...scene,items:[...scene.items,{id:'below',children:[]},{id:'distant',children:[]}],roots:[...scene.roots,'below','distant']};
const reserved = inlineHierarchyLayout(topologyScene,base);
assert.deepEqual(reserved.positions.get('owner').headerBounds,base.get('owner'),'expanded header retains original size and position');
assert.equal(reserved.positions.get('neighbor').headerBounds.y,100,'right neighbor stays in its row');
assert.ok(reserved.positions.get('neighbor').headerBounds.x>480,'only rightward space is added');
assert.equal(reserved.positions.get('below').headerBounds.x,100,'lower neighbor stays in its column');
assert.ok(reserved.positions.get('below').headerBounds.y>260,'lower neighbor is pushed below expanded content');
assert.deepEqual(reserved.positions.get('distant').headerBounds,base.get('distant'),'unaffected neighborhood does not move');
for (const a of topologyScene.roots) for (const b of topologyScene.roots) if(a!==b) assert.ok(!overlap(reserved.positions.get(a).cardBounds,reserved.positions.get(b).cardBounds));
const closed = {...topologyScene,items:topologyScene.items.filter(item=>!item.parentId).map(item=>({...item,expanded:false,children:[]}))};
const restoredGeometry = inlineHierarchyLayout(closed,base);
for (const [id,box] of base) assert.deepEqual(restoredGeometry.positions.get(id).headerBounds,box,'collapse restores original positions');

// Expanding a deep subtree must not turn a three-column row into one column.
const stableNodes=[node('root'),...Array.from({length:6},(_,i)=>node('s'+i)),...Array.from({length:7},(_,i)=>node('deep'+i))];
const stableEdges=[...stableNodes.slice(1,7).map(n=>({source:'root',target:n.id,relation:'contains'})),{source:'s0',target:'deep0',relation:'contains'},...Array.from({length:6},(_,i)=>({source:'deep'+i,target:'deep'+(i+1),relation:'contains'}))];
const stableGraph={nodes:stableNodes,edges:stableEdges};
const beforeDeep=inlineHierarchyLayout(buildInlineHierarchy(stableGraph,{roots:[descriptor(stableNodes[0])],expandedIds:new Set(['root']),maxDepth:12}));
const afterDeep=inlineHierarchyLayout(buildInlineHierarchy(stableGraph,{roots:[descriptor(stableNodes[0])],expandedIds:new Set(['root','s0',...stableNodes.slice(7).map(n=>n.id)]),maxDepth:12}));
for (const [a,b] of [['s0','s1'],['s1','s2'],['s3','s4'],['s4','s5']]) {
 assert.equal(beforeDeep.positions.get(a).y,beforeDeep.positions.get(b).y);
 assert.equal(afterDeep.positions.get(a).y,afterDeep.positions.get(b).y,'siblings retain row membership during deep expansion');
 assert.ok(afterDeep.positions.get(a).x<afterDeep.positions.get(b).x);
}
assert.equal(afterDeep.positions.get('s1').headerBounds.width,beforeDeep.positions.get('s1').headerBounds.width,'neighbor headers are not narrowed');
assert.ok(afterDeep.positions.get('s3').y>beforeDeep.positions.get('s3').y);
for(const id of ['s0','s1','s2','s3','s4','s5']) assert.ok(contains(afterDeep.positions.get('root').cardBounds,afterDeep.positions.get(id).cardBounds));
