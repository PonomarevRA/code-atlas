/** Isolated Chromium checks for catalog/page recovery, scoped navigation and host reflow. */
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
const {chromium} = await import(process.env.ATLAS_PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.ATLAS_WIKI_URL || 'http://127.0.0.1:50699';
const output = 'outputs/wiki-mods-20261028';
await mkdir(output, {recursive: true});
const browser = await chromium.launch({headless: true, ...(process.env.ATLAS_TEST_CHROMIUM ? {executablePath: process.env.ATLAS_TEST_CHROMIUM} : {})});
const context = await browser.newContext({viewport: {width: 1280, height: 900}, reducedMotion: 'reduce'});
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const pages = [{id:'overview',title:'Обзор чтения',path:'wiki/Overview.md'}, {id:'detail',title:'Подробности переходов',path:'wiki/nested/Detail.MD'}];
const mod = {id:'fixture-reader',name:'Документация синтетического мода',version:'1.0',fingerprint:'fixture-v1',origin:'user',enabled:false,status:'disabled',section:'Explore-Code',pages};
let catalogMode = 'error', pageMode = 'ok', delay = 0, calls = 0;
await page.route('**/api/mods/wiki', route => route.fulfill({status:catalogMode === 'error' ? 500 : 200, json:catalogMode === 'error' ? {error:'Synthetic catalog outage'} : {apiVersion:1,mods:[mod],truncated:false}}));
await page.route('**/api/mods/fixture-reader/wiki/*', async route => {
  calls++;
  const url = new URL(route.request().url());
  assert.equal(url.searchParams.get('fingerprint'), mod.fingerprint);
  const selected = pages.find(item => url.pathname.endsWith('/' + item.id));
  const mode = pageMode, wait = delay;
  if (wait) await new Promise(resolve => setTimeout(resolve, wait));
  if (mode === 'error' || mode === 'changed') return route.fulfill({status:mode === 'changed' ? 409 : 500,json:{error:'Synthetic document failure'}});
  return route.fulfill({json:{modId:mod.id,pageId:mode === 'mismatch' ? 'wrong' : selected.id,title:selected.title,section:mod.section,format:'markdown',fingerprint:mod.fingerprint,source:{origin:'user',path:selected.path},text:selected.id === 'overview' ? '# Обзор чтения\n\n[Открыть подробности](nested/Detail.MD#Доказательства) [Небезопасно](javascript:alert) <img src=x onerror=bad>\n\n'+Array.from({length:32},(_,i)=>'Абзац '+i+' — текст для проверки восстановления места чтения.').join('\n\n') : '# Подробности переходов\n\n[Назад к обзору](../Overview.md)\n\n## Доказательства\n\n[Внешний источник](https://example.test/docs)\n\nДокументация выключенного мода доступна для чтения.'}});
});
/** Wait for the reader to finish the expected page without assuming network-idle shell APIs. */
async function article(title) {
  await page.locator('#wiki-article h1').filter({hasText:title}).waitFor();
  await page.waitForFunction(() => document.querySelector('#wiki-article').getAttribute('aria-busy') === 'false');
}
try {
  await page.goto(base+'/wiki.html?page=Home&repo=selected');
  await page.locator('#wiki-mod-state').filter({hasText:'недоступны'}).waitFor();
  assert.match(await page.locator('#wiki-state').innerText(), /без API/);
  assert.ok(await page.locator('#wiki-article p').count());
  await page.locator('#wiki-pages a[href*="page=Explore-Code"]').click();
  assert.match(await page.locator('#wiki-state').innerText(), /без API/);
  catalogMode='ok';
  await page.locator('#wiki-catalog-load').click();
  await page.locator('details[data-mod="fixture-reader"] summary').waitFor();
  const summary=page.locator('details[data-mod="fixture-reader"] summary');
  await summary.focus(); await page.keyboard.press('Enter');
  assert.equal(await summary.evaluate(node=>getComputedStyle(node).outlineStyle),'solid');
  await page.locator('#wiki-pages a[href*="modPage=overview"]').click();
  await article('Обзор чтения');
  assert.equal(await page.locator('#wiki-article').evaluate(node=>node===document.activeElement),true);
  assert.match(await page.locator('#wiki-breadcrumbs').innerText(),/Документация синтетического мода/);
  assert.match(await page.locator('#wiki-state').innerText(),/выключен/);
  assert.equal(await page.locator('#wiki-article img, #wiki-article script').count(),0);
  assert.equal(await page.locator('#wiki-article a').count(),1);
  const before=await page.url();
  await page.locator('#wiki-filter').fill('Подробности');
  assert.equal(page.url(),before);
  assert.equal(await page.locator('#wiki-filter').evaluate(node=>node===document.activeElement),true);
  assert.equal(await page.locator('#wiki-article h1').innerText(),'Обзор чтения');
  await page.locator('#wiki-filter').fill('');
  await page.evaluate(()=>scrollTo(0,650));
  await page.locator('#wiki-catalog-load').evaluate(node=>node.click());
  await page.waitForFunction(()=>document.querySelector('#wiki-catalog-load').getAttribute('aria-busy')==='false');
  await page.waitForFunction(()=>scrollY>600);
  await page.locator('#wiki-next a').click();
  await article('Подробности переходов');
  assert.equal(new URL(page.url()).searchParams.get('repo'),'selected');
  assert.equal(await page.locator('#wiki-article a[target="_blank"]').getAttribute('rel'),'noopener noreferrer');
  await page.goBack(); await article('Обзор чтения');
  await page.waitForFunction(()=>scrollY>400);
  await page.goForward(); await article('Подробности переходов');
  // A reload drops the bounded in-memory page cache, exercising actual recovery requests.
  pageMode='error'; await page.reload();
  await page.locator('#wiki-mod-retry').waitFor({state:'visible'});
  pageMode='ok'; await page.locator('#wiki-mod-retry').click(); await article('Подробности переходов');
  pageMode='changed'; await page.reload();
  await page.locator('#wiki-catalog-retry').waitFor({state:'visible'});
  mod.fingerprint='fixture-v2'; pageMode='ok';
  await page.locator('#wiki-catalog-retry').click(); await article('Подробности переходов');
  pageMode='mismatch'; await page.reload();
  await page.locator('#wiki-catalog-retry').waitFor({state:'visible'});
  assert.equal(await page.locator('#wiki-article').getAttribute('aria-busy'),'false');
  pageMode='ok'; await page.locator('#wiki-catalog-retry').click(); await article('Подробности переходов');
  mod.status='auto';
  await page.locator('#wiki-catalog-load').click();
  await page.locator('#wiki-state').filter({hasText:'автовыбор по файлам'}).waitFor();
  assert.equal(mod.enabled,false);
  delay=450;
  await page.goto(base+'/wiki.html?page=Explore-Code&mod=fixture-reader&modPage=overview');
  await page.locator('#wiki-state').filter({hasText:'Читаем документацию'}).waitFor();
  await page.locator('#wiki-pages a[href*="page=Home"]').click();
  await page.waitForTimeout(600);
  assert.equal(new URL(page.url()).searchParams.get('page'),'Home');
  assert.doesNotMatch(await page.locator('#wiki-article h1').innerText(),/Обзор чтения/);
  delay=0;
  await page.evaluate(()=>{history.pushState({},'', '/wiki.html?page=Unknown');dispatchEvent(new PopStateEvent('popstate'));});
  await article('Раздел не найден');
  assert.equal(await page.title(),'Раздел не найден · Wiki Atlas');
  assert.equal(await page.locator('#wiki-breadcrumbs').innerText(),'Wiki Atlas');
  const reflow=[];
  for(const mode of ['light','dark']) {
    await page.addInitScript(mode=>localStorage.setItem('atlas-color-scheme',JSON.stringify({id:mode,base:mode})),mode);
    for(const width of [320,800,1280]) for(const scale of [1,2]) {
      await page.setViewportSize({width,height:900});
      await page.goto(base+'/wiki.html?page=Explore-Code&mod=fixture-reader&modPage=detail');
      await article('Подробности переходов');
      await page.evaluate(scale=>document.documentElement.style.fontSize=(16*scale)+'px',scale);
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const size=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth,font:getComputedStyle(document.querySelector('#wiki-article')).fontSize,mode:document.documentElement.dataset.colorMode}));
      const overflow = await page.evaluate(()=>({x:scrollX,nodes:[...document.querySelectorAll('body *')].filter(node=>node.getBoundingClientRect().right+scrollX>innerWidth+1).map(node=>({tag:node.tagName,id:node.id,cls:node.className,right:node.getBoundingClientRect().right+scrollX,text:node.textContent.slice(0,100)}))}));
      assert.ok(size.scroll<=size.width+1,JSON.stringify({mode,width,scale,...size,overflow}));
      assert.equal(size.mode,mode); assert.equal(size.font,(16*scale)+'px');
      reflow.push({mode,width,textScale:scale,...size});
      if((width===1280&&scale===1)||(width===320&&scale===2)) await page.screenshot({path:`${output}/wiki-${mode}-${width}-${scale}.png`,fullPage:true});
    }
  }
  assert.deepEqual(errors,[]);
  await writeFile(output+'/frontend-browser.json',JSON.stringify({result:'PASS',engine:'isolated Playwright Chromium',catalogOffline:true,pageRetry:true,fingerprintConflict:true,identityMismatch:true,staleResponseFenced:true,historyScroll:true,keyboardFocus:true,reflow,calls,pageErrors:errors},null,2));
  console.log('Wiki browser recovery, identity, navigation, keyboard, history and 12 reflow cases PASS');
} finally {await browser.close();}
