/** Browser navigation restores bounded map metadata; source and graph never enter URLs or history state. */
import assert from 'node:assert/strict';
import { NavigationHistory, hasNavigationRoute, navigationInteraction, navigationSnapshot, normalizeRoute, pathDetailToken, readRoute, resolveNavigation, writeRoute } from '../web/navigation-history.js';

/** Browser facade follows real push/replace semantics and delivers popstate with its own state/URL. */
class Browser {
  constructor(href = 'http://localhost/?unrelated=keep#map') {
    this.location = { href };
    this.entries = [{ state: null, href }];
    this.index = 0;
    this.listeners = new Map();
    this.pushes = 0;
    const owner = this;
    this.history = {
      get state() { return owner.entries[owner.index].state; },
      go(offset) { owner.lastTraversal = owner.go(offset); },
      pushState(state, _, url) {
        owner.pushes++;
        owner.entries.splice(owner.index + 1);
        owner.entries.push({ state: structuredClone(state), href: url });
        owner.index++;
        owner.location.href = url;
      },
      replaceState(state, _, url) {
        owner.entries[owner.index] = { state: structuredClone(state), href: url };
        owner.location.href = url;
      },
    };
  }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  async go(offset) {
    this.index += offset;
    const entry = this.entries[this.index];
    this.location.href = entry.href;
    await this.listeners.get('popstate')({ state: structuredClone(entry.state) });
  }
}

assert.equal(normalizeRoute(null).view, 'architecture');
assert.equal(navigationSnapshot(null).route.repo, null);
assert.equal(navigationSnapshot({ route: null }).route.level, 'projects');
assert.equal(normalizeRoute({ repo: 'r', view: 'unknown', level: 'bogus', page: '-1', display: 'invalid' }).page, 0);
assert.equal(normalizeRoute({ repo: 'r', view: 'solution', level: 'symbols', project: 'p', page: '2.5' }).view, 'system');
assert.equal(normalizeRoute({ repo: 'r', page: 'Infinity' }).page, 0);
assert.equal(normalizeRoute({ repo: 'r', page: '100001' }).page, 100000);
assert.equal(normalizeRoute({ repo: 'r', level: 'files' }).level, 'projects');
const route = { repo: 'repo', view: 'architecture', level: 'symbols', project: 'project', file: 'src/a file.cs', page: 3, node: 'raw:<&>?', context: 'other-raw', isolate: 'raw:<&>?', display: 'flow' };
const href = writeRoute(route, 'http://localhost/?unrelated=keep&view=garbage#map');
assert.deepEqual(readRoute(href), normalizeRoute(route));
assert.equal(new URL(href).searchParams.get('unrelated'), 'keep');
assert.equal(new URL(href).hash, '#map');
assert.equal(writeRoute(route, href), href, 'canonical order stays deterministic after repeated writes');
assert.equal(hasNavigationRoute('http://localhost/?unrelated=keep'), false);
assert.equal(hasNavigationRoute(href), true);
const select = { tagName: 'SELECT', type: 'select-one', closest: selector => selector.includes("data-navigation-ignore") ? null : select };
const checkbox = { tagName: 'INPUT', type: 'checkbox', closest: selector => selector.includes("data-navigation-ignore") ? null : checkbox };
const search = { tagName: 'INPUT', type: 'search', closest: selector => selector.includes("data-navigation-ignore") ? null : search };
assert.equal(navigationInteraction({ type: 'input', target: select }), null);
assert.equal(navigationInteraction({ type: 'input', target: checkbox }), null);
assert.equal(navigationInteraction({ type: 'change', target: select }), 'push');
assert.equal(navigationInteraction({ type: 'change', target: checkbox }), 'push');
assert.equal(navigationInteraction({ type: 'input', target: search }), 'replace');
assert.equal(navigationInteraction({ type: 'keydown', key: ' ', target: search }), null);
const compact = navigationSnapshot({ route, graph: { nodes: ['private'] }, source: 'private source', camera: { x: 5, y: 8, scale: 0.9 }, pathExpanded: Array.from({ length: 300 }, (_, i) => `raw${i}`) });
assert.equal(compact.pathExpanded.length, 300);
assert.equal(compact.source, undefined);
assert.equal(compact.graph, undefined);
assert.equal(navigationSnapshot({ camera: { x: 5, y: Infinity, scale: 1 } }).camera, null);
assert.equal(pathDetailToken(new Set(['b', 'a']), []), pathDetailToken(['a', 'b'], []));
assert.notEqual(pathDetailToken(['a'], []), pathDetailToken(['a', 'b'], []));
assert.equal(pathDetailToken([], []), null);
// Полностью раскрытый путь больше обычной карты (240) сохраняется в истории целиком.
const expanded600 = Array.from({ length: 600 }, (_, index) => `method-${index}`);
assert.equal(navigationSnapshot({ pathExpanded: expanded600 }).pathExpanded.length, 600);
assert.notEqual(pathDetailToken(expanded600, []), pathDetailToken(expanded600.filter(id => id !== 'method-500'), []));
const largeBrowser = new Browser();
let largeLive = navigationSnapshot({ route: { repo: 'repo' } });
const largeHistory = new NavigationHistory({ browser: largeBrowser, getSnapshot: () => largeLive, restore: async snapshot => { largeLive = snapshot; } });
await largeHistory.start({ repo: 'repo' });
largeHistory.checkpoint();
largeLive = navigationSnapshot({ route: { repo: 'repo', path: 'method-0', detail: pathDetailToken(expanded600, []) }, pathExpanded: expanded600 });
largeHistory.commit();
largeHistory.checkpoint();
largeLive = navigationSnapshot({ ...largeLive, route: { ...largeLive.route, node: 'method-500' } });
largeHistory.commit();
await largeBrowser.go(-1);
assert.equal(largeLive.pathExpanded.length, 600);
await largeBrowser.go(1);
assert.equal(largeLive.pathExpanded.length, 600);
await largeHistory.start(); // Тот же протокол history.state при перезагрузке вкладки.
assert.equal(largeLive.pathExpanded.length, 600);

