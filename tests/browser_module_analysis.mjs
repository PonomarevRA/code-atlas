/** Verify actual source HTTP/UI behavior in an isolated headless browser fixture. */
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';

const {chromium} = await import(process.env.ATLAS_PLAYWRIGHT_MODULE);
const origin = process.env.ATLAS_BROWSER_ORIGIN;
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const browser = await chromium.launch({
  headless: true, executablePath: process.env.ATLAS_TEST_CHROMIUM,
});
const page = await browser.newPage({viewport: {width: 1280, height: 900}});
const errors = [];
page.on('pageerror', error => errors.push(error.message));
let analysisCalls = 0;
page.on('request', request => {
  if (request.url().endsWith('/module-analysis') && request.method() === 'POST') analysisCalls += 1;
});
try {
  await page.goto(origin + '/module-analysis.html?repo=legacy-fixture');
  await page.locator('#snapshot').waitFor();
  await page.waitForFunction(() => document.querySelector('#snapshot').value === 'first');
  assert.equal(analysisCalls, 0, 'opening the page must not run analysis');
  await page.locator('[name="algorithm"][value="louvain"]').uncheck();
  await page.locator('[name="algorithm"][value="leiden"]').uncheck();
  await page.getByRole('button', {name: 'Сравнить модули'}).click();
  await page.locator('#module-results:not([hidden])').waitFor();
  assert.match(await page.locator('#module-results').innerText(), /Текущ|current/);
  assert.match(await page.locator('#module-results').innerText(), /first/);
  await page.locator('#module-results details').first().locator('summary').click();
  assert.match(await page.locator('#module-results details').first().innerText(), /A.*B|B.*A/s);
  assert.equal(analysisCalls, 1);
  assert.equal(await page.locator('#project').inputValue(), 'legacy-fixture');
  await page.locator('#folders').fill('A\nA/sub');
  await page.locator('#folders').dispatchEvent('change');
  await page.getByRole('button', {name: 'Сравнить модули'}).click();
  await page.waitForFunction(() => document.querySelector('#module-status').textContent.includes('overlap'));
  assert.equal(await page.locator('#module-results').isVisible(), false);
  await page.locator('#folders').fill('A');
  await page.locator('#folders').dispatchEvent('change');
  await page.getByRole('button', {name: 'Сравнить модули'}).click();
  await page.locator('#module-results:not([hidden])').waitFor();
  assert.match(await page.locator('#module-status').innerText(), /данных неизвестна/);
  assert.equal(await page.locator('h1').innerText(), 'Модули классов и папок');
  const output = process.env.ATLAS_MODULE_SCREENSHOTS;
  if (output) {
    await mkdir(output, {recursive: true});
    await page.screenshot({path: path.join(output, 'module-analysis-wide.png'), fullPage: true});
  }
  await page.setViewportSize({width: 390, height: 844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  if (output) await page.screenshot({path: path.join(output, 'module-analysis-mobile.png'), fullPage: true});
  const fixtureResponse = await page.request.post(origin + '/api/projects/legacy-fixture/module-analysis',
    {data: {snapshotId: 'first', folders: ['A'], algorithms: ['current']}});
  const oldReport = await fixtureResponse.json();
  // A controlled fetch ignoring AbortSignal exercises the generation fence,
  // independently of whether Chromium happens to cancel a real socket promptly.
  await page.evaluate(() => {
    window.__moduleOriginalFetch = window.fetch;
    window.fetch = (url, options) => {
      if (String(url).endsWith('/module-analysis')) return new Promise(resolve => { window.__moduleLate = resolve; });
      return window.__moduleOriginalFetch(url, options);
    };
  });
  await page.getByRole('button', {name: 'Сравнить модули'}).click();
  await page.waitForFunction(() => typeof window.__moduleLate === 'function');
  await page.locator('#folders').fill('B'); // Input only; no blur/change event.
  await page.evaluate(report => window.__moduleLate(new Response(JSON.stringify(report), {status: 200})), oldReport);
  await page.waitForTimeout(50);
  assert.equal(await page.locator('#module-results').isVisible(), false);
  assert.match(await page.locator('#module-status').innerText(), /Параметры изменены/);
  await page.evaluate(() => {
    window.fetch = window.__moduleOriginalFetch;
    delete window.__moduleOriginalFetch;
    delete window.__moduleLate;
  });
  await page.locator('#folders').fill('');
  const proposals = page.locator('#module-proposals');
  await proposals.getByRole('button', {name: 'Найти варианты'}).click();
  await proposals.locator('[data-proposal-output] h3').waitFor();
  assert.match(await proposals.locator('[data-proposal-output]').innerText(), /Варианты структуры.*first/s);
  await proposals.getByRole('button', {name: 'Сохранить базу обновления'}).click();
  await proposals.locator('svg').waitFor();
  assert.match(await proposals.locator('[data-proposal-output]').innerText(), /Обновление групп.*first/s);
  const pinControls = proposals.getByText('Положение модулей · закрепления', {exact:true});
  await pinControls.click();
  await proposals.getByRole('checkbox').first().check();
  assert.ok(await proposals.locator('rect[stroke-width="4"]').count() > 0);
  await proposals.getByRole('button', {name: 'Проверить обновление'}).click();
  await page.waitForFunction(() => !document.querySelector('#module-proposals').hasAttribute('aria-busy'));
  assert.match(await proposals.locator('[data-proposal-status]').innerText(), /Готово/);
  assert.ok(await proposals.locator('rect[stroke-width="4"]').count() > 0);
  assert.equal(await page.locator('#snapshot').inputValue(), 'first');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1), true);
  if (output) await proposals.screenshot({path:path.join(output,'module-proposals-mobile.png')});
  await page.setViewportSize({width:1280,height:900});
  if (output) await proposals.screenshot({path:path.join(output,'module-proposals-wide.png')});
  await page.goto(origin + '/wiki.html?page=Code-Patterns');
  await page.getByRole('heading', {name: /паттерн/i}).first().waitFor();
  assert.equal(errors.length, 0, errors.join('\n'));
  console.log(JSON.stringify({status: 'PASS', analysisCalls, javascriptErrors: errors.length, viewports: [1280, 390]}));
} finally {
  await browser.close();
}
