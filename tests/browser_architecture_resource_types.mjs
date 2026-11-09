/** Real Graphify architecture resource styles/clusters; only a marked synthetic profile is allowed. */
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir,realpath} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';

const base=process.env.ATLAS_ARCHITECTURE_URL,contextPath=process.env.ATLAS_ARCHITECTURE_CONTEXT,output=process.env.ATLAS_ARCHITECTURE_OUTPUT;
assert(base&&contextPath&&output,'Set explicit synthetic origin, context and output.');
const fixture=JSON.parse(await readFile(contextPath,'utf8')),origin=new URL(base);
assert.equal(origin.origin,fixture.origin);assert.equal(origin.protocol,'http:');assert.equal(origin.hostname,'127.0.0.1');
assert(origin.port&&!['8765','53407'].includes(origin.port));assert.equal(fixture.fixturePrepared,true);
assert.equal(origin.username,'');assert.equal(origin.password,'');assert.equal(origin.pathname,'/');
const dataRoot=await realpath(fixture.dataDir),sourceRoot=await realpath(fixture.fixtureDir);
assert(sourceRoot.startsWith(path.dirname(dataRoot)+path.sep));
assert.equal(await readFile(path.join(path.dirname(dataRoot),fixture.marker),'utf8'),'synthetic-fixture-only\n');
const paletteOnly=process.env.ATLAS_ARCHITECTURE_PHASE==='palette';
const kinds=['database','http','kafka','rabbit'],labels={database:'БД',http:'HTTP',kafka:'Kafka',rabbit:'RabbitMQ'};
const results=[];await mkdir(output,{recursive:true});
const {chromium}=await import(process.env.ATLAS_PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,...(process.env.ATLAS_TEST_CHROMIUM?{executablePath:process.env.ATLAS_TEST_CHROMIUM}:{})});

/**
 * Hashes the addressed frontend inputs, including this runner; drift invalidates every successful case.
 * @returns {Promise<object>} Relative paths and SHA256 digests; no source text is exported.
 */
async function sourceHashes(){
  const result={};
  for(const file of ['web/app.js','web/map-empty-state.js','web/architecture-resource-style.js','web/architecture-exploration.css','web/graph.js','web/flow-renderer.js','web/flow-card.js','web/node-card-layout.js','web/system-architecture.js','web/architecture-layout.js','web/architecture-clusters.js','web/architecture-session.js','web/architecture-blocks.js','web/layout.js','web/color-scheme.css','web/color-schemes.json','web/index.html','tests/browser_architecture_resource_types.mjs']){
    result[file]=createHash('sha256').update(await readFile(new URL('../'+file,import.meta.url))).digest('hex');
  }
  return result;
}
const before=await sourceHashes();

/**
 * Confirms the server/profile/snapshot and immutable synthetic input files before any UI interaction.
 * @param {import('playwright').BrowserContext} context Fresh nonpersistent browser context.
 * @returns {Promise<void>} Mismatched profiles, projects or source bytes fail closed.
 */
async function preflight(context){
  const storage=await context.request.get(origin.origin+'/api/technical/storage',{maxRedirects:0});assert.equal(storage.status(),200);
  assert.equal(await realpath((await storage.json()).storagePath),dataRoot);
  const response=await context.request.get(origin.origin+'/api/projects',{maxRedirects:0});assert.equal(response.status(),200);
  const projects=(await response.json()).projects;assert.equal(projects.length,1);assert.equal(projects[0].id,fixture.projectId);
  assert.equal(projects[0].snapshotId,fixture.snapshotId);assert.equal(await realpath(projects[0].path),sourceRoot);
  for(const [relative,digest] of Object.entries(fixture.sourceHashes)){
    const filename=await realpath(path.resolve(sourceRoot,relative));assert(filename.startsWith(sourceRoot+path.sep));
    assert.equal(createHash('sha256').update(await readFile(filename)).digest('hex'),digest);
  }
}

