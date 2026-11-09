/** Exercise actual bulk map deletion only in an explicitly marked, isolated synthetic profile. */
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir,realpath,stat} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';

assert(process.env.ATLAS_CLEANUP_CONTEXT,'Explicit synthetic context required');
const fixture=JSON.parse(await readFile(process.env.ATLAS_CLEANUP_CONTEXT,'utf8'));
const origin=new URL(fixture.origin);
assert.equal(origin.hostname,'127.0.0.1');assert.equal(origin.protocol,'http:');
assert(origin.port&&!['8765','53407'].includes(origin.port));
assert.equal(origin.pathname,'/');assert.equal(origin.search,'');assert.equal(origin.hash,'');
assert.equal(origin.username,'');assert.equal(origin.password,'');assert.equal(fixture.fixturePrepared,true);
const dataRoot=await realpath(fixture.dataDir),sourceRoot=await realpath(fixture.fixtureDir),profileRoot=path.dirname(dataRoot);
assert(sourceRoot.startsWith(profileRoot+path.sep),'Synthetic sources must share the marked profile');
assert.equal(await readFile(path.join(profileRoot,'.atlas-map-cleanup-test'),'utf8'),'synthetic-fixture-only\n');
const output=process.env.ATLAS_CLEANUP_OUTPUT||'outputs/map-delete/browser';await mkdir(output,{recursive:true});
const {chromium}=await import(process.env.ATLAS_PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,...(process.env.ATLAS_TEST_CHROMIUM?{executablePath:process.env.ATLAS_TEST_CHROMIUM}:{})});
const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
page.setDefaultTimeout(15000);const errors=[];page.on('pageerror',error=>errors.push(error.message));
let executeRequests=0,planId,committed,readdedProject;
const checks=[];

/**
 * Accepts only bounded public identities, never context-provided path fragments.
 * @param {unknown} id Catalog, snapshot, view or job ID from the explicit fixture/API.
 * @returns {string} Encoded route component; malformed identities fail before a request.
 */
function segment(id) {assert.equal(typeof id,'string');assert(/^[A-Za-z0-9_-]{1,128}$/.test(id));return encodeURIComponent(id);}

/**
 * Sends one same-origin JSON request without redirects or automatic mutation retries.
 * @param {string} route Host-owned /api route with validated identity components.
 * @param {?object} body Undefined performs GET; an explicit body performs POST.
 * @returns {Promise<object>} HTTP status and parsed response, including expected refusal responses.
 */
async function api(route,body) {
  assert(route.startsWith('/api/')&&!route.includes('://'));
  const target=new URL(route,origin);assert.equal(target.origin,origin.origin);
  const options={maxRedirects:0,...(body===undefined?{}:{data:body,headers:{Origin:origin.origin}})};
  const response=body===undefined?await context.request.get(target.href,options):await context.request.post(target.href,options);
  assert.equal(new URL(response.url()).origin,origin.origin);assert(response.status()<300||response.status()>=400,'Redirect refused');
  return {status:response.status(),body:await response.json()};
}

/**
 * Reads only a named file contained in a known synthetic root and records immutable content/stat evidence.
 * @param {string} root Canonical fixture source/data root.
 * @param {string} relative Fixed or explicitly listed fixture-relative file name.
 * @returns {Promise<object>} SHA256 and exact file identity; escaped/missing files fail the test.
 */
async function fileIdentity(root,relative) {
  assert(!path.isAbsolute(relative));const filename=await realpath(path.resolve(root,relative));assert(filename.startsWith(root+path.sep));
  const value=await stat(filename,{bigint:true});assert(value.isFile());
  return {sha256:createHash('sha256').update(await readFile(filename)).digest('hex'),device:String(value.dev),inode:String(value.ino),size:String(value.size),mtimeNs:String(value.mtimeNs)};
}

/**
 * Waits for one owned operation/build with a finite deadline; unexpected terminal states fail immediately.
 * @param {string} route Exact known operation/job route; no new operation is created while polling.
 * @returns {Promise<object>} Completed server receipt; failure/timeout remains a failed qualification.
 */
async function waitCompleted(route) {
  const deadline=Date.now()+120000;
  while(Date.now()<deadline){const result=await api(route);assert.equal(result.status,200);if(result.body.status==='completed')return result.body;
    assert(['queued','running','purging'].includes(result.body.status),JSON.stringify(result.body));await page.waitForTimeout(150);}
  throw Error('Synthetic operation timed out');
}

/**
 * Finds one exact code/data row without interpreting titles as selectors or HTML.
 * @param {string} kind Public map layer.
 * @param {string} id Fixture catalog identity.
 * @returns {Promise<import('playwright').Locator>} Current literal row; absence is a test failure.
 */
async function rowFor(kind,id) {
  const key=JSON.stringify([kind,id]);
  for(const row of await page.locator('#storage-maps [data-map-key]').all())if(await row.getAttribute('data-map-key')===key)return row;
  throw Error('Expected synthetic map row missing');
}

