/** Contract tests cover read-only navigation, stale replies, safe text and scoped content. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/** Minimal DOM retains strings and listeners without interpreting document markup. */
class Control {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.value = ''; this.hidden = false; this.listeners = {}; this.textContent = ''; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll() { return []; }
  focus() { this.focused = true; }
}
const controls = new Map();
globalThis.document = {
  getElementById(id) { if (!controls.has(id)) controls.set(id, new Control()); return controls.get(id); },
  createElement(tag) { return new Control(tag); }, addEventListener() {}
};
globalThis.location = new URL('http://localhost/documents.html?map=m&snapshot=s');
globalThis.history = { pushState(state, title, url) { globalThis.location = new URL(url); }, replaceState(state, title, url) { globalThis.location = new URL(url); } };
globalThis.window = { addEventListener() {} };
const calls = [];
let releaseOld, releaseView;
const manifest = { mapId: 'm', snapshotId: 's', title: 'Fixture documents', projectId: 'p', coverage: {status:'complete'}, freshness:{status:'current'}, documents:[{documentId:'d',coverage:{status:'complete'},warnings:[]}] };
const root = {id:'r',kind:'document',parentId:null,label:'a.md',documentId:'d',sourcePath:'a.md'};
const selected = {id:'n',kind:'section',parentId:'r',label:'<img src=x onerror=alert(1)>',text:'<script>never execute</script>',documentId:'d',sourcePath:'a.md',sourceLocator:{kind:'text',lineStart:1},attributes:{}};
globalThis.fetch = async (path, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : undefined; calls.push({path,body});
  let value;
  if (path === '/api/document-capabilities') value = {providers:[]};
  else if (path.startsWith('/api/document-maps?')) value = path.includes('cursor=') ? {maps:[{id:'more',title:'More'}],nextCursor:null} : {maps:[{id:'m',title:'Fixture',status:'ready',snapshotId:'s'}],nextCursor:'next-maps'};
  else if (path === '/api/document-files') value = {files:[{path:body.cursor ? 'b.md' : 'a.md',sizeBytes:10}],nextCursor:body.cursor ? null : 'next-files',total:2};
  else if (path === '/api/document-maps/m/views/slow') return new Promise(resolve=>{releaseView=()=>resolve({ok:true,json:async()=>({snapshotId:'s',title:'Stale saved view',nodes:[root],edges:[]})});});
  else if (path === '/api/document-maps/m/views/v') value = {snapshotId:'s',title:'Saved view',nodes:[{...root,id:'persisted-root',label:'Saved root'},{...selected,id:'persisted-child',parentId:'persisted-root',label:'Saved child'}],edges:[{source:'persisted-root',target:'persisted-child',relation:'references'}]};
  else if (path.startsWith('/api/document-maps/other?')) value = {...manifest,mapId:'other',snapshotId:'other-s'};
  else if (path === '/api/document-maps/old') return new Promise(resolve => { releaseOld = () => resolve({ok:true,json:async()=>({...manifest,mapId:'old',snapshotId:'old'})}); });
  else if (path.startsWith('/api/document-maps/m?') || path === '/api/document-maps/m') value = manifest;
  else if (path.endsWith('/query/tree')) value = {items:body.parentId ? [selected] : [root],nextCursor:null};
  else if (path.endsWith('/query/node')) value = body.nodeId === 'r' ? root : selected;
  else if (path.endsWith('/query/content')) value = {text:'😀<script>',offset:0,totalCharacters:9,nextOffset:null};
  else if (path.endsWith('/query/graph')) value = {nodes:[root,selected],edges:[]};
  else if (path.endsWith('/query/search')) value = {items:[selected],nextCursor:null};
  else throw new Error('Unexpected API request: ' + path);
  return {ok:true,json:async()=>value};
};
let source = readFileSync(new URL('../web/document-workspace.js', import.meta.url),'utf8');
source = source.replace("import { DataScene } from './data-scene.js';", 'class DataScene { constructor() {} setGraph(nodes,edges) {this.graph={nodes,edges};} reset() {} }');
source += '\nexport {state,loadMap,selectNode,content,restore,mode,discover,loadFiles,catalog,scene};';
// Keep the new request feedback modules real when this DOM harness uses a data URL.
for (const file of ['loading-state.js','loading-labels.js']) source = source.replaceAll(
  `from './${file}'`, `from '${new URL('../web/'+file, import.meta.url).href}'`);
