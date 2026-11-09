/** Read-only real-server acceptance for project orientation and partial analysis. */
import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile,readdir,lstat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';

const {chromium}=await import(process.env.ATLAS_PLAYWRIGHT_MODULE || 'playwright');
const base=new URL(process.env.ATLAS_ORIENTATION_URL || 'http://127.0.0.1:63587');
assert.equal(base.hostname,'127.0.0.1');
assert.equal(base.port,'63587','only the owned fixture server is permitted');
const projectId=process.env.ATLAS_ORIENTATION_PROJECT || 'a011b6d8f4751463';
const fixture=JSON.parse(await readFile('outputs/atlas-orientation-implementation-20261030/browser-context.json','utf8'));
assert.equal(projectId,fixture.projectId);
assert.ok(fixture.profile.startsWith('/private/tmp/atlas208-browser-20261030-'));
assert.equal(await readFile(path.join(fixture.profile,'.atlas208-owned'),'utf8'),'isolated-source-native-browser-fixture\n');
const registry=JSON.parse(await readFile(path.join(fixture.dataDir,'projects.json'),'utf8'));
const registration=registry[projectId];assert.equal(registration.path,fixture.root);
assert.ok(registration._graph.startsWith(fixture.profile+'/'));
assert.equal((await lstat(registration._graph)).isFile(),true);
const pinnedSnapshot=path.basename(path.dirname(registration._graph));
const storage=await (await fetch(new URL('/api/technical/storage',base),{redirect:'error'})).json();
assert.equal(storage.storagePath,fixture.dataDir,'server owns the expected isolated storage');
const catalog=await (await fetch(new URL('/api/projects',base),{redirect:'error'})).json();
const published=catalog.projects.find(project=>project.id===projectId);
assert.equal(published?.snapshotId,pinnedSnapshot);assert.equal(published?.snapshotAvailable,true);
assert.equal(published?.status,'completed');assert.equal(catalog.activeJob,null);
assert.equal(published.path,fixture.root);assert.equal((await lstat(fixture.root)).isSymbolicLink(),false);
const output=process.env.ATLAS_ORIENTATION_OUTPUT || 'outputs/atlas-orientation-implementation-20261030/browser/source';
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.ATLAS_TEST_CHROMIUM?{executablePath:process.env.ATLAS_TEST_CHROMIUM}:{})});
const checks=[],errors=[],blocked=[],requests=[];

/** Snapshot only our marked fixture source; refuse links instead of traversing them. */
async function sourceIdentity(directory){
  const result={};
  for(const entry of await readdir(directory,{withFileTypes:true})){
    const name=path.join(directory,entry.name);assert.equal(entry.isSymbolicLink(),false);
    if(entry.isDirectory())Object.assign(result,await sourceIdentity(name));
    else if(entry.isFile())result[path.relative(fixture.root,name)]=createHash('sha256').update(await readFile(name)).digest('hex');
  }
  return result;
}
const sourceBefore=await sourceIdentity(fixture.root);

/** Keep acceptance read-only, including requests initiated by optional extensions. */
async function guard(context){
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(!['http:','https:'].includes(url.protocol))return route.continue();
    const readProject=[projectId,'81f3d8b46513338e'].includes(url.pathname.split('/')[3]);
    const readPost=request.method()==='POST'&&readProject
      &&/\/(search-nodes|graph-node|node-description|method-flow|system-architecture|source|semantic-search)$/.test(url.pathname);
    if(url.origin!==base.origin || (!['GET','HEAD'].includes(request.method())&&!readPost)){
      blocked.push({method:request.method(),path:url.pathname});return route.abort();
    }
    requests.push({method:request.method(),path:url.pathname});return route.continue();
  });
}

/** Wait for the compact overview; secondary details intentionally remain closed. */
async function openOverview(page){
  const root=page.locator('#project-overview');
  if(!(await root.isVisible()))await page.locator('#project-overview-open').click();
  await root.waitFor({state:'visible'});
  await page.waitForFunction(()=>document.querySelector('#project-overview [data-overview-body]')?.getAttribute('aria-busy')==='false');
  return root;
}

