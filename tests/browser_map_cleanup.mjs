/** Exercise actual cleanup HTTP and native controls only in a marked synthetic profile. */
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir,realpath} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
const {chromium}=await import(process.env.ATLAS_PLAYWRIGHT_MODULE || 'playwright');
assert.ok(process.env.ATLAS_CLEANUP_CONTEXT,'Explicit synthetic context required');
const fixture=JSON.parse(await readFile(process.env.ATLAS_CLEANUP_CONTEXT,'utf8'));
const origin=new URL(fixture.origin);
assert.equal(origin.hostname,'127.0.0.1');assert.equal(origin.protocol,'http:');
assert.ok(!['8765','53407'].includes(origin.port));assert.equal(fixture.fixturePrepared,true);
assert.equal(await readFile(path.join(path.dirname(fixture.dataDir),'.atlas-map-cleanup-test'),'utf8'),'synthetic-fixture-only\n');
const output=process.env.ATLAS_CLEANUP_OUTPUT || 'outputs/map-cleanup-20261028/browser';
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.ATLAS_TEST_CHROMIUM?{executablePath:process.env.ATLAS_TEST_CHROMIUM}:{})});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
const page=await context.newPage();page.setDefaultTimeout(12000);
const errors=[];page.on('pageerror',error=>errors.push(error.message));
let executeRequests=0,planId;

/** Send a same-origin API request and retain HTTP errors for explicit stale-plan assertions. */
async function api(route,body) {
  assert.ok(route.startsWith('/api/')&&!route.includes('://'));
  const target=new URL(route,fixture.origin);assert.equal(target.origin,origin.origin);
  const options={maxRedirects:0,...(body===undefined?{}:{data:body,headers:{Origin:fixture.origin}})};
  const response=body===undefined?await context.request.get(target.href,options):await context.request.post(target.href,options);
  assert.equal(new URL(response.url()).origin,origin.origin);
  assert.ok(response.status()<300||response.status()>=400,'Redirect rejected');
  return {status:response.status(),body:await response.json()};
}

/** Poll one real owned build/operation with a bounded deadline and terminal failure evidence. */
async function waitTerminal(route,expected='completed') {
  const deadline=Date.now()+120000;
  while(Date.now()<deadline) {
    const result=await api(route);assert.equal(result.status,200);
    if(result.body.status===expected)return result.body;
    assert.ok(['queued','running','purging'].includes(result.body.status),JSON.stringify(result.body));
    await page.waitForTimeout(150);
  }
  throw Error('Synthetic operation timed out');
}

/** Address one literal map identity; identical code/data IDs must never collide. */
async function checkbox(kind,id) {
  const rows=await page.locator('#storage-maps [data-map-key]').all();
  for(const row of rows) {
    if(await row.getAttribute('data-map-key')===JSON.stringify([kind,id]))return row.locator('input[type=checkbox]');
  }
  throw Error('Missing selected map row');
}

/** Verify authored views, unrelated maps and source files survive actual cleanup. */
async function verifyPreserved() {
  for(const [relative,digest] of Object.entries(fixture.sourceHashes)) {
    const actual=createHash('sha256').update(await readFile(path.join(fixture.fixtureDir,relative))).digest('hex');
    assert.equal(actual,digest,'Source changed: '+relative);
  }
  for(const [relative,digest] of Object.entries(fixture.protected)) {
    assert.equal(createHash('sha256').update(await readFile(path.join(fixture.dataDir,relative))).digest('hex'),digest);
  }
  assert.equal((await api('/api/views/'+fixture.savedCodeViewId)).status,200);
  assert.equal((await api('/api/data-maps/'+fixture.dataMap.id+'/views/'+fixture.savedDataViewId)).status,200);
  const projects=(await api('/api/projects')).body.projects;
  assert.equal(projects.find(item=>item.id===fixture.unrelatedProject.id).snapshotId,fixture.unrelatedProject.snapshotId);
}

