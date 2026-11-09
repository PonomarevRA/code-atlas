/** Real current-map recovery and registration-only removal, confined to a marked owned profile. */
import assert from 'node:assert/strict';
import {readFile,realpath,rm,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
const fixture=JSON.parse(await readFile(process.env.ATLAS_CLEANUP_CONTEXT,'utf8'));
const origin=new URL(fixture.origin);
assert.equal(origin.hostname,'127.0.0.1');assert.ok(!['8765','53407'].includes(origin.port));
assert.equal(await readFile(path.join(path.dirname(fixture.dataDir),'.atlas-map-cleanup-test'),'utf8'),'synthetic-fixture-only\n');
const {chromium}=await import(process.env.ATLAS_PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,...(process.env.ATLAS_TEST_CHROMIUM?{executablePath:process.env.ATLAS_TEST_CHROMIUM}:{})});
const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage();
page.setDefaultTimeout(60000);
const output=process.env.ATLAS_CLEANUP_OUTPUT;assert.ok(output);await mkdir(output,{recursive:true});

/** Read only this fixed local origin and refuse redirects before parsing a response. */
async function get(route) {
  const target=new URL(route,origin);assert.equal(target.origin,origin.origin);
  const response=await context.request.get(target.href,{maxRedirects:0});assert.equal(response.status(),200);
  return response.json();
}

try {
  const storage=await get('/api/technical/storage');
  assert.equal(await realpath(storage.storagePath),await realpath(fixture.dataDir));
  const projects=(await get('/api/projects')).projects;
  assert.deepEqual(projects.map(x=>x.id).sort(),[fixture.project.id,fixture.unrelatedProject.id].sort());
  const project=projects.find(x=>x.id===fixture.project.id);
  assert.equal(await realpath(project.path),await realpath(path.join(fixture.fixtureDir,'Cleanup Alpha')));
  assert.match(project.id,/^[a-f0-9]{16}$/);assert.match(project.snapshotId,/^[a-f0-9]{32}$/);
  const snapshot=path.join(fixture.dataDir,'maps/projects',project.id,project.snapshotId);
  assert.equal(await realpath(snapshot),snapshot);
  // Only the checked synthetic snapshot is removed to reproduce external folder deletion.
  await rm(snapshot,{recursive:true});
  const replies=[];
  page.on('response',async response=>{
    if(response.url().includes('/api/projects/'+project.id+'/graph?')&&response.status()===409)replies.push(await response.json());
  });
  const mode=process.env.ATLAS_RECOVERY_VIEW || 'architecture';
  let staleHint=false;
  if(mode==='system')await page.route('**/api/projects',async route=>{
    const response=await route.fetch();const catalog=await response.json();
    if(!staleHint){catalog.projects.find(x=>x.id===project.id).snapshotAvailable=true;staleHint=true;}
    await route.fulfill({response,json:catalog});
  });
  await page.goto(origin.origin+'/?repo='+project.id+'&view='+mode);
  await page.locator('#project-name').filter({hasText:'Cleanup Alpha'}).waitFor();
  await page.locator('#status').filter({hasText:mode==='system'?'Системная карта открыта':'Карта загружена'}).waitFor();
  const restored=(await get('/api/projects')).projects.find(x=>x.id===project.id);
  assert.ok(restored.snapshotId&&restored.snapshotId!==project.snapshotId);
  assert.equal(replies.filter(x=>x.code==='SNAPSHOT_REBUILD_STARTED').length,1);
  assert.ok(replies.every(x=>['SNAPSHOT_REBUILD_STARTED','SNAPSHOT_REBUILD_RUNNING'].includes(x.code)));
  // Real per-row confirmation forgets only the registration, preserving even its current files.
  const currentFile=path.join(fixture.dataDir,'maps/projects',project.id,restored.snapshotId,'atlas-graph.json');
  const hash=createHash('sha256').update(await readFile(currentFile)).digest('hex');
  // Publication can finish before derived health workers drain; respect the same admission fence as users.
  let ready=false;
  for(let attempt=0;attempt<300;attempt++) {
    const response=await context.request.post(origin.origin+'/api/technical/map-cleanup/preview',{
      maxRedirects:0,headers:{Origin:origin.origin},data:{maps:[{kind:'code',id:project.id}],action:'forget-registration'},
    });
    const body=await response.json();
    if(response.status()===200){ready=true;break;}
    assert.equal(response.status(),409);assert.equal(body.code,'CLEANUP_BUSY');
    await page.waitForTimeout(200);
  }
  assert.equal(ready,true,'Derived workers must finish before metadata mutation');
  await page.goto(origin.origin+'/technical.html#atlas-storage');
  const row=page.locator('#storage-maps tr').filter({hasText:'Cleanup Alpha'});
  await row.locator('.map-cleanup-forget').click();
  await page.locator('#map-cleanup-dialog').waitFor({state:'visible'});
  assert.equal(await page.evaluate(()=>document.activeElement?.id),'map-cleanup-cancel');
  await page.keyboard.press('Escape');assert.equal(await row.count(),1);
  await row.locator('.map-cleanup-forget').click();await page.locator('#map-cleanup-confirm').click();
  await row.waitFor({state:'detached'});
  assert.equal((await get('/api/projects')).projects.some(x=>x.id===project.id),false);
  assert.equal(createHash('sha256').update(await readFile(currentFile)).digest('hex'),hash);
  await get('/api/views/'+fixture.savedCodeViewId);
  await get('/api/data-maps/'+fixture.dataMap.id+'/views/'+fixture.savedDataViewId);
  assert.equal((await get('/api/projects')).projects.find(x=>x.id===fixture.unrelatedProject.id).snapshotId,fixture.unrelatedProject.snapshotId);
  for(const [name,expected] of Object.entries(fixture.sourceHashes))assert.equal(createHash('sha256').update(await readFile(path.join(fixture.fixtureDir,name))).digest('hex'),expected);
  await page.screenshot({path:output+'/removed-entry.png',fullPage:true});
  await writeFile(output+'/receipt.json',JSON.stringify({status:'PASS',mode,staleCachedAvailability:staleHint,oneAutomaticRecovery:true,currentMapOpenedAfterRecovery:true,registrationRemoved:true,snapshotFilesPreserved:true,savedViewsPreserved:true,sourceHashesPreserved:true,unrelatedProjectPreserved:true},null,2));
  console.log('Real missing-snapshot browser recovery and registration-only forget PASS');
} catch(error) {await page.screenshot({path:output+'/failure.png',fullPage:true});throw error;}
finally {await browser.close();}
