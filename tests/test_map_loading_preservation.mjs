/** Exercise actual app loading functions with deferred transport and isolated committed state. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {beginLoading, loadingSnapshot} from '../web/loading-state.js';
import {mapOverviewGraph, mapPartPath} from '../web/progressive-map.js';

const source = await readFile(new URL('../web/app.js', import.meta.url), 'utf8');
// app.js boots the browser immediately. Extract its owning function block,
// rather than replacing the implementation with a second test-only loader.
const mapCode = source.slice(source.indexOf('function commitProjectContext('), source.indexOf('/** Строит checklist'));
const drawCode = source.slice(source.indexOf('function draw('),source.indexOf('/** Переходит по архитектуре'));
assert.match(drawCode, /const progressive = system \? null : state\.progressiveMap;/,
  'draw preserves the loaded map object rather than converting it to a truthy flag');
assert.match(drawCode, /progressiveMap\.render\(progressive,/);
const jobCode = source.slice(source.indexOf('async function watchJob('), source.indexOf("$('project-form').addEventListener"));
assert(mapCode.includes('async function openProject('));
assert(jobCode.includes('await monitorJob('));

/** A deferred promise lets a request finish out of order even when it ignores cancellation. */
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}

/** A graph's IDs and snapshot stay distinguishable from every other project fixture. */
function graph(id, algorithm = 'old') {
  return {nodes: [{id: id + '-node', label: id + '()'}], edges: [],
    metadata: {snapshotId: id + '-snapshot', groupingAlgorithm: algorithm}};
}

/** Supply browser collaborators while executing the actual staged-load code. */
function harness({empty = false} = {}) {
  const projects = ['a', 'b', 'c'].map(id => ({id, name: id.toUpperCase(), path: '/' + id, snapshotId: id + '-snapshot'}));
  const original = graph('a');
  const state = {projects, project: empty ? null : projects[0], graph: empty ? {nodes: [], edges: []} : original,
    nodes: new Map(empty ? [] : [[original.nodes[0].id, original.nodes[0]]]), selected: empty ? null : 'a-node',
    algorithm: 'old', loadedAlgorithm: 'old', algorithms: [{id: 'old'}, {id: 'one'}, {id: 'two'}],
    revision: 7, answerRevision: 3, loading: false, pendingMapLoad: null,
    ingress: {revision: 0, data: {old: true}}, comparison: null};
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) elements.set(id, {value: id === 'view-mode' ? 'architecture' : id === 'algorithm' ? 'old' : '',
      options: [], replaceChildren(...children) {this.options = children;}});
    return elements.get(id);
  };
  const requests = [], effects = [], messages = [];
  /** Every callback must observe either a complete old scope or a complete new scope. */
  function effect(name) {
    if (state.project?.snapshotId) assert.equal(state.graph.metadata?.snapshotId, state.project.id + '-snapshot', name);
    else if(state.project) assert.equal(state.graph.metadata?.snapshotId,undefined,name);
    effects.push(name);
  }
  const host = {state, $, mapLoadRevision: 0, AbortController, mapOverviewGraph, mapPartPath,
    progressiveMap:{hide:()=>{}},
    model: {setGraph: () => effect('model')},
    healthRefresh: {watch: () => effect('health')}, navigationHistory: {checkpoint: () => effect('checkpoint'), commit: () => effect('history')},
    wikiViewer: {close: () => effect('wiki.close')}, mapBlocks: {reset: () => effect('blocks.reset')},
    systemView: {reset: () => effect('system.reset')}, ingressController: {catalog: {reset: () => effect('ingress.reset')}},
    regexSearch: {cancel: () => effect('regex.cancel')}, agentActivity: {sync: () => effect('agent.sync')},
    updateProjectDataMaps: () => effect('data.sync'), relatedRepositories: {resetForPrimary: () => effect('related.reset')},
    clearFilters: () => effect('filters.clear'), clearComparison: () => effect('comparison.clear'),
    Option: class {constructor(label, value) {this.label = label; this.value = value;}},
    el: (tag, text) => ({tag, text}), localStorage: {setItem: () => effect('persist')},
    loadIngress: () => effect('ingress.load'), populateFilter: () => {}, renderCodeProjects: () => effect('projects.render'),
    status: (message, error) => messages.push({message, error}), draw: () => effect('draw'),
    controls: () => effects.push(state.loading ? 'controls.empty-busy' : 'controls.interactive'),
    isSystemMode: mode => mode === 'system',
    api: (path, body, options) => {
      const request = {...deferred(), path, signal: options.signal}; requests.push(request); return request.promise;
    },
  };
  const names = Object.keys(host);
  const factory = new Function(...names, 'let discoveryUserChoice = false;\n' + mapCode + '\nreturn {openProject};');
  const {openProject} = factory(...names.map(name => host[name]));
  return {state, original, requests, effects, messages, openProject, $};
}

