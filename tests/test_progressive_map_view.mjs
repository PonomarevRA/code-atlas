import assert from 'node:assert/strict';
import { createMapPageReader, mapDetailPage, mapOverviewCards, mapOverviewGraph, needsFullMap } from '../web/progressive-map.js';

const scope = {projectId:'fixture',snapshotId:'revision-one',algorithm:'graphistry'};
const metadata = {snapshotId:scope.snapshotId,groupingAlgorithm:scope.algorithm,nodeCount:120,edgeCount:14};
const fixture = {
  snapshotId:scope.snapshotId, metadata, codeProjects:[{id:'api',label:'API',nodeCount:5}],
  communities:[{id:'first',label:'First',nodeCount:5},{id:'second',label:'Second',nodeCount:10}],
  communityEdges:[{source:'first',target:'second',weight:1}], groupProjects:{first:{api:4},second:{domain:2}},
};
const overview = mapOverviewGraph(fixture, scope);

assert.deepEqual(overview.nodes,[]);
assert.deepEqual(overview.edges,[]);
assert.throws(() => mapOverviewGraph({...fixture,snapshotId:'wrong'},scope),/Снимок/);
assert.equal(needsFullMap({route:{level:'projects'}}),false);
assert.equal(needsFullMap({route:{level:'symbols'}}),true);
assert.equal(needsFullMap({filters:{codeProjectIds:[]}}),true);
const groups = mapOverviewCards(overview, null, {limit:1});
assert.equal(groups.cards[0].id,'second');
assert.equal(groups.nextOffset,1);
assert.deepEqual(groups.edges,[]);
assert.equal(mapOverviewCards(overview,null).edges.length,1);
const project = mapOverviewCards(overview, 'api');
assert.deepEqual(project.cards.map(row => [row.id,row.nodeCount]), [['first',4]]);
assert.deepEqual(project.edges,[],'project subset does not inherit cross-project edges');

const a = {groupId:'first',projectScope:'api',offset:0};
const b = {groupId:'second',projectScope:null,offset:0};
/** One valid exact-revision payload with a self-edge wholly inside the page. */
function detail(page) {
  return {snapshotId:scope.snapshotId, metadata, groupId:page.groupId, projectScope:page.projectScope,
    offset:page.offset, nodes:[{id:'node'}],edges:[{source:'node',target:'node'}],total:1,nextOffset:null};
}
assert.equal(mapDetailPage(detail(a),scope,a).total,1);
assert.throws(() => mapDetailPage({...detail(a),edges:[{source:'node',target:'other'}]},scope,a),/пределами/);
assert.throws(() => mapDetailPage(detail(b),scope,a),/неполную/);
assert.throws(() => mapDetailPage({...detail(a),nextOffset:0},scope,a),/неполную/);

let currentScope = scope;
const requests = [], observed = [];
const reader = createMapPageReader((path, body, {signal}) => new Promise((resolve,reject) => {
  requests.push({path,signal,resolve,reject});
}), () => currentScope, {
  pending:page => observed.push(['pending',page.groupId]),
  commit:page => observed.push(['commit',page.groupId]),
  failed:page => observed.push(['error',page.groupId]),
});
const older = reader.read(a);
const newer = reader.read(b);
assert.match(requests[0].path,/map-group\?snapshotId=revision-one&algorithm=graphistry&groupId=first&projectScope=api&offset=0&limit=48/);
assert.equal(requests[0].signal.aborted,true);
requests[1].resolve(detail(b));
assert.equal(await newer,true);
requests[0].resolve(detail(a));
assert.equal(await older,false);
assert.deepEqual(observed,[['pending','first'],['pending','second'],['commit','second']]);

const pageLost = reader.read(a);
currentScope = {...scope,snapshotId:'revision-two'};
requests[2].resolve(detail(a));
assert.equal(await pageLost,false);
assert.notDeepEqual(observed.at(-1),['commit','first']);

currentScope = scope;
const pageFailed = reader.read(a);
requests[3].reject(new Error('unavailable'));
assert.equal(await pageFailed,false);
assert.deepEqual(observed.at(-1),['error','first']);
console.log('Progressive map summary, page scoping, coverage and obsolete reply guards PASS');