/** Open the map activity only when its actual controls are hidden, preserving an already-open panel. */
async function openMapControls(page){
  if(await page.locator('#workspace-left').getAttribute('aria-hidden')==='true'||!await page.locator('#view-mode').isVisible())await page.locator('[data-activity="map"]').click();
}

/** Close actual narrow drawers instead of bypassing their hit targets. */
async function closeDrawers(page){
  if(page.viewportSize().width>=600)return;
  for(const [id,activity] of [['workspace-left','map'],['workspace-right','details']])if(await page.locator('#'+id).getAttribute('aria-hidden')==='false')await page.locator('[data-activity="'+activity+'"]').click();
}

/** Visit one real raw declaration through public search, recording architecture exploration normally. */
async function visit(page,name){
  if(!await page.locator('#navigation-search').isVisible())await page.locator('[data-activity="search"]').click();
  await page.locator('#navigation-search').fill(name);await page.locator('#navigation-results .navigation-result').first().waitFor();
  await page.locator('#navigation-search').press('Enter');
}

/**
 * Captures actual SVG boxes in viewport map coordinates and resolved colors without changing geometry.
 * @param {import('playwright').Page} page Loaded synthetic Architecture view.
 * @returns {Promise<object>} Current transforms, card/cluster bounds, glyph/text boxes and opaque color samples.
 */
async function scene(page){
  return page.evaluate(()=>{
    const viewport=document.querySelector('#viewport'),inverse=viewport.getCTM().inverse();
    const box=element=>{
      const b=element.getBBox(),m=inverse.multiply(element.getCTM());
      const points=[[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]].map(([x,y])=>new DOMPoint(x,y).matrixTransform(m));
      const left=Math.min(...points.map(p=>p.x)),right=Math.max(...points.map(p=>p.x)),top=Math.min(...points.map(p=>p.y)),bottom=Math.max(...points.map(p=>p.y));
      return {left,right,top,bottom,width:right-left,height:bottom-top};
    };
    const canvas=document.createElement('canvas');canvas.width=canvas.height=1;const ctx=canvas.getContext('2d');
    const color=value=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=value;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data];};
    const all=[...viewport.querySelectorAll('[data-node-id]')].map(group=>({id:group.getAttribute('data-node-id'),kind:group.getAttribute('data-architecture-kind')||'owners',transform:group.getAttribute('transform'),box:box(group),bodyBox:group.querySelector('.flow-card-body')?box(group.querySelector('.flow-card-body')):null}));
    const cards=[...viewport.querySelectorAll('.architecture-resource-card')].map(group=>{
      const surface=group.querySelector('.architecture-resource-surface,.flow-card-body'),type=group.querySelector('.architecture-resource-type-label'),icon=group.querySelector('.architecture-resource-icon');
      const content=[...group.querySelectorAll('.flow-card-name,.flow-card-context,.flow-port-label,.community-label')].filter(el=>el.textContent.trim()).map(el=>({text:el.textContent,box:box(el)}));
      return {id:group.getAttribute('data-node-id'),kind:group.getAttribute('data-architecture-kind'),label:type?.textContent,iconKind:icon?.getAttribute('data-resource-icon'),iconShape:icon?.innerHTML,
        box:box(group),surfaceBox:surface&&box(surface),badgeBox:type&&box(type),iconBox:icon&&box(icon),content,
        stroke:surface&&color(getComputedStyle(surface).stroke),background:surface&&color(getComputedStyle(surface).fill),foreground:type&&color(getComputedStyle(type).fill),ink:icon&&color(getComputedStyle(icon).stroke),typeFont:type&&getComputedStyle(type).fontSize,ariaLabel:group.getAttribute('aria-label')};
    });
    const clusters=[...viewport.querySelectorAll('.architecture-resource-cluster')].map(group=>{
      const surface=group.querySelector('.architecture-cluster-surface'),heading=group.querySelector('.architecture-cluster-heading');
      return {kind:group.getAttribute('data-cluster-kind'),label:heading?.textContent,box:surface&&box(surface),headingBox:heading&&box(heading),background:surface&&color(getComputedStyle(surface).fill),foreground:heading&&color(getComputedStyle(heading).fill),pointerEvents:getComputedStyle(group).pointerEvents};
    });
    const matrix=viewport.transform.baseVal.consolidate().matrix;
    return {svgWidth:viewport.ownerSVGElement.clientWidth,svgHeight:viewport.ownerSVGElement.clientHeight,cameraValues:{x:matrix.e,y:matrix.f,scale:matrix.a},camera:viewport.getAttribute('transform'),all,cards,clusters,theme:document.documentElement.dataset.colorMode,documentWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth,reduced:matchMedia('(prefers-reduced-motion:reduce)').matches,rootFont:getComputedStyle(document.documentElement).fontSize,controlFont:getComputedStyle(document.querySelector('#view-mode')).fontSize};
  });
}

