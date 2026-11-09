/** Minimal map chrome: reversible focus, accessible groups, persistence and viewport height. */
import assert from 'node:assert/strict';
import { MapChrome } from '../web/map-chrome.js';
class Element {
  constructor(doc) { this.ownerDocument=doc; this.attributes={}; this.children=[]; this.listeners=new Map(); this.classes=new Set(); this.properties=new Map(); this.hidden=false; this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)}; this.style={getPropertyValue:name=>this.properties.get(name),setProperty:(name,value)=>this.properties.set(name,value)}; }
  contains(element) { return element===this||this.children.includes(element); }
  addEventListener(name,fn) { this.listeners.set(name,fn); }
  removeEventListener(name) { this.listeners.delete(name); }
  setAttribute(name,value) { this.attributes[name]=value; }
  getAttribute(name) { return this.attributes[name]; }
  querySelector() { return this.children[0]; }
  focus() { this.ownerDocument.activeElement=this; }
  getBoundingClientRect() { return this.rect||{top:0,bottom:40}; }
}
/** Controllers are injected; chrome cannot load a graph or modify browser navigation. */
function fixture(storage=null) {
  const elements=new Map(); const doc={getElementById:id=>elements.get(id)};
  const viewport=new Element(doc); viewport.innerHeight=900; viewport.scrollTo=()=>{}; doc.defaultView=viewport;
  const body=new Element(doc), toolbar=new Element(doc), search=new Element(doc), controls=new Element(doc), context=new Element(doc), canvas=new Element(doc), canvasControls=new Element(doc);
  canvas.rect={top:240,bottom:900};
  const input=new Element(doc);input.value='CostChange';search.children.push(input);
  const toggles={search:new Element(doc),controls:new Element(doc),focus:new Element(doc),canvas:new Element(doc)};
  const project=new Element(doc);project.textContent='Financial project';elements.set('project-name',project);
  const title=new Element(doc);title.textContent='Файлы';elements.set('view-title',title);
  const root=new Element(doc), discovery={root,setOpen:open=>{root.hidden=!open;}};
  const panels={mobile:false,state:{left:true,right:false},savedPreferences:{desktop:{left:true,right:false},mobile:{left:false,right:false}},setOpen:(side,open,opts)=>{ assert.equal(opts.remember,false);panels.state[side]=open; }};
  const chrome=new MapChrome({body,toolbar,search,controls,discovery,panels,toggles,context,canvas,canvasControls,storage});
  return {chrome,doc,body,toolbar,search,controls,context,canvas,canvasControls,input,toggles,panels,root,viewport,elements};
}
const values=new Map();const storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};
const f=fixture(storage);
assert.equal(f.canvas.properties.get('--map-canvas-height'),'660px');
f.input.focus(); f.toggles.search.listeners.get('click')();
assert.equal(f.search.hidden,true);assert.equal(f.search.inert,true);assert.equal(f.doc.activeElement,f.toggles.search);assert.equal(f.input.value,'CostChange');assert.equal(f.root.hidden,true);
f.toggles.search.listeners.get('click')(); assert.equal(f.input.value,'CostChange');assert.equal(f.doc.activeElement,f.input);assert.equal(f.search.hidden,false);
f.toggles.focus.listeners.get('click')();
assert.deepEqual(f.panels.state,{left:false,right:false});assert.equal(f.body.classes.has('map-focus'),true);
assert.equal(f.controls.hidden,true);assert.equal(f.controls.inert,true);assert.equal(f.context.hidden,false);assert.equal(f.context.textContent,'Financial project · Файлы');
assert.equal(f.toggles.focus.attributes['aria-pressed'],'true');
assert.equal(f.canvasControls.hidden,true);
f.chrome.setOpen('canvas',true);assert.equal(f.canvasControls.hidden,false);
f.chrome.setOpen('canvas',false);
f.canvas.rect.top=40;f.viewport.listeners.get('resize')();assert.equal(f.canvas.properties.get('--map-canvas-height'),'860px');
// After reload, focus still closes the panels without rewriting their own preferences.
const restored=fixture(storage);assert.equal(restored.chrome.state.focus,true);assert.deepEqual(restored.panels.state,{left:false,right:false});
restored.chrome.setFocus(false);assert.deepEqual(restored.panels.state,{left:true,right:false});assert.equal(restored.controls.hidden,false);assert.equal(restored.search.hidden,false);assert.equal(restored.canvasControls.hidden,false);
restored.chrome.dispose();
// A manually opened panel is retained on leaving focus.
f.panels.state.right=true;f.chrome.syncPanels({reason:'action'});f.chrome.setFocus(false);assert.deepEqual(f.panels.state,{left:true,right:true},'Opening right panel does not lose original left preference');assert.equal(f.input.value,'CostChange');
f.chrome.setOpen('controls',false);assert.equal(f.controls.hidden,true);assert.equal(f.search.hidden,false,'Search and map menus are independent');
f.chrome.setFocus(true);f.panels.mobile=true;f.panels.state.left=true;f.chrome.syncPanels({reason:'viewport'});assert.deepEqual(f.panels.state,{left:false,right:false});f.chrome.setFocus(false);assert.deepEqual(f.panels.state,{left:false,right:false},'Mobile restores its own saved sides');
f.chrome.dispose();assert.equal(f.viewport.listeners.size,0);assert.equal(f.toggles.search.listeners.size,0);
const broken=fixture({getItem(){throw new Error('disabled');},setItem(){throw new Error('disabled');}});broken.chrome.setFocus(true);assert.equal(broken.controls.hidden,true);broken.chrome.dispose();
for(const value of ['bad json',JSON.stringify({search:'yes',controls:false,focus:true}),'x'.repeat(2050)]){const invalid=fixture({getItem:()=>value});assert.deepEqual(invalid.chrome.state,{search:true,controls:true,canvas:true,focus:false});invalid.chrome.dispose();}
console.log('Map chrome reversible focus, persistence, ARIA, query retention and available canvas height PASS');

const legacy=fixture({getItem:()=>JSON.stringify({search:false,controls:false,focus:false})});assert.equal(legacy.chrome.state.canvas,true);legacy.chrome.dispose();

const legacyFocus=fixture({getItem:()=>JSON.stringify({search:false,controls:false,focus:true})});assert.equal(legacyFocus.canvasControls.hidden,true);legacyFocus.chrome.dispose();
