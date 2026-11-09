import assert from 'node:assert/strict';
import { solutionArchitecture } from '../web/solution-architecture.js';
import { systemProjection } from '../web/system-architecture.js';
// Одинаковые имена разных репозиториев не создают слияние; внутренние связи исчезают.
const data = { nodes: [
  { id:'a',kind:'project',label:'API',codeProjectId:'api',repositoryId:'one',repositoryName:'One' },
  { id:'b',kind:'project',label:'Worker',codeProjectId:'worker',repositoryId:'one',repositoryName:'One' },
  { id:'c',kind:'project',label:'API',codeProjectId:'api',repositoryId:'two',repositoryName:'Two' },
  { id:'db',kind:'database',label:'Database' }, { id:'k',kind:'kafka',label:'Kafka' }, { id:'pkg',kind:'package',label:'Library' }
], edges: [
  {source:'a',target:'b',relation:'calls',weight:10},
  {source:'a',target:'db',relation:'configured-database',weight:2,evidence:[{file:'api.json'}]},
  {source:'b',target:'db',relation:'configured-database',weight:3,evidence:[{file:'worker.json'}]},
  {source:'c',target:'db',relation:'configured-database',weight:1},
  {source:'a',target:'c',relation:'project-reference',weight:1},
  {source:'b',target:'k',relation:'configured-kafka',weight:1},
  {source:'a',target:'pkg',relation:'package-reference',weight:1}
] };
const before = JSON.stringify(data);
const result = solutionArchitecture(data,{id:'one',name:'One'});
assert.equal(result.nodes.length,4);
assert.equal(result.edges.length,4);
const db = result.edges.find(edge=>edge.source==='solution:one' && edge.target==='db');
assert.equal(db.weight,5); assert.equal(db.participants.length,2); assert.equal(db.evidence.length,2);
assert.equal(result.nodes.find(node=>node.id==='solution:one').memberProjects.length,2);
assert.ok(result.edges.some(edge=>edge.target==='solution:two'));
assert.ok(result.edges.every(edge=>edge.source!==edge.target));
const found=systemProjection(result,{query:'Kafka'});
assert.deepEqual(new Set(found.nodes.map(node=>node.id)),new Set(['solution:one','k']));
assert.equal(JSON.stringify(data),before);
const scoped=systemProjection(data,{projectIds:[],primaryRepositoryId:'one'});
assert.equal(solutionArchitecture({nodes:scoped.allNodes,edges:scoped.allEdges},{id:'one',name:'One'}).nodes.filter(node=>node.kind==='project').length,1);
console.log('solution architecture PASS');