/** WCAG2 ratio for measured opaque RGBA pairs; this does not certify the full interface or AT. */
function contrast(a,b){
  assert.equal(a?.[3],255,'Opaque measured foreground required');assert.equal(b?.[3],255,'Opaque measured background required');
  const luminance=rgb=>{let sum=0;for(let i=0;i<3;i++){const s=rgb[i]/255;sum+=(s<=.04045?s/12.92:((s+.055)/1.055)**2.4)*[.2126,.7152,.0722][i];}return sum;};
  const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
}
/** Whether two measured boxes overlap beyond numeric rounding. */
function intersects(a,b){return Math.min(a.right,b.right)-Math.max(a.left,b.left)>.5&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>.5;}
/** Whether one full measured box is contained with a one-pixel rendering tolerance. */
function contains(outer,inner){return inner.left>=outer.left-1&&inner.right<=outer.right+1&&inner.top>=outer.top-1&&inner.bottom<=outer.bottom+1;}

/** Validate four visual identities and actual text/glyph separation, including nonzero IN/OUT labels. */
function checkCards(value,display){
  assert.deepEqual([...new Set(value.cards.map(card=>card.kind))].sort(),kinds);
  const strokes=new Map(),glyphs=new Map(),ratios=[];
  for(const card of value.cards){
    assert.equal(card.label,labels[card.kind]);assert.equal(card.iconKind,card.kind);assert(card.ariaLabel.includes(card.label)||card.ariaLabel.includes('ресурсы'));
    assert(card.surfaceBox&&card.iconBox&&card.badgeBox);assert(contains(card.surfaceBox,card.iconBox));assert(contains(card.surfaceBox,card.badgeBox));
    for(const text of card.content){assert(!intersects(card.iconBox,text.box),'Icon overlaps text '+card.id+': '+text.text);assert(!intersects(card.badgeBox,text.box),'Type label overlaps text '+card.id+': '+text.text);}
    const textRatio=contrast(card.foreground,card.background),iconRatio=contrast(card.ink,card.background);assert(textRatio>=4.5);assert(iconRatio>=3);
    strokes.set(card.kind,JSON.stringify(card.stroke));glyphs.set(card.kind,card.iconShape);ratios.push({id:card.id,kind:card.kind,textRatio,iconRatio});
  }
  assert.equal(new Set(strokes.values()).size,4,'Four actual computed stroke colors required');assert.equal(new Set(glyphs.values()).size,4,'Four distinct glyph structures required');
  if(display==='flow')for(const kind of kinds)assert(value.cards.some(card=>card.kind===kind&&card.content.some(text=>/^(IN|OUT)\s+[1-9]/.test(text.text))),'Expected positive connection count for '+kind);
  return ratios;
}

