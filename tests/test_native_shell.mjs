/** Native framing stays within the WebView session; browser pages retain their own shell. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const source = await readFile(new URL('../web/color-scheme-init.js', import.meta.url), 'utf8');

/** Run the real pre-paint initializer with isolated browser/session adapters. */
function initialize(search, savedSession = new Map()) {
  const classes = new Set();
  const sessionStorage = {setItem:(key,value)=>savedSession.set(key,value),getItem:key=>savedSession.get(key)};
  const document = {documentElement:{classList:{add:name=>classes.add(name)},setAttribute:()=>{}}};
  vm.runInNewContext(source,{document,location:{search},URLSearchParams,sessionStorage,localStorage:{getItem:()=>null}});
  return classes;
}

test('explicit native mode survives same-session local page navigation only', () => {
  const session = new Map();
  assert.ok(initialize('?desktop=1&repo=x',session).has('native-shell'));
  assert.ok(initialize('?repo=x',session).has('native-shell'));
  assert.ok(!initialize('?repo=x').has('native-shell'));
});
