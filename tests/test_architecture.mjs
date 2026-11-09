/** Проверяет полный структурный обзор и доступность всех узлов через страницы. */
import assert from 'node:assert/strict';
import {architectureGraph} from '../web/architecture.js';
const graph={codeProjects:[{id:'p',label:'Project'},{id:'q',label:'Other'}],nodes:[
{id:'a',label:'First',file:'src/a.cs',codeProjectId:'p'},{id:'b',label:'Second',file:'src/a.cs',codeProjectId:'p'},
{id:'c',label:'Third',file:'src/b.cs',codeProjectId:'p'},{id:'d',label:'Other',file:'other.cs',codeProjectId:'q'},
{id:'missing',label:'External',file:'',codeProjectId:'__unassigned__'}],edges:[
{source:'a',target:'b',relation:'contains'},{source:'a',target:'c',relation:'calls'},{source:'c',target:'a',relation:'references'},
{source:'a',target:'d',relation:'imports'},{source:'d',target:'a',relation:'calls'},{source:'d',target:'missing',relation:'implements'},
{source:'c',target:'missing',relation:'rationale_for'}]};
const before=JSON.stringify(graph);
const projects=architectureGraph(graph,graph.nodes);
assert.equal(projects.groups.length,3);assert.equal(projects.rawEdges.length,5);assert.equal(projects.nodes.length,5);
assert.equal(projects.total,3);assert.equal(projects.pageCount,1);
const p=projects.groups.find(group=>group.codeProjectId==='p'),q=projects.groups.find(group=>group.codeProjectId==='q');
assert.equal(p.internalEdges,2);assert.equal(p.nodeCount,3);
assert.equal(projects.edges.find(edge=>edge.source===p.id&&edge.target===q.id).weight,1);
assert.equal(projects.edges.find(edge=>edge.source===q.id&&edge.target===p.id).weight,1);
assert.equal(architectureGraph(graph,graph.nodes,{preset:'all'}).rawEdges.length,7);
assert.equal(architectureGraph(graph,graph.nodes,{relation:'contains'}).rawEdges.length,0);
assert.equal(architectureGraph(graph,graph.nodes,{preset:'all',relation:'contains'}).rawEdges.length,1);
const files=architectureGraph(graph,graph.nodes,{level:'files',projectId:'p'});
assert.equal(files.groups.length,2);assert.equal(files.rawEdges.length,2);
const file=files.groups.find(group=>group.targetFile==='src/a.cs');assert.equal(file.nodeCount,2);
const symbols=architectureGraph(graph,graph.nodes,{level:'symbols',projectId:'p',file:file.targetFile,preset:'all'});
assert.deepEqual(new Set(symbols.nodes.map(node=>node.id)),new Set(['a','b']));assert.equal(symbols.rawEdges.length,1);
for(const node of symbols.nodes)assert.equal(symbols.membership.get(node.id),node.id);
const missing=architectureGraph(graph,graph.nodes,{level:'symbols',projectId:'__unassigned__',file:null});
assert.deepEqual(missing.nodes.map(node=>node.id),['missing']);
const filtered=architectureGraph(graph,[graph.nodes[0],graph.nodes[3]]);
assert.equal(filtered.groups.reduce((sum,group)=>sum+group.nodeCount,0),2);assert.equal(filtered.rawEdges.length,2);
// 501 определение доступно без усечения: 3 страницы, номера ID не заменяются агрегатами.
const large={nodes:Array.from({length:501},(_,index)=>({id:`n${index}`,label:`Node${String(index).padStart(3,'0')}`,file:'many.cs',codeProjectId:'p'})),edges:[],codeProjects:graph.codeProjects};
large.edges=[{source:'n0',target:'n500',relation:'calls'}];
const seen=new Set();
for(let page=0;page<3;page++){
 const current=architectureGraph(large,large.nodes,{level:'symbols',projectId:'p',file:'many.cs',page});
 assert.equal(current.total,501);assert.equal(current.pageCount,3);assert.equal(current.rawEdges.length,1);
 assert.ok(current.nodes.length<=240);for(const node of current.nodes){assert.ok(!seen.has(node.id));seen.add(node.id);}
}
assert.equal(seen.size,501);
const manyFiles={...large,nodes:large.nodes.map(node=>({...node,file:`${node.id}.cs`}))};
const firstFiles=architectureGraph(manyFiles,manyFiles.nodes,{level:'files',projectId:'p'});
assert.equal(firstFiles.groups.length,240);assert.equal(firstFiles.total,501);assert.equal(firstFiles.allEdges.length,1);
assert.equal(architectureGraph(large,large.nodes,{level:'symbols',projectId:'p',file:'many.cs',page:999}).page,2);
assert.equal(architectureGraph(graph,[],{level:'files',projectId:'p'}).total,0);
assert.equal(JSON.stringify(graph),before);
const inheritanceGraph={...graph,edges:[...graph.edges,{source:'a',target:'c',relation:'inherits'},{source:'a',target:'d',relation:'implements'}]};
const inherited=architectureGraph(inheritanceGraph,inheritanceGraph.nodes,{level:'symbols',projectId:'p',file:'src/a.cs'});
assert.deepEqual(inherited.nodes.filter(n=>n.inheritanceContext).map(n=>n.id),['c','d']);
assert.equal(inherited.total,2,'parents are context, not extra symbols of the selected file');
assert(inherited.edges.some(e=>e.relation==='inherits'&&e.target==='c'));
assert.equal(architectureGraph(inheritanceGraph,inheritanceGraph.nodes.filter(n=>n.id!=='c'),{level:'symbols',projectId:'p',file:'src/a.cs'}).inheritanceContextCount,1,'parents obey global filters');
assert.equal(architectureGraph(inheritanceGraph,inheritanceGraph.nodes,{level:'symbols',projectId:'p',file:'src/a.cs',relation:'calls'}).inheritanceContextCount,0);
console.log('Architecture drilldown and complete pagination PASS');
const {markChangedCommunities}=await import('../web/changes.js');
const annotated=projects.scopedNodes.map(node=>({...node,community:projects.membership.get(node.id)}));
const marked=markChangedCommunities(projects.groups,annotated,['a','d','missing','unknown']);
assert.equal(marked.reduce((sum,group)=>sum+group.changedNodeCount,0),3);
assert.equal(marked.find(group=>group.id===p.id).changedNodeCount,1);
const regexCounts=new Map();
for(const id of ['a','c','missing']){const owner=projects.membership.get(id);regexCounts.set(owner,(regexCounts.get(owner)||0)+1);}
assert.equal(regexCounts.get(p.id),2);
for(const group of projects.groups)assert.ok((regexCounts.get(group.id)||0)<=group.nodeCount);
const back=architectureGraph(graph,graph.nodes,{level:'projects'});
assert.deepEqual(back.groups,projects.groups);
console.log('Architecture Git and regex totals PASS');
// Проекты тоже имеют страницы: ни один из 501 владельца не теряется за лимитом SVG.
const manyProjects={nodes:large.nodes.map((node,index)=>({...node,codeProjectId:`project${index}`})),edges:large.edges,codeProjects:[]};
const seenProjects=new Set();
for(let page=0;page<3;page++){
 const current=architectureGraph(manyProjects,manyProjects.nodes,{level:'projects',page});
 assert.equal(current.total,501);assert.equal(current.pageCount,3);assert.ok(current.groups.length<=240);
 assert.equal(current.rawEdges.length,1);assert.equal(current.allEdges.length,1);
 for(const group of current.groups){assert.ok(!seenProjects.has(group.id));seenProjects.add(group.id);assert.equal(group.targetFile,null);}
}
assert.equal(seenProjects.size,501);
console.log('All project pages accessible PASS');
const {communityGeometry}=await import('../web/layout.js');
const labelGroups=architectureGraph(manyProjects,manyProjects.nodes,{level:'projects'}).groups;
const labelled=communityGeometry(labelGroups,[]);
for(const group of labelGroups){
 const point=labelled.get(group.id);assert.equal(point.labelLines.join(''),group.displayLabel||group.label);
 assert.ok(Number.isFinite(point.labelWidth)&&Number.isFinite(point.labelBottom));
}
const labelPoints=[...labelled.values()];
for(let first=0;first<labelPoints.length;first++)for(let second=first+1;second<labelPoints.length;second++){
 const a=labelPoints[first],b=labelPoints[second];
 assert.ok(a.x+a.labelWidth/2<=b.x-b.labelWidth/2||b.x+b.labelWidth/2<=a.x-a.labelWidth/2||a.y+a.labelBottom<=b.y-b.r||b.y+b.labelBottom<=a.y-a.r,`Project label envelopes overlap: ${a.id}/${b.id}`);
}
console.log('Project labels remain complete and separated PASS');

// Duplicate display names remain conservative while saved source facts distinguish each scope.
{
  const input={nodes:[{id:'html',file:'index.html',codeProjectId:'h'},{id:'ts',file:'main.ts',codeProjectId:'t'},{id:'py',file:'pyproject.toml',codeProjectId:'p'}],edges:[],codeProjects:[
    {id:'h',label:'Корень',path:'.',language:'html'},{id:'t',label:'Корень',path:'.',language:'typescript'},{id:'p',label:'Корень',path:'.',manifest:'pyproject.toml'}]};
  const groups=architectureGraph(input,input.nodes).groups;
  assert.equal(groups.find(x=>x.codeProjectId==='h').sourceSubtitle,'. · html');
  assert.equal(groups.find(x=>x.codeProjectId==='t').sourceSubtitle,'. · typescript');
  assert.equal(groups.find(x=>x.codeProjectId==='p').sourceSubtitle,'. · pyproject.toml');
  assert(groups.every(x=>x.label==='Корень'),'presentation facts never rewrite saved labels or ownership IDs');
}
