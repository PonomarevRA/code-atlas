/** Missing snapshots stay actionable, and an explicit repair can still be monitored. */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {MapCleanupController} from '../web/map-cleanup.js';
import {MapCleanupState} from '../web/map-cleanup-state.js';
import {mapCleanupLabels} from '../web/map-cleanup-labels.js';

/** Minimal text controls exercise state transitions without a renderer or browser. */
function element() {
  return {hidden: false, textContent: '', value: '', children: [], listeners: {},
    classList: {toggle() {}}, addEventListener(name, fn) {this.listeners[name] = fn;},
    replaceChildren(...items) {this.children = items;}, append(...items) {this.children.push(...items);},
    setAttribute(name, value) {this[name] = value;}, removeAttribute(name) {delete this[name];},
    querySelectorAll() {return [];}};
}
const controls = new Map();
globalThis.document = {getElementById(id) {if (!controls.has(id)) controls.set(id, element()); return controls.get(id);},
  createElement: element, createTextNode: text => ({textContent: text})};
globalThis.location = {search: ''};
globalThis.history = {replaceState() {}};
globalThis.window = {addEventListener() {}};
const bad = {id: 'broken', title: 'Data', status: 'unavailable', snapshotId: 'old', builtAt: '2026-10-23T08:00:00Z',
  freshness: {status: 'unavailable', code: 'SNAPSHOT_MISSING', message: 'Файлы снимка отсутствуют. Обновите карту явно.'}};
let current = bad;
const calls = [];
globalThis.fetch = async (path, options) => {
  calls.push([path, options]);
  let value;
  if (path === '/api/data-maps') value = {maps: [current]};
  else if (path === '/api/data-maps/broken') value = current;
  else if (path === '/api/data-jobs/repair') {
    current = {...bad, status: 'ready', snapshotId: 'new', freshness: {status: 'current'}};
    value = {id: 'repair', mapId: 'broken', status: 'completed'};
  } else if (path.includes('/query/')) {
    assert.equal(current.freshness.status, 'current', 'Unavailable evidence must not be queried');
    value = {items: [], freshness: {status: 'current'}};
  } else throw new Error('Unexpected endpoint: ' + path);
  return {ok: true, json: async () => value};
};
let source = readFileSync(new URL('../web/data-controller.js', import.meta.url), 'utf8');
source = source.replace("import { DataScene } from './data-scene.js';", 'class DataScene {reset() {} add() {} setRelations() {}}')
  .replace("import { DataKeyExplorer } from './data-key-controller.js';", 'class DataKeyExplorer {revision = 0; reset() {} async run(fn) {await fn();} async loadKeys() {}}');
// A data URL has no relative module base. Keep the actual dependency modules,
// resolving them against the source file rather than stubbing loading behavior.
source = source.replace(/from '(\.\/[^']+)'/g, (_, relative) =>
  `from '${new URL('../web/'+relative.slice(2), import.meta.url).href}'`);
const controller = await import('data:text/javascript;base64,' + Buffer.from(source + '\nexport {openMap};').toString('base64'));
await controller.openMap('broken');
assert.equal(calls.filter(([path]) => path.includes('/query/')).length, 0);
assert.equal(controls.get('refresh').hidden, false);
assert.equal(controls.get('json-export').hidden, true);
assert.equal(controls.get('json-export').href, undefined);
assert.match(controls.get('freshness').textContent, /SNAPSHOT_MISSING/);
assert.match(controls.get('freshness').textContent, /Обновите карту/);
current = {...bad, status: 'queued', jobId: 'repair'};
await controller.openMap('broken');
assert.ok(calls.some(([path]) => path === '/api/data-jobs/repair'));
assert.equal(controls.get('json-export').hidden, false);
assert.match(controls.get('json-export').href, /snapshotId=new/);
assert.equal(calls.filter(([path]) => path.includes('/query/schema')).length, 1);

// Technical rows now belong to MapCleanupController. Exercise its actual
// renderer, retaining the unavailable-size/reason checks in the current schema.
const state = new MapCleanupState();
state.setCatalog({maps:[{...bad,sizeBytes:null,cleanup:{available:false,reason:'missing-snapshot'}}],codeMaps:[]});
const storage = Object.create(MapCleanupController.prototype);
Object.assign(storage, {state, rows:new Map(), table:element(), labels:mapCleanupLabels(),
  document:globalThis.document, sync(){},
  element(tag,text){const result=element();if(text!==undefined)result.textContent=text;return result;},
  button(label){return this.element('button',this.labels.text(label));},
});
storage.renderRows();
const item = storage.rows.get(JSON.stringify(['data','broken']));
assert.match(item.reason.textContent, /Снимок.*отсутствует/);
assert.equal(item.cells[6].children[0].textContent, 'неизвестно');
assert.match(item.cells[5].children[0].textContent, /Снимок недоступен/);
console.log('PASS: unavailable maps display reasons, retain Refresh, skip queries/export and monitor repair');
