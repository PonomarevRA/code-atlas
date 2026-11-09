/** A menu at either screen edge must stay within the same viewport after placement. */
import assert from 'node:assert/strict';
import {menuViewportPlacement} from '../web/menu-placement.js';
for (const rect of [
  {left:-141,right:139,top:44},
  {left:600,right:880,top:44},
  {left:120,right:400,top:44},
]) {
  const result=menuViewportPlacement(rect,720,800);
  assert.ok(rect.left+result.shiftX>=12);
  assert.ok(rect.right+result.shiftX<=708);
  assert.equal(result.maxHeight,744);
}
assert.deepEqual(menuViewportPlacement({left:12,right:292,top:44},304,60),{shiftX:0,maxHeight:4});
assert.equal(menuViewportPlacement({left:12,right:100,top:100},320,80).maxHeight,0);
console.log('Menu viewport placement PASS');