/** Committed references and selection must survive every unsuccessful pending operation. */
function unchanged(h, references) {
  assert.equal(h.state.project, references.project);
  assert.equal(h.state.graph, references.graph);
  assert.equal(h.state.nodes, references.nodes);
  assert.equal(h.state.selected, references.selected);
  assert.equal(h.state.revision, references.revision);
}

/** Capture references, not copies, so clearing/rebuilding the old graph cannot pass unnoticed. */
function committed(h) {
  const {project, graph, nodes, selected, revision} = h.state;
  return {project, graph, nodes, selected, revision};
}

for (const target of ['a', 'b']) {
  const h = harness(), before = committed(h);
  const loading = h.openProject(target, true);
  unchanged(h, before);
  assert.equal(h.state.loading, false);
  assert.deepEqual(h.effects, ['controls.interactive']);
  assert.equal(h.state.pendingMapLoad.project.id, target);
  // A user can read the selected old node under its original project and snapshot.
  assert.equal(h.state.nodes.get(h.state.selected).label, 'a()');
  h.requests[0].resolve(graph(target));
  assert.equal(await loading, true);
  assert.equal(h.state.project.id, target);
  assert.equal(h.state.graph.metadata.snapshotId, target + '-snapshot');
  assert.equal(h.state.pendingMapLoad, null);
}

for (const failure of ['network', 'shape']) {
  const h = harness(), before = committed(h), loading = h.openProject('b');
  if (failure === 'network') h.requests[0].reject(new Error('fixture offline'));
  else h.requests[0].resolve({nodes: null, edges: []});
  assert.equal(await loading, false);
  unchanged(h, before);
  assert(h.messages.at(-1).error);
  assert(h.messages.at(-1).message.includes('Открытая карта сохранена'));
  assert.equal(h.$('projects').value, 'a');
  assert.equal(h.state.requestedProjectId, 'b', 'a still-building target can open when its snapshot is ready');
}

{
  const h = harness(), before = committed(h), signal = new AbortController();
  const loading = h.openProject('b', false, {signal: signal.signal});
  signal.abort();
  assert.equal(h.requests[0].signal.aborted, true);
  h.requests[0].resolve(graph('b'));
  assert.equal(await loading, false);
  unchanged(h, before);
  assert.equal(h.state.requestedProjectId, 'a', 'cancelled navigation no longer requests the other project');
  assert.equal(h.messages.at(-1).message, 'Загрузка отменена. Открытая карта остаётся доступной.');
}

{
  const h = harness({empty: true}), signal = new AbortController();
  const loading = h.openProject('b', false, {signal: signal.signal});
  signal.abort(); h.requests[0].resolve(graph('b'));
  assert.equal(await loading, false);
  assert.equal(h.state.project, null);
  assert.equal(h.messages.at(-1).message, 'Загрузка карты отменена. Выберите проект, чтобы продолжить.');
}

{
  const h = harness(), first = h.openProject('b'), second = h.openProject('c');
  assert.equal(h.requests[0].signal.aborted, true);
  h.requests[1].resolve(graph('c')); assert.equal(await second, true);
  const committedMessage = h.messages.at(-1);
  h.requests[0].resolve(graph('b')); assert.equal(await first, false);
  assert.equal(h.state.project.id, 'c');
  assert.equal(h.$('projects').value, 'c');
  assert.equal(h.messages.at(-1), committedMessage, 'a stale abort cannot replace the accepted newer status');
}

