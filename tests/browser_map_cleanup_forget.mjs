import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

const origin=process.env.ATLAS_TEST_URL,output=process.env.ATLAS_LAYOUT_OUTPUT;
assert(origin&&output,'Set ATLAS_TEST_URL and ATLAS_LAYOUT_OUTPUT for an isolated source server.');
const action=process.env.ATLAS_CLEANUP_ACTION||'forget-registration';
assert(['forget-registration','delete-map'].includes(action),'Only supported synthetic registration-removing scenarios are tested.');
const combined=action==='delete-map';
const {chromium}=await import(process.env.ATLAS_PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,...(process.env.ATLAS_TEST_CHROMIUM?{executablePath:process.env.ATLAS_TEST_CHROMIUM}:{})});
await mkdir(output,{recursive:true});const results=[];

/**
 * Builds a browser-only catalog of missing-snapshot registrations; no real storage paths are used.
 * @returns {object} Independent code/data identities and an explicit metadata-only and combined-delete availability hints.
 */
function fixtureCatalog() {
  const row=(id,title)=>({id,title,url:'/',status:'completed',builtAt:null,sizeBytes:0,freshness:{status:'unavailable'},cleanup:{available:false,reason:'missing-snapshot',forgetAvailable:true,deleteAvailable:true,forgetReason:'metadata-only',logicalBytes:null,versionStatus:'unknown'}});
  return {maps:[row('fixture-data','Fixture missing data')],codeMaps:[row('fixture-code','Fixture missing code')],totalMapBytes:0};
}

/**
 * Captures only the seven owned source files so concurrent edits invalidate this UI receipt.
 * @returns {Promise<object>} Relative file names and exact SHA256 digests; missing files fail the run.
 */
async function sourceHashes() {
  const hashes={};
  for(const path of ['web/map-cleanup.js','web/map-cleanup-state.js','web/map-cleanup-labels.js','web/technical.html','web/technical.css','tests/test_map_cleanup.mjs','tests/browser_map_cleanup_forget.mjs']) {
    hashes[path]=createHash('sha256').update(await readFile(new URL('../'+path,import.meta.url))).digest('hex');
  }
  return hashes;
}
const before=await sourceHashes();

