/** The same production scene builder consumes all eleven provider descriptions. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildMethodSequence } from '../web/method-sequence.js';
const items=JSON.parse(readFileSync(process.argv[2]));
const summary=[];
for(const item of items){
 const graphBefore=JSON.stringify(item.graph), descriptionBefore=JSON.stringify(item.description);
 const scene=buildMethodSequence(item.graph,item.root,{rootData:item.description,getData:id=>id===item.root?item.description:undefined});
 assert.ok(scene?.sequence,item.language);
 assert.ok(scene.visuals.length>2,item.language);
 assert.ok(scene.visuals.some(card=>card.kind==='call'&&card.targetNodeIds?.includes(item.language+'-work')),item.language+' lost raw call target');
 assert.ok(scene.visuals.some(card=>card.kind==='return'),item.language+' lost return');
 for(const card of scene.visuals) for(const key of ['x','y','w','h']) assert.ok(Number.isFinite(card[key]),item.language+':'+key);
 assert.equal(JSON.stringify(item.graph),graphBefore);
 assert.equal(JSON.stringify(item.description),descriptionBefore);
 summary.push({language:item.language,cards:scene.visuals.length,kinds:[...new Set(scene.visuals.map(card=>card.kind))]});
}
console.log(JSON.stringify(summary));console.log('One production scene builder for 11 provider trees PASS');
