import test from 'node:test';
import assert from 'node:assert/strict';
import {moduleLayoutContinuity} from '../web/module-layout-continuity.js';

const card = (x, y, width = 100, height = 70) => ({x, y, width, height});
const options = {frameId:'scope:1', previousFrameId:'scope:1'};

test('existing geometry preserves shared stable centers independently of collection order', () => {
  const previous = new Map([['a',card(0,0)],['b',card(300,0)]]);
  const current = new Map([['b',card(5,10)],['a',card(7,8)],['c',card(0,0)]]);
  const input = structuredClone(current), old = structuredClone(previous);
  const result = moduleLayoutContinuity(current, previous, options);
  assert.equal(result.positions.get('a').x, 0);
  assert.equal(result.positions.get('b').x, 300);
  assert.equal(result.report.maxDisplacement, 0);
  assert.equal(result.report.readableWithoutOverlaps, true);
  assert.deepEqual(current,input);assert.deepEqual(previous,old);
});

test('new colliding cards have deterministic placement independent of input response order', () => {
  const one = new Map([['b',card(0,0)],['a',card(0,0)]]);
  const two = new Map([...one].reverse());
  assert.deepEqual(moduleLayoutContinuity(one,new Map(),options).positions,
    moduleLayoutContinuity(two,new Map(),options).positions);
});

test('current dimensions and label widths cause non-pinned repair, keeping pin center', () => {
  const previous = new Map([['a',card(0,0)],['b',card(150,0)]]);
  const current = new Map([['a',{...card(900,0),labelWidth:400}],['b',card(-1,0,250,120)]]);
  const result = moduleLayoutContinuity(current,previous,{...options,pinnedIds:['a']});
  assert.equal(result.positions.get('a').x,0);
  assert.equal(result.positions.get('a').width,400);
  assert.equal(result.positions.get('b').height,120);
  assert.ok(result.report.repairedNonPinned.includes('b'));
  assert.ok(result.report.maxDisplacement>0);
  assert.equal(result.report.overlaps.length,0);
});

test('incompatible pins stay fixed with explicit unreadable overlap evidence', () => {
  const previous = new Map([['a',card(0,0)],['b',card(50,0)]]);
  const result = moduleLayoutContinuity(previous,previous,{...options,pinnedIds:new Set(['a','b'])});
  assert.equal(result.positions.get('a').x,0);assert.equal(result.positions.get('b').x,50);
  assert.equal(result.report.readableWithoutOverlaps,false);
  assert.deepEqual(result.report.overlaps,[{source:'a',target:'b',bothPinned:true}]);
});

test('new pins retain caller center even if an existing helper would place them elsewhere', () => {
  const current = new Map([['other',card(0,0)],['pin',card(0,0)]]);
  const result = moduleLayoutContinuity(current,new Map(),{...options,pinnedIds:['pin']});
  assert.equal(result.positions.get('pin').x,0);
  assert.equal(result.report.readableWithoutOverlaps,true);
  assert.deepEqual(result.report.unknownPins,['pin']);
});

test('frame mismatch does not compare or reuse unrelated old coordinates', () => {
  const previous = new Map([['a',card(900,900)]]), current = new Map([['a',card(0,0)]]);
  const result = moduleLayoutContinuity(current,previous,{frameId:'new',previousFrameId:'old',pinnedIds:['a']});
  assert.equal(result.positions.get('a').x,0);assert.equal(result.report.comparable,false);
  assert.equal(result.report.meanDisplacement,null);assert.deepEqual(result.report.displacement,[]);
  assert.ok(result.report.fallbackReason);
});

test('removed cards do not reserve space and missing pins remain unknown', () => {
  const result = moduleLayoutContinuity(new Map([['b',card(0,0)]]),new Map([['a',card(0,0)]]),{...options,pinnedIds:['a']});
  assert.equal(result.positions.get('b').x,0);assert.deepEqual(result.report.unknownPins,['a']);
});

test('invalid/oversized geometry and absent coordinate frame fail explicitly', () => {
  assert.throws(()=>moduleLayoutContinuity(new Map([['a',card(NaN,0)]]),new Map(),options));
  assert.throws(()=>moduleLayoutContinuity(new Map([['a',card(0,0,0)]]),new Map(),options));
  assert.throws(()=>moduleLayoutContinuity(new Map(),new Map()));
  assert.throws(()=>moduleLayoutContinuity(new Map(Array.from({length:201},(_,i)=>[String(i),card(i,0)])),new Map(),options));
});
