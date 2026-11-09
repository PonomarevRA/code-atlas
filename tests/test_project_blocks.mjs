/** Проверяет проектные фрагменты и геометрические границы без изменения исходного графа. */
import assert from 'node:assert/strict';
import { aggregateProjectFragments, projectCommunityId, projectContainerGeometry } from '../web/project-blocks.js';
import { markChangedCommunities } from '../web/changes.js';

const graph = {
  codeProjects: [{id:'api',label:'API'}, {id:'api-extra',label:'API extra'}],
  communities: [{id:'shared',label:'Shared'}, {id:'other',label:'Other'}],
  nodes: [
    {id:'a',community:'shared',codeProjectId:'api'},
    {id:'b',community:'shared',codeProjectId:'api'},
    {id:'c',community:'shared',codeProjectId:'api-extra'},
    {id:'d',community:'other',codeProjectId:'api-extra'},
    {id:'missing',community:'shared'},
  ],
  edges: [
    {source:'a',target:'b',relation:'calls'},
    {source:'a',target:'c',relation:'calls'},
    {source:'c',target:'a',relation:'calls'},
    {source:'c',target:'d',relation:'imports'},
    {source:'d',target:'missing',relation:'calls'},
  ],
};
const before = JSON.stringify(graph);
const result = aggregateProjectFragments(graph, graph.nodes);
assert.equal(result.communities.length, 4);
assert.deepEqual(result.edges, graph.edges);
assert.equal(result.communities.reduce((sum,group)=>sum+group.nodeCount,0),5);
const apiId=projectCommunityId(graph.nodes[0]), otherId=projectCommunityId(graph.nodes[2]);
assert.notEqual(apiId,otherId);
const api=result.communities.find(group=>group.id===apiId);
assert.equal(api.codeProjectId,'api');
assert.equal(api.originalCommunityId,'shared');
assert.equal(api.nodeCount,2);
assert.equal(api.internalEdges,1);
assert.equal(api.externalEdges,2);
assert.equal(result.communityEdges.find(edge=>edge.source===apiId&&edge.target===otherId).weight,1);
assert.equal(result.communityEdges.find(edge=>edge.source===otherId&&edge.target===apiId).weight,1);
assert.equal(result.displayNodes.find(node=>node.id==='c').community,otherId);
const marked=markChangedCommunities(result.communities,result.displayNodes,['a']);
assert.equal(marked.find(group=>group.id===apiId).changedNodeCount,1);
assert.equal(marked.find(group=>group.id===otherId).changedNodeCount,0);
const calls=aggregateProjectFragments(graph,graph.nodes,{relation:'calls'});
assert.equal(calls.edges.length,4);
const onlyApi=aggregateProjectFragments(graph,graph.nodes.filter(node=>node.codeProjectId==='api'));
assert.equal(onlyApi.communities.length,1);assert.equal(onlyApi.edges.length,1);
assert.equal(JSON.stringify(graph),before);