const project = { id: 'repo' };
const nodes = [{ id: 'raw', codeProjectId: 'project', file: 'src/a.cs' }, { id: 'other', codeProjectId: 'project', file: 'src/a.cs' }];
const requested = navigationSnapshot({ route: { repo: 'repo', level: 'symbols', project: 'project', file: 'src/a.cs', node: 'raw', context: 'other', path: 'raw', detail: pathDetailToken(['other'], []) }, snapshotId: 'old', camera: { x: 2, y: 4, scale: 0.9 }, pathExpanded: ['other'] });
assert.deepEqual(resolveNavigation(requested, { projects: [project] }).messages, [], 'catalog-only phase must not report missing graph symbols');
const stale = resolveNavigation(requested, { projects: [project], nodes, snapshotId: 'new' });
assert.equal(stale.snapshot.route.node, 'raw');
assert.equal(stale.snapshot.route.context, null);
assert.deepEqual(stale.snapshot.pathExpanded, []);
assert.equal(stale.snapshot.camera, null);
assert.ok(stale.messages[0].includes('Снимок изменился'));
const noFile = resolveNavigation({ route: { ...route, file: 'missing.cs' } }, { projects: [project], nodes });
assert.equal(noFile.snapshot.route.level, 'files');
assert.equal(noFile.snapshot.route.node, null);
const noProject = resolveNavigation({ route: { ...route, project: 'missing' } }, { projects: [project], nodes });
assert.equal(noProject.snapshot.route.level, 'projects');
const noRepository = resolveNavigation({ route: { repo: 'gone' } }, { projects: [project], nodes });
assert.equal(noRepository.snapshot.route.repo, null);
assert.equal(resolveNavigation({route:{repo:'repo',isolate:'other'}},{projects:[project],nodes}).snapshot.route.isolate,'other');
assert.equal(resolveNavigation({route:{repo:'repo',isolate:'missing'}},{projects:[project],nodes}).snapshot.route.isolate,null);
assert.equal(normalizeRoute({isolate:'raw'}).isolate,null);
const standaloneDetail = resolveNavigation({ route: { repo: 'repo', path: 'raw', detail: pathDetailToken(['other'], []) } }, { projects: [project], nodes });
assert.equal(standaloneDetail.snapshot.route.detail, null);
assert.ok(standaloneDetail.messages[0].includes('истории этой вкладки'));
const mismatchedDetail = resolveNavigation({ route: { repo: 'repo', path: 'raw', detail: '00000000' }, pathExpanded: ['other'] }, { projects: [project], nodes });
assert.deepEqual(mismatchedDetail.snapshot.pathExpanded, []);
assert.equal(mismatchedDetail.snapshot.route.detail, null);
assert.ok(mismatchedDetail.messages[0].includes('не соответствуют адресу'));

