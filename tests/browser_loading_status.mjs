/** Real Atlas assets in isolated Chromium; synthetic API scopes never touch user projects. */
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
const {chromium}=await import(process.env.ATLAS_PLAYWRIGHT_MODULE || 'playwright');
const base=process.env.ATLAS_LOADING_URL || 'http://127.0.0.1:53407';
const output='outputs/loading-indicator-20261028';await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.ATLAS_TEST_CHROMIUM ? {executablePath: process.env.ATLAS_TEST_CHROMIUM} : {})});
const context=await browser.newContext({viewport:{width:1280,height:900}});
const page=await context.newPage();page.setDefaultTimeout(7000);const errors=[];page.on('pageerror',error=>errors.push(error.message));
const projects=['a','b'].map(id=>({id,name:id.toUpperCase(),path:'/fixture/'+id,snapshotId:'snap-'+id,status:'completed'}));
let holding=false, pending=[], sourceScopes=[];
const graph=id=>({nodes:[{id:id+'-method',label:'Run()',kind:'method',file:id+'.py',language:'python',line:1,codeProjectId:'p',community:'c',x:0,y:0}],edges:[],metadata:{snapshotId:'snap-'+id,groupingAlgorithm:'graphistry'},codeProjects:[{id:'p',label:'P',path:'.',language:'python'}],communities:[{id:'c',label:'Fixture',nodeCount:1}]});
await page.route('**/api/mods',route=>route.fulfill({json:{mods:[]}}));
await page.route('**/api/projects**',async route=>{
 const url=new URL(route.request().url()),path=url.pathname;
 if(path==='/api/projects')return route.fulfill({json:{projects,activeJob:null}});
 const id=path.split('/')[3],snapshotId='snap-'+id;
 if(path.endsWith('/graph')) {
  if(holding) return new Promise(resolve=>pending.push({id,url:url.href,finish:async(status=200)=>{try{await route.fulfill({status,json:status===200?graph(id):{error:'Synthetic graph failure'}});}catch{}resolve();}}));
  return route.fulfill({json:graph(id)});
 }
 if(path.endsWith('/source')) {sourceScopes.push({id,...route.request().postDataJSON()});return route.fulfill({json:{snapshotId,file:id+'.py',text:'def Run():\n    return 1',line:1,totalLines:2,syntax:{language:'python',status:'unavailable'}}});}
 if(path.endsWith('/node-description')) return route.fulfill({json:{snapshotId,nodeId:id+'-method',label:'Run()',kind:'method',file:id+'.py',line:1,events:[],unresolved:[]}});
 if(path.endsWith('/documentation-index')) return route.fulfill({json:{snapshotId,modelAvailable:false,index:null}});
 if(path.endsWith('/code-health/refresh')) return route.fulfill({json:{snapshotId,status:'not-requested'}});
 return route.fulfill({json:{snapshotId,entries:[],repositories:[],results:[],nodes:[],edges:[]}});
});
const indicator=()=>page.locator('[data-atlas-loading-status]');
const waitPending=async count=>{for(let i=0;i<100&&pending.length<count;i++)await page.waitForTimeout(20);assert.ok(pending.length>=count,'expected pending graph');};
try {
 await page.goto(base+'/?repo=a&node=a-method');
 await page.locator('#project-name').filter({hasText:'A'}).waitFor();
 await page.locator('#source-open:not([disabled])').waitFor({state:'attached'});
 if(!await page.locator('#source-open').isVisible()) await page.locator('[data-activity="tools"]').click();
 await page.locator('#source-open').waitFor({state:'visible'});
 await page.waitForTimeout(300);
 assert.equal(await indicator().isVisible(),false,'idle polling is quiet');
 await page.locator('[data-activity="map"]').click();
 await page.locator('#view-mode').selectOption('groups');
 await page.locator('#status').filter({hasText:'Карта загружена'}).waitFor();
 await page.locator('#algorithm').evaluate(node=>{for(let parent=node.parentElement;parent;parent=parent.parentElement)if(parent instanceof HTMLDetailsElement)parent.open=true;});
 holding=true;
 await page.locator('#algorithm').selectOption('natural');await waitPending(1);
 await indicator().waitFor({state:'visible'});
 assert.equal(await page.locator('#source-open').isDisabled(),false);
 assert.equal(await page.locator('#navigation-search').isDisabled(),false);
 assert.equal(await page.locator('#loading-graph').isVisible(),false);
 assert.equal(await indicator().evaluate(node=>getComputedStyle(node).pointerEvents),'none');
 await page.locator('#source-open').click();
 await page.waitForFunction(()=>document.querySelector('#source-viewer')?.textContent.includes('return 1'));
 assert.deepEqual(sourceScopes.at(-1),{id:'a',nodeId:'a-method',snapshotId:'snap-a'});
 // Opening the source cancels an in-flight history restore; its stale error must not replace the reader.
 for(const item of pending.splice(0)) await item.finish(500);
 await indicator().waitFor({state:'hidden'});
 await page.locator('#status').filter({hasText:'Загрузка отменена'}).waitFor();
 assert.equal(await page.locator('#source-open').isDisabled(),false);
 // A separate current request fails without a navigation action cancelling it.
 await page.locator('#algorithm').selectOption('natural');await waitPending(1);
 await indicator().waitFor({state:'visible'});
 await pending.shift().finish(500);
 await page.locator('#status').filter({hasText:'Synthetic graph failure'}).waitFor();
 assert.equal(await page.locator('#source-open').isDisabled(),false,'failed refresh keeps committed selection');
 await indicator().waitFor({state:'hidden'});
 await page.locator('#source-viewer [data-source="close"]').click();
 // A new repository is pending; source actions still address the committed A snapshot.
 await page.locator('[data-activity="repository"]').click();
 await page.locator('#projects').selectOption('b');await waitPending(1);
 await indicator().waitFor({state:'visible'});
 assert.equal(await page.locator('#project-name').innerText(),'A');
 assert.equal(await page.locator('#source-open').isDisabled(),false);
 await page.locator('#projects').selectOption('a');await waitPending(2);
 const stale=pending.shift(),latest=pending.shift();await latest.finish();
 await page.locator('#project-name').filter({hasText:'A'}).waitFor();await stale.finish();
 await page.waitForTimeout(200);assert.equal(await page.locator('#project-name').innerText(),'A');
 await indicator().waitFor({state:'hidden'});
 const scenarios=[];
 for(const config of [{name:'wide-light',width:1280,height:900,dark:false,scale:100,reduce:false},{name:'wide-dark',width:1280,height:900,dark:true,scale:100,reduce:false},{name:'narrow-text200',width:360,height:800,dark:false,scale:200,reduce:true},{name:'short-dark-text200',width:800,height:450,dark:true,scale:200,reduce:true}]) {
  await page.setViewportSize({width:config.width,height:config.height});
  await page.emulateMedia({reducedMotion:config.reduce?'reduce':'no-preference'});
  await page.evaluate(async config=>{
   document.documentElement.style.fontSize=config.scale+'%';
   const {colorSchemes}=await import('/color-scheme.js');
   const themes=colorSchemes();await themes.ready;themes.apply({id:config.dark?'dark':'light'},false);
   const {beginLoading}=await import('/loading-state.js');window.finishLoadingFixture=beginLoading({ru:'Загрузка подробностей выбранного узла…',en:'Loading selected node details…'});
  },config);
  await indicator().waitFor({state:'visible'});
  const bounds=await indicator().boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=config.width+1&&bounds.y>=0&&bounds.y+bounds.height<=config.height);
  assert.equal(await indicator().locator('progress').getAttribute('value'),null);
  if(config.reduce)assert.equal(await indicator().locator('progress').isVisible(),false);
  assert.equal(await indicator().evaluate(node=>node.scrollWidth<=node.clientWidth),true);
  if(!config.reduce) {
   await indicator().locator('button').click();
   assert.equal(await indicator().locator('progress').isVisible(),false,'pause hides native motion while status remains');
   await indicator().locator('button').press('Space');
   assert.equal(await indicator().locator('progress').isVisible(),true);
  }
  await page.screenshot({path:output+'/'+config.name+'.png'});
  scenarios.push({name:config.name,bounds,reducedMotion:config.reduce});
  await page.evaluate(()=>window.finishLoadingFixture());await indicator().waitFor({state:'hidden'});
 }
 assert.deepEqual(errors,[]);
 await writeFile(output+'/browser.json',JSON.stringify({pass:true,scenarios,checks:['idle-quiet','same-project-retained','source-readable-during-load','failure-retained','cross-project-committed-scope','superseded-response-rejected','no-pointer-block','native-indeterminate','narrow/text200/themes/reducedmotion'],errors},null,2));
 console.log('Loading browser PASS: retained interactive graph, scope/cancel/error, quiet idle and4layout scenarios');
} catch(error) {await page.screenshot({path:output+'/browser-failure.png'});console.error('Page errors',errors);console.error('Parents',await page.locator('#source-open').evaluate(node=>{const rows=[];for(let p=node;p;p=p.parentElement)rows.push({tag:p.tagName,id:p.id,cls:p.className,hidden:p.hidden,display:getComputedStyle(p).display,visibility:getComputedStyle(p).visibility});return rows;}));console.error('Status',await page.locator('#status').textContent().catch(()=>''));throw error;}
finally {await browser.close();}