/** Проверяет границу всей окружности узла относительно настоящего эллипса проекта. */
function circleInsideEllipse(point,box){
  const rx=box.width/2,ry=box.height/2,cx=box.x+rx,cy=box.y+ry;
  for(let step=0;step<72;step++){
    const angle=step*Math.PI*2/72;
    const x=point.x+point.r*Math.cos(angle),y=point.y+point.r*Math.sin(angle);
    assert.ok(((x-cx)/rx)**2+((y-cy)/ry)**2<=1.000001,`Circle ${point.id} leaves ellipse ${box.id}`);
  }
}
const points=graph.nodes.map(node=>({...node,r:node.id==='a'?55:12,color:'#aaa'}));
const pointBefore=JSON.stringify(points);
const geometry=projectContainerGeometry(points,graph.edges,graph.codeProjects);
assert.equal(geometry.positions.size,5);
assert.equal(geometry.containers.length,3);
for(const box of geometry.containers){
  assert.equal(box.cx,box.x+box.width/2);assert.equal(box.cy,box.y+box.height/2);
  assert.equal(box.rx,box.width/2);assert.equal(box.ry,box.height/2);
  for(const x of [box.contentX,box.contentX+box.contentWidth])for(const y of [box.contentY,box.contentY+box.contentHeight]){
    assert.ok(((x-box.cx)/box.rx)**2+((y-box.cy)/box.ry)**2<=1.000001,'Content envelope leaves ellipse');
  }
}
for(const original of points){
  const point=geometry.positions.get(original.id);
  const block=geometry.containers.find(container=>container.id===(original.codeProjectId??'__unassigned__'));
  assert.ok(block,original.id);
  assert.ok(Number.isFinite(point.x)&&Number.isFinite(point.y));
  circleInsideEllipse(point,block);
  assert.ok(point.x-point.r>=block.x-.01&&point.x+point.r<=block.x+block.width+.01,original.id);
  assert.ok(point.y-point.r>=block.y-.01&&point.y+point.r<=block.y+block.height+.01,original.id);
}
for(let first=0;first<geometry.containers.length;first++)for(let second=first+1;second<geometry.containers.length;second++){
  const a=geometry.containers[first],b=geometry.containers[second];
  assert.ok(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y,`${a.id}/${b.id}`);
}
assert.equal(JSON.stringify(points),pointBefore);
assert.equal(JSON.stringify(graph),before);
assert.deepEqual(projectContainerGeometry(points,graph.edges,graph.codeProjects),geometry);
const empty=projectContainerGeometry([],[],[]);
assert.equal(empty.positions.size,0);assert.equal(empty.containers.length,0);
const many=Array.from({length:320},(_,index)=>({id:`n${index}`,codeProjectId:`p${index%16}`,r:7}));
const manyEdges=many.slice(1).map((node,index)=>({source:many[index].id,target:node.id}));
const bounded=projectContainerGeometry(many,manyEdges);
assert.equal(bounded.positions.size,320);assert.equal(bounded.containers.length,16);
const longLabel='Very.Long.Project.Name.'.repeat(8);
const longBox=projectContainerGeometry([{id:'one',codeProjectId:'long',r:95}],[],[{id:'long',label:longLabel}]);
assert.equal(longBox.containers[0].labelLines.join(''),longLabel);
circleInsideEllipse(longBox.positions.get('one'),longBox.containers[0]);
assert.ok(longBox.positions.get('one').y-95>longBox.containers[0].contentY+42+(longBox.containers[0].labelLines.length-1)*34);
console.log('Project blocks PASS');

