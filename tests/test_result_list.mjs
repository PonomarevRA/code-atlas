import assert from 'node:assert/strict';
import { Svg } from './svg_fixture.mjs';
import { createNodeResult, setResultSelected, activateResultCard } from '../web/result-list.js';
import { complexityPresentation, appendComplexityBadge } from '../web/complexity-badge.js';
import { GraphView } from '../web/graph.js';

// Exercise the actual HTML renderer with the shared DOM fixture, without nested focusable controls.
globalThis.document = { createElement: tag => {
  const element = new Svg(tag); element.dataset = {}; element.style = {};
  Object.defineProperty(element, 'className', { set(value) { this.setAttribute('class', value); } });
  return element;
}, createElementNS: (_, tag) => new Svg(tag) };
for (const [score, status, label, color] of [[0,'complete','0','#327f70'],[5,'complete','5','#986718'],
  [10,'partial','≥10','#b13e43'],[null,'unavailable','?','#7b8784']]) {
  const complexity = { score, status, maxNestingDepth: 2, averageNestingDepth: 1, branchingCount: 3, loopCount: 0 };
  let selected;
  const button = createNodeResult({ id:'method',label:'<unsafe>()',file:'src/a.cs',complexity }, 'other', id => selected = id);
  const badge = button.querySelector('.result-complexity');
  assert.equal(badge.textContent, label); assert.equal(badge.style.backgroundColor, color);
  assert.equal(button.querySelector('strong').textContent, '<unsafe>()');
  assert.equal(button.attributes['aria-pressed'], 'false');
  button.listeners.click(); assert.equal(selected, 'method');
  setResultSelected(button,true); assert.equal(button.attributes['aria-pressed'], 'true');
  const svg = new Svg('g'); appendComplexityBadge(svg,complexity,0,0);
  assert.equal(svg.querySelector('circle').attributes.fill,color);
  assert.equal(badge.attributes['aria-label'],complexityPresentation(complexity).description);
  assert.equal(badge.attributes.tabindex,undefined);
}
assert.equal(complexityPresentation(undefined).label,'?');

// Real GraphView selection and camera math must resolve occurrence IDs, not raw IDs.
const view = Object.create(GraphView.prototype);
Object.assign(view, { pathSceneActive:true, displayProvider:'flow', selectedId:'other',
  nodeElements:new Map(), positions:new Map(), camera:{x:0,y:0,scale:1.4},
  svg:{clientWidth:800,clientHeight:600}, sequenceInspectionIds:new Set(['method']),
  pathSequenceScene:{byId:new Map([['statement',{kind:'return'}],['header',{kind:'method'}]])},
  transform(){this.transforms=(this.transforms||0)+1;}, updatePathRelations(){} });
for (const [key,x,y] of [['statement',900,800],['header',400,300]]) {
  const group=new Svg('g');group.setAttribute('class','path-card method-sequence-card');
  view.nodeElements.set(key,{group,inspectionId:'method'});view.positions.set(key,{x,y});
}
view.highlightSelection = id => { for (const item of view.nodeElements.values()) item.group.classList.toggle('selected',item.inspectionId===id); };
activateResultCard(view,'method',(id,reveal,sequence) => {
  assert.equal(reveal,true);assert.equal(sequence,true);view.setSelected(id);
});
assert.equal(view.selectedId,'method');assert.equal(view.camera.scale,1.4);
assert.deepEqual(view.camera,{x:-160,y:-120,scale:1.4});
assert.ok(view.nodeElements.get('header').group.classList.contains('selected'));
assert.equal(view.transforms,1);
activateResultCard(view,'missing',()=>{});assert.equal(view.transforms,1,'Rejected or missing selections must not move the camera');
console.log('Result metrics, unknown/partial/zero, safe DOM, accessible selection and occurrence camera PASS');

// Real sequence rendering exposes selection even for compact rows without their own shell.
globalThis.ResizeObserver=class { observe(){} };
const graphSvg=new Svg('svg'), viewport=new Svg('g');viewport.setAttribute('id','viewport');graphSvg.append(viewport);
const rendered=new GraphView(graphSvg,()=>{},()=>{});rendered.pathRootId='root';
rendered.renderPathCards([{id:'root',label:'Build()',file:'a.cs',line:1,events:[],
  methodFlow:{status:'parsed',steps:[{kind:'return',label:'return 1;',line:2}]}}],[],'root');
const ring=viewport.querySelector('.sequence-selection-ring');
assert.ok(ring,'Active source cards need an explicit outline independent of their shell type');
assert.equal(ring.attributes['pointer-events'],'none','Selection outline must not intercept source or expand actions');
assert.equal(ring.parent.attributes['aria-pressed'],'true');
rendered.setSelected('missing');
assert.equal(viewport.querySelectorAll('.sequence-selection-ring').length,0);