/**
 * Opens a real delete preview, retrying only the explicit pre-commit CLEANUP_BUSY admission refusal.
 * @returns {Promise<object>} Exact ready delete-map receipt; every other error fails without weaker fallback.
 */
async function showDeleteReview() {
  const deadline=Date.now()+15000;
  while(Date.now()<deadline){
    const responseWait=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/technical/map-cleanup/preview'&&response.request().method()==='POST');
    await page.locator('#map-cleanup-delete').click();const response=await responseWait,body=await response.json();
    if(response.status()===200){assert.equal(body.action,'delete-map');assert.equal(body.status,'ready');
      assert.deepEqual(body.maps.map(({kind,id})=>({kind,id})).sort((a,b)=>a.kind.localeCompare(b.kind)),fixture.selection.slice().sort((a,b)=>a.kind.localeCompare(b.kind)));
      await page.locator('#map-cleanup-dialog').waitFor({state:'visible'});return body;}
    assert.equal(response.status(),409);assert.equal(body.code,'CLEANUP_BUSY');await page.waitForTimeout(150);
  }
  throw Error('Expected quiescent synthetic delete preview');
}

let unrelatedIdentity;
const unrelatedRelative=path.join('maps','projects',segment(fixture.unrelatedProject.id),segment(fixture.unrelatedProject.snapshotId),'atlas-graph.json');

/**
 * Verifies source/protected-file hashes, authored views and the exact unrelated snapshot remain intact.
 * @returns {Promise<void>} Any missing/changed evidence fails; no repair or recreation is attempted.
 */
async function verifyPreserved() {
  for(const [relative,digest] of Object.entries(fixture.sourceHashes))assert.equal((await fileIdentity(sourceRoot,relative)).sha256,digest,'Source changed: '+relative);
  for(const [relative,digest] of Object.entries(fixture.protected))assert.equal((await fileIdentity(dataRoot,relative)).sha256,digest,'Protected file changed: '+relative);
  assert.equal((await api('/api/views/'+segment(fixture.savedCodeViewId))).status,200);
  assert.equal((await api('/api/data-maps/'+segment(fixture.dataMap.id)+'/views/'+segment(fixture.savedDataViewId))).status,200);
  const projects=(await api('/api/projects')).body.projects;
  assert.equal(projects.find(item=>item.id===fixture.unrelatedProject.id)?.snapshotId,fixture.unrelatedProject.snapshotId);
  assert.deepEqual(await fileIdentity(dataRoot,unrelatedRelative),unrelatedIdentity,'Unrelated snapshot changed');
}

