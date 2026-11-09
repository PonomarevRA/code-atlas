/** Повторяемый CPU baseline на реальном снимке; не измеряет браузерный DOM или сеть. */
import fs from 'node:fs';
import {performance} from 'node:perf_hooks';
import {GraphModel} from '../web/graph-model.js';
import {filterNodes,aggregateCommunities} from '../web/filters.js';
import {architectureGraph} from '../web/architecture.js';
import {nodeGeometry,communityGeometry} from '../web/layout.js';
const path=process.argv[2];
if(!path)throw new Error('Передайте путь к сохранённому JSON-графу.');
const payload=JSON.parse(fs.readFileSync(path,'utf8')),graph=payload.graph||payload;
const byFile=new Map();
for(const node of graph.nodes){
  if(!node.file)continue;
  const key=JSON.stringify([node.codeProjectId,node.file]);
  if(!byFile.has(key))byFile.set(key,[]);
  byFile.get(key).push(node);
}
const [largest]=[...byFile.values()].sort((a,b)=>b.length-a.length);
const scope={level:'symbols',projectId:largest[0].codeProjectId,file:largest[0].file,preset:'dependencies'};
const measure=(name,operation)=>{
  operation();const runs=[];
  for(let i=0;i<9;i++){const start=performance.now();operation();runs.push(performance.now()-start);}
  runs.sort((a,b)=>a-b);return{name,medianMs:+runs[4].toFixed(4),maxMs:+runs[8].toFixed(4)};
};
const result={nodes:graph.nodes.length,edges:graph.edges.length,file:scope.file,fileNodes:largest.length,measurements:[]};
result.measurements.push(measure('filter+aggregate+symbol-layout',()=>{
  const nodes=filterNodes(graph);aggregateCommunities(graph,nodes);
  const detail=architectureGraph(graph,nodes,scope);
  nodeGeometry(detail.nodes,[],detail.edges.slice(0,1200),null,null,graph.codeProjects);
}));
result.measurements.push(measure('projects-aggregate+layout',()=>{
  const overview=architectureGraph(graph,filterNodes(graph));
  communityGeometry(overview.groups,overview.edges);
}));
const model=new GraphModel(graph),visible=model.filter();
model.architecture(visible,scope);
result.measurements.push(measure('indexed-selection-data-no-layout',()=>{
  const nodes=model.filter();
  model.nodeIds(nodes).has(largest[0].id);
  model.incident(largest[0].id,nodes);
  model.architecture(nodes,scope);
}));
console.log(JSON.stringify(result,null,2));
