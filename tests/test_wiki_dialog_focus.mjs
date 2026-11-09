/** Wiki integration must use the same modal isolation as the source reader. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {WikiViewer} from '../web/wiki-view.js';

class Element {
  constructor(document, tag='button') { this.ownerDocument=document; this.tagName=tag.toUpperCase(); this.listeners={}; this.children=[]; this.hidden=false; this.inert=false; this.isConnected=true; }
  addEventListener(name, handler) { this.listeners[name]=handler; }
  replaceChildren(...nodes) { this.children=nodes; }
  focus() { this.ownerDocument.activeElement=this; }
  closest() { return this.hidden||this.inert?this:null; }
  getClientRects() { return this.hidden?[]:[{}]; }
  querySelectorAll() { return this.children; }
}
const document={};globalThis.document=document;
const body=document.body=new Element(document,'body');
const background=new Element(document,'main'),preserved=new Element(document,'aside');preserved.inert=true;
const root=new Element(document,'section');root.hidden=true;root.parentElement=body;
const fields=new Map(['title','status','content','close'].map(name=>[name,new Element(document)]));
const last=new Element(document);root.children=[fields.get('close'),last];body.children=[background,preserved,root];
root.querySelector=selector=>fields.get(selector.match(/"(.*?)"/)[1]);
const trigger=new Element(document);trigger.focus();let navigations=0;
const wiki=new WikiViewer(root,{getScope:()=>({}),onNavigate:()=>navigations++});
wiki.begin('Guide',true);
assert.equal(background.inert,true);assert.equal(preserved.inert,true);assert.equal(document.activeElement,fields.get('close'));
last.focus();let prevented=0;
root.listeners.keydown({key:'Tab',preventDefault(){prevented++;}});
assert.equal(document.activeElement,fields.get('close'));assert.equal(prevented,1);
last.focus();wiki.begin('Second document',false);
assert.equal(document.activeElement,fields.get('close'),'next document moves focus out of replaced content');
root.listeners.keydown({key:'Escape',preventDefault(){},stopPropagation(){}});
assert.equal(root.hidden,true);assert.equal(background.inert,false);assert.equal(preserved.inert,true);
assert.equal(document.activeElement,trigger,'document navigation must retain the original trigger');assert.equal(navigations,2);
wiki.begin('For code navigation',false);wiki.close(false);
assert.equal(background.inert,false,'silent navigation must release modal background');assert.equal(navigations,2);
const html=await readFile(new URL('../web/index.html',import.meta.url),'utf8');
for(const name of ['Предыдущая','Следующая']) assert.match(html,new RegExp(`aria-label="${name} страница каталога"`));
console.log('Wiki modal integration and catalogue action names PASS');
