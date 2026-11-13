/** Actual overview controller: literal evidence, task callbacks, races, same-key retention and disposal. */
import assert from 'node:assert/strict';
import {createProjectOverview,validateOverview,projectScopeLabel,OVERVIEW_LOCALE} from '../web/project-overview.js';

class Element {
  constructor(tag,owner){this.tagName=tag.toUpperCase();this.ownerDocument=owner;this.children=[];this.dataset={};this.listeners={};this.attributes={};this.classList={toggle(){}};this.open=false;}
  append(...nodes){this.children.push(...nodes);}
  prepend(...nodes){this.children.unshift(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(key,value){this.attributes[key]=value;}
  addEventListener(key,callback){this.listeners[key]=callback;}
  removeEventListener(key){delete this.listeners[key];}
  focus(){this.ownerDocument.activeElement=this;}
  contains(node){return this===node||this.children.some(child=>child.contains?.(node));}
  querySelectorAll(selector){const all=[];for(const child of this.children){if(selector==='[data-overview-action]'&&child.dataset.overviewAction)all.push(child);all.push(...child.querySelectorAll(selector));}return all;}
  set innerHTML(_){throw Error('Unsafe HTML');}
}
const owner={activeElement:null,createElement(tag){return new Element(tag,this);}};globalThis.document=owner;
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function dto(id='a',sid=id+'-snapshot'){
  return {apiVersion:1,projectId:id,snapshotId:sid,state:sid?'ready':'build_failed_no_snapshot',snapshot:{available:Boolean(sid)},
    project:{id,name:id},purpose:{text:'<script>do not execute</script>',status:'captured',provenance:[{path:'README.md'}]},
    areas:[{id:'src',title:'Source',path:'src',nodeIds:['a-node'],role:'source',nodeCount:10,provenance:[{path:'src/main.py'}]}],
    suggestedStarts:[],build:{error:null},limitations:[]};
}
function harness(){
  const root=new Element('section',owner),details=new Element('details',owner),summary=new Element('summary',owner),body=new Element('div',owner),notice=new Element('p',owner);
  const tasks=new Element('div',owner),purpose=new Element('p',owner),issues=new Element('details',owner),issueSummary=new Element('summary',owner),issueList=new Element('div',owner);
  details.append(summary,body);issues.append(issueSummary,issueList);root.append(purpose,tasks,notice,details,issues);
  details.querySelector=()=>summary;issues.querySelector=()=>issueSummary;
  const fields={'[data-overview-details]':details,'[data-overview-body]':body,'[data-overview-status]':notice,'[data-overview-tasks]':tasks,'[data-overview-purpose]':purpose,'[data-overview-issues]':issues,'[data-overview-issue-list]':issueList};
  root.querySelector=selector=>fields[selector];
  const requests=[],actions=[];
  const controller=createProjectOverview(root,{request:(url,options)=>{const pending={...deferred(),url,...options};requests.push(pending);return pending.promise;},onAction:(...args)=>actions.push(args)});
  return {root,details,body,tasks,notice,issues,issueSummary,issueList,purpose,controller,requests,actions};
}
const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
const buttons=h=>h.root.querySelectorAll('[data-overview-action]');
assert.deepEqual(OVERVIEW_LOCALE.availableLocales,['ru']);
assert.throws(()=>validateOverview(dto('b'),{projectId:'a',snapshotId:'a-snapshot'}));
assert.throws(()=>validateOverview(dto('a','other'),{projectId:'a',snapshotId:'a-snapshot'}));
assert.match(projectScopeLabel({project:{id:'a',name:'Alpha'},mode:'system'}),/Alpha › Архитектура › нет доступного снимка/);
assert.match(projectScopeLabel({project:{id:'a'},snapshotId:'123456789',mode:'architecture',file:'src/main.py'}),/src\/main.py › снимок 12345678/);
{
  const h=harness();h.controller.sync({project:{id:'a'},snapshotId:'a-snapshot'});
  assert.equal(h.requests.length,1);assert.match(h.notice.textContent,/Читаем обзор/);
  h.requests[0].resolve(dto());await flush();
  assert.equal(buttons(h).length,4);assert.equal(h.tasks.children.length,3,'three task starts are outside the closed evidence/details');
  assert.equal(h.details.open,false,'evidence is progressively disclosed, not a default scrolling wall');
  assert.equal(h.root.hidden,true,'map opens without the overview panel');
  h.controller.setOpen(true);
  assert.equal(h.root.hidden,false);
  assert.equal(h.details.open,false,'overview and source disclosure have separate states');
  h.controller.setOpen(false);
  assert.equal(h.root.hidden,true);
  assert(h.body.children.some(section=>section.children.some(child=>child.textContent==='<script>do not execute</script>')));
  for(const id of ['understand','entrypoints','impact'])buttons(h).find(node=>node.dataset.overviewAction===id).listeners.click();
  assert.deepEqual(h.actions.map(x=>x[0]),['understand','entrypoints','impact']);
  assert(h.actions.every(x=>x[2].snapshotId==='a-snapshot'));
  h.controller.sync({project:{id:'a'},snapshotId:'a-snapshot'});assert.equal(h.requests.length,1,'same render does not issue another read');
  h.controller.setOpen(false);const refresh=h.controller.refresh();assert.equal(h.controller.isOpen(),false);
  assert(buttons(h).length,'same-key refresh preserves useful actions');
  h.requests[1].reject(Error('private backend detail'));await refresh;
  assert.match(h.notice.textContent,/Карта и её текущий контекст сохранены/);assert(!h.notice.textContent.includes('private'));
  h.controller.dispose();assert.equal(h.requests[1].signal.aborted,true);
}
{
  const h=harness();h.controller.sync({project:{id:'a'},snapshotId:'a-snapshot'});
  h.controller.sync({project:{id:'b'},snapshotId:'b-snapshot'});assert.equal(h.requests[0].signal.aborted,true);
  h.requests[1].resolve(dto('b'));await flush();h.requests[0].resolve(dto());await flush();
  buttons(h).find(node=>node.dataset.overviewAction==='understand').listeners.click();assert.equal(h.actions[0][2].projectId,'b','ignored abort cannot publish another project');
  h.controller.sync({project:{id:'c'},snapshotId:'c-snapshot'});assert.equal(buttons(h).length,0,'cross-key request clears foreign overview actions');
  h.controller.dispose();h.requests[2].resolve(dto('c'));await flush();assert.equal(buttons(h).length,0,'disposed late result cannot render');
}
{
  const h=harness();h.controller.sync({project:{id:'a',status:'failed'},snapshotId:null});h.requests[0].resolve(dto('a',null));await flush();
  assert.equal(h.notice.hidden,true,'map state has one persistent host notice, not repeated overview warnings');
  assert(buttons(h).filter(node=>['understand','entrypoints','impact'].includes(node.dataset.overviewAction)).every(node=>node.disabled));
  assert.equal(h.details.open,false);assert.equal(h.actions.length,0,'no automatic rebuild from failed overview');
  h.controller.dispose();
}
console.log('Project overview literal evidence, source task actions, snapshot guards, read races, retention and disposal PASS');

/** Usable partial maps keep task starts while visible file failures expose source-backed alternatives. */
{
  const h=harness(),value=dto();value.analysisStatus='partial';value.diagnostics={version:1,analysisStatus:'partial',errorCount:2,failedFileCount:2,processedFileCount:8,sourceFileCount:10,truncated:false,
    issues:[{code:'SOURCE_PARSE_FAILED',path:'broken.ts',message:'<img onerror=fail> parse error',nodeId:'error:1',severity:'error'},
      {code:'SOURCE_DATA_ALTERNATIVE',path:'report.json',message:'JSON data belongs to the data layer',nodeId:'error:2',severity:'error',alternative:{kind:'data-map',file:'report.json',status:'not-built'}}]};
  h.controller.sync({project:{id:'a'},snapshotId:'a-snapshot'});h.requests[0].resolve(value);await flush();
  assert.equal(h.notice.hidden,true,'partial summary belongs to the one host notice');assert.equal(h.issues.hidden,false);assert.equal(h.issues.open,false);assert.equal(h.issueSummary.textContent,'Проблемы (2)');
  assert(buttons(h).filter(node=>['understand','entrypoints','impact'].includes(node.dataset.overviewAction)).every(node=>!node.disabled),'valid nodes remain usable on partial maps');
  const all=node=>[node,...node.children.flatMap(all)],nodes=all(h.root);
  assert(nodes.some(node=>node.textContent==='<img onerror=fail> parse error'),'parser message is literal');
  const link=nodes.find(node=>node.tagName==='A'&&node.href.startsWith('/data.html'));
  const target=new URL(link.href,'http://127.0.0.1');assert.equal(target.searchParams.get('project'),'a');assert.equal(target.searchParams.get('file'),'report.json');
  assert(nodes.some(node=>node.textContent?.includes('ещё не построена')));h.controller.dispose();
}

{
  const h=harness();h.controller.sync({project:{id:'a'},snapshotId:'a-snapshot'});const stale=new Error('changed');stale.status=409;h.requests[0].reject(stale);await flush();
  const reload=buttons(h).find(node=>node.dataset.overviewAction==='reload');assert(reload);reload.listeners.click();
  assert.deepEqual(h.actions[0],['reload',null,{projectId:'a',snapshotId:'a-snapshot'}]);h.controller.dispose();
}
assert.throws(()=>validateOverview(dto(),{projectId:'a',snapshotId:null}),'a formerly unbuilt registration cannot silently borrow a new snapshot');

{
  const h=harness();
  h.controller.sync({project:{id:'a'},snapshotId:'a-snapshot'});
  h.controller.sync({project:{id:'b'},snapshotId:'b-snapshot',lazy:true});
  assert.equal(h.requests[0].signal.aborted,true);
  assert.equal(h.requests.length,1,'compact map defers the closed narrative overview');
  h.controller.setOpen(true);
  assert.equal(h.requests.length,2,'cancelled older request does not block opening');
  h.requests[1].resolve(dto('b'));await flush();
  assert.equal(h.root.hidden,false);
  h.controller.dispose();
}