const workspace = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
assert.equal(workspace.state.snapshot,'s');
assert.equal(calls.filter(call=>call.path.includes('/ensure')).length,0);
await workspace.selectNode('n','d');
assert.equal(controls.get('document-node-title').textContent,selected.label);
assert.equal(controls.get('document-preview').textContent,selected.text);
assert.equal(controls.get('document-locator').textContent.includes('lineStart'),true);
await workspace.content();
const request = calls.findLast(call=>call.path.endsWith('/query/content'));
assert.deepEqual(request.body,{snapshotId:'s',projectId:'p',nodeId:'n',documentId:'d',offset:0,limit:4000});
assert.match(controls.get('document-content-info').textContent,/1–9/);
const pending = workspace.loadMap('old');
await workspace.loadMap('m','s');
releaseOld(); await pending;
assert.equal(workspace.state.map,'m');
assert.equal(workspace.state.snapshot,'s');
await workspace.mode('graph');
assert.equal(controls.get('graph').hidden,false);
assert.equal(calls.filter(call=>call.path.includes('/ensure')).length,0);
controls.get('document-root').value = '/fixture';
await workspace.discover({preventDefault(){}});
const firstFile = controls.get('document-files').children[0].children[0]; firstFile.checked = true;
await workspace.loadFiles(workspace.state.fileCursor);
assert.equal(controls.get('document-files').children.length,2);
assert.equal(controls.get('document-files').children[0].children[0],firstFile);
assert.equal(firstFile.checked,true);
const firstMap = controls.get('document-catalog').children[0];
await workspace.catalog(workspace.state.catalogCursor);
assert.equal(controls.get('document-catalog').children.length,2);
assert.equal(controls.get('document-catalog').children[0],firstMap);
globalThis.location = new URL('http://localhost/documents.html?map=m&view=v');
const graphRequestsBeforeRestore = calls.filter(call=>call.path.endsWith('/query/graph')).length;
await workspace.restore();
assert.equal(workspace.state.mode,'graph');
assert.equal(controls.get('document-title').textContent,'Saved view');
assert.equal(calls.filter(call=>call.path.endsWith('/query/graph')).length,graphRequestsBeforeRestore);
assert.deepEqual(workspace.scene.graph.nodes.map(node=>node.id),['persisted-root','persisted-child']);
assert.deepEqual(workspace.scene.graph.edges,[{source:'persisted-root',target:'persisted-child',relation:'references'}]);
await workspace.mode('tree'); await workspace.mode('graph');
assert.equal(calls.filter(call=>call.path.endsWith('/query/graph')).length,graphRequestsBeforeRestore);
assert.equal(location.searchParams.get('view'),'v');
await workspace.loadMap('other','other-s',null,false);
assert.equal(location.searchParams.get('map'),'other');
assert.equal(location.searchParams.has('view'),false);
assert.equal(workspace.state.savedView,null);
await workspace.restore();
assert.equal(calls.filter(call=>call.path.includes('/other/views/')).length,0);
globalThis.location = new URL('http://localhost/documents.html?map=m&view=slow');
const pendingView = workspace.restore();
await workspace.loadMap('other','other-s',null,false);
releaseView(); await pendingView;
assert.equal(workspace.state.map,'other');
assert.notEqual(controls.get('document-title').textContent,'Stale saved view');
assert.equal(calls.filter(call=>call.path.includes('/ensure')).length,0);
globalThis.location = new URL('http://localhost/documents.html');
await workspace.restore();
assert.equal(workspace.state.map,null);
assert.equal(controls.get('document-tools').hidden,true);
assert.equal(controls.get('document-node-title').textContent,'Выберите узел');
console.log('PASS: read-only navigation, scoped Unicode content, escaped document strings, stale replies, paginated selection, literal saved graphs, view URL lifecycle and Back reset');
