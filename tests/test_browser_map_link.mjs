import assert from 'node:assert/strict';
import { browserMapURL, readBrowserMapURL, bindBrowserMapLink } from '../web/browser-map-link.js';
import { navigationSnapshot } from '../web/navigation-history.js';

const root = 'method_' + 'x'.repeat(350);
const expanded = []; let previous = root;
for (let depth = 0; depth < 12; depth++) { previous += '::method:' + root + '::inline-call:1:1'; expanded.push(previous); }
const snapshot = navigationSnapshot({ route: { repo: 'project', path: root, node: root, algorithm: 'natural', display: 'flow' },
  snapshotId: 'snapshot', pathExpanded: expanded, camera: { x: 10, y: 20, scale: .8 },
  searchContext: { codeQuery: 'пифы' }, filters: { search: 'русский текст', projectBlocks: true },
  methodPlacements: [{ id: 'details', parentId: root, kind: 'body', x: 0, y: 0, width: 100, height: 100 }] });
const href = browserMapURL('http://127.0.0.1:8765/?repo=project', snapshot);
assert.ok(href.length < 12000, 'Prefix sharing keeps a twelve-level handoff compact');
const restored = readBrowserMapURL(href);
assert.equal(new URL(restored.href).hash, '');
assert.deepEqual(restored.snapshot.pathExpanded, snapshot.pathExpanded);
assert.deepEqual(restored.snapshot.camera, snapshot.camera);
assert.equal(restored.snapshot.filters.search, 'русский текст');
assert.deepEqual(restored.snapshot.methodPlacements, snapshot.methodPlacements);
for (const fragment of ['#atlas-map=!bad!', '#atlas-map=' + btoa(JSON.stringify({ version: 9 })), '#different']) {
  assert.equal(readBrowserMapURL('http://127.0.0.1:8765/' + fragment), null);
}
const invalid = { version: 1, snapshot: { route: { repo: 'project' } }, path: [[999, 'unsafe']], inline: [] };
assert.equal(readBrowserMapURL('http://127.0.0.1:8765/#atlas-map=' + btoa(JSON.stringify(invalid))), null);
const link = { listeners: {}, addEventListener(name, callback) { this.listeners[name] = callback; } };
const messages = [];
bindBrowserMapLink(link, () => snapshot, { location: { href: 'http://127.0.0.1:8765/' } }, message => messages.push(message));
link.listeners.click({ preventDefault() { assert.fail('Valid map handoff must navigate'); } });
assert.deepEqual(readBrowserMapURL(link.href).snapshot.pathExpanded, snapshot.pathExpanded);
assert.deepEqual(messages, []);
console.log('Browser map handoff retains Unicode, twelve-level occurrences, camera and filters with bounded safe fragments PASS');