/** Минимальный SVG DOM для проверки реальных renderOverview/fit без браузерных зависимостей. */
class SvgElement {
  constructor(tag) { this.tag=tag; this.attributes={}; this.children=[]; this.listeners={}; this.classList={toggle:()=>{}}; }
  setAttribute(name,value) { this.attributes[name]=value; }
  append(child) { this.children.push(child); child.parent=this; }
  replaceChildren(...children) { this.children=children; }
  addEventListener(name,callback) { this.listeners[name]=callback; }
  querySelector(selector) { return this.children.find(child=>selector.startsWith('.') ? child.attributes.class===selector.slice(1) : child.tag===selector); }
  remove() { this.parent.children=this.parent.children.filter(child=>child!==this); }
}
const {GraphView}=await import('../web/graph.js');
globalThis.document={createElementNS:(_,tag)=>new SvgElement(tag)};
const view=Object.create(GraphView.prototype);
Object.assign(view,{viewport:new SvgElement('g'),nodeElements:new Map(),communityElements:new Map(),svg:{clientWidth:1000,clientHeight:800},onCommunity:id=>{view.clicked=id;}});
view.renderOverview(result.communities,result.communityEdges,null,graph.codeProjects);
assert.equal(view.viewport.children.filter(child=>child.tag==='path').length,result.communityEdges.length);
assert.equal(view.containers.length,3);
for(const block of view.viewport.children.filter(child=>child.attributes.class==='project-block')){
  assert.equal(block.querySelector('.project-block-box').tag,'ellipse');
  const box=view.containers.find(item=>item.id===block.attributes['data-project-id']);
  const labels=block.children.filter(item=>item.attributes.class==='project-block-title');
  const stats=block.querySelector('.project-block-stats');
  assert.equal(labels.length,box.labelLines.length);
  for(const text of [...labels,stats]){
    assert.ok(text.attributes.x>=box.contentX&&text.attributes.x<box.contentX+box.contentWidth);
    assert.ok(text.attributes.y-28>=box.contentY&&text.attributes.y+8<=box.contentY+box.contentHeight);
  }
  const members=[...view.positions.values()].filter(point=>(point.codeProjectId??'__unassigned__')===box.id);
  for(const point of members) assert.ok(point.y-point.r>stats.attributes.y+8,'Node overlaps reserved header');
}
const projectTitles=view.viewport.children.filter(child=>child.attributes.class==='project-block').map(block=>block.querySelector('.project-block-title').textContent);
assert.deepEqual(new Set(projectTitles),new Set(['API','API extra','Без проекта']));
view.communityElements.get(apiId).group.listeners.click();assert.equal(view.clicked,apiId);
const regexCounts=new Map([[apiId,1],[otherId,1]]);
assert.equal(view.setSearchMatches(['a','c'],regexCounts),2);
assert.equal(view.communityElements.get(apiId).group.querySelector('.regex-count').textContent,'⌕ 1');
assert.equal(view.communityElements.get(otherId).group.querySelector('.regex-count').textContent,'⌕ 1');
const originalPositions=JSON.stringify([...view.positions]);
view.camera={x:21,y:31,scale:.7};
const cameraBefore={...view.camera};
const edgeElements=[...view.edgeElements];
const pathsBefore=edgeElements.map(item=>item.path.attributes.d);
view.setBundled(false);
assert.ok(edgeElements.every(item=>item.path.attributes.d.includes(' L ')));
assert.equal(JSON.stringify([...view.positions]),originalPositions);
assert.deepEqual(view.camera,cameraBefore);
assert.equal(view.communityElements.get(apiId).group.querySelector('.regex-count').textContent,'⌕ 1');
view.setBundled(true);
assert.deepEqual(view.edgeElements.map(item=>item.path.attributes.d),pathsBefore);
assert.ok(view.edgeElements.every((item,index)=>item.path===edgeElements[index].path));
view.fit();
for(const box of view.containers){
  const left=box.x*view.camera.scale+view.camera.x,right=(box.x+box.width)*view.camera.scale+view.camera.x;
  const top=box.y*view.camera.scale+view.camera.y,bottom=(box.y+box.height)*view.camera.scale+view.camera.y;
  assert.ok(left>=0&&right<=1000&&top>=0&&bottom<=800,box.id);
}
view.renderOverview(graph.communities,[],null);
assert.equal(view.containers.length,0);
assert.equal(view.communityElements.size,2);
assert.equal(view.viewport.children.filter(child=>child.attributes.class==='project-block').length,0);
console.log('Project render and fit PASS');
view.render(graph.nodes,graph.edges,'a',graph.communities,graph.codeProjects);
assert.equal(view.edgeElements.length,graph.edges.length);
assert.ok(view.edgeElements.every(item=>item.path.tag==='path'&&item.path.attributes['marker-end']?.startsWith('url(#')));
const detailEdges=[...view.edgeElements];
view.setBundled(false);view.setBundled(true);
assert.ok(view.edgeElements.every((item,index)=>item.path===detailEdges[index].path));
console.log('Bundled edges preserve SVG identities PASS');
const {nodeColor,projectColor}=await import('../web/colors.js');
const colorNodes=graph.nodes.map(node=>({...node,label:`Name ${node.id}`,changedFile:node.id==='a'}));
view.colorProjects=graph.codeProjects;
view.render(colorNodes,graph.edges,'a',graph.communities,graph.codeProjects);
const expectedColor=nodeColor(colorNodes[0],graph.codeProjects);
const selectedGroup=view.nodeElements.get('a').group;
assert.equal(selectedGroup.children[0].attributes.fill,expectedColor);
assert.ok(selectedGroup.querySelector('.change-ring'));
view.setSearchMatches(['a']);
assert.ok(selectedGroup.querySelector('.regex-ring'));
assert.equal(selectedGroup.children[0].attributes.fill,expectedColor);
const apiBlock=view.viewport.children.find(child=>child.attributes['data-project-id']==='api');
assert.equal(apiBlock.querySelector('.project-block-box').attributes.fill,projectColor('API').fill);
view.render(colorNodes,graph.edges,null,graph.communities,graph.codeProjects);
assert.equal(view.nodeElements.get('a').group.children[0].attributes.fill,expectedColor);
console.log('Base colors and semantic overlays PASS');
/** Находит созданный SVG marker по ссылке конкретной линии. */
function findMarker(element,id){
  if(element.attributes.id===id)return element;
  for(const child of element.children){const found=findMarker(child,id);if(found)return found;}
}
/** Проверяет цвет источника и соответствующую ему стрелку во всех путях текущего слоя. */
function assertOutgoingColors(){
  for(const item of view.edgeElements){
    const sourceColor=view.positions.get(item.path.attributes['data-source']).color;
    assert.equal(item.path.attributes.stroke,sourceColor);
    const markerId=item.path.attributes['marker-end'].slice(5,-1);
    const marker=findMarker(view.viewport,markerId);
    assert.ok(marker,markerId);
    assert.equal(marker.children[0].attributes.fill,sourceColor);
  }
}
assertOutgoingColors();
const directedStyles=view.edgeElements.map(item=>[item.path.attributes.stroke,item.path.attributes['marker-end']]);
view.setBundled(false);view.setBundled(true);assertOutgoingColors();
assert.deepEqual(view.edgeElements.map(item=>[item.path.attributes.stroke,item.path.attributes['marker-end']]),directedStyles);
view.renderOverview(result.communities,result.communityEdges,null,graph.codeProjects);
assertOutgoingColors();
const forward=view.edgeElements.find(item=>item.path.attributes['data-source']===apiId&&item.path.attributes['data-target']===otherId);
const backward=view.edgeElements.find(item=>item.path.attributes['data-source']===otherId&&item.path.attributes['data-target']===apiId);
assert.ok(forward&&backward);assert.notEqual(forward.path.attributes.stroke,backward.path.attributes.stroke);
assert.notEqual(forward.path.attributes.d,backward.path.attributes.d);
const {readFileSync}=await import('node:fs');
const styles=readFileSync(new URL('../web/styles.css',import.meta.url),'utf8');
for(const selector of ['.community-edge.highlighted','.graph-edge.active']){
  const escaped=selector.replaceAll('.','\\.');
  for(const match of styles.matchAll(new RegExp(escaped+'\\s*\\{([^}]*)\\}','g')))assert.ok(!/(?:^|;)\s*stroke\s*:/.test(match[1]),selector+' overrides source color');
}
console.log('Outgoing edge colors and reciprocal direction PASS');
const longFile='VeryLongFileName'.repeat(15)+'.cs';
view.renderOverview([{id:'architecture-file',label:longFile,displayLabel:longFile,nodeCount:1,architecture:true,architectureLevel:'files'}],[]);
const fileGroup=view.communityElements.get('architecture-file').group;
const fileLabels=fileGroup.children.filter(child=>child.attributes.class==='community-label');
assert.equal(fileLabels.length,2);assert.ok(fileLabels.at(-1).textContent.endsWith('…'));
assert.ok(fileGroup.children.find(child=>child.tag==='title').textContent.startsWith(longFile));
const filePoint=view.positions.get('architecture-file'),bounds=view.geometryBounds();
assert.ok(bounds.maxY>=filePoint.y+Number(fileLabels.at(-1).attributes.y));
assert.ok(bounds.maxX>=filePoint.x+filePoint.labelWidth/2);
console.log('Long file label bounds and full tooltip PASS');
// Входы остаются отдельным SVG-слоем: не попадают в rawpositions и счётчики подсветки.
const graphPositionsBefore=JSON.stringify([...view.positions]);
const graphGroupsBefore=[...view.communityElements.keys()];
const graphEdgesBefore=[...view.edgeElements];
view.setSearchMatches([],new Map([['architecture-file',1]]));
const ingressEntry={id:'entry-synthetic',kind:'http',label:'GET /route',route:'/route',origin:'External client unknown',targets:['architecture-file']};
let selectedIngress=null;
view.renderIngress([ingressEntry,{id:'unmapped',kind:'rabbit',label:'Unknown',targets:[]}],entry=>{selectedIngress=entry;});
assert.equal(JSON.stringify([...view.positions]),graphPositionsBefore);
assert.deepEqual([...view.communityElements.keys()],graphGroupsBefore);
assert.deepEqual(view.edgeElements,graphEdgesBefore);
assert.ok(!view.positions.has('entry-synthetic'));
assert.equal(view.ingressPositions.length,1);
const ingressLayer=view.viewport.children.find(child=>child.attributes.class==='ingress-layer');
const incoming=ingressLayer.children.find(child=>child.attributes.class==='ingress-edge');
assert.equal(incoming.attributes['data-target'],'architecture-file');
const start=view.ingressPositions[0],target=view.positions.get('architecture-file');
assert.ok(incoming.attributes.d.startsWith(`M ${start.x} ${start.y}`));
assert.ok(incoming.attributes.d.endsWith(`${target.x} ${target.y}`));
assert.ok(incoming.attributes['marker-end']);
const ingressNode=ingressLayer.children.find(child=>child.attributes.class==='ingress-node');
ingressNode.listeners.click();assert.equal(selectedIngress.id,'entry-synthetic');
assert.equal(view.communityElements.get('architecture-file').group.querySelector('.regex-count').textContent,'⌕ 1');
assert.ok(start.x>=bounds.minX&&start.x<=bounds.maxX);
assert.ok(start.y<bounds.minY);
view.fit();assert.ok(start.x*view.camera.scale+view.camera.x>=0);
assert.ok((start.y-30)*view.camera.scale+view.camera.y>=0);
console.log('Ingress direction and separate raw identity PASS');

