import assert from 'node:assert/strict';
import { createDesktopAutomation, rememberDesktopMapScope, storedDesktopMapScope } from '../web/desktop-automation.js';

const scope = { projectId: 'calc', snapshotId: 'snapshot-1', nodeId: null, view: 'architecture', revision: 1 };
const command = (action, fields = {}) => ({
  action, projectId: scope.projectId, snapshotId: scope.snapshotId, ...fields,
});
const document = { activeElement: null, getElementById: () => null, querySelector: () => null };
const controls = new Map(['toggle', 'query'].map(name => [name, {
  value: '',
  getClientRects: () => [],
  focus() { document.activeElement = this; },
}]));
const activity = {
  projectId: 'calc',
  open: false,
  isOpen() { return this.open; },
  panel: { getClientRects: () => [], contains: () => false },
  part(name) { return controls.get(name); },
  action(action) {
    if (action === 'toggle') this.open = !this.open;
    if (action === 'filters') this.filtersOpen = true;
    if (action === 'close') {
      this.open = false;
      this.part('toggle').focus();
    }
  },
  async loadHistory() {
    this.historyPage = { events: [] };
    return true;
  },
};
const automation = createDesktopAutomation({
  document,
  location: { pathname: '/' },
  activity,
  readScope: () => scope,
  openProject: async (projectId, snapshotId) => {
    if (snapshotId !== 'snapshot-2') return false;
    Object.assign(scope, { projectId, snapshotId, revision: scope.revision + 1 });
    return true;
  },
});
const initial = await automation.inspect();
assert.equal((await automation.execute(command('open_project'))).status, 'ok', 'the current map stays open');
assert.equal((await automation.execute(command('open_history', { snapshotId: 'stale' }))).error, 'stale_context');
assert.equal(activity.open, false);
assert.equal((await automation.execute(command('clear_history'))).error, 'unknown_action');
assert.equal((await automation.execute(command('open_history'))).state.history.mode, 'history');

const filtered = await automation.execute(command('filter_history', { query: 'search_nodes' }));
assert.equal(filtered.status, 'ok');
assert.equal(filtered.state.history.resultCount, 0);
assert.equal(filtered.state.focus.target, 'history-query');
assert.equal(activity.part('query').value, 'search_nodes');
assert.ok(!JSON.stringify(filtered).includes('search_nodes'));
const loadHistory = activity.loadHistory;
activity.loadHistory = async () => false;
const failedFilter = await automation.execute(command('filter_history', { query: 'unavailable' }));
assert.equal(failedFilter.error, 'history_not_loaded');
activity.loadHistory = loadHistory;

const closed = await automation.execute(command('close_history'));
assert.equal(closed.state.history.open, false);
assert.equal(closed.state.focus.target, 'activity-launcher');

const rejected = await automation.execute(command('open_project', { projectId: 'another', snapshotId: 'stale' }));
assert.equal(rejected.error, 'project_not_opened');
assert.equal(rejected.state.projectId, 'calc');
const switched = await automation.execute(command('open_project', { projectId: 'another', snapshotId: 'snapshot-2' }));
assert.equal(switched.state.projectId, 'another');
assert.equal(switched.state.snapshotId, 'snapshot-2');
assert.equal(switched.state.revision.document, initial.revision.document);
assert.equal(switched.state.revision.map, initial.revision.map + 1);

const storage = new Map();
const session = () => ({ getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) });
rememberDesktopMapScope(session, () => scope);
assert.deepEqual(storedDesktopMapScope(session), { projectId: 'another', snapshotId: 'snapshot-2' });

console.log('Desktop automation scope, history, empty filter and focus checks passed');
