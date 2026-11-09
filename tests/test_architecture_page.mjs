/** State and proof rendering stay pinned under out-of-order browser requests. */
import assert from 'node:assert/strict';
import {ArchitecturePage} from '../web/architecture-page.js';
import {renderProofMap} from '../web/architecture-proof.js';

class Element {
  constructor(tag,document) {this.tagName=tag.toUpperCase();this.ownerDocument=document;this.children=[];this.listeners={};this.dataset={};this.value='';this.attrs={};this.isConnected=true;}
  append(...nodes) {for(const node of nodes){node.parentElement=this;this.children.push(node);if(this.tagName==='SELECT' && !this.value)this.value=node.value;}}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  addEventListener(event,fn){this.listeners[event]=fn;}
  setAttribute(key,value){this.attrs[key]=value;}
  querySelectorAll(selector){const found=[];for(const node of this.children){if(selector==='button' && node.tagName==='BUTTON')found.push(node);found.push(...node.querySelectorAll(selector));}return found;}
  closest(){return null;}
  getClientRects(){return [{}];}
  focus(){this.ownerDocument.activeElement=this;}
  showModal(){this.open=true;}
  close(){this.open=false;}
  set innerHTML(_){throw Error('Unsafe markup');}
}
const elements=new Map();
const document={createElement:tag=>new Element(tag,document),createElementNS:(_,tag)=>new Element(tag,document),
  getElementById:id=>{if(!elements.has(id))elements.set(id,new Element(id.endsWith('project')?'select':'div',document));return elements.get(id);},
  querySelectorAll:()=>[...elements.values()].filter(item=>item.tagName==='BUTTON')};
document.body=new Element('body',document);document.activeElement=document.body;
const $=id=>document.getElementById('architecture-'+id);
const dialog=$('source');document.body.append(new Element('main',document),dialog);dialog.append($('source-close'));
const storage=new Map(); storage.setItem=(k,v)=>storage.set(k,v);storage.getItem=k=>storage.get(k);
const pending=[];
const request=(url,options={})=>new Promise((resolve,reject)=>pending.push({url,options,resolve,reject}));
const page=new ArchitecturePage(document,{request,storage});
const loading=page.loadProjects();pending.shift().resolve({projects:[{id:'a',name:'A',snapshotId:'s'},{id:'b',name:'B',snapshotId:'s2'}]});await loading;
assert.equal(page.project.id,'a');
const run=page.execute();
assert.equal(JSON.parse(pending[0].options.body).snapshotId,'s');
const old=pending.shift();$('project').value='b';page.selectProject();
assert.equal(old.options.signal.aborted,true);old.resolve({runId:'old',status:'violated'});await run;
assert.equal(page.run,null);assert.equal(page.project.id,'b');
assert.equal($('run').disabled,false);
const newRun=page.execute();pending.shift().resolve({runId:'new',snapshotId:'s2',status:'unknown',counts:{passed:0,violated:0,unknown:1},sourceFingerprint:'hash',rulesFingerprint:'rules',items:[{ruleId:'r',status:'unknown',reasons:['parse-error'],selectedFiles:1,findingCount:0,coverage:'unknown'}],nextOffset:null,total:1});
await new Promise(resolve=>setTimeout(resolve,0));pending.shift().resolve({items:[],nextOffset:null,total:0});await newRun;
assert.ok($('summary').textContent.includes('Недостаточно данных'));
assert.ok($('results').children[0].children.some(node=>node.textContent==='parse-error'));
assert.ok($('findings').children[0].textContent.includes('unknown'));
const proofLoad=page.openProof('f',0);const edge={source:'<script>a</script>',target:'b.py',relation:'python-import',confidence:'declared',file:'a.py',line:1};
pending.shift().resolve({findingId:'f',kind:'cycles',items:[edge],offset:0,total:2,nextOffset:1});await proofLoad;
assert.ok($('proof-status').textContent.includes('часть пути'));
assert.equal($('more-edges').hidden,false);
assert.equal($('map').children[0].attrs['aria-hidden'],'true');
const opener=$('edges').children[0].children[1];opener.focus();
const source=page.openSource(0,opener);pending.shift().resolve({file:'a.py',line:1,startLine:1,snapshotId:'s2',sourceByteHash:'h',text:'<script>literal</script>',truncated:false});await source;
assert.equal(dialog.open,true);assert.equal($('source-text').textContent,'<script>literal</script>');
page.closeSource();assert.equal(document.activeElement,opener);
const staleSource=page.openSource(0,opener);const oldSource=pending.shift();$('project').value='a';page.selectProject();oldSource.resolve({text:'wrong'});await staleSource;
assert.equal(dialog.open,false);assert.equal(page.run,null);
const retry=page.execute();pending.shift().reject(Object.assign(Error('Карта обновилась'),{status:409}));await retry;
assert.equal($('status').textContent,'Карта обновилась');assert.equal($('run').disabled,false);
renderProofMap($('map'),[edge]);assert.equal($('map').children[0].children[2].textContent,edge.source);
page.storage={getItem(){throw Error('disabled');},setItem(){throw Error('quota');}};
$('rules').value='edited A draft';page.saveRules();
const refreshed=page.loadProjects();pending.shift().resolve({projects:[{id:'a',name:'A',snapshotId:'s3'},{id:'b',name:'B',snapshotId:'s4'}]});await refreshed;
assert.equal($('rules').value,'edited A draft');assert.equal(page.project.snapshotId,'s3');
$('project').value='b';page.selectProject();$('rules').value='edited B draft';page.saveRules();
$('project').value='a';page.selectProject();assert.equal($('rules').value,'edited A draft');
$('project').value='b';page.selectProject();assert.equal($('rules').value,'edited B draft');
page.dispose();console.log('Architecture browser state PASS');
