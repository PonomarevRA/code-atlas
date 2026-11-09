/** Сохранённый SVG использует закреплённую геометрию; форма не меняет контекст карты. */
import assert from 'node:assert/strict';
import {Svg} from './svg_fixture.mjs';
import {renderSavedView} from '../web/saved-view-renderer.js';
import {SavedViewsPanel} from '../web/saved-views.js';
class Element extends Svg {
  constructor(tag='div'){super(tag);this.value='';this.disabled=false;}
  get childNodes(){return this.children;}
  set innerHTML(value){throw new Error('Unsafe HTML');}
}
globalThis.document={createElementNS:(_,tag)=>new Element(tag),createElement:tag=>new Element(tag)};
const view={id:'a'.repeat(32),title:'<script>title</script>',query:'why',nodes:[{id:'raw',label:'Raw',kind:'explanation',color:'#123456'},{id:'other',label:'Other',kind:'class'}],edges:[{source:'raw',target:'other',relation:'calls'},{source:'raw',target:'other',relation:'calls'},{source:'other',target:'raw',relation:'references'}],annotations:[{id:'note',kind:'explanation',title:'Author',text:'<img src=x onerror=alert(1)>'}],annotationEdges:[{source:'note',target:'raw'}],layout:{width:1000,height:500,nodes:{raw:{x:70,y:120,width:240,height:80,displayLines:['Pinned raw label']},other:{x:370,y:120,width:240,height:80,displayLines:['Other']}},projects:[{id:'p',label:'P',x:40,y:80,width:610,height:200,color:{fill:'#abcabc',stroke:'#123123',text:'#234234'}}],annotations:{note:{x:700,y:100,width:260,height:150,titleLines:['Pinned title'],displayLines:['<img src=x onerror=alert(1)>']}}}};
const original=JSON.stringify(view),svg=new Element('svg'),selected=[];
renderSavedView(svg,view,(node,options)=>selected.push([node.id,options.annotation]));
assert.equal(JSON.stringify(view),original);
assert.equal(svg.attributes.viewBox,'0 0 1000 500');
const groups=svg.querySelectorAll('g');
assert.equal(groups.length,3);
for(const group of groups){const id=group.attributes['data-node-id'];const expected=view.layout.nodes[id]||view.layout.annotations[id];const rect=group.querySelector('rect');for(const key of ['x','y','width','height'])assert.equal(+rect.attributes[key],expected[key]);group.listeners.click();}
assert.deepEqual(selected,[['raw',false],['other',false],['note',true]]);
const paths=svg.children.filter(item=>item.tag==='path');
assert.equal(paths.filter(item=>item.attributes['marker-end']).length,3);
assert.equal(paths.filter(item=>item.attributes['stroke-dasharray']).length,1);
assert.ok(svg.querySelectorAll('text').some(item=>item.textContent==='Pinned raw label'));
assert.ok(svg.querySelectorAll('text').some(item=>item.textContent==='<img src=x onerror=alert(1)>'));
assert.equal(svg.children.find(item=>item.tag==='rect').attributes.fill,'#abcabc');
assert.ok(!svg.querySelectorAll('rect').some(item=>Object.values(item.attributes).includes('[object Object]')));

const fields=Object.fromEntries(['title','query','exclude','exclude-nodes','depth','max-nodes','note','create','refresh','status','list'].map(name=>[name,new Element()]));
fields['exclude-nodes'].value='hidden\nhidden\nother-hidden';fields.depth.value='1';fields['max-nodes'].value='20';fields.exclude.value='DTO, field';fields.note.value='Author note';
let context={projectId:'p',snapshotId:'first',nodeId:'raw',label:'Raw'},pending=[];
const panel=new SavedViewsPanel({querySelector:selector=>fields[selector.match(/"([^\"]+)"/)[1]]},{getContext:()=>context,fetcher:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve}))});
panel.sync();
const saving=panel.create();
const body=JSON.parse(pending[0].options.body);
assert.deepEqual(body.excludeNodeIds,['hidden','other-hidden']);assert.deepEqual(body.nodeIds,['raw']);assert.deepEqual(body.annotations[0].nodeIds,['raw']);assert.deepEqual(body.excludeKinds,['DTO','field']);
assert.ok(!('nodes' in body)&&!('edges' in body)&&!('source' in body));
context={...context,nodeId:'other',label:'Other'};panel.sync();
pending[0].resolve({ok:true,json:async()=>view});await saving;
assert.equal(fields.status.children.length,0);assert.ok(fields.status.textContent.includes('Other'));
assert.equal(pending.length,1);
console.log('Saved view geometry, facts, text safety and stale create PASS');
// Автономная страница честно показывает неполную классификацию kind и exact-ID исключения.
const pageFields=Object.fromEntries(['saved-graph','saved-details','saved-title','saved-query','saved-provenance','saved-status','saved-png','saved-zoom-in','saved-zoom-out','saved-fit','saved-retry'].map(id=>[id,new Element()]));
for(const field of Object.values(pageFields))field.style={};
pageFields['saved-graph'].parentElement={clientWidth:600};
globalThis.window={addEventListener(){}};
globalThis.document.getElementById=id=>pageFields[id];
globalThis.location={search:'?id='+view.id};
globalThis.fetch=async()=>({ok:true,json:async()=>({...view,snapshotId:'first',createdAt:'now',metadata:{kindFilterWarning:'symbol types are not classified',selection:{depth:1,maxNodes:20,excludeKinds:['field'],excludeNodeIds:['hidden']}}})});
await import('../web/view-page.js');
await new Promise(resolve=>setTimeout(resolve,0));
assert.ok(pageFields['saved-status'].children.some(item=>item.textContent==='symbol types are not classified'));
assert.ok(pageFields['saved-provenance'].children.some(item=>item.textContent.includes('Исключено по ID: 1')));
console.log('Saved view classification warning PASS');