/** Validate real cluster frames, full card/label containment and absence of foreign-node occlusion. */
function checkClusters(value){
  const resourceClusters=value.clusters.filter(cluster=>kinds.includes(cluster.kind));
  assert.deepEqual([...new Set(resourceClusters.map(cluster=>cluster.kind))].sort(),[...new Set(value.cards.map(card=>card.kind))].sort());
  for(const card of value.cards)assert(resourceClusters.some(cluster=>cluster.kind===card.kind&&contains(cluster.box,card.box)),'Card/label not contained: '+card.id);
  for(const cluster of value.clusters){
    assert(cluster.box?.width>0&&cluster.box.height>0&&cluster.label);assert(contains(cluster.box,cluster.headingBox),'Cluster heading outside '+JSON.stringify({kind:cluster.kind,box:cluster.box,heading:cluster.headingBox}));assert.equal(cluster.pointerEvents,'none');assert(contrast(cluster.foreground,cluster.background)>=4.5);
    const own=value.all.filter(node=>node.kind===cluster.kind&&contains(cluster.box,node.box));assert(own.length,'Empty/ghost cluster '+cluster.kind);
    for(const node of value.all)if(node.kind!==cluster.kind)assert(!intersects(cluster.box,node.box),'Cluster covers foreign node: '+cluster.kind+' → '+node.id);
  }
  for(let i=0;i<value.all.length;i++)for(let j=i+1;j<value.all.length;j++)assert(!intersects(value.all[i].box,value.all[j].box),'Visible node/label overlap '+value.all[i].id+' / '+value.all[j].id);
}

/** Keep retained centers and viewport camera byte-identical after disclosure/pin/filter operations. */
function stable(previous,current){
  assert.equal(current.camera,previous.camera,'Camera changed during enrichment');const positions=new Map(current.all.map(node=>[node.id,node.transform]));
  for(const node of previous.all)if(positions.has(node.id))assert.equal(positions.get(node.id),node.transform,'Retained node moved: '+node.id);
}

/**
 * Checks public Flow fit against actual post-hook card coordinates and the160-unit routing gutter.
 * @param {object} value Measured scene after an explicit native Fit action.
 */
function checkFlowFit(value){
  const boxes=value.all.map(node=>node.bodyBox).filter(Boolean);assert(boxes.length);
  const left=Math.min(...boxes.map(box=>box.left))-160,right=Math.max(...boxes.map(box=>box.right))+160;
  const top=Math.min(...boxes.map(box=>box.top))-160,bottom=Math.max(...boxes.map(box=>box.bottom))+160;
  const scale=Math.max(.02,Math.min(1.6,(value.svgWidth-70)/(right-left),(value.svgHeight-130)/(bottom-top)));
  assert(Math.abs(value.cameraValues.scale-scale)<.00001,'Flow Fit uses stale pre-cluster bounds');
  assert(Math.abs(value.cameraValues.x-(value.svgWidth/2-(left+right)/2*scale))<.5);
  assert(Math.abs(value.cameraValues.y-((value.svgHeight-50)/2-(top+bottom)/2*scale))<.5);
}

/** Metadata-only Architecture owns its own empty/error UI, even when raw Code has zero nodes. */
async function noRawEmpty(page){assert.equal(await page.locator('#empty').isHidden(),true,'Generic raw-map empty overlay obstructs Architecture');}

/** Download the canonical revealed model via actual host controls; do not crop to the visible camera. */
async function exportJSON(page,name){
  await closeDrawers(page);await page.locator('#architecture-export-format').selectOption('json');const pending=page.waitForEvent('download');await page.locator('#architecture-export').click();
  const artifact=await pending,filename=path.join(output,name);await artifact.saveAs(filename);return JSON.parse(await readFile(filename,'utf8'));
}