try {
  // A stale port/context must never authorize deletion in another local profile.
  const liveStorage=await api('/api/technical/storage');assert.equal(liveStorage.status,200);
  assert.equal(await realpath(liveStorage.body.storagePath),await realpath(fixture.dataDir));
  const liveProjects=(await api('/api/projects')).body.projects;
  assert.deepEqual(liveProjects.map(item=>item.id).sort(),[fixture.project.id,fixture.unrelatedProject.id].sort());
  for(const expected of [fixture.project,fixture.unrelatedProject]) {
    const live=liveProjects.find(item=>item.id===expected.id);
    assert.equal(live.snapshotId,expected.snapshotId);assert.equal(live.path,expected.path);
    assert.ok((await realpath(live.path)).startsWith((await realpath(fixture.fixtureDir))+path.sep));
  }
  const liveData=(await api('/api/data-maps/'+fixture.dataMap.id)).body;
  assert.equal(liveData.snapshotId,fixture.dataMap.snapshotId);assert.equal(liveData.root,fixture.dataMap.root);
  await page.goto(fixture.origin+'/technical.html#atlas-storage');
  await page.locator('#storage-maps input[type=checkbox]').first().waitFor();
  assert.equal(await page.locator('#storage-maps input:checked').count(),0);
  assert.equal(await page.locator('#map-cleanup-preview').isDisabled(),true);
  await (await checkbox('code',fixture.project.id)).check();
  await (await checkbox('data',fixture.dataMap.id)).check();
  await page.locator('#map-cleanup-preview').click();
  await page.locator('#map-cleanup-dialog').waitFor({state:'visible'});
  assert.equal(await page.evaluate(()=>document.activeElement?.id),'map-cleanup-cancel');
  assert.equal(await page.locator('#map-cleanup-dialog img').count(),0,'Names must remain literal');
  await page.keyboard.press('Escape');
  await page.locator('#map-cleanup-dialog').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.activeElement?.id==='map-cleanup-preview');
  assert.equal((await api('/api/projects')).body.projects.find(item=>item.id===fixture.project.id).snapshotId,fixture.project.snapshotId);
  // Only this explicitly confirmed request reaches the server; the response is deliberately lost.
  await page.route('**/api/technical/map-cleanup/*/execute',async route=>{
    executeRequests++;planId=new URL(route.request().url()).pathname.split('/').at(-2);
    await route.fetch();await route.abort('connectionreset');
  });
  await page.locator('#map-cleanup-preview').click();
  await page.locator('#map-cleanup-confirm').click();
  await page.locator('#map-cleanup-check').waitFor({state:'visible'});
  assert.equal(executeRequests,1);
  const result=await waitTerminal('/api/technical/map-cleanup/'+planId);
  assert.equal(result.mapsCleared,true);assert.equal(result.pendingPurgeCount,0);
  assert.ok(Number.isFinite(result.removedBytes)&&result.removedBytes>0);
  await page.locator('#map-cleanup-check').click();
  await page.locator('#map-cleanup-dialog').waitFor({state:'hidden'});
  await page.locator('#map-cleanup-status').filter({hasText:'Снимки очищены'}).waitFor();
  const projects=(await api('/api/projects')).body.projects;
  const code=projects.find(item=>item.id===fixture.project.id);
  assert.equal(code.snapshotId,null);assert.equal(code.status,'not-built');
  const data=(await api('/api/data-maps/'+fixture.dataMap.id)).body;
  assert.equal(data.snapshotId,null);assert.equal(data.status,'not-built');
  await verifyPreserved();
  await page.screenshot({path:output+'/completed.png',fullPage:true});
  // Rebuild the retained registrations, then replay the old operation identity.
  const rebuilt=await api('/api/projects/'+fixture.project.id+'/rebuild',{});
  assert.equal(rebuilt.status,202);await waitTerminal('/api/jobs/'+rebuilt.body.job.id);
  const refreshed=await api('/api/data-maps/'+fixture.dataMap.id+'/refresh',{force:true});
  assert.equal(refreshed.status,202);await waitTerminal('/api/data-jobs/'+refreshed.body.job.id);
  const newCode=(await api('/api/projects')).body.projects.find(item=>item.id===fixture.project.id);
  const newData=(await api('/api/data-maps/'+fixture.dataMap.id)).body;
  assert.ok(newCode.snapshotId&&newCode.snapshotId!==fixture.project.snapshotId);
  assert.ok(newData.snapshotId&&newData.snapshotId!==fixture.dataMap.snapshotId);
  const replay=await api('/api/technical/map-cleanup/'+planId+'/execute',{confirm:true});
  assert.equal(replay.status,200);assert.equal(replay.body.planId,planId);
  assert.equal((await api('/api/projects')).body.projects.find(item=>item.id===fixture.project.id).snapshotId,newCode.snapshotId);
  assert.equal((await api('/api/data-maps/'+fixture.dataMap.id)).body.snapshotId,newData.snapshotId);
  await verifyPreserved();
  let stalePlan;
  for(let attempt=0;attempt<100;attempt++) {
    const preview=await api('/api/technical/map-cleanup/preview',{maps:[{kind:'code',id:fixture.project.id}]});
    if(preview.status===200){stalePlan=preview.body;break;}
    assert.equal(preview.status,409);assert.equal(preview.body.code,'CLEANUP_BUSY');
    await page.waitForTimeout(100);
  }
  assert.ok(stalePlan,'Expected a quiescent preview before staleness check');
  const changed=await api('/api/projects/'+fixture.project.id+'/rebuild',{});
  assert.equal(changed.status,202);await waitTerminal('/api/jobs/'+changed.body.job.id);
  const changedId=(await api('/api/projects')).body.projects.find(item=>item.id===fixture.project.id).snapshotId;
  let rejected;
  for(let attempt=0;attempt<100;attempt++) {
    rejected=await api('/api/technical/map-cleanup/'+stalePlan.planId+'/execute',{confirm:true});
    if(rejected.body.code!=='CLEANUP_BUSY')break;
    await page.waitForTimeout(100);
  }
  assert.equal(rejected.status,409);assert.equal(rejected.body.code,'CLEANUP_STALE');
  assert.equal((await api('/api/projects')).body.projects.find(item=>item.id===fixture.project.id).snapshotId,changedId);
  await verifyPreserved();assert.deepEqual(errors,[]);
  await writeFile(output+'/receipt.json',JSON.stringify({status:'PASS',executeRequests,planId,removedBytes:result.removedBytes,
    checks:['empty-default-selection','batch-code-data','cancel-focus-escape-return','literal-names','lost-ack-same-plan-get',
      'real-purge','registrations-not-built','source-hashes','historical-views','unrelated-map-and-scopes','rebuild','old-replay-preserves-new-snapshots','stale-plan-refused'],errors},null,2));
  console.log('Cleanup real browser PASS: explicit batch purge, lost reply, preserved views/sources, rebuild and safe replay');
} catch(error) {
  await page.screenshot({path:output+'/failure.png',fullPage:true});
  console.error('Cleanup status:',await page.locator('#map-cleanup-status').textContent().catch(()=>''));throw error;
} finally {await browser.close();}
