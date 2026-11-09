/** Проверяет пагинацию, безопасное отображение и гонки локального просмотрщика. */
import assert from 'node:assert/strict';
import { sourceWindow, SourceViewer } from '../web/source-view.js';
const text=Array.from({length:501},(_,index)=>`line${index+1}`).join('\r\n');
const context=sourceWindow(text,250);
assert.equal(context.lines.length,81);assert.equal(context.start,210);assert.equal(context.end,290);
assert.deepEqual(context.lines.filter(line=>line.highlight).map(line=>line.number),[250]);
const full=sourceWindow(text,250,{mode:'full',page:1,pageSize:1000});
assert.equal(full.lines.length,200);assert.equal(full.start,201);assert.equal(full.end,400);
assert.equal(sourceWindow(text,9999).lines.some(line=>line.highlight),false);
assert.equal(sourceWindow(text,null).start,1);
assert.equal(sourceWindow(text,1,{mode:'full',page:-9}).page,0);
assert.equal(sourceWindow(text,1,{mode:'full',page:9999}).end,501);
assert.deepEqual(sourceWindow('a\rb\nc',2).lines.map(line=>line.text),['a','b','c']);
/** Минимальный DOM запрещает innerHTML и сохраняет созданные текстовые узлы. */
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.offsetTop=0;this.clientHeight=100;}
  addEventListener(name,callback){this.listeners[name]=callback;}
  setAttribute(name,value){this[name]=value;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  querySelector(selector){return this.children.find(child=>child.className?.split(' ').includes(selector.slice(1)));}
  set innerHTML(value){throw new Error('Unsafe HTML insertion');}
}
globalThis.document={createElement:tag=>new Element(tag)};
const elements=new Map(['title','status','close','context','full','previous','next','range','code'].map(name=>[name,new Element(name)]));
const root={hidden:true,querySelector:selector=>elements.get(selector.match(/"(.*?)"/)[1])};
let selected={projectId:'p',snapshotId:'s',nodeId:'a'};
const pending=[];
const fetcher=(url,options)=>new Promise(resolve=>pending.push({resolve,url,options}));
const viewer=new SourceViewer(root,()=>selected,fetcher);
/** Завершает управляемый HTTP-запрос, затем пропускает микрозадачи async sync. */
async function reply(index,data,ok=true){pending[index].resolve({ok,status:ok?200:409,json:async()=>data});await new Promise(resolve=>setImmediate(resolve));}
viewer.open();assert.equal(pending.length,1);
selected={...selected,nodeId:'b'};const b=viewer.sync();
assert.ok(pending[0].options.signal.aborted);
await reply(1,{text:'<script>alert(1)</script>',file:'b.js',line:1,snapshotId:'s'});await b;
assert.equal(viewer.data.file,'b.js');
assert.equal(elements.get('code').children[0].children[1].textContent,'<script>alert(1)</script>');
await reply(0,{text:'old',file:'a.js',line:1,snapshotId:'s'});
assert.equal(viewer.data.file,'b.js');
selected={...selected,snapshotId:'new'};const changed=viewer.sync();
await reply(2,{text:'wrong snapshot',file:'wrong.js',snapshotId:'s'});await changed;
assert.equal(viewer.data,null);assert.ok(elements.get('status').textContent.includes('Индекс изменился'));
selected={...selected,nodeId:'c'};const closing=viewer.sync();viewer.close();
await reply(3,{text:'closed',file:'c.js',snapshotId:'new'});await closing;
assert.equal(viewer.data,null);assert.equal(root.hidden,true);
viewer.open();await reply(4,{text,file:'many.js',line:250,snapshotId:'new'});
elements.get('full').listeners.click();assert.equal(elements.get('code').children.length,200);
assert.equal(elements.get('range').textContent,'Строки 201–400 из 501');
selected={...selected,line:12};const lined=viewer.sync();
await reply(5,{text,file:'many.js',line:250,snapshotId:'new'});await lined;
assert.equal(viewer.data.line,12);
assert.equal(String(elements.get('code').querySelector('.source-line-selected').children[0].textContent),'12');
console.log('Source viewer safety and stale responses PASS');

// Нативный fetch нельзя вызывать как метод SourceViewer: receiver будет неправильным.
const originalFetch=globalThis.fetch;
let defaultFetchCalls=0;
globalThis.fetch=function(url,options){
  assert.ok(this===undefined||this===globalThis,'Native fetch received SourceViewer as receiver');
  defaultFetchCalls++;
  return Promise.resolve({ok:true,json:async()=>({text:'default transport',file:'native.js',line:1,snapshotId:'new'})});
};
try{
  const nativeViewer=new SourceViewer(root,()=>selected);
  nativeViewer.open();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(defaultFetchCalls,1);
  assert.equal(nativeViewer.data?.text,'default transport');
}finally{globalThis.fetch=originalFetch;}
console.log('Default fetch receiver PASS');