// Выбор уже видимого символа сохраняет настоящий SVG и камеру, а не только ID в helper.
const {selectInCurrentView}=await import('../web/selection-controller.js');
view.render(colorNodes,graph.edges,null,graph.communities,graph.codeProjects);
view.setSearchMatches(['a']);
view.camera={x:123,y:456,scale:.8};
const stablePositions=JSON.stringify([...view.positions]),stableCamera={...view.camera};
const stableViewport=[...view.viewport.children],stableEdges=view.edgeElements.map(item=>item.path);
const stableNode=view.nodeElements.get('a').group;
assert.equal(selectInCurrentView(view,'a'),true);
assert.equal(stableNode.attributes['aria-pressed'],true);
assert.equal(stableNode.querySelector('.topology-card-shell').attributes['stroke-width'],2.5);
assert.equal(stableNode.querySelector('.topology-card-shell').attributes.stroke,'#17382c');
assert.equal(stableNode.children[0].attributes.width,view.positions.get('a').width);
assert.equal(stableNode.children[0].attributes.height,view.positions.get('a').height);
assert.ok(stableNode.querySelector('.change-ring'));
assert.ok(stableNode.querySelector('.regex-ring'));
assert.equal(stableNode.children[0].attributes.fill,expectedColor);
assert.equal(selectInCurrentView(view,'b'),true);
assert.equal(stableNode.attributes['aria-pressed'],false);
assert.equal(stableNode.querySelector('.topology-card-shell').attributes['stroke-width'],1.2);
assert.equal(stableNode.querySelector('.topology-card-shell').attributes.stroke,view.positions.get('a').color);
assert.equal(JSON.stringify([...view.positions]),stablePositions);
assert.deepEqual(view.camera,stableCamera);
for(let index=0;index<stableViewport.length;index++)assert.equal(view.viewport.children[index],stableViewport[index]);
assert.deepEqual(view.edgeElements.map(item=>item.path),stableEdges);
assert.equal(selectInCurrentView(view,'not-visible'),false);
assert.equal(selectInCurrentView(view,'a',{focused:true}),false);
console.log('Visible selection preserves actual SVG and camera PASS');
