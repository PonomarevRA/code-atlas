import assert from 'node:assert/strict';
import { createMapBlocks, blockMembers, executableBlock } from '../web/map-blocks.js';
import { GraphView } from '../web/graph.js';
import { isolateCallRoots } from '../web/call-isolation.js';
import { navigationSnapshot, pathDetailToken, inlineDetailToken, resolveNavigation, readRoute, writeRoute } from '../web/navigation-history.js';
import { Svg } from './svg_fixture.mjs';
class Element extends Svg {
  constructor(tag) { super(tag); this.value = ''; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  focus() { document.activeElement = this; }
  querySelectorAll(selector) {
    if (selector.startsWith('[data-node-action=')) {
      const value = selector.match(/="(.*?)"/)[1];
      const result = [];
      for (const child of this.children) { if (child.getAttribute('data-node-action') === value) result.push(child); result.push(...child.querySelectorAll(selector)); }
      return result;
    }
    return super.querySelectorAll(selector);
  }
}
const controls = new Map();
for (const id of ['view-mode','relation','architecture-preset','selection','answer','visible-count']) controls.set(id,new Element('div'));
controls.get('view-mode').value='architecture'; controls.get('relation').value=''; controls.get('architecture-preset').value='dependencies';
globalThis.document = { getElementById:id=>controls.get(id), createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag) };
globalThis.Option = class extends Element { constructor(text,value) { super('option');this.textContent=text;this.value=value; } };
globalThis.ResizeObserver=class { observe() {} };
const svg = new Element('svg'), viewport = new Element('g'); viewport.setAttribute('id','viewport');svg.append(viewport);
const nodes = [{id:'type',label:'Calculator',_callable:true,_callable_class:true,codeProjectId:'p',file:'Calc.cs'},
  {id:'method',label:'.Calculate()',_callable:true,codeProjectId:'p',file:'Calc.cs'},
  {id:'later',label:'.Later()',_callable:true,codeProjectId:'p',file:'Other.cs'},
  {id:'outside',label:'.External()',_callable:true,codeProjectId:'other',file:'External.cs'}];
const graph = {nodes,edges:[{source:'type',target:'method',relation:'method'},{source:'method',target:'outside',relation:'calls'},{source:'outside',target:'later',relation:'calls'}]};
const state={graph,nodes:new Map(nodes.map(n=>[n.id,n])),answerRevision:0};
const selected=[],entered=[],paths=[];
const view=new GraphView(svg,id=>selected.push(id),()=>{}); view.colorProjects=[{id:'p',label:'Project'}];view.camera={x:47,y:88,scale:.7};
const project={id:JSON.stringify(['project','p']),architectureLevel:'projects',targetProjectId:'p',label:'Project',nodeCount:3};
const render=()=>view.renderOverview([project],[],null);
const controller=createMapBlocks({state,view,filteredNodes:()=>nodes,communityId:()=> 'group',
 selectNode:id=>{selected.push(id);state.selected=id;state.selectedBlock=null;},navigate:target=>entered.push(target),openCommunity:id=>entered.push(id),
 analyzePath:id=>paths.push(id),isolate:()=>{},draw:render,controls:()=>{},checkpoint:()=>{},commit:()=>{},reveal:()=>{}});
render();const camera={...view.camera};
controller.select(project.id);
assert.equal(state.selectedBlock,project.id);assert.equal(state.selected,null);assert.deepEqual(entered,[]);assert.deepEqual(paths,[]);assert.deepEqual(view.camera,camera);
assert.deepEqual(blockMembers(controller.address(),nodes,()=>'' ).map(n=>n.id),['type','method','later']);
assert.equal(executableBlock(nodes[0]),false,'class must not be offered as a method');
assert.equal(controls.get('selection').querySelector('select').children.length,2);
controller.toggle(project.id);
assert.equal(view.inlineSceneActive,true);assert.equal(state.inlineExpanded.has(project.id),true);assert.equal(view.camera.scale,camera.scale);
assert.deepEqual(view.camera,camera,'first expansion keeps the screen placement of the original header');
assert.equal(view.viewport.querySelectorAll('.inline-hierarchy-frame').length,1);assert.equal(view.communityElements.size,3,'parent plus file headers');
const file=state.inlineScene.items.find(item=>item.kind==='file'&&item.file==='Calc.cs');
controller.toggle(file.id);assert.equal(view.nodeElements.has('type'),true);assert.equal(view.nodeElements.has('method'),false,'method stays inside collapsed type');
controller.toggle('type');assert.equal(view.nodeElements.has('method'),true);
controller.select('method');assert.deepEqual(selected,['method']);assert.deepEqual(paths,[]);
controller.enter('method');assert.deepEqual(paths,['method']);
controller.enter('type');assert.deepEqual(paths,['method','type'],'type opens method overview through the shared path viewer');
controller.toggle('method');assert.deepEqual(paths,['method','type'],'plus keeps the method body on this map');
assert.ok(state.inlineExpanded.has('method'));assert.ok(view.viewport.querySelector('.inline-method-card'),'body loads in a reserved frame without requiring structural children');
controller.enter(file.id);assert.deepEqual(entered,[{level:'symbols',projectId:'p',file:'Calc.cs',page:0}]);
controller.toggle(project.id);assert.equal(view.inlineSceneActive,false);assert.equal(view.nodeElements.size,0);
controller.toggle(project.id);assert.equal(view.nodeElements.has('method'),true,'nested expansion remembers state across collapse');
const scope=isolateCallRoots(graph,['type','method','later']);assert.equal(scope.allNodeIds.has('outside'),true);assert.equal(scope.totalNodes,4);
const many=Array.from({length:500},(_,i)=>({id:`m${i}`}));
const bounded=isolateCallRoots({nodes:[...many,{id:'external'}],edges:[{source:'m499',target:'external',relation:'calls'}]},many.map(n=>n.id));
assert.equal(bounded.nodes.length,240);assert.equal(bounded.allNodeIds.has('external'),true,'last undisplayed seed contributes full outgoing chain');
const fragment={id:'["p","c"]',kind:'community',groupId:'c',projectScoped:true,projectId:'p'};
const groupNodes=[{id:'a',community:'c',codeProjectId:'p'},{id:'b',community:'c',codeProjectId:'p'},{id:'other',community:'c',codeProjectId:'other'}];
assert.deepEqual(blockMembers(fragment,groupNodes,()=> 'presentation-changed').map(n=>n.id),['a','b']);
const snapshot=navigationSnapshot({route:{repo:'r',view:'architecture',block:project.id,inline:pathDetailToken(state.inlineExpanded,[]),isolateBlock:project.id},
 inlineExpanded:state.inlineExpanded,isolatedBlock:{...controller.address(),id:project.id,kind:'project',projectId:'p',memberIds:many,graph},snapshotId:'s',camera:view.camera});