try {
  // All profile/path/snapshot fences run before the first preview or execute request.
  const storage=await api('/api/technical/storage');assert.equal(storage.status,200);assert.equal(await realpath(storage.body.storagePath),dataRoot);
  const projects=(await api('/api/projects')).body.projects;
  assert.deepEqual(projects.map(item=>item.id).sort(),[fixture.project.id,fixture.unrelatedProject.id].sort());
  for(const expected of [fixture.project,fixture.unrelatedProject]){
    const live=projects.find(item=>item.id===expected.id);assert.equal(live.snapshotId,expected.snapshotId);assert.equal(live.path,expected.path);
    assert((await realpath(live.path)).startsWith(sourceRoot+path.sep));
  }
  const data=(await api('/api/data-maps/'+segment(fixture.dataMap.id))).body;
  assert.equal(data.snapshotId,fixture.dataMap.snapshotId);assert.equal(data.root,fixture.dataMap.root);
  assert.deepEqual(storage.body.maps.map(item=>item.id),[fixture.dataMap.id]);
  assert.deepEqual(fixture.selection,[{kind:'code',id:fixture.project.id},{kind:'data',id:fixture.dataMap.id}]);
  unrelatedIdentity=await fileIdentity(dataRoot,unrelatedRelative);await verifyPreserved();checks.push('marked-profile-origin-path-and-snapshot-fences');
  await page.goto(origin.origin+'/technical.html#atlas-storage');await page.locator('#storage-maps input[type=checkbox]').first().waitFor();
  assert.equal(await page.locator('#storage-maps input:checked').count(),0);assert.equal(await page.locator('#map-cleanup-delete').isDisabled(),true);
  for(const map of fixture.selection)await (await rowFor(map.kind,map.id)).locator('input[type=checkbox]').check();
  await showDeleteReview();assert.equal(await page.evaluate(()=>document.activeElement?.id),'map-cleanup-cancel');
  assert.equal(await page.locator('#map-cleanup-dialog img').count(),0);assert.match(await page.locator('#map-cleanup-consequences').innerText(),/записи выбранных карт.*текущие и предыдущие снимки/);
  await page.keyboard.press('Escape');await page.locator('#map-cleanup-dialog').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.activeElement?.id==='map-cleanup-delete');await verifyPreserved();
  assert.equal((await api('/api/projects')).body.projects.find(item=>item.id===fixture.project.id)?.snapshotId,fixture.project.snapshotId);
  checks.push('empty-default-selection','distinct-native-delete-consent','cancel-focus-escape-return','literal-names');
  const review=await showDeleteReview();planId=review.planId;segment(planId);
  await page.route('**/api/technical/map-cleanup/*/execute',async route=>{
    const request=route.request(),url=new URL(request.url());assert.equal(url.origin,origin.origin);
    assert.equal(url.pathname,'/api/technical/map-cleanup/'+segment(planId)+'/execute');assert.deepEqual(request.postDataJSON(),{confirm:true});
    executeRequests++;await route.fetch({maxRedirects:0});await route.abort('connectionreset');
  });
  await page.locator('#map-cleanup-confirm').click();await page.locator('#map-cleanup-check').waitFor({state:'visible'});
  await page.locator('#map-cleanup-dialog').waitFor({state:'hidden'});assert.equal(executeRequests,1);
  committed=await waitCompleted('/api/technical/map-cleanup/'+segment(planId));
  assert.equal(committed.action,'delete-map');assert.equal(committed.mapsCleared,true);assert.equal(committed.registrationRemoved,true);
  assert.equal(committed.pendingPurgeCount,0);assert(Number.isFinite(committed.removedBytes)&&committed.removedBytes>0);
  // The unknown reply keeps old rows until the user explicitly checks and a fresh catalog arrives.
  for(const map of fixture.selection)assert(await rowFor(map.kind,map.id));
  await page.locator('#map-cleanup-check').click();
  await page.waitForFunction(selection=>selection.every(map=>![...document.querySelectorAll('#storage-maps [data-map-key]')].some(row=>row.dataset.mapKey===JSON.stringify([map.kind,map.id]))),fixture.selection);
  assert.match(await page.locator('#map-cleanup-status').innerText(),/Удаление выбранных карт.*завершено/);
  assert.equal((await api('/api/projects')).body.projects.some(item=>item.id===fixture.project.id),false);
  assert.equal((await api('/api/data-maps/'+segment(fixture.dataMap.id))).status,404);
  const afterStorage=(await api('/api/technical/storage')).body;
  assert.equal(afterStorage.codeMaps.some(item=>item.id===fixture.project.id),false);assert.equal(afterStorage.maps.some(item=>item.id===fixture.dataMap.id),false);
  await verifyPreserved();checks.push('actual-code-data-deletion','lost-ack-same-plan-get','both-commit-flags','fresh-catalog-row-removal','physical-purge-completed','sources-protected-files-views-unrelated-snapshot-preserved');
  await page.locator('#atlas-storage').screenshot({path:output+'/completed.png'});
  // Re-register exactly the original synthetic code path; old receipt replay must not touch this new map.
  const readded=await api('/api/projects',{path:fixture.project.path});assert.equal(readded.status,202);assert.equal(readded.body.project.id,fixture.project.id);
  await waitCompleted('/api/jobs/'+segment(readded.body.job.id));
  readdedProject=(await api('/api/projects')).body.projects.find(item=>item.id===fixture.project.id);
  assert(readdedProject?.snapshotId&&readdedProject.snapshotId!==fixture.project.snapshotId);assert.equal(readdedProject.path,fixture.project.path);
  const replay=await api('/api/technical/map-cleanup/'+segment(planId)+'/execute',{confirm:true});
  assert.equal(replay.status,200);assert.equal(replay.body.action,'delete-map');assert.equal(replay.body.planId,planId);
  const current=(await api('/api/projects')).body.projects.find(item=>item.id===fixture.project.id);
  assert.equal(current.snapshotId,readdedProject.snapshotId);assert.equal(current.path,fixture.project.path);
  assert.equal((await api('/api/projects/'+segment(current.id)+'/graph')).status,200);
  assert.equal((await api('/api/data-maps/'+segment(fixture.dataMap.id))).status,404);
  await verifyPreserved();checks.push('same-code-path-reregistration','old-operation-replay-preserves-new-registration-and-snapshot','new-graph-readable','saved-views-still-readable-after-replay');
  assert.deepEqual(errors,[]);
  await writeFile(output+'/receipt.json',JSON.stringify({status:'PASS',action:'delete-map',executeRequests,planId,removedBytes:committed.removedBytes,
    originalSnapshots:{code:fixture.project.snapshotId,data:fixture.dataMap.snapshotId},readdedProject,unrelatedIdentity,checks,errors,
    scope:'Actual HTTP/native browser controls in the marked synthetic profile only. Data registration remains absent; only the original code source is re-registered. No installed/default-user profile is touched.'},null,2));
  console.log('Delete-map real browser PASS: actual batch deletion, lost reply, preserved files/views, re-registration and safe replay');
}catch(error){
  await page.locator('#atlas-storage').screenshot({path:output+'/failure.png'}).catch(()=>{});
  await writeFile(output+'/receipt.json',JSON.stringify({status:'FAIL',action:'delete-map',executeRequests,planId,checks,error:error.message,errors},null,2));throw error;
}finally{await browser.close();}
