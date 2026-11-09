/** Проверяет трассировку отдельных связей через общий коридор проектов. */
import assert from 'node:assert/strict';
import {edgeAnchors,routeEdge} from '../web/edge-routing.js';
const source={x:10,y:20,r:7},target={x:1010,y:60,r:7};
const left={id:'A',cx:0,cy:0,rx:120,ry:90},right={id:'B',cx:1000,cy:0,rx:120,ry:90};
const options={sourceContainer:left,targetContainer:right,key:'a->b',bundled:true};
const before=JSON.stringify({source,target,options});
const anchors=edgeAnchors(source,target,options),path=routeEdge(source,target,options);
assert.deepEqual(anchors[0],{x:source.x,y:source.y});assert.deepEqual(anchors.at(-1),{x:target.x,y:target.y});
assert.equal(path,routeEdge(source,target,options));assert.ok(path.includes(' C '));
assert.equal(JSON.stringify({source,target,options}),before);
const otherSource={x:-40,y:15,r:12},otherTarget={x:1040,y:-10,r:12};
const other=edgeAnchors(otherSource,otherTarget,{...options,key:'different'});
assert.ok(Math.hypot(anchors[2].x-other[2].x,anchors[2].y-other[2].y)<20,'Pair corridor diverges');
const reversed=edgeAnchors(target,source,{sourceContainer:right,targetContainer:left,key:'b->a'});
assert.ok(anchors[2].y>0&&reversed[2].y<0,'Opposite directions must have separate lanes');
for(const index of [1,3]){const point=anchors[index],box=index===1?left:right;assert.ok(Math.abs(((point.x-box.cx)/box.rx)**2+((point.y-box.cy)/box.ry)**2-1)<1e-10);}
assert.equal(routeEdge(source,target,{...options,bundled:false}),`M ${source.x} ${source.y} L ${target.x} ${target.y}`);
for(const [a,b,opts] of [[source,source,{}],[source,{...source},options],[source,target,{}],[source,target,{sourceContainer:left,targetContainer:left}]]){
  const result=routeEdge(a,b,opts);assert.ok(!/NaN|Infinity/.test(result));assert.ok(result.endsWith(`${b.x} ${b.y}`));
}
const reordered=new Map(['edge3','edge1','edge2'].map(key=>[key,routeEdge(source,target,{...options,key})]));
for(const key of ['edge2','edge3','edge1'])assert.equal(reordered.get(key),routeEdge(source,target,{...options,key}));
// Контрольные точки внутри указанной области гарантируют ту же границу всей кривой Безье.
const bounds={minX:-150,minY:-110,maxX:1150,maxY:110};
const bounded=routeEdge(source,target,{...options,bounds});
const values=bounded.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);
for(let index=0;index<values.length;index+=2){assert.ok(Number.isFinite(values[index])&&Number.isFinite(values[index+1]));assert.ok(values[index]>=bounds.minX&&values[index]<=bounds.maxX&&values[index+1]>=bounds.minY&&values[index+1]<=bounds.maxY);}
console.log('Edge routing PASS');
const {localRoutingRegions}=await import('../web/edge-routing.js');
const box={id:'local',x:0,y:0,width:400,height:400,cx:200,cy:200,rx:200,ry:200};
const locals=[{id:'a1',codeProjectId:'local',x:21,y:22},{id:'a2',codeProjectId:'local',x:35,y:33},{id:'b1',codeProjectId:'local',x:322,y:325},{id:'b2',codeProjectId:'local',x:348,y:351}];
const regions=localRoutingRegions(locals,[box]);
const localOptions=(a,b,key)=>({sourceContainer:box,targetContainer:box,sourceRegion:regions.get(a.id),targetRegion:regions.get(b.id),key});
const firstLocal=edgeAnchors(locals[0],locals[2],localOptions(locals[0],locals[2],'local1'));
const secondLocal=edgeAnchors(locals[1],locals[3],localOptions(locals[1],locals[3],'local2'));
assert.equal(firstLocal.length,5);assert.equal(secondLocal.length,5);
assert.deepEqual(firstLocal[1],secondLocal[1]);assert.deepEqual(firstLocal[3],secondLocal[3]);
assert.notDeepEqual(firstLocal[0],secondLocal[0]);assert.notDeepEqual(firstLocal.at(-1),secondLocal.at(-1));
assert.ok(Math.hypot(firstLocal[2].x-secondLocal[2].x,firstLocal[2].y-secondLocal[2].y)<5);
const reverseRegions=localRoutingRegions([...locals].reverse(),[box]);
for(const point of locals)assert.deepEqual(regions.get(point.id),reverseRegions.get(point.id));
console.log('Intra-project shared corridors PASS');
