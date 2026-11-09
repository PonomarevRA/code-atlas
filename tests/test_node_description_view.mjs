/** Описание независимо от пути, экранирует документы и привязано к снимку/узлу. */
import assert from 'node:assert/strict';
import {NodeDescriptionViewer} from '../web/node-description.js';
import {branchLabels,renderControlFlow,renderDocumentation} from '../web/path-annotations.js';
class Element {
 constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};}
 addEventListener(name,callback){this.listeners[name]=callback;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 setAttribute(name,value){this[name]=value;}
 set innerHTML(value){throw new Error('Unsafe HTML');}
}
globalThis.document={createElement:tag=>new Element(tag)};
const text=node=>[node.textContent||'',...node.children.map(text)].join(' ');
const flow={branches:[{id:'if',kind:'if',condition:'x < 0',line:2,arms:[{id:'yes',kind:'true',line:3,endLine:4},{id:'no',kind:'false',line:5,endLine:6}]},{id:'switch',kind:'switch',selector:'value',line:8,parentArmIds:['no'],arms:[{id:'case',kind:'case',label:'case > 1',condition:'Allowed()',line:9,endLine:10}]}],terminals:[{kind:'return',line:3,text:'return x;',branchIds:['yes']}],unsupported:['loop'],truncated:true};
const labels=branchLabels(flow,['no','case','no']);
assert.equal(labels.length,2);assert.ok(labels[0].includes('x < 0')&&labels[0].includes('else'));
assert.ok(labels[1].includes('case > 1')&&labels[1].includes('Allowed()'));
assert.ok(branchLabels(flow,['missing'])[0].includes('не сопоставлено'));
const rendered=renderControlFlow(flow);
assert.ok(text(rendered).includes('Внутри: if (x < 0) — ложь / else'));
assert.ok(text(rendered).includes('Не разобрано: loop'));
assert.ok(text(rendered).includes('показаны не все'));
const docs={summary:'<img onerror=attack()>',params:[{name:'x',text:'<script>bad()</script>'}],returns:'result',comments:[],truncated:false};
assert.ok(text(renderDocumentation(docs)).includes(docs.summary));
assert.ok(text(renderDocumentation(docs)).includes(docs.params[0].text));
const elements=new Map(['close','retry','title','status','body'].map(name=>[name,new Element('div')]));
const root={hidden:true,querySelector:selector=>elements.get(selector.match(/"(.*?)"/)[1])};
let context={projectId:'p',snapshotId:'s',nodeId:'A'};
const requests=[],sourceIds=[];
const viewer=new NodeDescriptionViewer(root,()=>context,{fetcher:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve})),onSource:id=>sourceIds.push(id)});
const response={nodeId:'B',snapshotId:'s',label:'B',file:'B.cs',signature:'void B()',documentation:docs,controlFlow:flow,typeContext:{name:'Class',documentation:{summary:'CLASS_DOC'}}};
const old=viewer.open();
assert.equal(requests[0].url,'/api/projects/p/node-description');
assert.deepEqual(JSON.parse(requests[0].options.body),{nodeId:'A',snapshotId:'s'});
context={...context,nodeId:'B'};viewer.sync();assert.ok(requests[0].options.signal.aborted);
const current=viewer.open();requests[1].resolve({ok:true,json:async()=>response});await current;
requests[0].resolve({ok:true,json:async()=>({...response,nodeId:'A'})});await old;
assert.ok(text(elements.get('body')).includes('void B()'));
assert.ok(!text(elements.get('body')).includes('void A()'));
assert.ok(text(elements.get('status')).includes('не сгенерированное'));
assert.ok(text(elements.get('body')).includes('Документация содержащего типа'));
elements.get('body').children.find(node=>node.textContent==='Исходник символа').listeners.click();
assert.deepEqual(sourceIds,['B']);
const stale=viewer.open();requests[2].resolve({ok:true,json:async()=>({...response,snapshotId:'other'})});await stale;
assert.equal(elements.get('retry').hidden,false);assert.equal(elements.get('body').children.length,0);
const closed=viewer.open();viewer.close();requests[3].resolve({ok:true,json:async()=>response});await closed;
assert.equal(root.hidden,true);assert.equal(elements.get('body').children.length,0);
// Связи в ответе могут быть ограничены backend, но UI показывает исходный total.
viewer.render({...response,factualSummary:'FACT_SUMMARY',counts:{incoming:0,outgoing:205},calls:{incoming:[],outgoing:Array.from({length:200},(_,i)=>({source:'B',target:String(i),relation:'calls'}))},truncation:{outgoing:true},analyzerVersion:'test-version'});
assert.ok(text(elements.get('body')).includes('205'));
assert.ok(text(elements.get('body')).includes('FACT_SUMMARY'));
assert.ok(text(elements.get('body')).includes('test-version'));
assert.ok(text(elements.get('body')).includes('50'));
console.log('Node description isolation, branch labels, safe documentation and stale requests PASS');
viewer.render({...response,infrastructureTotal:2,infrastructure:[{id:'infra',kind:'kafka',resourceKind:'topic',operation:'publishes',status:'configured',name:'<script>topic</script>',file:'B.cs',line:9,provider:'test',basis:'fixture',evidence:[]}]});
assert.ok(text(elements.get('body')).includes('Ресурсы метода · 1 из 2'));
assert.ok(text(elements.get('body')).includes('<script>topic</script>'));
const resources=elements.get('body').children.find(n=>text(n).includes('Ресурсы метода'));
resources.children[2].children.find(n=>n.textContent==='Место обращения · строка 9').listeners.click();
assert.equal(sourceIds.at(-1),'B');
console.log('Selected method resources, safe names and source navigation PASS');