const browser = new Browser();
let live = navigationSnapshot({ route: { repo: 'repo' }, snapshotId: 's', camera: { x: 0, y: 0, scale: 1 } });
const restored = [];
const history = new NavigationHistory({ browser, getSnapshot: () => live, restore: async snapshot => { live = snapshot; restored.push(snapshot); } });
await history.start({ repo: 'repo' });
assert.equal(browser.pushes, 0);
assert.equal(readRoute(browser.location.href).repo, 'repo');

/** Every meaningful map transition records its prior camera, even when a select already changed DOM value. */
function navigate(patch) {
  history.checkpoint();
  live = navigationSnapshot({ ...live, route: { ...live.route, ...patch } });
  history.commit();
}
navigate({ level: 'files', project: 'project' });
navigate({ level: 'symbols', file: 'src/a.cs', page: 2 });
live.camera = { x: 71, y: -42, scale: 0.8 };
navigate({ node: 'raw', context: 'raw' });
assert.equal(browser.pushes, 3);
await browser.go(-1);
assert.equal(live.route.node, null);
assert.equal(live.route.level, 'symbols');
assert.deepEqual(live.camera, { x: 71, y: -42, scale: 0.8 });
await browser.go(-1);
assert.equal(live.route.level, 'files');
await browser.go(1);
assert.equal(live.route.page, 2);
await browser.go(1);
assert.equal(live.route.context, 'raw');

// Expert views retain distinct displays; Code legacy routes normalize to one reading surface.
assert.equal(normalizeRoute({ repo: 'repo', view: 'architecture', display: 'topology' }).display, 'flow');
navigate({ view: 'groups', display: 'topology' });
// Real selects emit input followed by change; input must not replace the prior topology entry.
const beforeDisplay = browser.pushes;
live.route.display = 'flow';
for (const type of ['input', 'change']) {
  const interaction = navigationInteraction({ type, target: select });
  if (interaction) {
    history.checkpoint();
    history.scheduleCommit({ replace: interaction === 'replace' });
  }
}
await new Promise(resolve => setTimeout(resolve, 5));
assert.equal(browser.pushes, beforeDisplay + 1);
await browser.go(-1);
assert.equal(live.route.level, 'symbols');
assert.equal(live.route.display, 'topology');
await browser.go(1);
assert.equal(live.route.display, 'flow');
// Continue the other view transitions from the original display for their own assertions.
live.route.view = 'architecture';
live.route.display = 'topology';
history.commit({ replace: true });

live.route.view = 'system'; // Browser change event fires after select.value changed.
history.checkpoint();
history.commit();
await browser.go(-1);
assert.equal(live.route.view, 'architecture', 'changed select value must not overwrite previous route');
await browser.go(1);
navigate({ scale: 'projects', display: 'flow', page: 4 });
await browser.go(-1);
assert.equal(live.route.scale, 'repositories');
assert.equal(live.route.display, 'topology');
await browser.go(1);
assert.equal(live.route.scale, 'projects');
assert.equal(live.route.display, 'flow');

