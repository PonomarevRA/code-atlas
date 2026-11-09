import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../web/architecture-export.js', import.meta.url), 'utf8');
const { architectureExport } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const graph = { nodes: [
  { id: 'owner', kind: 'project', label: 'App "quoted"\n!script /private/secret', password: 'DO_NOT_EXPORT' },
  { id: 'topic', kind: 'kafka', resourceKind: 'topic', label: 'assets', ownerId: 'owner', status: 'candidate',
    proof: { basis: 'library-call', sourceKind: 'working-tree', rawSource: 'SECRET_SOURCE' },
    configurationCandidates: [{ name: 'assets', file: 'appsettings.json', line: 3, connectionString: 'SECRET_CONNECTION' }] },
], edges: [{ id: 'fact:1', factId: 'fact:1', source: 'owner', target: 'topic', operation: 'publishes',
  evidence: [{ file: 'src/a.cs', line: 9, sourceHash: 'abc', rawNodeId: 'raw', rawSource: 'SECRET_SQL' }] }],
  repositories: [{ id: 'project', name: 'Repo', snapshotId: 'snapshot' }],
  exploration: { visitedNodeIds: ['raw'], appliedFactIds: ['fact:1'], addedNodes: 1, limited: false },
  coverage: { readFiles: 4, complete: false, perRepository: [{repositoryId: 'project', readFiles: 4}], rawSource: 'SECRET_COVERAGE' } };
const options = { projectId: 'project', snapshotId: 'snapshot', pinnedIds: ['topic'], positions: { topic: { x: 14, y: 17 } } };
const json = architectureExport(graph, { ...options, format: 'json' });
const parsed = JSON.parse(json.text);
assert.equal(parsed.schemaVersion, 'atlas-architecture-v1');
assert.equal(parsed.repositories[0].snapshotId, 'snapshot');
assert.deepEqual(parsed.exploration.appliedFactIds, ['fact:1']);
assert.equal(parsed.coverage.perRepository[0].readFiles, 4);
assert.deepEqual(parsed.pinnedIds, ['topic']);
assert.deepEqual(parsed.positions, { topic: { x: 14, y: 17 } });
assert.equal(parsed.edges[0].evidence[0].rawNodeId, 'raw');
assert.equal(parsed.nodes.find(n => n.id === 'topic').proof.sourceKind, 'working-tree');
for (const secret of ['DO_NOT_EXPORT', 'SECRET_SOURCE', 'SECRET_CONNECTION', 'SECRET_SQL', 'SECRET_COVERAGE']) assert.ok(!json.text.includes(secret));
assert.equal(json.text, architectureExport({ ...graph, nodes: [...graph.nodes].reverse() }, { ...options, format: 'json' }).text);
const dsl = architectureExport(graph, { ...options, format: 'structurizr' });
assert.ok(dsl.text.includes('!impliedRelationships false'));
assert.ok(dsl.text.includes('element "assets" "kafka"'));
assert.ok(dsl.text.includes('custom a'));
assert.ok(!dsl.text.split('\n').some(line => /^\s*!script/.test(line)));
assert.ok(dsl.losses.length >= 3);
assert.equal(dsl.text, architectureExport({ ...graph, nodes: [...graph.nodes].reverse() }, { ...options, format: 'structurizr' }).text);
assert.throws(() => architectureExport({ nodes: [graph.nodes[0], graph.nodes[0]], edges: [] }, { ...options, format: 'json' }), /duplicated/);
assert.throws(() => architectureExport({ nodes: [], edges: graph.edges }, { ...options, format: 'json' }), /dangling/);
assert.throws(() => architectureExport({ nodes: Array(4097).fill({}), edges: [] }, { ...options, format: 'json' }), /limits/);
assert.throws(() => architectureExport(graph, { format: 'plantuml' }), /Choose/);
const unsafe = architectureExport({ nodes: [{ id: 'n', label: 'https://u:p@host/path', file: '/private/token', rawSource: 'secret' }], edges: [] }, { ...options, format: 'json' });
assert.ok(!unsafe.text.includes('https:') && !unsafe.text.includes('/private'));

for (const label of ['Host=localhost;Database=demo', 'SELECT secret FROM accounts']) {
  const result = architectureExport({nodes:[{id:'n',label}],edges:[]},{...options,format:'json'});
  assert.ok(!result.text.includes(label));
}
assert.throws(() => architectureExport(graph,{format:'json'}), /identity/);
const many = {nodes:[],edges:[]};
for (let i=0;i<4096;i++) many.nodes.push({id:'node'+i,label:'a'.repeat(500),logicalName:'b'.repeat(500),host:'c'.repeat(500)});
assert.throws(() => architectureExport(many,{...options,format:'json'}), /4MiB/);
const exotic = architectureExport({nodes:[{id:'__proto__',label:'safe'}],edges:[]},
 {...options,format:'json',pinnedIds:['__proto__'],positions:JSON.parse('{"__proto__":{"x":1,"y":2}}')});
