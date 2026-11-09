/** State tests exercise isolation, pagination and delayed replies without a browser. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../web/data-key-controller.js', import.meta.url), 'utf8');
const { DataKeyExplorer } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
globalThis.Option = class { constructor(label,value) { this.label=label; this.value=value; } };
/** Minimal controls retain event handlers and options, without interpreting markup. */
function control() { return { hidden:true, value:'', options:[], listeners:{},
  addEventListener(name,callback) {this.listeners[name]=callback;},
  replaceChildren(...options) {this.options=options;}, append(option) {this.options.push(option);},
  setAttribute(name,value) {this[name]=value;} }; }
const controls = Object.fromEntries(['bar','select','moreKeys','moreValues','isolate','moreRows','info'].map(name=>[name,control()]));
const calls = []; let graph, rows, release;
const key={id:'k',kind:'data-key',column:0};
const values=[{id:'v1',kind:'data-value',owner:'k',valueRecordId:'r1',label:'A',valueType:'string'},
  {id:'v2',kind:'data-value',owner:'k',valueRecordId:'r2',label:'B',valueType:'string'}];
const path={nodes:[key,values[0],{id:'r1',kind:'data-record',column:2}],links:[{source:'v1',target:'r1',relation:'value_record'}],matchedRecords:1};
const query=async (operation,body)=>{calls.push([operation,body]);
  if(operation==='keys')return {items:[{...key,field:'/group',file:'a.csv',repeatOccurrences:2,matchedRecords:0}]};
  if(operation==='key-values')return {key,items:values};
  if(body.valueRecordId==='r2')return new Promise(resolve=>{release=resolve;});
  return path;
};
const explorer = new DataKeyExplorer(controls,{setGraph(nodes,links){graph={nodes,links};}},query,
  result=>{rows=result;},async()=>{},error=>{throw error;});
await explorer.loadKeys();
controls.select.value='k'; await explorer.chooseKey();
assert.deepEqual(graph.nodes.map(node=>node.id),['k','v1','v2']);
await explorer.select(values[0]);
assert.ok(graph.nodes.some(node=>node.id==='v2') && graph.nodes.some(node=>node.id==='r1'));
assert.equal(rows,path);
controls.isolate.listeners.click();
assert.deepEqual(graph.nodes.map(node=>node.id),['k','v1','r1']);
assert.equal(controls.isolate['aria-pressed'],'true');
controls.isolate.listeners.click(); assert.ok(graph.nodes.some(node=>node.id==='v2'));
const delayed=explorer.chooseValue(values[1]);
explorer.reset(); const lastGraph=graph;
release({...path,nodes:[{id:'unrelated'}]}); await delayed;
assert.equal(graph,lastGraph); assert.equal(explorer.path,null);
assert.ok(calls.every(([operation])=>!['create','refresh'].includes(operation)));
console.log('PASS: values, isolated/restored path and stale reply suppression; no build requests');