const beforeCamera = browser.pushes;
const beforePanURL = browser.location.href;
live.camera = { x: 135, y: -42, scale: 0.8 };
history.scheduleCamera();
history.scheduleCamera();
assert.deepEqual(browser.history.state.camera, live.camera, 'native Back can happen immediately after an arrow pan');
await browser.go(-1);
await browser.go(1);
assert.deepEqual(live.camera, { x: 135, y: -42, scale: 0.8 }, 'immediate Back/Forward restores the last keyboard position');
live.filters.search = 'typed phrase';
history.scheduleCommit({ replace: true });
history.commit(); // A draw listener may run before the native input dispatch has finished.
await new Promise(resolve => setTimeout(resolve, 5));
assert.equal(browser.pushes, beforeCamera);
assert.equal(browser.location.href, beforePanURL);
assert.deepEqual(browser.history.state.camera, live.camera);

navigate({ view: 'architecture', path: 'raw' });
navigate({ isolate:'raw' });
assert.equal(readRoute(browser.location.href).isolate,'raw');
navigate({ node:'other' });
assert.equal(live.route.isolate,'raw','selecting a child preserves the pinned root');
await browser.go(-1);
assert.equal(live.route.node,'raw');
await browser.go(-1);
assert.equal(live.route.isolate,null);
await browser.go(1);
assert.equal(live.route.isolate,'raw');
const beforeExpand = browser.location.href;
history.checkpoint();
live.pathExpanded = ['other'];
live.route.detail = pathDetailToken(live.pathExpanded, live.pathCollapsed);
history.commit();
assert.notEqual(browser.location.href, beforeExpand, 'expansion changes address without exposing its whole ID set');
assert.equal(new URL(browser.location.href).searchParams.get('detail').length, 8);
await browser.go(-1);
assert.deepEqual(live.pathExpanded, []);
await browser.go(1);
assert.deepEqual(live.pathExpanded, ['other']);

const direct = new Browser(href);
direct.entries[0].state = { codeAtlasNavigation: 1, route: null, camera: { x: 0, y: 0, scale: 1 } };
let directLive;
const directHistory = new NavigationHistory({ browser: direct, getSnapshot: () => directLive, restore: async snapshot => { directLive = snapshot; } });
await directHistory.start({ repo: 'wrong' });
assert.equal(directLive.route.repo, 'repo', 'address overrides malformed or stale stored route');
assert.equal(directLive.route.file, 'src/a file.cs');

// Rapid popstate: a late graph/path result cannot resurrect the repository of an older Back.
const racingBrowser = new Browser();
let racingLive = navigationSnapshot({ route: { repo: 'initial' } });
const requests = [];
const racing = new NavigationHistory({ browser: racingBrowser, getSnapshot: () => racingLive, restore: (snapshot, guard) => new Promise(resolve => requests.push({ snapshot, guard, resolve })) });
const older = racing.restoreEntry({ route: { repo: 'old' } });
const newer = racing.restoreEntry({ route: { repo: 'new' } });
assert.ok(requests[0].guard.signal.aborted);
racingLive = requests[1].snapshot;
requests[1].resolve();
await newer;
assert.equal(readRoute(racingBrowser.location.href).repo, 'new');
assert.equal(requests[0].guard.isCurrent(), false);
requests[0].resolve();
await older;
assert.equal(readRoute(racingBrowser.location.href).repo, 'new');
assert.equal(racingBrowser.pushes, 0);