assert.deepEqual(JSON.parse(exotic.text).positions['__proto__'],{x:1,y:2});

const repeated = architectureExport({...graph,edges:[graph.edges[0],{...graph.edges[0],id:'fact:2',factId:'fact:2'}]}, {...options,format:'structurizr'});
assert.equal(repeated.text.match(/ -> /g).length,1);
assert.ok(repeated.text.includes('"atlas.factCount" "2"'));
assert.ok(repeated.text.includes('fact:1') && repeated.text.includes('fact:2'));
const aggregated = architectureExport({...graph,edges:[{source:'owner',target:'topic',relation:'publishes',factIds:['f2','f1'],operations:['publishes'],statuses:['candidate'],evidence:[]}]}, {...options,format:'json'});
assert.deepEqual(JSON.parse(aggregated.text).edges[0].factIds,['f1','f2']);
assert.deepEqual(JSON.parse(aggregated.text).exploration.appliedFactIds,['f1','f2']);

const projected = architectureExport({...graph,projection:{scale:'repositories',displayNodeIds:['solution:r'],rawSource:'secret'}},
 {...options,format:'json',pinnedIds:['solution:r'],positions:{'solution:r':{x:33,y:44}}});
assert.deepEqual(JSON.parse(projected.text).projection,{scale:'repositories',displayNodeIds:['solution:r']});
assert.deepEqual(JSON.parse(projected.text).positions,{'solution:r':{x:33,y:44}});
assert.deepEqual(JSON.parse(projected.text).pinnedIds,['solution:r']);



for (const length of [501,4096]) {
 const rawId = 'x'.repeat(length);
 const result = architectureExport({...graph,exploration:{visitedNodeIds:[rawId],processedNodeIds:[rawId],unprocessedNodeIds:[]}}, {...options,format:'json'});
 assert.deepEqual(JSON.parse(result.text).exploration.visitedNodeIds,[rawId]);
 assert.deepEqual(JSON.parse(result.text).exploration.processedNodeIds,[rawId]);
}
const protocols = {nodes:[{id:'http',kind:'http',label:'api.example',scheme:'http'},{id:'https',kind:'http',label:'api.example',scheme:'https'}],edges:[]};
assert.equal(JSON.parse(architectureExport(protocols,{...options,format:'json'}).text).nodes.length,2);
assert.equal((architectureExport(protocols,{...options,format:'structurizr'}).text.match(/ = element /g)||[]).length,2);


const nullable = architectureExport({nodes:[{id:'db',kind:'database',label:'Main',resourceKind:'table',scheme:null,ownerId:'',status:'   ',runtimeSelectionKnown:false}],edges:[]}, {...options,format:'structurizr'});
assert.ok(!nullable.text.includes('"atlas.scheme"'));
assert.ok(!nullable.text.includes('"atlas.ownerId"'));
assert.ok(!nullable.text.includes('"atlas.status"'));
assert.ok(nullable.text.includes('"atlas.runtimeSelectionKnown" "false"'));
assert.ok(!/"atlas\.[^"]+" ""/.test(nullable.text));


const sameLabels = {nodes:[{id:'table',kind:'database',resourceKind:'table',label:'orders'},
 {id:'topic',kind:'kafka',resourceKind:'topic',label:'orders'},
 {id:'project1',kind:'project',label:'Service'},{id:'project2',kind:'project',label:'Service'}],edges:[]};
const uniqueDsl = architectureExport(sameLabels,{...options,format:'structurizr'}).text;
const uniqueNames = [...uniqueDsl.matchAll(/ = element "([^"]+)"/g)].map(m=>m[1]);
assert.equal(new Set(uniqueNames).size,4);
assert.equal((uniqueDsl.match(/"atlas.label" "orders"/g)||[]).length,2);
assert.equal((uniqueDsl.match(/"atlas.label" "Service"/g)||[]).length,2);
const collisionLabel = uniqueNames.find(name=>name.startsWith('orders ['));
const adversarial = {...sameLabels,nodes:[...sameLabels.nodes,{id:'natural-collision',kind:'project',label:collisionLabel}]};
const collisionDsl = architectureExport(adversarial,{...options,format:'structurizr'}).text;
const collisionNames = [...collisionDsl.matchAll(/ = element "([^"]+)"/g)].map(m=>m[1]);
assert.equal(new Set(collisionNames).size,5);
assert.equal(collisionDsl,architectureExport({...adversarial,nodes:[...adversarial.nodes].reverse()},{...options,format:'structurizr'}).text);
assert.deepEqual(JSON.parse(architectureExport(sameLabels,{...options,format:'json'}).text).nodes.map(n=>n.label),['Service','Service','orders','orders']);
console.log('Architecture export PASS');
