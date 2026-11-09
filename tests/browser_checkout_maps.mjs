/** Actual extraction, sliding lifetime, settings labels and exact-session deletion. */
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';

const {chromium} = await import(process.env.ATLAS_PLAYWRIGHT_MODULE);
const origin = process.env.ATLAS_BROWSER_ORIGIN;
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const browser = await chromium.launch({headless: true, executablePath: process.env.ATLAS_TEST_CHROMIUM});
const page = await browser.newPage({viewport: {width: 1280, height: 900}});
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const sessions = [];
/** Reject an HTTP failure before interpreting the actual source response. */
async function json(response) {
  assert.equal(response.ok(), true, await response.text());
  return response.json();
}
/** Poll readiness only; it must not extend the private map's lifetime. */
async function ready(id, deadline = Date.now() + 70000) {
  while (Date.now() < deadline) {
    const state = await json(await page.request.get(`${origin}/api/checkout-sessions/${id}`));
    if (state.project?.snapshotId && !state.activeJob) return state;
    assert.notEqual(state.project?.status, 'failed', JSON.stringify(state));
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  throw new Error('Private fixture extraction exceeded 70 seconds');
}
try {
  const catalogBefore = await json(await page.request.get(origin+'/api/projects'));
  for (const owner of ['browser-a', 'browser-b']) {
    const session = await json(await page.request.post(origin+'/api/checkout-sessions', {data: {
      checkout: process.env.ATLAS_BROWSER_CHECKOUT, ownerKey: 'atlas239:'+owner, label: 'Проверка '+owner,
    }}));
    sessions.push(session.sessionId);
  }
  const [first, second] = await Promise.all(sessions.map(id => ready(id)));
  assert.notEqual(first.path, second.path);
  const search = await json(await page.request.post(`${origin}/api/checkout-sessions/${first.sessionId}/search`,
    {data: {query: 'label', snapshotId: first.project.snapshotId, limit: 10}}));
  assert.ok(search.nodes.length > 0, JSON.stringify(search));
  const touched = await json(await page.request.get(`${origin}/api/checkout-sessions/${first.sessionId}`));
  assert.ok(Date.parse(touched.expiresAt) > Date.parse(first.expiresAt));
  await page.goto(origin+'/technical.html#atlas-temporary-checkouts');
  const section = page.locator('#atlas-temporary-checkouts');
  const row = section.locator(`[data-checkout-session="${first.sessionId}"]`);
  await row.waitFor();
  assert.match(await row.innerText(), /Временная/);
  assert.match(await row.innerText(), /Готова|Доступна/);
  assert.ok((await row.innerText()).includes(first.path));
  assert.ok((await row.innerText()).includes(process.env.ATLAS_BROWSER_CHECKOUT));
  assert.notEqual(await row.locator('td').nth(4).innerText(), '—');
  const output = process.env.ATLAS_MODULE_SCREENSHOTS;
  if (output) {
    await mkdir(output, {recursive: true});
    await section.screenshot({path: path.join(output, 'temporary-checkouts-wide.png')});
  }
  await page.setViewportSize({width: 390, height: 844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1), true);
  assert.ok((await row.locator('td').nth(3).boundingBox()).width > 240, 'mobile path retains a readable line width');
  assert.ok((await row.getByRole('button', {name:/Удалить/}).boundingBox()).width > 70, 'delete label stays readable');
  if (output) await section.screenshot({path: path.join(output, 'temporary-checkouts-mobile.png')});
  await page.evaluate(() => { document.documentElement.style.zoom = '200%'; });
  assert.equal(await row.getByRole('button', {name: /Удалить/}).isEnabled(), true);
  await page.evaluate(() => { document.documentElement.style.zoom = ''; });
  const still = await json(await page.request.get(`${origin}/api/checkout-sessions/${first.sessionId}`));
  assert.equal(still.expiresAt, touched.expiresAt, 'settings/status must not renew the lifetime');
  await row.getByRole('button', {name: /Удалить/}).click();
  await row.waitFor({state: 'detached'});
  assert.equal((await page.request.get(`${origin}/api/checkout-sessions/${first.sessionId}`)).status(), 410);
  assert.equal((await page.request.get(`${origin}/api/checkout-sessions/${second.sessionId}`)).status(), 200);
  const catalogAfter = await json(await page.request.get(origin+'/api/projects'));
  assert.deepEqual(catalogAfter.projects.map(p => [p.id,p.snapshotId]), catalogBefore.projects.map(p => [p.id,p.snapshotId]));
  assert.equal(errors.length, 0, errors.join('\n'));
  console.log(JSON.stringify({status:'PASS', actualExtraction:true, isolatedFolders:true,
    ttlExtendedOnSearch:true, settingsDoNotTouch:true, exactSessionDeletion:true, javascriptErrors:errors.length}));
} finally {
  for (const id of sessions) await page.request.delete(`${origin}/api/checkout-sessions/${id}`).catch(()=>{});
  await browser.close();
}
