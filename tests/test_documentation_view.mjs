/** Документация отправляет только keywords, отбрасывает stale ответы и не интерпретирует HTML. */
import assert from 'node:assert/strict';
import {DocumentationViewer,documentationQuery,safeDocumentationUrl} from '../web/documentation-view.js';
assert.equal(documentationQuery('PortfolioImportService'),'Portfolio Import Service');
assert.ok(documentationQuery('x'.repeat(600)).length<=500);
for(const url of ['javascript:alert(1)','data:text/html,abc','file:///etc/passwd','https://u:p@host/path'])assert.equal(safeDocumentationUrl(url),null);
assert.equal(safeDocumentationUrl('https://docs.example/ref'),'https://docs.example/ref');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.value='';}
 get firstChild(){return this.children[0];}
 addEventListener(name,callback){this.listeners[name]=callback;}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 set innerHTML(value){throw new Error('Unsafe HTML');}
}
globalThis.document={createElement:tag=>new Element(tag)};
const fields=new Map(['url','discover','tool','query-argument','arguments','save','disconnect','auto','query','search','status','results','config-status'].map(name=>[name,new Element('div')]));
const root={querySelector:selector=>fields.get(selector.match(/"(.*?)"/)[1])};
const field=name=>fields.get(name);
let scope={projectId:'p',snapshotId:'s'};const pending=[];
const viewer=new DocumentationViewer(root,{getScope:()=>scope,fetcher:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve}))});
assert.equal(field('auto').checked,false);
viewer.version='v';viewer.select({id:'rawA',label:'PortfolioService',source:'RAW_SECRET_CODE'});assert.equal(pending.length,0);
const first=viewer.search();assert.deepEqual(JSON.parse(pending[0].options.body),{query:'Portfolio Service',configVersion:'v'});
assert.ok(!pending[0].options.body.includes('RAW_SECRET_CODE'));
viewer.select({id:'syntheticB',label:'KafkaConsumer'});assert.ok(pending[0].options.signal.aborted);
const second=viewer.search();pending[1].resolve({ok:true,json:async()=>({configVersion:'v',documents:[{title:'<script>attack()</script>',text:'<img onerror=attack()>',url:'javascript:attack()'},{title:'Safe',text:'plain',url:'https://docs.example/ref'}]})});await second;
pending[0].resolve({ok:true,json:async()=>({configVersion:'v',documents:[{title:'STALE',text:'old'}]})});await first;
assert.ok(field('results').children.some(node=>node.textContent==='<script>attack()</script>'));
assert.ok(field('results').children.some(node=>node.textContent==='<img onerror=attack()>'));
assert.ok(!field('results').children.some(node=>node.textContent==='STALE'));
assert.equal(field('results').children.filter(node=>node.tag==='a').length,1);
assert.equal(field('results').children.find(node=>node.tag==='a').rel,'noopener noreferrer');
const mismatch=viewer.search();pending[2].resolve({ok:true,json:async()=>({configVersion:'other',documents:[{text:'WRONG_CONFIG'}]})});await mismatch;
assert.equal(field('results').children.length,0);assert.ok(field('status').textContent.includes('изменилось'));
const editing=viewer.search();field('query').value='new query';field('query').listeners.input();pending[3].resolve({ok:true,json:async()=>({configVersion:'v',documents:[{text:'STALE_QUERY'}]})});await editing;assert.equal(field('results').children.length,0);
const switching=viewer.search();scope={...scope,snapshotId:'new'};viewer.sync();pending[4].resolve({ok:true,json:async()=>({configVersion:'v',documents:[{text:'STALE_SCOPE'}]})});await switching;assert.equal(field('results').children.length,0);
const discovery=viewer.discover();pending[5].resolve({ok:true,json:async()=>({tools:[{name:'search_docs',description:'Search',warning:'unconfirmed'}],truncated:true})});await discovery;
assert.equal(field('tool').value,'');assert.ok(field('config-status').textContent.includes('ограничен'));
const callsBefore=pending.length;await viewer.save();assert.equal(pending.length,callsBefore);
viewer.version='old';const load=viewer.loadConfig();pending[6].resolve({ok:true,json:async()=>({configured:false,config:null,configVersion:'none'})});await load;assert.equal(viewer.version,null);assert.equal(field('url').value,'');
viewer.version='v';field('auto').checked=true;viewer.select({id:'automatic',label:'AutoNode'});
await new Promise(resolve=>setTimeout(resolve,400));assert.equal(pending.length,8);assert.ok(pending[7].url.endsWith('/search'));
const disconnect=viewer.disconnect();assert.ok(pending[7].options.signal.aborted);assert.equal(field('auto').checked,false);
pending[8].resolve({ok:true,json:async()=>({configured:false})});await disconnect;
pending[7].resolve({ok:true,json:async()=>({configVersion:'v',documents:[{text:'DISCONNECTED_OLD'}]})});await new Promise(resolve=>setImmediate(resolve));
assert.equal(viewer.version,null);assert.equal(field('results').children.length,0);
console.log('Documentation keywords, opt-in auto, safe rendering and stale context PASS');