try {
  for(const scenario of [{id:'wide-light',width:1280,height:900,theme:'light'},{id:'wide-dark',width:1100,height:800,theme:'dark'},{id:'narrow-text200',width:390,height:844,theme:'light',text200:true,reduced:true}]) {
    const context=await browser.newContext({viewport:{width:scenario.width,height:scenario.height},reducedMotion:scenario.reduced?'reduce':'no-preference'});
    await context.addInitScript(theme=>localStorage.setItem('atlas-color-scheme',JSON.stringify({id:theme,base:theme})),scenario.theme);
    const page=await context.newPage(),catalog=fixtureCatalog();let receipt,executed=false,previewCount=0,executeCount=0,statusReads=0,forwardedMutations=0;const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    // Interception owns every test mutation; no real preview or execute request reaches the server.
    await page.route('**/api/**',async route=>{
      const request=route.request(),path=new URL(request.url()).pathname;
      const json=value=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
      if(path==='/api/technical/storage')return json(executed?{...catalog,maps:[],codeMaps:combined?[]:catalog.codeMaps}:catalog);
      if(path==='/api/technical/map-cleanup')return json({schemaVersion:'atlas-map-cleanup-v1',operations:[],truncated:false});
      if(path==='/api/technical/map-cleanup/preview') {
        const body=request.postDataJSON();assert.equal(body.action,action);assert.deepEqual(body.maps,combined?[{kind:'data',id:'fixture-data'},{kind:'code',id:'fixture-code'}]:[{kind:'data',id:'fixture-data'}]);previewCount++;
        receipt={schemaVersion:'atlas-map-cleanup-v1',action:body.action,registrationRemoved:false,mapsCleared:false,planId:'browser-fixture',status:'ready',expiresAt:new Date(Date.now()+600000).toISOString(),maps:body.maps.map(item=>({...item,title:(item.kind==='data'?catalog.maps:catalog.codeMaps)[0].title,sizeBytes:0,logicalBytes:0})),totals:{mapCount:body.maps.length,sizeBytes:0},warnings:[],preserved:[]};return json(receipt);
      }
      if(path==='/api/technical/map-cleanup/browser-fixture/execute'){executeCount++;executed=true;return route.abort('failed');}
      if(path==='/api/technical/map-cleanup/browser-fixture'){statusReads++;return json({...receipt,status:'completed',registrationRemoved:executed,mapsCleared:combined&&executed,canRetry:false});}
      if(!['GET','HEAD'].includes(request.method())){forwardedMutations++;return route.abort('blockedbyclient');}
      return route.continue();
    });
    try {
      await page.goto(origin+'/technical.html#atlas-storage');
      const row=page.locator('#storage-maps tr').filter({hasText:'Fixture missing data'}),forget=row.getByRole('button',{name:/Удалить запись из списка/});
      await forget.waitFor();assert.equal(await page.locator('#storage-maps input:checked').count(),0);
      if(scenario.text200){await page.route('**/atlas-forget-layout.css',route=>route.fulfill({status:200,contentType:'text/css',body:'#atlas-storage{font-size:200%}#atlas-storage table{font-size:inherit}#atlas-storage h2{font-size:1.4em}'}));await page.addStyleTag({url:origin+'/atlas-forget-layout.css'});}
      if(combined){for(const checkbox of await page.locator('#storage-maps input[type=checkbox]').all())await checkbox.check();assert.equal(await page.locator('#map-cleanup-preview').isDisabled(),true);}
      const trigger=combined?page.locator('#map-cleanup-delete'):forget;
      await trigger.focus();await trigger.press('Enter');await page.locator('#map-cleanup-dialog').waitFor({state:'visible'});
      await page.waitForFunction(()=>document.activeElement?.id==='map-cleanup-cancel');
      await page.getByRole('heading',{name:combined?'Удалить выбранные карты и их снимки?':'Удалить запись карты из списка?'}).waitFor();assert.match(await page.locator('#map-cleanup-consequences').innerText(),combined?/текущие и предыдущие снимки/:/только запись/);
      assert.equal(await row.count(),1);
      const geometry=await page.evaluate(()=>{
        const rect=element=>{const r=element.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
        const dialog=document.querySelector('#map-cleanup-dialog');return {documentWidth:document.documentElement.scrollWidth,dialog:rect(dialog),cancel:rect(document.querySelector('#map-cleanup-cancel')),confirm:rect(document.querySelector('#map-cleanup-confirm')),body:rect(dialog.querySelector('.map-cleanup-dialog-body')),storageFont:getComputedStyle(document.querySelector('#atlas-storage')).fontSize,theme:document.documentElement.dataset.colorMode,background:getComputedStyle(dialog).backgroundColor,reduced:matchMedia('(prefers-reduced-motion:reduce)').matches};
      });
      assert.equal(geometry.theme,scenario.theme);
      const background=geometry.background.match(/[\d.]+/g).slice(0,3).map(Number);
      if(scenario.theme==='dark')assert(background.every(value=>value<128),'Requested dark theme must render a real dark palette, not a light fallback.');
      assert(geometry.documentWidth<=scenario.width+2);assert(geometry.dialog.left>=-1&&geometry.dialog.right<=scenario.width+1&&geometry.dialog.top>=-1&&geometry.dialog.bottom<=scenario.height+1);
      for(const action of [geometry.cancel,geometry.confirm])assert(action.width>=24&&action.height>=24&&action.bottom<=geometry.dialog.bottom+1);
      assert(geometry.body.height>40);if(scenario.text200){assert.equal(geometry.storageFont,'28px');assert.equal(geometry.reduced,true);}
      await page.locator('#map-cleanup-dialog').screenshot({path:output+'/'+scenario.id+'-confirmation.png'});
      await page.keyboard.press('Escape');await page.waitForFunction(combined=>combined?document.activeElement?.id==='map-cleanup-delete':document.activeElement?.classList.contains('map-cleanup-forget'),combined);
      assert.equal(await row.count(),1);assert.equal(executeCount,0);
      await trigger.click();await page.locator('#map-cleanup-confirm').click();await page.locator('#map-cleanup-dialog').waitFor({state:'hidden'});
      await page.locator('#map-cleanup-check').waitFor({state:'visible'});assert.equal(await row.count(),1,'lost acknowledgement retains row');
      await page.locator('#map-cleanup-check').click();await page.waitForFunction(()=>document.querySelectorAll('#storage-maps tr').length===1);
      assert.match(await page.locator('#map-cleanup-status').innerText(),combined?/по этому плану завершено/:/по этому плану подтверждено/);assert.equal(await page.locator('#storage-maps tr').filter({hasText:'Fixture missing code'}).count(),combined?0:1);
      await page.locator('#atlas-storage').screenshot({path:output+'/'+scenario.id+'-completed.png'});
      assert.equal(previewCount,2);assert.equal(executeCount,1);assert.equal(statusReads,1);assert.equal(forwardedMutations,0);assert.deepEqual(errors,[]);
      results.push({scenario:scenario.id,status:'PASS',geometry,previewCount,executeCount,statusReads,realApiMutationRequests:0,pageErrors:0});
    }catch(error){results.push({scenario:scenario.id,status:'FAIL',error:error.message});await page.locator('#atlas-storage').screenshot({path:output+'/'+scenario.id+'-failure.png'}).catch(()=>{});throw error;}
    finally{await context.close();}
  }
}finally {
  await browser.close();const after=await sourceHashes(),sourceStable=JSON.stringify(before)===JSON.stringify(after);
  if(!sourceStable)for(const result of results)if(result.status==='PASS')result.status='INVALID';
  await writeFile(output+'/receipt.json',JSON.stringify({sourceStable,sources:{before,after},timestamp:new Date().toISOString(),node:process.version,action,results,scope:'Real source HTML/CSS/JS with intercepted synthetic catalog, preview, lost execute acknowledgement and same-plan GET. No real API mutation, screen-reader/native-app certification or physical deletion claim.'},null,2));
  assert(sourceStable,'Owned source changed during the browser run; receipt invalid.');
}
console.log(JSON.stringify(results.map(({scenario,status})=>({scenario,status}))));