const interrupted = racing.restoreEntry({ route: { repo: 'back' } });
racing.checkpoint({ cancelRestore: true });
assert.ok(requests[2].guard.signal.aborted);
racingLive = navigationSnapshot({ route: { repo: 'user' } });
racing.commit();
requests[2].resolve();
await interrupted;
assert.equal(readRoute(racingBrowser.location.href).repo, 'user');
console.log('Navigation URL, compact snapshots, Back/Forward, camera isolation, malformed/stale targets and restore races PASS');
const occurrence='raw::inline-call:10:2::method:other::inline-call:30:4';
const occurrenceRequest=navigationSnapshot({route:{repo:'repo',path:'raw',detail:pathDetailToken([occurrence],[])},pathExpanded:[occurrence],snapshotId:'s'});
const restoredOccurrence=resolveNavigation(occurrenceRequest,{projects:[project],nodes,snapshotId:'s'}).snapshot;
assert.deepEqual(restoredOccurrence.pathExpanded,[occurrence]);
assert.equal(resolveNavigation(occurrenceRequest,{projects:[project],nodes,snapshotId:'next'}).snapshot.pathExpanded.length,0);


// Visible toolbar buttons use the same native traversal and bounded history positions.
const buttonBrowser = new Browser(), buttonStates = [];
let buttonLive = navigationSnapshot({ route: { repo: 'repo' }, camera:{x:10,y:20,scale:.7} });
const buttonHistory = new NavigationHistory({browser:buttonBrowser,getSnapshot:()=>buttonLive,
 restore:async snapshot=>{buttonLive=snapshot;},onChange:value=>buttonStates.push(value)});
await buttonHistory.start({repo:'repo'});
assert.deepEqual(buttonStates.at(-1),{canBack:false,canForward:false});
assert.equal(buttonHistory.go(-1),false);assert.equal(buttonHistory.go(1),false);
function buttonNavigate(node){buttonHistory.checkpoint();buttonLive=navigationSnapshot({...buttonLive,route:{...buttonLive.route,node}});buttonHistory.commit();}
for(const id of ['a','b','c'])buttonNavigate(id);
assert.deepEqual(buttonStates.at(-1),{canBack:true,canForward:false});
buttonLive.camera={x:40,y:50,scale:.8};buttonHistory.scheduleCamera();
assert.equal(buttonHistory.cursor.index,3,'Pan never creates another step');
assert.equal(buttonHistory.go(-1),true);await buttonBrowser.lastTraversal;
assert.equal(buttonLive.route.node,'b');
assert.deepEqual(buttonStates.at(-1),{canBack:true,canForward:true});
assert.equal(buttonHistory.go(1),true);await buttonBrowser.lastTraversal;
assert.equal(buttonLive.route.node,'c');assert.deepEqual(buttonLive.camera,{x:40,y:50,scale:.8});
buttonHistory.go(-1);await buttonBrowser.lastTraversal;buttonHistory.go(-1);await buttonBrowser.lastTraversal;
buttonNavigate('new-branch');
assert.equal(buttonHistory.cursor.end,2);assert.equal(buttonHistory.go(1),false,'New navigation discards the old forward branch');
buttonHistory.go(-1);await buttonBrowser.lastTraversal;
assert.equal(buttonHistory.cursor.end,2,'An older entry must not revive its obsolete forward bound');
const reloadedStates=[];
const reloadedHistory=new NavigationHistory({browser:buttonBrowser,getSnapshot:()=>buttonLive,
 restore:async snapshot=>{buttonLive=snapshot;},onChange:value=>reloadedStates.push(value)});
await reloadedHistory.start();
assert.deepEqual(reloadedStates.at(-1),{canBack:true,canForward:true},'Reload retains known back/forward bounds');
reloadedHistory.go(1);await buttonBrowser.lastTraversal;
assert.equal(buttonLive.route.node,'new-branch');assert.equal(reloadedStates.at(-1).canForward,false);
assert.equal(reloadedHistory.go(0),false);
console.log('Toolbar Back/Forward bounds, native traversal, camera, reload and forward-branch truncation PASS');
