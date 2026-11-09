/** The status owns only its small presentation, with no focus or content mutation. */
import assert from 'node:assert/strict';
import {mountLoadingStatus} from '../web/loading-status.js';
import {beginLoading} from '../web/loading-state.js';
class Node extends EventTarget {
 constructor(tag) {super();this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.hidden=false;this.textContent='';}
 append(...nodes) {for(const node of nodes){this.children.push(node);node.parent=this;}}
 remove() {if(this.parent)this.parent.children=this.parent.children.filter(node=>node!==this);}
 setAttribute(name,value) {this.attributes[name]=String(value);}
}
const view=new EventTarget(); view.setTimeout=setTimeout;view.clearTimeout=clearTimeout;
const document=new EventTarget();
Object.assign(document,{head:new Node('head'),body:new Node('body'),documentElement:{lang:'ru'},defaultView:view,createElement:tag=>new Node(tag),activeElement:{id:'existing-reader'}});
const oldContent=new Node('article'); document.body.append(oldContent);
const mounted=mountLoadingStatus(document,{delayMs:5});
assert.equal(mountLoadingStatus(document),mounted);
const root=document.body.children.at(-1),[progress,text,pause]=root.children;
const quick=beginLoading('fast');quick(); await new Promise(resolve=>setTimeout(resolve,10));
assert.equal(root.hidden,true);
const slow=beginLoading({ru:'Читаем исходник…',en:'Loading source…'});
await new Promise(resolve=>setTimeout(resolve,10));
assert.equal(root.hidden,false);assert.equal(progress.attributes.value,undefined);
assert.equal(text.attributes.role,'status');assert.equal(text.textContent,'Читаем исходник…');
pause.dispatchEvent(new Event('click'));assert.equal(root.dataset.motion,'paused');assert.equal(pause.attributes['aria-pressed'],'true');
const parallel=beginLoading('Второй запрос');assert.match(text.textContent,/\+1/);
parallel(); assert.equal(root.hidden,false);
document.documentElement.lang='en';document.dispatchEvent(new Event('atlas-locale-change'));
assert.equal(text.textContent,'Loading source…');
slow();assert.equal(root.hidden,true);
assert.equal(document.activeElement.id,'existing-reader');assert.equal(document.body.children[0],oldContent);
const pending=beginLoading('pending');mounted.dispose();pending();
assert.deepEqual(document.body.children,[oldContent]);assert.equal(document.head.children.length,0);
console.log('Loading presentation PASS: no flash, concurrency, locale, focus/content preservation, disposal');