assert.equal(snapshot.isolatedBlock.memberIds,undefined);assert.equal(snapshot.isolatedBlock.graph,undefined);
const route=readRoute(writeRoute(snapshot.route,'http://localhost/'));assert.equal(route.block,project.id);assert.ok(route.inline);
const restored=resolveNavigation(snapshot,{projects:[{id:'r'}],nodes,snapshotId:'s'}).snapshot;assert.deepEqual(restored.inlineExpanded,snapshot.inlineExpanded);
const stale=resolveNavigation(snapshot,{projects:[{id:'r'}],nodes,snapshotId:'next'}).snapshot;
assert.deepEqual(stale.inlineExpanded,[]);assert.equal(stale.route.block,null);assert.equal(stale.isolatedBlock,null);assert.equal(stale.camera,null);
const expanded = Array.from({length:400}, (_,i)=>`block-${i}`);
const capped=navigationSnapshot({route:{repo:'r',inline:inlineDetailToken(expanded)},inlineExpanded:expanded,snapshotId:'s'});
assert.equal(capped.inlineExpanded.length,320);
assert.deepEqual(resolveNavigation(capped,{projects:[{id:'r'}],nodes,snapshotId:'s'}).snapshot.inlineExpanded,capped.inlineExpanded,'bounded token must survive 241+ expanded IDs');
controller.reset(); view.setDisplayProvider('flow'); render();
const flowPoint = view.positions.get(project.id), flowCamera = {...view.camera};
const flowHeader = {x:flowPoint.x-flowPoint.width/2,y:flowPoint.y-flowPoint.height/2,width:flowPoint.width,height:flowPoint.height};
controller.toggle(project.id);
assert.deepEqual(view.positions.get(project.id).headerBounds.x,flowHeader.x);
assert.deepEqual(view.positions.get(project.id).headerBounds.y,flowHeader.y);
assert.deepEqual(view.positions.get(project.id).headerBounds.width,flowHeader.width);
assert.deepEqual(view.positions.get(project.id).headerBounds.height,flowHeader.height);
assert.deepEqual(view.camera,flowCamera,'flow expansion retains actual rectangular header center');
console.log('Map blocks selection, explicit enter, nested toggles, full membership and bounded history PASS');

// The same controller renders project, file, class and method disclosure in Code reading mode.
controller.reset(); view.codeReading = true; view.setDisplayProvider('flow'); render();
assert.equal(view.communityElements.get(project.id).group.attributes['data-reading-design'], 'entry-path');
controller.toggle(project.id);
const readingFile = state.inlineScene.items.find(item => item.kind === 'file' && item.file === 'Calc.cs');
assert.equal(view.communityElements.get(readingFile.id).group.attributes['data-reading-design'], 'entry-path');
controller.toggle(readingFile.id); controller.toggle('type');
const readingMethod = view.nodeElements.get('method');
assert.equal(readingMethod.group.attributes['data-reading-design'], 'entry-path');
assert.ok(readingMethod.actions.enter.querySelector('text').textContent.includes('Схема'));
const pathCount = paths.length;
readingMethod.actions.enter.listeners.click({ stopPropagation() {}, preventDefault() {} });
assert.deepEqual(paths.slice(pathCount), ['method']);
assert.ok(state.inlineScene.items.filter(item => item.kind === 'raw').every(item => nodes.some(node => node.id === item.rawId)));
console.log('Unified Code project/file/class/method disclosure and labelled path action PASS');

// Groups summaries keep selection separate from entering the project's group scope.
controller.reset(); view.codeReading = false; controls.get('view-mode').value = 'groups';
const groupProjects = [];
const summary = { ...project, groupSummary: { scope: 'project', groups: 2, files: 2 } };
const summaryController = createMapBlocks({ state, view, filteredNodes: () => nodes, communityId: () => 'group',
  selectNode: id => selected.push(id), navigate: target => entered.push(target), openCommunity: () => {},
  openGroupProject: id => groupProjects.push(id), analyzePath: () => {}, isolate: () => {}, draw: () => {},
  controls: () => {}, checkpoint: () => {}, commit: () => {}, reveal: () => {} });
view.renderOverview([summary], [], null);
summaryController.select(summary.id);
assert.equal(view.communityElements.get(summary.id).group.querySelectorAll('.selection-ring').length, 1);
assert.deepEqual(groupProjects, []);
assert.equal(view.communityElements.get(summary.id).group.querySelector('.node-action-inline'), undefined);
summaryController.enter(summary.id); assert.deepEqual(groupProjects, ['p']);
console.log('Group summary selection and explicit project-to-groups transition PASS');