try{
  for(const config of [
    {name:'wide-light',width:1440,height:1000,dark:false,scale:100,reduce:false},
    {name:'wide-dark',width:1440,height:1000,dark:true,scale:100,reduce:false},
    {name:'narrow-text200',width:390,height:844,dark:false,scale:200,reduce:true},
  ]){
    const context=await browser.newContext({viewport:{width:config.width,height:config.height},reducedMotion:config.reduce?'reduce':'no-preference'});
    await guard(context);
    const page=await context.newPage();page.setDefaultTimeout(12000);page.on('pageerror',error=>errors.push(error.message));
    await page.goto(new URL('/?repo='+projectId+'&view=system',base).href);
    const overview=page.locator('#project-overview');
    await page.locator('#project-overview-open').waitFor();
    assert.equal(await overview.isVisible(),false,'the map starts without expanded overview');
    const initialGraph=await page.locator('#graph').boundingBox();
    const root=await openOverview(page);
    await page.evaluate(async config=>{
      document.documentElement.style.fontSize=config.scale+'%';
      const {colorSchemes}=await import('/color-scheme.js');const themes=colorSchemes();await themes.ready;themes.apply({id:config.dark?'dark':'light'},false);
    },config);
    assert.match(await root.innerText(),/Local example service|Local reusable design/);
    for(const label of ['Понять проект','Найти обработчик или запуск','Оценить влияние изменения','Области и источники'])assert.ok((await root.innerText()).includes(label),label);
    assert.equal(await root.locator('[data-overview-status]').getAttribute('role'),'status');
    assert.equal(await root.locator('[data-overview-status]').evaluate(node=>Boolean(node.closest('[aria-busy]'))),false);
    assert.equal(await page.locator('#project-load-state').isVisible(),true);
    assert.match(await page.locator('#project-load-state').innerText(),/частич/);
    const issue=root.locator('.analysis-issue').first();await issue.waitFor({state:'attached'});
    const alternative=issue.getByRole('link',{name:'Открыть этот файл как данные',includeHidden:true});
    const href=await alternative.getAttribute('href');assert.ok(href.startsWith('/data.html?project='+projectId));
    const bounds=await root.boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=config.width+1,'overview fits viewport');
    const graphBounds=await page.locator('#graph').boundingBox();
    if(config.width>1000){assert.ok(graphBounds.height>=config.height*.45,'map retains at least45% of initial viewport height');assert.equal(await page.locator('#workspace-right').getAttribute('aria-hidden'),'true');}
    assert.equal(await root.locator('[data-overview-details]').evaluate(node=>node.open),false);
    assert.equal(await root.locator('[data-overview-issues]').evaluate(node=>node.open),false);
    assert.equal(await page.locator('#architecture-advanced').evaluate(node=>node.open),false);
    assert.equal(await root.evaluate(node=>node.scrollWidth<=node.clientWidth+1),true,'no panel horizontal overflow');
    await page.screenshot({path:output+'/'+config.name+'.png'});
    checks.push({name:config.name,bounds,graphBounds,initialGraph,partialVisible:true,starts:3});
    if(config.name==='wide-light'){
      await page.locator('#project-overview-close').click();
      assert.equal(await page.locator('#project-overview-open').evaluate(node=>node===document.activeElement),true);
      await openOverview(page);
      await root.locator('[data-overview-details]>summary').focus();await root.locator('[data-overview-details]>summary').press('Enter');
      await page.waitForFunction(()=>new URL(location.href).searchParams.get('overview')==='open');
      const original=page.url();
      await root.locator('[data-overview-action^="area:"]').first().click();
      await page.waitForFunction(()=>document.querySelector('#status')?.textContent.includes('Область'));
      assert.ok(page.url()!==original,'area has restorable URL');
      await page.goBack();await openOverview(page);
      assert.equal(page.url(),original,'back restores overview scope');
      await root.locator('[data-overview-action="impact"]').click();
      await page.locator('#project-task-context').waitFor({state:'visible'});
      assert.match(await page.locator('#project-task-hint').innerText(),/выберите|Оценка влияния/);
      assert.ok(new URL(page.url()).searchParams.has('task'));
      await page.goto(new URL('/?repo='+projectId+'&node=service_api_load_items&view=architecture',base).href);
      await openOverview(page);await root.locator('[data-overview-action="impact"]').click();
      await page.locator('#project-impact-show:not([disabled])').waitFor({state:'attached'});
      assert.match(await page.locator('#status').innerText(),/связи|выберите/i);
      await page.goto(new URL('/?repo='+projectId+'&view=system',base).href);await openOverview(page);
      await root.locator('[data-overview-action="entrypoints"]').click();
      assert.equal(await page.locator('#discovery-mode').inputValue(),'entries');
      await page.goto(new URL('/?repo='+projectId+'&view=system',base).href);await openOverview(page);
      await root.locator('[data-overview-action="understand"]').click();
      assert.equal(await page.locator('#discovery-mode').inputValue(),'meaning');
      await page.goto(new URL(href,base).href);
      await page.locator('#create-form').waitFor({state:'visible'});
      assert.equal(await page.locator('#files input:checked').count(),1,'only exact discovered file selected');
      assert.equal(requests.some(request=>request.path==='/api/data-maps'&&request.method==='POST'),false,'data navigation never imports automatically');
      checks.push({name:'real-task-actions-history-and-data-file-selection',pass:true});
    }
    await context.close();
  }
  // Qualify geometry on the real large map too, using only a previously copied
  // snapshot under this same marked profile. No current checkout is indexed.
  const realCopy=JSON.parse(await readFile('outputs/atlas-orientation-implementation-20261030/browser/ag-design-copy.json','utf8'));
  const realRegistration=registry[realCopy.projectId];assert.ok(realRegistration._graph.startsWith(fixture.profile+'/'));
  assert.equal(createHash('sha256').update(await readFile(realRegistration._graph)).digest('hex'),realCopy.copiedGraphSha256);
  for(const viewport of [{width:1440,height:900},{width:1280,height:800}]){
    const context=await browser.newContext({viewport});await guard(context);
    const page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',error=>errors.push(error.message));
    const began=performance.now();
    await page.goto(new URL('/?repo='+realCopy.projectId+'&view=architecture&level=projects&display=flow',base).href);
    await page.locator('#project-overview-open').waitFor();await page.locator('#viewport [data-node-id]').first().waitFor();
    const graphDomReadyMs=Math.round(performance.now()-began);
    const graphBounds=await page.locator('#graph').boundingBox();
    assert.ok(graphBounds.height>=viewport.height*.45,'large map retains45% of first screen');
    assert.equal(await page.locator('#workspace-right').getAttribute('aria-hidden'),'true');
    assert.equal(await page.locator('#project-overview [data-overview-details]').evaluate(node=>node.open),false);
    await page.screenshot({path:output+'/ag-design-'+viewport.width+'.png'});
    checks.push({name:'actual-ag-design-copy-'+viewport.width,snapshotId:realCopy.snapshotId,nodes:realCopy.nodeCount,graphBounds,graphDomReadyMs,visibleCards:await page.locator('#viewport [data-node-id]').count()});
    await context.close();
  }
  assert.deepEqual(errors,[],'no browser exceptions');assert.deepEqual(blocked,[],'no unexpected mutation or external traffic');
  assert.deepEqual(await sourceIdentity(fixture.root),sourceBefore,'fixture sources unchanged');
  const finalRegistry=JSON.parse(await readFile(path.join(fixture.dataDir,'projects.json'),'utf8'));
  assert.deepEqual(finalRegistry[projectId],registration,'pinned fixture registration unchanged');
  await writeFile(output+'/acceptance.json',JSON.stringify({pass:true,projectId,snapshotId:pinnedSnapshot,origin:base.origin,storageRoot:fixture.dataDir,checks,errors,blocked,sourceBefore,requestCount:requests.length,limitations:['Synthetic project; no user study or assistive-technology certification.','Read-only browser; data import is qualified separately by the existing streaming engine.']},null,2));
  console.log('Orientation browser PASS: real overview, partial errors, three task starts, history and data selection in three layouts.');
}catch(error){
  await writeFile(output+'/failure.json',JSON.stringify({pass:false,error:String(error),errors,blocked,checks},null,2));throw error;
}finally{await browser.close();}