{
  const h = harness(), first = h.openProject('a', true, {algorithm: 'one'});
  assert.equal(h.state.algorithm, 'old');
  const second = h.openProject('a', true, {algorithm: 'two'});
  const newerMessage = h.messages.at(-1);
  h.requests[0].resolve(graph('a', 'one')); assert.equal(await first, false);
  assert.equal(h.state.pendingMapLoad.algorithm, 'two');
  assert.equal(h.messages.at(-1), newerMessage, 'a superseded abort cannot replace the pending newer status');
  h.requests[1].resolve(graph('a', 'two')); assert.equal(await second, true);
  assert.equal(h.state.algorithm, 'two'); assert.equal(h.state.loadedAlgorithm, 'two');
  assert(h.requests[0].path.includes('algorithm=one') && h.requests[1].path.includes('algorithm=two'));
}

{
  const h = harness({empty: true}), loading = h.openProject('b');
  assert.equal(h.state.loading, true);
  assert.equal(h.state.project, null, 'an initial pending target is not a committed project');
  h.requests[0].resolve(graph('b')); assert.equal(await loading, true);
  assert.equal(h.state.loading, false);
}

{
  const h = harness();
  const loading = h.openProject('b', false, {viewMode: 'system'});
  assert.equal(h.requests[0].path,'/projects');
  h.requests[0].resolve({projects:h.state.projects.map(x=>({...x,snapshotAvailable:true}))});
  assert.equal(await loading, true);
  assert.equal(h.requests.length, 1, 'system projection checks current metadata but does not eagerly fetch the full code graph');
  assert.equal(h.state.project.id, 'b'); assert.equal(h.state.graph.metadata.snapshotId, 'b-snapshot');
}

{
  const h=harness(),before=committed(h),loading=h.openProject('b',false,{viewMode:'system'});
  h.requests[0].resolve({projects:h.state.projects.map(x=>({...x,snapshotAvailable:false}))});
  await Promise.resolve();
  assert(h.requests[1].path.includes('/b/graph?'),'missing current snapshot reaches the repair-enabled graph endpoint');
  unchanged(h,before);h.requests[1].resolve(graph('b'));assert.equal(await loading,true);
}

{
  const h=harness(),before=committed(h),loading=h.openProject('b',false,{viewMode:'system'});
  h.requests[0].resolve({projects:h.state.projects.filter(x=>x.id!=='b')});
  assert.equal(await loading,false);unchanged(h,before);
  assert.match(h.messages.at(-1).message,/Запись карты больше недоступна/);
}

{
  const h=harness(),first=h.openProject('b',false,{viewMode:'system'}),second=h.openProject('c');
  h.requests[1].resolve(graph('c'));assert.equal(await second,true);
  h.requests[0].resolve({projects:h.state.projects.map(x=>({...x,snapshotAvailable:false}))});
  assert.equal(await first,false);assert.equal(h.state.project.id,'c');assert.equal(h.requests.length,2);
}

/** Execute actual logical-job lifetime code against the shared loading registry. */
function jobs(requestedProjectId = null) {
  const monitors = [], state = {project: {id: 'other'}};
  state.requestedProjectId = requestedProjectId;
  const opened = [];
  const openedOptions = [];
  const host = {state, beginLoading, controls: () => {}, status: () => {}, api: async () => ({}),
    refreshProjects: async () => {}, openProject: async (id,_preserve,options) => {opened.push(id);openedOptions.push(options); return true;},
    monitorJob: (_job, callbacks) => {const pending = {...deferred(), callbacks}; monitors.push(pending); return pending.promise;}};
  const names = Object.keys(host);
  const watchJob = new Function(...names, 'let jobLoadingRelease = null; let jobLoadingRevision = 0;\n' + jobCode + '\nreturn watchJob;')(...names.map(name => host[name]));
  return {state, monitors, watchJob, opened,openedOptions};
}

{
  const h = jobs(), pending = h.watchJob({id: 'job'});
  assert.equal(loadingSnapshot().count, 1);
  await h.monitors[0].callbacks.onState({status: 'running'});
  h.monitors[0].callbacks.onTransientError(new Error('offline'), 3000);
  assert.equal(loadingSnapshot().count, 1, 'the server job stays visible between status polls and reconnects');
  await h.monitors[0].callbacks.onState({status: 'failed', error: 'fixture failure'});
  h.monitors[0].resolve(); await pending;
  assert.equal(loadingSnapshot().count, 0);
}

