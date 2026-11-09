/** Coverage exercises actual DOM/controller behavior, stale transports and evidence boundaries. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { CoverageView, coverageMapUrl, coverageMapTargets, coverageReportPaths, filterCoverageConditions } from '../web/coverage-view.js';
import { CoveragePage } from '../web/coverage-page.js';
import { architectureGraph } from '../web/architecture.js';
import { readRoute } from '../web/navigation-history.js';

class Element {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.value = ''; this.textContent = ''; this.hidden = false; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  setAttribute(name, value) { this.attributes[name] = value; }
  focus(options) { this.focusOptions = options; globalThis.document.activeElement = this; }
  scrollIntoView(options) { this.scrollCalls ||= []; this.scrollCalls.push(options); }
  set innerHTML(value) { throw new Error('Project text must not become HTML'); }
}
globalThis.document = { createElement: tag => new Element(tag) };
const elements = () => Object.fromEntries(['back', 'project', 'scope', 'clear-scope', 'reports', 'analyze', 'status', 'provenance',
  'results', 'summary', 'warnings', 'filters', 'search', 'kind', 'runtime', 'test-kind', 'count', 'conditions', 'pagination', 'detail', 'tests', 'tests-count']
  .map(name => [name, new Element()]));
function descendants(root, predicate) {
  const matches = [];
  for (const child of root.children) { if (predicate(child)) matches.push(child); matches.push(...descendants(child, predicate)); }
  return matches;
}
function text(root) { return [root.textContent, ...root.children.map(text)].join('\n'); }
function viewFields(fields) {
  return { summary: fields.summary, warnings: fields.warnings, conditions: fields.conditions, count: fields.count,
    pagination: fields.pagination, detail: fields.detail, tests: fields.tests, testsCount: fields['tests-count'] };
}
function dataset({ count = 4, nodeId = 'raw', snapshotId = 's', projectId = 'p' } = {}) {
  return {
    schemaVersion: 1, analyzerVersion: 'test-coverage-v1', projectId, snapshotId,
    sourceKind: 'working-tree-and-snapshot', scope: { nodeId, mode: nodeId ? 'node-chain' : 'repository' }, status: 'partial',
    sourceFacts: [{ file: 'Calculator.cs', sourceHash: 'current-source-hash', mtimeNs: 1760000000000000000, stableRead: true }],
    summary: { conditions: count, methods: 1, tests: 2, reports: 1 }, limits: { maxConditions: 6000 },
    truncation: { files: true }, warnings: ['Partial scan <script>not executable</script>'],
    reports: [{ file: 'artifacts/coverage.xml', format: 'cobertura', status: 'parsed', sourceHash: 'captured-report-hash', mtimeNs: 1760000000000000000, warnings: [] }],
    tests: [
      { id: 'unit', nodeId: 'test-raw', kind: 'unit', subtype: 'theory', framework: 'xunit', name: 'Unit <img onerror=bad()>',
        namespace: 'Tests', file: 'CalcTests.cs', line: 4, declaredStatus: 'disabled', skipReasons: ['waiting <b>data</b>'],
        exampleRows: 3, exampleRowsTruncated: false, runtime: { status: 'unknown' } },
      { id: 'feature', kind: 'gherkin', subtype: 'scenario-outline', name: 'Scenario currency', file: 'Currency.feature', line: 8,
        declaredStatus: 'enabled', tags: ['@currency'], exampleRows: 2, runtime: { status: 'unknown' } },
    ],
    methods: [{ id: 'method', name: 'Calculate' }],
    conditions: Array.from({ length: count }, (_, index) => ({
      id: `condition-${index}`, ownerNodeId: 'raw', methodId: 'method', methodLabel: 'Calculate <img onerror=bad()>',
      namespace: 'Domain', file: 'Calculator.cs', line: index + 10, kind: index % 2 ? 'foreach' : 'if',
      condition: `value > ${index} <script>alert(1)</script>`, sourceStatus: 'parsed',
      objectives: [{ id: 'true', kind: 'true', label: 'истина', runtimeStatus: 'unknown' }],
      runtime: { status: ['unknown', 'uncovered', 'covered', 'partial'][index % 4], lineHits: index ? index : null,
        branchesCovered: index % 3, branchesValid: 2, reportPaths: index ? ['artifacts/coverage.xml'] : [], reason: 'Line counters only', freshness: 'timestamp-compatible-not-source-hash-proof' },
      testLinks: [{ testId: index % 2 ? 'feature' : 'unit', targetNodeId: 'raw', path: ['test-raw', 'raw'], relations: ['calls'], confidence: 'candidate', sourceKind: 'graph-snapshot' }],
    })),
  };
}
const response = value => ({ ok: true, json: async () => value });
const graph = { nodes: [{ id: 'raw', kind: 'method', label: '.Calculate()' }, { id: 'test-raw', kind: 'method', label: '.Theory()' },
  { id: 'attribute', kind: 'attribute', label: 'HttpPost()' }], metadata: { snapshotId: 's' } };
const projectList = { projects: [{ id: 'p', name: 'Project', snapshotId: 's' }, { id: 'q', name: 'Other', snapshotId: 't' }] };
const tick = () => new Promise(resolve => setImmediate(resolve));

// Input paths never become arbitrary local/remote URLs or filesystem-open requests.
assert.deepEqual(coverageReportPaths('artifacts/a.xml,\n artifacts/b.xml\na.xml'), ['artifacts/a.xml', 'artifacts/b.xml', 'a.xml']);
for (const path of ['/etc/passwd', '../secret.xml', 'https://host/coverage.xml', 'file:///secret', 'C:\\coverage.xml', 'a/../secret.xml', 'a\0.xml']) {
  assert.throws(() => coverageReportPaths(path));
}
assert.throws(() => coverageReportPaths(Array.from({ length: 9 }, (_, index) => `${index}.xml`).join(',')));
const map = coverageMapUrl({ projectId: 'p&other=x', snapshotId: 's' }, 'javascript:alert(1)');
assert.ok(map.startsWith('/?'));
assert.equal(new URL(map, 'http://localhost').searchParams.get('node'), 'javascript:alert(1)');
assert.equal(new URL(map, 'http://localhost').searchParams.get('repo'), 'p&other=x');
const manySymbols = Array.from({ length: 270 }, (_, index) => ({ id: `symbol-${index}`, label: `.Method${String(index).padStart(3, '0')}()`,
  file: 'Domain/Large.cs', codeProjectId: 'internal-project', kind: 'method' }));
const symbolsBefore = JSON.stringify(manySymbols);
const mapTargets = coverageMapTargets(manySymbols);
const destination = readRoute('http://localhost' + coverageMapUrl({ projectId: 'p', snapshotId: 's' }, 'symbol-265', mapTargets.get('symbol-265')));
assert.equal(destination.level, 'symbols');
assert.equal(destination.project, 'internal-project');
assert.equal(destination.file, 'Domain/Large.cs');
assert.equal(destination.page, 1);
const projection = architectureGraph({ nodes: manySymbols, edges: [] }, manySymbols,
  { level: destination.level, projectId: destination.project, file: destination.file, page: destination.page });
assert.ok(projection.nodes.some(node => node.id === destination.node), 'Generated link opens the actual visible method, including later symbol pages');
assert.equal(JSON.stringify(manySymbols), symbolsBefore);

// Filters use real associated test inventory, preserve source data and distinguish unknown/uncovered.
const data = dataset();
const before = JSON.stringify(data);
assert.deepEqual(filterCoverageConditions(data, { runtime: 'unknown' }).map(item => item.id), ['condition-0']);
assert.deepEqual(filterCoverageConditions(data, { runtime: 'uncovered' }).map(item => item.id), ['condition-1']);
assert.equal(filterCoverageConditions(data, { kind: 'loop' }).length, 2);
assert.equal(filterCoverageConditions({conditions:[{id:'ternary',kind:'conditional'}]}, {kind:'if'})[0].id, 'ternary');
assert.equal(filterCoverageConditions(data, { search: 'Scenario currency', testKind: 'gherkin' }).length, 2);
assert.equal(JSON.stringify(data), before);

const fields = elements();
const selections = [], sources = [];
const view = new CoverageView(viewFields(fields), { onSelect: id => selections.push(id), onSource: (nodeId, line) => new Promise(resolve => sources.push({ nodeId, line, resolve })) });
view.setData(data, { projectId: 'p', snapshotId: 's' }, new Set(['raw', 'test-raw']));
assert.ok(text(fields.conditions).includes('<script>alert(1)</script>'));
assert.ok(text(fields.warnings).includes('captured-report-hash'));
assert.ok(!text(fields.summary).includes('%'));
const buttons = descendants(fields.conditions, element => element.tag === 'button');
buttons[2].listeners.click();
assert.deepEqual(selections, ['condition-2']);
assert.equal(globalThis.document.activeElement.attributes['aria-pressed'], 'true');
assert.deepEqual(globalThis.document.activeElement.focusOptions, { preventScroll: true });
assert.ok(text(fields.detail).includes('истина · выполнение неизвестно'));
assert.ok(text(fields.detail).includes('Счётчики строки не доказывают'));
assert.ok(text(fields.detail).includes('captured-report-hash') && text(fields.detail).includes('2025-10-09'));
assert.ok(text(fields.detail).includes('current-source-hash') && text(fields.detail).includes('совпадение версии по hash не доказано'));
assert.ok(text(fields.detail).includes('отключён в исходнике'));
assert.ok(text(fields.detail).includes('3 строк данных, запуск не подтверждён'));
assert.ok(text(fields.detail).includes('Возможная связь'));
const sourceButton = descendants(fields.detail, item => item.tag === 'button')[0];
const firstSource = sourceButton.listeners.click();
assert.deepEqual({ nodeId: sources[0].nodeId, line: sources[0].line }, { nodeId: 'raw', line: 12 });
view.select('condition-1');
sources[0].resolve({ text: 'STALE_SOURCE', warning: 'working-tree' });
await firstSource;
assert.ok(!text(fields.detail).includes('STALE_SOURCE'));
assert.ok(descendants(fields.detail, item => item.tag === 'a').every(link => link.href.startsWith('/?')));
view.setData(dataset({ count: 121 }), { projectId: 'p', snapshotId: 's' }, new Set(['raw', 'test-raw']));
assert.equal(descendants(fields.conditions, item => item.tag === 'button').length, 50);
descendants(fields.pagination, item => item.tag === 'button')[1].listeners.click();
assert.equal(view.page, 1);
assert.equal(globalThis.document.activeElement.tag, 'button');
assert.equal(globalThis.document.activeElement.textContent, 'Следующая →');
assert.equal(descendants(fields.conditions, item => item.tag === 'button').length, 50);
view.setFilters({ runtime: 'unknown' });
assert.equal(view.page, 0);
assert.equal(descendants(fields.conditions, item => item.tag === 'button').length, 31);
view.setData(dataset({ count: 121 }), { projectId: 'p', snapshotId: 's' }, new Set(['raw']), { page: 1.5 });
assert.equal(view.page, 0, 'Fractional URL page is not a valid pagination state');

// User selection reveals stacked mobile details; restoring/filtering data never moves the viewport.
const mobileFields = elements();
const mobileSelections = [];
const mobileView = new CoverageView(viewFields(mobileFields), { onSelect: id => mobileSelections.push(id) });
globalThis.matchMedia = query => ({ matches: query === '(max-width: 900px)' });
mobileView.setData(dataset({ count: 121 }), { projectId: 'p', snapshotId: 's' }, new Set(['raw']), { conditionId: 'condition-1' });
assert.equal(mobileFields.detail.scrollCalls, undefined, 'URL restoration cannot scroll to details');
descendants(mobileFields.conditions, item => item.tag === 'button')[0].listeners.click();
assert.deepEqual(mobileSelections, ['condition-0']);
assert.deepEqual(mobileFields.detail.scrollCalls, [{ block: 'start', inline: 'nearest', behavior: 'auto' }]);
assert.equal(globalThis.document.activeElement, mobileFields.detail, 'Mobile keyboard activation continues at the details, not above the long list');
assert.equal(mobileFields.detail.attributes.tabindex, '-1');
assert.ok(text(mobileFields.detail).includes('value > 0'));
mobileView.setFilters({ runtime: 'uncovered' });
assert.equal(mobileFields.detail.scrollCalls.length, 1, 'Local filtering must preserve the viewport');
globalThis.matchMedia = () => ({ matches: false });
mobileView.select('condition-1');
assert.equal(mobileFields.detail.scrollCalls.length, 1, 'Desktop selection must not scroll the page');
assert.equal(globalThis.document.activeElement.attributes['aria-pressed'], 'true');
delete globalThis.matchMedia;

// A compact response retains coverage of test kinds and explicitly omits proof paths, not calls.
const compact = dataset();
compact.conditions[0].testLinksTotal = 10;
compact.conditions[0].testLinksTruncated = true;
compact.conditions[0].testKinds = { unit: 8, gherkin: 2 };
compact.conditions[0].testLinks[0] = { ...compact.conditions[0].testLinks[0], path: [], pathTruncated: true,
  pathLength: 15, pathPrefix: ['test-raw'], pathSuffix: ['raw'], relations: Array(14).fill('calls') };
assert.ok(filterCoverageConditions(compact, { testKind: 'gherkin' }).some(condition => condition.id === 'condition-0'), 'Omitted representative evidence cannot remove the known test kind');
view.setFilters({});
view.setData(compact, { projectId: 'p', snapshotId: 's' }, new Set(['raw', 'test-raw']));
view.select('condition-0');
assert.ok(text(fields.detail).includes('Показано 1 из 10 статических связей'));
assert.ok(text(fields.detail).includes('Путь из 15 узлов сокращён'));
assert.ok(text(fields.detail).includes('не означают прямой вызов'));

// Initial deep link makes exactly one analysis. Local filters/selection use replaceState only.
const pageFields = elements(), calls = [], historyCalls = [];
const page = new CoveragePage(pageFields, {
  href: 'http://localhost/coverage.html?repo=p&snapshot=s&node=raw&condition=condition-2',
  history: { state: {}, replaceState: (_, unused, url) => historyCalls.push(url), pushState: () => assert.fail('Unexpected history entry') },
  fetcher: async (url, options = {}) => {
    calls.push({ url, options });
    if (url === '/api/projects') return response(projectList);
    if (url.endsWith('/graph')) return response(graph);
    if (url.endsWith('/source')) return response({ snapshotId: 's', text: 'source line', warning: 'Working tree' });
    const body = JSON.parse(options.body);
    return response(dataset({ nodeId: body.nodeId || null }));
  },
});
await page.start();
assert.equal(calls.length, 3);
assert.deepEqual(JSON.parse(calls[2].options.body), { snapshotId: 's', nodeId: 'raw', reportPaths: [] });
assert.equal(page.view.selectedId, 'condition-2');
assert.equal(pageFields.results.hidden, false);
const statusButtons = descendants(pageFields.summary, element => element.tag === 'button');
statusButtons[0].listeners.click();
assert.equal(pageFields.runtime.value, 'unknown');
assert.equal(calls.length, 3);
pageFields.search.value = 'currency'; pageFields.search.listeners.input();
pageFields.runtime.value = 'unknown'; pageFields.runtime.listeners.change();
assert.equal(calls.length, 3, 'Filtering must not analyze or read files');
assert.ok(historyCalls.some(url => url.includes('q=currency')));
assert.ok(historyCalls.every(url => url.startsWith('/coverage.html?')));
let prevented = false; pageFields.filters.listeners.submit({ preventDefault() { prevented = true; } });
assert.ok(prevented, 'Enter in search does not reload the page');
pageFields.search.value = ''; pageFields.search.listeners.input();
pageFields.runtime.value = 'all'; pageFields.runtime.listeners.change();
await page.source('raw');
assert.deepEqual(JSON.parse(calls.at(-1).options.body), { nodeId: 'raw', snapshotId: 's' });
const beforeInvalid = calls.length;
await assert.rejects(page.source('attribute'));
assert.equal(calls.length, beforeInvalid);
pageFields.reports.value = '../unsafe.xml'; await page.analyze();
assert.equal(calls.length, beforeInvalid);
pageFields.reports.value = 'artifacts/coverage.xml';
page.context.nodeId = null;
await page.analyze();
assert.deepEqual(JSON.parse(calls.at(-1).options.body), { snapshotId: 's', reportPaths: ['artifacts/coverage.xml'] });
assert.equal(pageFields['clear-scope'].hidden, true);
assert.deepEqual(page.url.searchParams.getAll('report'), ['artifacts/coverage.xml']);

// Custom validated report paths are reproducible on reload; invalid URL input never triggers analysis.
const customFields = elements(), customCalls = [];
const custom = new CoveragePage(customFields, {
  href: 'http://localhost' + historyCalls.at(-1), history: { replaceState() {} },
  fetcher: async (url, options = {}) => {
    customCalls.push({ url, options });
    return response(url === '/api/projects' ? projectList : url.endsWith('/graph') ? graph : dataset({ nodeId: null }));
  },
});
await custom.start();
assert.equal(customFields.reports.value, 'artifacts/coverage.xml');
assert.deepEqual(JSON.parse(customCalls.at(-1).options.body).reportPaths, ['artifacts/coverage.xml']);
const invalidQueryFields = elements(), invalidQueryCalls = [];
const invalidQuery = new CoveragePage(invalidQueryFields, {
  href: 'http://localhost/coverage.html?repo=p&snapshot=s&report=..%2Fsecret.xml', history: { replaceState() {} },
  fetcher: async (url, options) => { invalidQueryCalls.push(url); return response(url === '/api/projects' ? projectList : graph); },
});
await invalidQuery.start();
assert.equal(invalidQueryCalls.length, 2);
assert.ok(invalidQueryCalls.every(url => !url.endsWith('/test-coverage')));
assert.ok(invalidQueryFields.status.textContent.includes('внутри репозитория'));

// Ignoring AbortSignal cannot display the response for an old project/snapshot.
const raceFields = elements(), pending = [];
const race = new CoveragePage(raceFields, {
  href: 'http://localhost/coverage.html?repo=p&snapshot=s&node=raw', history: { replaceState() {} },
  fetcher: (url, options) => new Promise(resolve => pending.push({ url, options, resolve })),
});
const initial = race.start(); pending[0].resolve(response(projectList)); await tick();
pending[1].resolve(response(graph)); await tick();
assert.ok(pending[2].url.endsWith('/test-coverage'));
race.context = { projectId: 'q', snapshotId: null, nodeId: null };
const newProject = race.loadProject(false);
assert.ok(pending[2].options.signal.aborted);
pending[2].resolve(response(dataset())); await initial;
assert.equal(raceFields.results.hidden, true);
assert.equal(race.view.data, null);
pending[3].resolve(response({ nodes: [{ id: 'other', kind: 'method', label: '.Other()' }], metadata: { snapshotId: 't' } }));
await newProject;
assert.equal(race.context.snapshotId, 't');
assert.equal(pending.length, 4, 'Project changes await an explicit analysis');

// Stale URL never silently switches snapshot, and foreign response metadata is rejected.
const staleFields = elements(), staleCalls = [];
const stale = new CoveragePage(staleFields, {
  href: 'http://localhost/coverage.html?repo=p&snapshot=old&node=raw', history: { replaceState() {} },
  fetcher: async (url, options) => { staleCalls.push(url); return response(url === '/api/projects' ? projectList : graph); },
});
await stale.start();
assert.equal(staleCalls.length, 2);
assert.ok(staleFields.status.textContent.includes('устарел'));
const wrongFields = elements();
const wrong = new CoveragePage(wrongFields, {
  href: 'http://localhost/coverage.html?repo=p&snapshot=s&node=raw', history: { replaceState() {} },
  fetcher: async url => response(url === '/api/projects' ? projectList : url.endsWith('/graph') ? graph : dataset({ snapshotId: 'foreign' })),
});
await wrong.start();
assert.equal(wrongFields.results.hidden, true);
assert.ok(wrongFields.status.textContent.includes('другой области'));

// Every bootstrap field exists in the real document and static handlers cannot bypass module wiring.
const html = fs.readFileSync(new URL('../web/coverage.html', import.meta.url), 'utf8');
for (const id of Object.keys(elements())) assert.ok(html.includes(`id="coverage-${id}"`), `Missing bootstrap field ${id}`);
assert.ok(!html.includes('onsubmit='));
assert.ok(html.includes('href="/coverage.css"') && html.includes('src="/coverage-page.js"'));
console.log('Coverage provenance, DOM paging, local filters, safe sources/URLs, auto-load and stale scope guards PASS');
