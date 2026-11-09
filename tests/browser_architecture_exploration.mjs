/**
 * Real Graphify fixture → Atlas HTTP → native graph controls → downloaded semantic files.
 * Run after test_architecture_pipeline.build_published_fixture in an isolated source server.
 */
import assert from 'node:assert/strict';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
const {chromium} = await import(process.env.ATLAS_PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.ATLAS_ARCHITECTURE_URL || 'http://127.0.0.1:65149';
assert.equal(new URL(base).hostname, '127.0.0.1');
const fixture = JSON.parse(await readFile(process.env.ATLAS_ARCHITECTURE_CONTEXT || '/private/tmp/atlas-architecture-preview-20261028/context.json', 'utf8'));
const output = 'outputs/architecture-map-20261028/browser';
await mkdir(output, {recursive: true});
const browser = await chromium.launch({headless: true, ...(process.env.ATLAS_TEST_CHROMIUM ? {executablePath: process.env.ATLAS_TEST_CHROMIUM} : {})});
const results = [];
/**
 * Read mounted architecture transforms and viewport camera without changing the scene.
 * @param {import('playwright').Page} page Isolated fixture tab.
 * @returns {Promise<object>} Stable node transforms and camera SVG transform.
 */
async function geometry(page) {
  return page.evaluate(() => ({camera: document.querySelector('#viewport').getAttribute('transform'),
    nodes: Object.fromEntries([...document.querySelectorAll('#viewport [data-node-id]')].map(node => [node.getAttribute('data-node-id'), node.getAttribute('transform')]))}));
}
/**
 * Select a real declaration through the public search and keyboard navigation.
 * @param {import('playwright').Page} page Isolated fixture tab.
 * @param {string} name Exact synthetic declaration name.
 */
async function visit(page, name) {
  if (!await page.locator('#navigation-search').isVisible()) await page.locator('[data-activity="search"]').click();
  await page.locator('#navigation-search').fill(name);
  await page.locator('#navigation-results .navigation-result').first().waitFor();
  await page.locator('#navigation-search').press('Enter');
}
/**
 * Close native narrow-screen drawers before interacting with the underlying map.
 * @param {import('playwright').Page} page Fixture tab with the Architecture activity selected.
 */
async function closeDrawers(page) {
  if (page.viewportSize().width >= 600) return;
  for (const [id, activity] of [['workspace-left', 'map'], ['workspace-right', 'details']]) {
    if (await page.locator('#' + id).getAttribute('aria-hidden') === 'false') {
      await page.locator('[data-activity="' + activity + '"]').click();
    }
  }
}
/**
 * Download through the actual native export controls.
 * @param {import('playwright').Page} page Fixture tab with a loaded architecture.
 * @param {string} format json or structurizr.
 * @param {string} name Artifact basename inside this test output.
 * @returns {Promise<string>} Downloaded UTF-8 content.
 */
async function download(page, format, name) {
  await closeDrawers(page);
  await page.locator('#architecture-export-format').selectOption(format);
  const waiting = page.waitForEvent('download');
  await page.locator('#architecture-export').click();
  const artifact = await waiting;
  const path = output + '/' + name;
  await artifact.saveAs(path);
  return readFile(path, 'utf8');
}
try {
  for (const config of [
    {name:'wide-light-topology', width:1440, height:1000, display:'topology', dark:false, scale:100, reduce:false},
    {name:'wide-dark-flow', width:1440, height:1000, display:'flow', dark:true, scale:100, reduce:false},
    {name:'narrow-text200', width:390, height:844, display:'topology', dark:false, scale:200, reduce:true},
  ].filter(config => !process.env.ATLAS_ARCHITECTURE_CASE || config.name === process.env.ATLAS_ARCHITECTURE_CASE)) {
    const context = await browser.newContext({viewport:{width:config.width,height:config.height}, acceptDownloads:true, reducedMotion:config.reduce?'reduce':'no-preference'});
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {if (new URL(request.url()).pathname.endsWith('/system-architecture')) requests.push({method:request.method(), body:request.postDataJSON()});});
    try {
      await page.goto(base + '/?repo=' + fixture.projectId + '&view=architecture&display=' + config.display);
      await page.locator('#project-name').filter({hasText:'Architecture Fixture'}).waitFor();
      await page.evaluate(async config => {
        document.documentElement.style.fontSize = config.scale + '%';
        const {colorSchemes} = await import('/color-scheme.js');
        const themes = colorSchemes(); await themes.ready; themes.apply({id:config.dark?'dark':'light'}, false);
      }, config);
      for (const name of ['Produce','Consume','Query','Http','external_call']) await visit(page, name);
      await page.locator('[data-activity="map"]').click();
      await page.locator('#view-mode').selectOption('system');
      await page.locator('#architecture-export:not([disabled])').waitFor();
      // View switches restore the saved renderer; the URL alone does not select it here.
      await page.locator('#display-provider').selectOption(config.display);
      assert.equal(await page.locator('#display-provider').inputValue(), config.display);
      const rendererSelector = config.display === 'flow' ? '#viewport .flow-card' : '#viewport .community-node';
      assert.ok(await page.locator(rendererSelector).count() > 0, 'expected actual renderer DOM');
      if (config.width < 600 && await page.locator('#workspace-left').getAttribute('aria-hidden') === 'false') {
        await page.locator('[data-activity="map"]').click();
      }
      assert.equal(await page.locator('#view-mode option[value="system"]').textContent(), 'Архитектура');
      assert.ok(requests.length && requests.every(request => request.method === 'POST'));
      assert.ok(Object.values(fixture.visits).every(id => requests.at(-1).body.visitedNodeIds.includes(id)));
      const collapsedText = await download(page, 'json', config.name + '-collapsed.json');
      const collapsed = JSON.parse(collapsedText);
      assert.equal(collapsed.schemaVersion, 'atlas-architecture-v1');
      assert.deepEqual([...new Set(collapsed.nodes.filter(node => node.resourceKind).map(node => node.resourceKind))].sort(), ['endpoint','queue','table','topic']);
      assert.equal(collapsed.edges.filter(edge => edge.factId).length, 5);
      for (const kind of ['kafka','rabbit','database']) {
        await closeDrawers(page);
        await page.locator('#fit').click();
        const block = page.locator('#viewport [data-node-id^="architecture-block:"][data-node-id$=":' + kind + '"]');
        await block.waitFor();
        const before = await geometry(page);
        await block.focus(); await block.press('Enter');
        await page.waitForFunction(kind => document.querySelector('#viewport [data-node-id$=":' + kind + '"]')?.getAttribute('aria-expanded') === 'true', kind);
        const after = await geometry(page);
        assert.equal(after.camera, before.camera, 'disclosure must preserve camera');
        for (const [id, transform] of Object.entries(before.nodes)) if (id in after.nodes) assert.equal(after.nodes[id], transform, 'existing node moved: ' + id);
        assert.equal(await page.evaluate(() => document.activeElement.getAttribute('data-node-id')), await block.getAttribute('data-node-id'), 'keyboard focus must remain on the block');
        const member = collapsed.nodes.find(node => node.kind === kind && node.resourceKind);
        assert.ok(member);
        assert.ok(await page.locator('#results').innerText().then(text => text.includes(member.label)), 'expanded resource must appear in the graph projection');
        await closeDrawers(page);
        await page.locator('#architecture-pin').click();
        assert.equal(await page.locator('#architecture-pin').getAttribute('aria-pressed'), 'true');
      }
      const jsonText = await download(page, 'json', config.name + '.json');
      const json = JSON.parse(jsonText);
      assert.equal(json.pinnedIds.length, 3);
      const ids = new Set(json.nodes.map(node => node.id));
      assert.ok(json.nodes.every(node => !node.ownerId || ids.has(node.ownerId)), 'resource owner must be exported');
      assert.ok(json.edges.every(edge => ids.has(edge.source) && ids.has(edge.target)));
      for (const secret of ['SQL_SECRET','HTTP_SECRET','QUERY_SECRET','CONFIG_SECRET','OTHER_SECRET','/v1/orders']) assert.ok(!jsonText.includes(secret));
      const repeated = await download(page, 'json', config.name + '-repeat.json');
      assert.equal(repeated, jsonText, 'same scoped model must export deterministically');
      const dsl = await download(page, 'structurizr', config.name + '.dsl');
      assert.ok(dsl.includes('!impliedRelationships false'));
      if (config.name === 'wide-light-topology') {
        // The upstream response is real; only this one delivery is delayed and changed to500.
        // This verifies recovery without mutating source, snapshot or user configuration.
        let release, markArrived;
        const gate = new Promise(resolve => { release = resolve; });
        const arrived = new Promise(resolve => { markArrived = resolve; });
        const pattern = '**/system-architecture';
        await page.route(pattern, async route => {
          await route.fetch();
          markArrived();
          await gate;
          await route.fulfill({status:500, json:{error:'Fixture enrichment failure'}});
        });
        await page.locator('#view-mode').selectOption('architecture');
        await visit(page, 'Flow');
        await page.locator('[data-activity="map"]').click();
        await page.locator('#view-mode').selectOption('system');
        await Promise.race([arrived, new Promise((_, reject) => setTimeout(() => reject(new Error('Expected architecture refresh')), 5000))]);
        await page.locator('[data-atlas-loading-status]').waitFor({state:'visible'});
        assert.ok(await page.locator('#viewport [data-node-id^="architecture-block:"]').count() >= 3);
        assert.equal(await page.locator('[data-atlas-loading-status]').evaluate(node => getComputedStyle(node).pointerEvents), 'none');
        const during = JSON.parse(await download(page, 'json', config.name + '-during-load.json'));
        assert.equal(during.edges.filter(edge => edge.factId).length, 5);
        release();
        await page.locator('#system-retry').waitFor({state:'visible'});
        await page.locator('#system-status p.error-text').filter({hasText:'Fixture enrichment failure'}).waitFor({state:'visible'});
        assert.ok(await page.locator('#viewport [data-node-id^="architecture-block:"]').count() >= 3);
        await page.unroute(pattern);
        await page.locator('#system-retry').click();
        await page.locator('#system-retry').waitFor({state:'hidden'});
        await page.locator('[data-atlas-loading-status]').waitFor({state:'hidden'});
      }
      await page.screenshot({path:output + '/' + config.name + '.png',fullPage:true});
      await page.locator('#architecture-reset').click();
      await page.waitForFunction(() => document.querySelector('#architecture-exploration-status')?.textContent.includes('Посещено 0/64'));
      await page.locator('#architecture-export:not([disabled])').waitFor();
      const reset = JSON.parse(await download(page, 'json', config.name + '-reset.json'));
      assert.equal(reset.pinnedIds.length, 0);
      assert.equal(reset.nodes.filter(node => node.resourceKind).length, 0);
      assert.deepEqual(errors, []);
      results.push({...config, actualRenderer:await page.locator('#display-provider').inputValue(), status:'PASS', requestCount:requests.length, factEdges:5, pinnedBlocks:3});
    } catch (error) {
      await page.screenshot({path:output + '/' + config.name + '-failure.png', fullPage:true});
      await writeFile(output + '/' + config.name + '-failure.html', await page.content());
      throw error;
    } finally {await context.close();}
  }
} finally {
  await browser.close();
  const reportName = process.env.ATLAS_ARCHITECTURE_CASE ? '/results-' + process.env.ATLAS_ARCHITECTURE_CASE + '.json' : '/results.json';
  await writeFile(output + reportName, JSON.stringify({scope:'Real local synthetic fixture; no native or broad assistive-technology qualification',results}, null, 2));
}
console.log(JSON.stringify({status:'PASS',results}));