{
  const h = jobs(), first = h.watchJob({id: 'first'}), second = h.watchJob({id: 'second'});
  assert.equal(loadingSnapshot().count, 1, 'replacement releases exactly the former logical job');
  assert.equal(h.monitors[0].callbacks.isCurrent('first'), false);
  h.monitors[0].resolve(); await first;
  assert.equal(loadingSnapshot().count, 1, 'stale finally cannot hide the new logical job');
  await h.monitors[1].callbacks.onState({status: 'completed', projectId: 'different'});
  h.monitors[1].resolve(); await second;
  assert.equal(loadingSnapshot().count, 0);
}

{
  const h = jobs(), pending = h.watchJob({id: 'throws'});
  h.monitors[0].reject(new Error('fixture callback failure'));
  await assert.rejects(pending, /fixture callback failure/);
  assert.equal(loadingSnapshot().count, 0);
}

for (const desired of ['new-project', 'another-project']) {
  const h = jobs(desired), pending = h.watchJob({id: 'new-build'});
  await h.monitors[0].callbacks.onState({status: 'completed', projectId: 'new-project'});
  h.monitors[0].resolve(); await pending;
  assert.deepEqual(h.opened, desired === 'new-project' ? ['new-project'] : [],
    'a completed first build opens only while it remains the requested project');
  assert.equal(loadingSnapshot().count, 0);
}

for(const desired of ['new-project','another-project',null]) {
  const h=jobs(desired),pending=h.watchJob({id:'repair-job',recovery:true});
  await h.monitors[0].callbacks.onState({status:'completed',projectId:'new-project'});
  h.monitors[0].resolve();await pending;
  assert.deepEqual(h.opened,desired==='new-project'?['new-project']:[],'recovery completion respects the latest requested selection');
}

{
  const h=jobs('new-project'),job={id:'repair-mode',recovery:true,recoveryViewMode:'architecture',recoveryAlgorithm:'one'};
  const pending=h.watchJob(job);
  Object.assign(h.state.job,{recoveryViewMode:'system',recoveryAlgorithm:'two'});
  await h.monitors[0].callbacks.onState({status:'completed',projectId:'new-project'});
  h.monitors[0].resolve();await pending;
  assert.deepEqual(h.openedOptions,[{viewMode:'system',algorithm:'two'}],'latest requested recovery mode/algorithm survive publication');
}

console.log('Map loading: same/cross-project retention, failure, cancellation, supersession, algorithm fencing, initial/system scope and logical-job lifetime PASS');

/** A registered failed first project stays selected without fabricating a snapshot or starting extraction. */
{
  const h=harness({empty:true});
  const target=h.state.projects[1];target.snapshotId=null;target.snapshotAvailable=false;target.status='failed';target.error='AST extraction failed';
  const result=await h.openProject('b');
  assert.equal(result,true);assert.equal(h.state.project,target);assert.equal(h.$('projects').value,'b');
  assert.equal(h.state.graph.metadata?.snapshotId,undefined);assert.deepEqual(h.state.graph.nodes,[]);
  assert.equal(h.requests.length,0,'opening a failed registration is read-only and does not auto-rebuild');
}

{
  const h = harness(), before = committed(h);
  const loading = h.openProject('b', false, {algorithm:'graphistry'});
  unchanged(h,before);
  assert.match(h.requests[0].path, /\/b\/map-overview\?snapshotId=b-snapshot&algorithm=graphistry/);
  h.requests[0].resolve({snapshotId:'b-snapshot',communities:[],communityEdges:[],codeProjects:[],groupProjects:{},
    metadata:{snapshotId:'b-snapshot',groupingAlgorithm:'graphistry',nodeCount:123,edgeCount:456}});
  assert.equal(await loading,true);
  assert.equal(h.requests.length,1,'compact overview never triggers full graph loading');
  assert.equal(h.state.graph.nodes.length,0);
  assert.equal(h.state.progressiveMap.projectId,'b');
  assert(!h.effects.includes('ingress.load'),'catalog capture does not block the compact map');
}

{
  const h = harness(), loading = h.openProject('b', false, {algorithm:'graphistry',full:true});
  assert.match(h.requests[0].path, /\/b\/graph\?algorithm=graphistry/);
  h.requests[0].resolve(graph('b','graphistry'));
  assert.equal(await loading,true);
  assert.equal(h.state.progressiveMap,null);
}