try{
  const cases=[];for(const display of ['topology','flow'])for(const dark of [false,true])cases.push({name:display+'-'+(dark?'dark':'light'),display,dark,width:1440,height:1000,scale:100,reduce:false});
  cases.push({name:'topology-narrow-text200',display:'topology',dark:false,width:390,height:844,scale:200,reduce:true},{name:'flow-narrow-text200',display:'flow',dark:true,width:390,height:844,scale:200,reduce:true});
  for(const config of cases.filter(item=>!process.env.ATLAS_ARCHITECTURE_CASE||item.name===process.env.ATLAS_ARCHITECTURE_CASE)){
    const context=await browser.newContext({viewport:{width:config.width,height:config.height},acceptDownloads:true,reducedMotion:config.reduce?'reduce':'no-preference'});const page=await context.newPage();page.setDefaultTimeout(12000);
    let evidence=null;const errors=[],blocked=[],requests=[];page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/api/**',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(['GET','HEAD'].includes(request.method()))return route.continue();
      const prefix='/api/projects/'+encodeURIComponent(fixture.projectId)+'/';
      if(url.origin===origin.origin&&request.method()==='POST'&&['system-architecture','graph-node','node-description','describe'].some(name=>url.pathname===prefix+name)){
        if(url.pathname.endsWith('/system-architecture')){const body=request.postDataJSON();assert.equal(body.snapshotId,fixture.snapshotId);assert((body.visitedNodeIds||[]).length<=64);requests.push(body);}
        return route.continue();
      }
      blocked.push({method:request.method(),path:url.pathname});return route.abort('blockedbyclient');
    });
    try{
      await preflight(context);await page.goto(origin.origin+'/?repo='+fixture.projectId+'&view=architecture&display='+config.display);
      await page.locator('#project-name').filter({hasText:fixture.projectName}).waitFor();
      await page.evaluate(async config=>{document.documentElement.style.fontSize=config.scale+'%';const {colorSchemes}=await import('/color-scheme.js');const themes=colorSchemes();await themes.ready;themes.apply({id:config.dark?'dark':'light'},false);},config);
      assert.equal(await page.locator('#viewport [data-architecture-kind]').count(),0,'Code map must retain ordinary styling');
      for(const name of ['Produce','Consume','Query','Http','external_call'])await visit(page,name);
      await openMapControls(page);await page.locator('#view-mode').selectOption('system');await page.locator('#architecture-export:not([disabled])').waitFor();
      await openMapControls(page);await page.locator('#system-scale').selectOption('projects');
      await page.locator('#display-provider').selectOption(config.display);assert.equal(await page.locator('#display-provider').inputValue(),config.display);
      assert(await page.locator(config.display==='flow'?'#viewport .flow-card':'#viewport .community-node').count());await closeDrawers(page);
      const collapsed=await exportJSON(page,config.name+'-collapsed.json');assert.equal(collapsed.edges.filter(edge=>edge.factId).length,5);
      let current=await scene(page);evidence=current;assert.equal(current.theme,config.dark?'dark':'light');assert(current.documentWidth<=current.viewportWidth+2);if(config.scale===200)assert(parseFloat(current.rootFont)>=32,'Actual root font scaling required');
      assert(current.cards.every(card=>config.dark?card.background.slice(0,3).every(v=>v<128):card.background.slice(0,3).every(v=>v>128)),'Actual theme palette required');
      const colorRatios=checkCards(current,config.display);if(!paletteOnly)checkClusters(current);
      for(const kind of paletteOnly?[]:['kafka','rabbit','database']){
        await closeDrawers(page);await page.locator('#fit').click();const block=page.locator('#viewport [data-node-id^="architecture-block:"][data-node-id$=":'+kind+'"]');
        await block.focus();const previous=await scene(page);if(config.display==='flow')checkFlowFit(previous);await block.press('Enter');await page.waitForFunction(kind=>document.querySelector('#viewport [data-node-id$=":'+kind+'"]')?.getAttribute('aria-expanded')==='true',kind);
        current=await scene(page);evidence=current;await noRawEmpty(page);stable(previous,current);checkCards(current,config.display);checkClusters(current);
        assert.equal(await page.evaluate(()=>document.activeElement?.getAttribute('data-node-id')),await block.getAttribute('data-node-id'));
        const child=collapsed.nodes.find(node=>node.kind===kind&&node.resourceKind);assert(child);assert(current.cards.some(card=>card.id===child.id),'Expanded child missing');
        const beforePin=current;await closeDrawers(page);await page.locator('#architecture-pin').click();assert.equal(await page.locator('#architecture-pin').getAttribute('aria-pressed'),'true');
        current=await scene(page);evidence=current;await noRawEmpty(page);stable(beforePin,current);checkClusters(current);
      }
      if(!paletteOnly){
        const full=await exportJSON(page,config.name+'-expanded.json');assert.equal(full.pinnedIds.length,3);assert.deepEqual(full.nodes,collapsed.nodes);assert.deepEqual(full.edges,collapsed.edges);
        await openMapControls(page);await page.locator('#system-search').fill('orders');await closeDrawers(page);const filtered=await scene(page);evidence=filtered;stable(current,filtered);checkClusters(filtered);
        await openMapControls(page);await page.locator('#system-search').fill('');await closeDrawers(page);current=await scene(page);evidence=current;checkClusters(current);
        if(config.width>=600){
          const projectScene=current;await openMapControls(page);await page.locator('#system-scale').selectOption('repositories');await closeDrawers(page);
          const repositories=await scene(page);evidence=repositories;checkCards(repositories,config.display);checkClusters(repositories);
          await openMapControls(page);await page.locator('#system-scale').selectOption('projects');await closeDrawers(page);current=await scene(page);evidence=current;stable(projectScene,current);checkClusters(current);
        }
      }
      await noRawEmpty(page);await page.locator('#fit').click();current=await scene(page);if(config.display==='flow')checkFlowFit(current);
      await page.screenshot({path:path.join(output,config.name+'.png')});
      await openMapControls(page);await page.locator('#view-mode').selectOption('architecture');
      await page.waitForFunction(()=>!document.querySelector('#viewport [data-architecture-kind]'));assert.equal(await page.locator('#viewport .architecture-resource-cluster').count(),0);
      assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);assert(requests.some(body=>Object.values(fixture.visits).every(id=>body.visitedNodeIds.includes(id))));await preflight(context);
      results.push({...config,status:paletteOnly?'PASS_PALETTE_ONLY':'PASS',actualRenderer:config.display,sourceSnapshot:fixture.snapshotId,colorRatios,clusters:current.clusters,cards:current.cards.map(({id,kind,box})=>({id,kind,box})),rootFont:current.rootFont,controlFont:current.controlFont,diagramTypeFonts:[...new Set(current.cards.map(card=>card.typeFont))],rootScalePercent:config.scale,requestCount:requests.length,blockedMutations:blocked.length});
    }catch(error){results.push({...config,status:'FAIL',error:error.message});await writeFile(path.join(output,config.name+'-failure-scene.json'),JSON.stringify({config,evidence},null,2));await page.screenshot({path:path.join(output,config.name+'-failure.png')}).catch(()=>{});throw error;}
    finally{await context.close();}
  }
  if(!process.env.ATLAS_ARCHITECTURE_CASE||process.env.ATLAS_ARCHITECTURE_CASE==='direct-system-first'){
    const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true}),page=await context.newPage();page.setDefaultTimeout(15000);
    let rawGraphRequests=0;const architectureRequests=[];
    try{
      await preflight(context);
      const response=await context.request.post(origin.origin+'/api/projects/'+encodeURIComponent(fixture.projectId)+'/system-architecture',{data:{snapshotId:fixture.snapshotId,relatedProjectIds:[],visitedNodeIds:[]},headers:{Origin:origin.origin},maxRedirects:0});
      assert.equal(response.status(),200);const data=await response.json(),byId=new Map(data.nodes.map(node=>[node.id,node]));
      const owners=data.nodes.filter(node=>node.kind==='project').map(node=>({node,score:new Set(data.edges.filter(edge=>edge.source===node.id||edge.target===node.id).map(edge=>byId.get(edge.source===node.id?edge.target:edge.source)?.kind).filter(kind=>kinds.includes(kind))).size})).sort((a,b)=>b.score-a.score);
      assert(owners[0]?.score===4,'Fixture requires one configured owner connected to all four resource types');const owner=owners[0].node;
      await page.route('**/api/**',async route=>{
        const request=route.request(),url=new URL(request.url()),prefix='/api/projects/'+encodeURIComponent(fixture.projectId)+'/';
        if(request.method()==='GET'&&url.pathname===prefix+'graph')rawGraphRequests++;
        if(['GET','HEAD'].includes(request.method()))return route.continue();
        assert.equal(url.pathname,prefix+'system-architecture');assert.equal(request.method(),'POST');const body=request.postDataJSON();assert.deepEqual(body.visitedNodeIds,[]);architectureRequests.push(body);return route.continue();
      });
      await page.goto(origin.origin+'/?repo='+fixture.projectId+'&view=system&display=flow');await page.locator('#architecture-export:not([disabled])').waitFor();await noRawEmpty(page);
      assert.match(await page.locator('#status').innerText(),/без загрузки полного графа/);
      await openMapControls(page);await page.locator('#system-scale').selectOption('projects');await page.locator('#display-provider').selectOption('flow');await closeDrawers(page);
      let target;for(const item of await page.locator('#viewport [data-node-id]').all())if(await item.getAttribute('data-node-id')===owner.id)target=item;assert(target);
      await target.focus();await target.press('Enter');await noRawEmpty(page);
      // Disclosure intentionally keeps the camera; fit before asking for viewport-mounted cards.
      await closeDrawers(page);await page.locator('#fit').click();
      await page.waitForFunction(()=>new Set([...document.querySelectorAll('#viewport [data-architecture-kind]')].map(node=>node.getAttribute('data-architecture-kind'))).size===4);
      await closeDrawers(page);await page.locator('#fit').click();await noRawEmpty(page);const value=await scene(page);checkCards(value,'flow');checkClusters(value);checkFlowFit(value);
      assert.equal(rawGraphRequests,0,'Direct System must use snapshot metadata without loading raw Code');assert(architectureRequests.length&&architectureRequests.every(body=>body.visitedNodeIds.length===0));await preflight(context);
      await page.screenshot({path:path.join(output,'direct-system-first.png')});results.push({name:'direct-system-first',status:'PASS',rawGraphRequests,metadataOnly:true,rawVisits:0,configuredTypes:kinds,emptyHidden:true,cameraValues:value.cameraValues,cardCount:value.cards.length,fit:'actual-post-hook-card-bounds+160gutter'});
    }catch(error){results.push({name:'direct-system-first',status:'FAIL',error:error.message});await page.screenshot({path:path.join(output,'direct-system-first-failure.png')}).catch(()=>{});throw error;}
    finally{await context.close();}
  }
}finally{
  await browser.close();const after=await sourceHashes(),sourceStable=JSON.stringify(before)===JSON.stringify(after);
  if(!sourceStable)for(const item of results)if(item.status.startsWith('PASS'))item.status='INVALID';
  const suffix=process.env.ATLAS_ARCHITECTURE_CASE?'-'+process.env.ATLAS_ARCHITECTURE_CASE:'';
  await writeFile(path.join(output,'receipt'+suffix+'.json'),JSON.stringify({timestamp:new Date().toISOString(),sourceStable,sources:{before,after},phase:paletteOnly?'palette-only':'full',results,scope:'Real Graphify snapshot, real local API and native SVG controls in a marked synthetic profile. Read-only API POST allowlist; no configuration changes or analyzed-code execution. Root font scaling is recorded separately from measured fixed-pixel diagram/control fonts; it is not full browser text-zoom qualification. Pair contrast and browser geometry are not AT/native/full accessibility or runtime-broker qualification.'},null,2));
  assert(sourceStable,'Frontend inputs changed; receipt invalid.');
}
console.log(JSON.stringify({status:paletteOnly?'PASS_PALETTE_ONLY':'PASS',cases:results.length}));
