/** Real-source menu acceptance: grouping, local search, disclosure state, focus and reflow. */
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
const {chromium} = await import(process.env.ATLAS_PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.ATLAS_WIKI_URL || 'http://127.0.0.1:53407';
const output = 'outputs/wiki-menu-20261028';
await mkdir(output, {recursive: true});
const browser = await chromium.launch({headless: true, ...(process.env.ATLAS_TEST_CHROMIUM ? {executablePath: process.env.ATLAS_TEST_CHROMIUM} : {})});
const page = await browser.newPage({viewport: {width: 1280, height: 900}, reducedMotion: 'reduce'});
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const group = id => page.locator(`.wiki-topic-group[data-group="${id}"]`);
try {
  await page.goto(base + '/wiki.html?page=Home&repo=selected');
  await page.locator('#wiki-count').filter({hasText: '47'}).waitFor();
  assert.equal(await page.locator('.wiki-topic-group').count(), 3);
  assert.equal(await page.locator('.wiki-navigation-section').count(), 15);
  assert.equal(await page.locator('details[data-mod]').count(), 47);
  assert.equal(await page.locator('#wiki-contents').evaluate(node => node.open), true);
  await group('develop').locator(':scope > summary').click();
  assert.equal(await group('develop').evaluate(node => node.open), true);
  const before = page.url();
  const title = await page.locator('#wiki-article h1').innerText();
  await page.locator('#wiki-filter').fill('no-such-chapter-575');
  await page.locator('.wiki-empty-search').waitFor({state: 'visible'});
  assert.match(await page.locator('#wiki-count').innerText(), /0 из 62/);
  assert.equal(await page.locator('#wiki-article h1').innerText(), title);
  assert.equal(page.url(), before);
  await page.locator('#wiki-filter').fill('document-pdf-basic');
  await page.locator('#wiki-pages a[href*="mod=document-pdf-basic"]').waitFor({state: 'visible'});
  assert.equal(await group('study').evaluate(node => node.open), true);
  await page.locator('#wiki-filter').fill('');
  assert.equal(await group('develop').evaluate(node => node.open), true);
  assert.equal(await group('study').evaluate(node => node.open), false);
  await page.locator('#wiki-pages a[href*="page=Architecture"]').click();
  assert.equal(new URL(page.url()).searchParams.get('repo'), 'selected');
  assert.equal(await page.locator('#wiki-article').evaluate(node => node === document.activeElement), true);
  if (!await group('start').evaluate(node => node.open)) await group('start').locator(':scope > summary').click();
  await group('start').locator(':scope > summary').click();
  assert.equal(await group('start').evaluate(node => node.open), false);
  const summary = group('develop').locator(':scope > summary');
  await summary.focus();
  await page.locator('#wiki-catalog-load').evaluate(node => node.click());
  await page.waitForFunction(() => document.querySelector('#wiki-catalog-load').getAttribute('aria-busy') === 'false');
  assert.equal(await group('start').evaluate(node => node.open), false);
  assert.equal(await summary.evaluate(node => node === document.activeElement), true);
  await page.setViewportSize({width: 320, height: 900});
  await page.waitForFunction(() => !document.querySelector('#wiki-contents').open);
  assert.equal(await page.locator('#wiki-contents > summary').evaluate(node => node === document.activeElement), true);
  await page.locator('#wiki-filter').fill('document-pdf-basic');
  assert.equal(await page.locator('#wiki-contents').evaluate(node => node.open), true);
  await page.locator('#wiki-pages a[href*="mod=document-pdf-basic"]').click();
  await page.waitForFunction(() => document.querySelector('#wiki-article').getAttribute('aria-busy') === 'false');
  assert.equal(await page.locator('#wiki-contents').evaluate(node => node.open), false);
  assert.equal(await page.locator('#wiki-article').evaluate(node => node === document.activeElement), true);
  assert.match(await page.locator('#wiki-article h1').innerText(), /PDF/);
  await page.setViewportSize({width: 1280, height: 900});
  await page.goto(base + '/wiki.html?page=Explore-Code&mod=language-python&modPage=overview');
  await page.waitForFunction(() => document.querySelector('#wiki-article').getAttribute('aria-busy') === 'false');
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const readingContext = await page.evaluate(() => {
    const selected = document.querySelector('#wiki-pages a[aria-current]').getBoundingClientRect();
    const scroller = document.querySelector('.wiki-sidebar-content').getBoundingClientRect();
    const search = document.querySelector('#wiki-filter').getBoundingClientRect();
    const footer = document.querySelector('.wiki-sidebar-footer').getBoundingClientRect();
    return {selectedTop: selected.top, selectedBottom: selected.bottom, top: scroller.top, bottom: scroller.bottom,
      searchTop: search.top, footerBottom: footer.bottom, height: innerHeight};
  });
  assert.ok(readingContext.selectedTop >= readingContext.top - 1 && readingContext.selectedBottom <= readingContext.bottom + 1, JSON.stringify(readingContext));
  assert.ok(readingContext.searchTop >= 0 && readingContext.footerBottom <= readingContext.height, JSON.stringify(readingContext));
  await page.setViewportSize({width: 800, height: 900});
  await page.locator('#wiki-contents > summary').click();
  await page.waitForFunction(() => {
    const selected = document.querySelector('#wiki-pages a[aria-current]').getBoundingClientRect();
    const scroller = document.querySelector('#wiki-pages').getBoundingClientRect();
    return selected.top >= scroller.top - 1 && selected.bottom <= scroller.bottom + 1;
  });
  const matrix = [];
  for (const theme of ['light', 'dark']) {
    await page.addInitScript(theme => localStorage.setItem('atlas-color-scheme', JSON.stringify({id: theme, base: theme})), theme);
    for (const width of [320, 800, 1280]) for (const scale of [1, 2]) {
      await page.setViewportSize({width, height: 900});
      await page.goto(base + '/wiki.html?page=Architecture');
      await page.locator('#wiki-count').filter({hasText: '47'}).waitFor();
      await page.evaluate(scale => document.documentElement.style.fontSize = 16 * scale + 'px', scale);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const geometry = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth,
        mode: document.documentElement.dataset.colorMode, font: getComputedStyle(document.querySelector('#wiki-article')).fontSize}));
      assert.ok(geometry.scroll <= width + 1, JSON.stringify({theme, scale, ...geometry}));
      assert.equal(geometry.font, 16 * scale + 'px'); assert.equal(geometry.mode, theme);
      if (width <= 1100 || scale === 2) {
        await page.locator('#wiki-contents > summary').focus(); await page.keyboard.press('Enter');
        assert.equal(await page.locator('#wiki-contents').evaluate(node => node.open), true);
      }
      const usable = await page.evaluate(() => {
        const selected = document.querySelector('#wiki-pages a[aria-current]').getBoundingClientRect();
        const scroller = document.querySelector('.wiki-compact') ? document.querySelector('#wiki-pages') : document.querySelector('.wiki-sidebar-content');
        const box = scroller.getBoundingClientRect();
        return selected.top >= box.top - 1 && selected.bottom <= box.bottom + 1 && box.height >= selected.height;
      });
      assert.ok(usable, `Selected chapter must fit within the usable contents area: ${width}/${scale}`);
      if ((width === 1280 && scale === 1) || (width === 320 && scale === 2)) {
        await page.screenshot({path: `${output}/menu-${theme}-${width}-${scale}.png`});
      }
      matrix.push({theme, width, scale, ...geometry});
    }
  }
  assert.deepEqual(errors, []);
  await writeFile(output + '/browser-menu.json', JSON.stringify({result: 'PASS',mocked: false,guidePages: 15,modPages: 47,
    groups: 3,searchRestoresDisclosure: true,refreshPreservesSummaryFocus: true,compactKeyboard: true,matrix,errors}, null, 2));
  console.log('Real wiki menu grouping/search/disclosures/focus and12 theme/reflow scenarios PASS');
} finally { await browser.close(); }
