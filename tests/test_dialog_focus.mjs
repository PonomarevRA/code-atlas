/** Контракт клавиатуры диалога и сворачивания поиска без mutation карты. */
import assert from 'node:assert/strict';
import { DialogFocus } from '../web/dialog-focus.js';
import { DiscoveryShell } from '../web/discovery-shell.js';

class Element {
  constructor(name, document) { this.name=name; this.ownerDocument=document; this.listeners={}; this.hidden=false; this.inert=false; this.children=[]; this.attributes={}; }
  addEventListener(type, callback) { this.listeners[type]=callback; }
  setAttribute(name,value) { this.attributes[name]=value; }
  getAttribute(name) { return this.attributes[name]; }
  focus() { this.ownerDocument.activeElement=this; }
  closest() { return this.hidden||this.inert?this:null; }
  contains(element) { return this===element||this.children.includes(element); }
  querySelectorAll() { return this.children; }
  querySelector() { return this.children[0]; }
}
const document={};
const body=new Element('body',document); document.body=body;
const background=new Element('background',document);
const preserved=new Element('preserved',document); preserved.inert=true;
const root=new Element('dialog',document); root.parentElement=body;
const close=new Element('close',document), code=new Element('code',document), disabled=new Element('disabled',document); disabled.disabled=true;
root.children=[close,disabled,code]; body.children=[background,preserved,root];
const trigger=new Element('trigger',document); trigger.focus();
let closes=0;
const shell=new DialogFocus(root,{onClose:()=>{closes++;shell.close();},initialFocus:()=>close});
shell.open(); assert.equal(document.activeElement,close); assert.equal(background.inert,true);
code.focus(); let prevented=0;
root.listeners.keydown({key:'Tab',preventDefault:()=>prevented++});
assert.equal(document.activeElement,close); assert.equal(prevented,1);
root.listeners.keydown({key:'Tab',shiftKey:true,preventDefault:()=>prevented++});
assert.equal(document.activeElement,code,'backward Tab wraps to last enabled control');
shell.open(); root.listeners.keydown({key:'Escape',preventDefault(){},stopPropagation(){}});
assert.equal(closes,1); assert.equal(document.activeElement,trigger,'reopening must preserve original trigger');
assert.equal(background.inert,false); assert.equal(preserved.inert,true,'preexisting inert is restored');
shell.close(); assert.equal(document.activeElement,trigger);

const discovery=new Element('discovery',document), toggle=new Element('toggle',document), input=new Element('query',document);
input.value='CostChange'; discovery.children=[input]; let changes=0;
const search=new DiscoveryShell(discovery,toggle,{onLayout:()=>changes++});
input.focus(); search.setOpen(false);
assert.equal(discovery.hidden,true); assert.equal(discovery.inert,true); assert.equal(document.activeElement,toggle);
assert.equal(toggle.attributes['aria-expanded'],'false'); assert.equal(input.value,'CostChange');
search.openForMode({focus:true}); assert.equal(document.activeElement,input); assert.equal(discovery.hidden,false);
assert.equal(toggle.attributes['aria-expanded'],'true'); assert.equal(changes,2);

// В toolbar находятся исходные controls: сворачивание результатов не скрывает запрос.
const inlineRoot=new Element('discovery',document); inlineRoot.hidden=true;
const inlineFields=new Map();
for (const [mode,id] of [['code','navigation-search'],['meaning','semantic-query'],['entries','ingress-catalog-search']]) {
  const field=new Element(id,document); field.id=id; field.value=`${mode}-query`;
  field.setAttribute('data-discovery-mode',mode); inlineFields.set(id,field);
}
const popup=new Element('popup',document), label=new Element('label',document);
document.getElementById=id=>id==='navigation-search-results'?popup:id==='workspace-search-label'?label:inlineFields.get(id);
document.querySelectorAll=()=>[...inlineFields.values()];
const inlineShell=new DiscoveryShell(inlineRoot,null);
inlineShell.syncMode('code');
assert.equal(inlineFields.get('navigation-search').hidden,false);
assert.equal(inlineFields.get('semantic-query').hidden,true);
assert.equal(label.attributes.for,'navigation-search');
inlineFields.get('navigation-search').listeners.focus(); assert.equal(inlineRoot.hidden,false);
inlineFields.get('navigation-search').listeners.keydown({key:'Escape'});
assert.equal(inlineRoot.hidden,true); assert.equal(popup.hidden,true);
assert.equal(inlineFields.get('navigation-search').hidden,false,'Escape cannot hide the toolbar input');
inlineShell.syncMode('meaning');
assert.equal(label.attributes.for,'semantic-query');
assert.equal(inlineFields.get('semantic-query').hidden,false);
assert.equal(inlineFields.get('navigation-search').hidden,true);
inlineFields.get('semantic-query').listeners.input(); assert.equal(inlineRoot.hidden,false);
inlineShell.syncMode('entries');
assert.equal(inlineFields.get('ingress-catalog-search').hidden,false);
assert.equal(inlineFields.get('semantic-query').value,'meaning-query','mode switches preserve separate queries');
console.log('Dialog focus and discovery shell PASS');
