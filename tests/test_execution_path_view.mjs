/** Дерево отличает рекурсию от повторного пути; viewer отклоняет устаревшие ответы. */
import assert from 'node:assert/strict';
import {buildExecutionTree,ExecutionPathViewer} from '../web/execution-path.js';
import {PATH_NODE_LIMIT} from '../web/execution-path-limits.js';
import {readFileSync} from 'node:fs';
const page=readFileSync(new URL('../web/index.html',import.meta.url),'utf8');
assert.ok(page.includes(`data-path="nodes" type="number" min="1" max="${PATH_NODE_LIMIT}" value="${PATH_NODE_LIMIT}"`));
const data={rootNodeId:'A',snapshotId:'s',steps:['A','B','C','D'].map(nodeId=>({nodeId,label:nodeId,file:`${nodeId}.cs`,line:1,endLine:4,events:[],sourceStatus:'parsed'})),edges:[
 {source:'A',target:'B',relation:'calls',sourceLine:1}, {source:'A',target:'C',relation:'calls',sourceLine:2},
 {source:'B',target:'D',relation:'calls'}, {source:'C',target:'D',relation:'calls'}, {source:'D',target:'B',relation:'calls'}
],cycles:[{source:'D',target:'B'}],truncation:{depth:true},warning:'STATIC ONLY'};
const before=JSON.stringify(data),tree=buildExecutionTree(data);
const flat=[];const visit=node=>{flat.push(node);for(const child of node.children)visit(child);};tree.roots.forEach(visit);
assert.ok(flat.some(item=>item.type==='cycle'&&item.step.nodeId==='B'));
assert.ok(flat.some(item=>item.type==='reference'&&item.step.nodeId==='D'));
assert.equal(flat.filter(item=>item.type==='step').length,4);
assert.equal(buildExecutionTree(data,{maxRows:2}).rows,2);assert.equal(buildExecutionTree(data,{maxRows:2}).limited,true);
assert.equal(JSON.stringify(data),before);
const many={rootNodeId:'node0',steps:Array.from({length:900},(_,i)=>({nodeId:`node${i}`})),edges:[]};
assert.equal(buildExecutionTree(many).rows,600,'The HTML tree stays bounded independently of the analysed node budget');
assert.equal(buildExecutionTree(many).limited,true);
/** DOM-фасад запрещает HTML и позволяет раскрывать ленивые details вручную. */
class Element{
 constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.open=false;}
 addEventListener(event,callback){this.listeners[event]=callback;}
 append(...nodes){for(const node of nodes){node.parentElement=this;this.children.push(node);}}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);}
 scrollIntoView(){this.scrolled=true;}
 setAttribute(name,value){this[name]=value;}
 set innerHTML(value){throw new Error('Unsafe HTML');}
}
globalThis.document={createElement:tag=>new Element(tag)};
const elements=new Map(['title','status','tree','close','retry','build','reset','depth','nodes','board','entry'].map(name=>[name,new Element('div')]));
const root={hidden:true,querySelector:selector=>elements.get(selector.match(/"(.*?)"/)[1])};
let context={projectId:'p',snapshotId:'s',nodeId:'A'};const pending=[],sources=[];
const viewer=new ExecutionPathViewer(root,()=>context,{fetcher:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve})),onSource:id=>sources.push(id)});
const first=viewer.open();assert.equal(JSON.parse(pending[0].options.body).nodeId,'A');
assert.equal(JSON.parse(pending[0].options.body).snapshotId,'s');
assert.equal(JSON.parse(pending[0].options.body).maxNodes,2000);
assert.equal(JSON.parse(pending[0].options.body).maxDepth,30);
context={...context,nodeId:'B'};viewer.sync();assert.equal(root.hidden,true);assert.ok(pending[0].options.signal.aborted);
const second=viewer.open();
const selectedData={...data,rootNodeId:'B',steps:[{nodeId:'B',label:'B',kind:'method',file:'B.cs',events:[{kind:'comment',text:'<script>alert(1)</script>',line:2,confidence:'source'}]}],edges:[]};
pending[1].resolve({ok:true,json:async()=>selectedData});await second;
pending[0].resolve({ok:true,json:async()=>data});await first;
const details=elements.get('tree').children[0];assert.equal(details.children[0].textContent.startsWith('B ·'),true);
const body=details.children[1];body.children.find(child=>child.textContent==='Исходник метода').listeners.click();assert.deepEqual(sources,['B']);
const event=body.children.find(child=>child.className==='path-event');assert.equal(event.children[1].textContent,'<script>alert(1)</script>');
const statusText=element=>[element.textContent||'',...element.children.map(statusText)].join(' ');
assert.ok(statusText(elements.get('status')).includes('не реальная трасса выполнения')); 
assert.ok(statusText(elements.get('status')).includes('глубина'));
const mismatch=viewer.open();pending[2].resolve({ok:true,json:async()=>({...selectedData,snapshotId:'other'})});await mismatch;
assert.equal(elements.get('retry').hidden,false);assert.equal(elements.get('tree').children.length,0);
const closed=viewer.open();viewer.close();pending[3].resolve({ok:true,json:async()=>selectedData});await closed;
assert.equal(root.hidden,true);assert.equal(elements.get('tree').children.length,0);
// Длинные тела методов не вставляются в DOM до раскрытия конкретного шага.
viewer.render({...data,steps:data.steps.map(step=>({...step,events:Array.from({length:150},(_,i)=>({kind:'log',text:`log${i}`,line:i+1}))}))});
const rootDetail=elements.get('tree').children[0],childDetail=rootDetail.children[2].children[0];
assert.equal(childDetail.children[1].children.length,0);childDetail.open=true;childDetail.listeners.toggle();
assert.equal(childDetail.children[1].children.filter(child=>child.className==='path-event').length,100);
assert.ok(childDetail.children[1].children.some(child=>child.textContent==='Показаны первые 100 событий метода.'));
// Обзор логов считает все события, ограничивает DOM и открывает реальный метод.
const observable=elements.get('status').children.find(child=>child.className==='path-observables');
assert.equal(observable.open,true);
assert.ok(observable.children[0].textContent.includes('Логи (600)'));
const observableList=observable.children.find(child=>child.className==='path-observable-list');
assert.equal(observableList.children.length,100);
observableList.children[0].children.find(child=>child.textContent==='Метод в исходнике').listeners.click();
assert.equal(sources.at(-1),'A');
// Пользовательские пределы ограничены API-диапазоном; reset действительно перестраивает.
elements.get('depth').value=999;elements.get('nodes').value=-3;
const bounded=viewer.open();
assert.equal(JSON.parse(pending[4].options.body).maxDepth,30);
assert.equal(JSON.parse(pending[4].options.body).maxNodes,1);
pending[4].resolve({ok:true,json:async()=>selectedData});await bounded;
elements.get('reset').listeners.click();
assert.equal(JSON.parse(pending[5].options.body).maxDepth,30);
assert.equal(JSON.parse(pending[5].options.body).maxNodes,2000);
pending[5].resolve({ok:true,json:async()=>selectedData});await new Promise(resolve=>setImmediate(resolve));
viewer.render({rootNodeId:'B',steps:[{nodeId:'B',label:'Calculate',file:'B.cs',line:10,controlFlow:{branches:[{kind:'if',line:12,condition:'amount > 0',arms:[{id:'a',kind:'true',label:'true'},{id:'b',kind:'false',label:'false'}]},{kind:'switch',line:20,selector:'kind',arms:[{id:'c',kind:'case',label:'case Total'}]}]}}],edges:[],truncation:{}});
const boardText=statusText(elements.get('board'));
assert.ok(boardText.includes('if'));
assert.ok(boardText.includes('истина'));
assert.ok(boardText.includes('иначе'));
assert.ok(boardText.includes('switch'));
assert.ok(boardText.includes('case Total'));
console.log('Execution tree cycles, source IDs, escaping, limits and stale viewer PASS');
