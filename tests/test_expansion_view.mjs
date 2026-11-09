/** Раскрытие закрепляет обзор в видимой миникарте; сворачивание возвращает тот же SVG и камеру. */
import assert from 'node:assert/strict';
import {GraphView} from '../web/graph.js';
import {Svg} from './svg_fixture.mjs';
import {expansionNeighborhood} from '../web/expansion.js';
import {selectInCurrentView} from '../web/selection-controller.js';
globalThis.document={createElementNS:(_,tag)=>new Svg(tag)};
globalThis.ResizeObserver=class{observe(){}};
const graph={nodes:[{id:'a',label:'A',codeProjectId:'p',changedFile:true},{id:'b',label:'B',codeProjectId:'q'},{id:'c',label:'C',codeProjectId:'q'}],edges:[{source:'a',target:'b',relation:'calls'},{source:'b',target:'a',relation:'references'},{source:'c',target:'a',relation:'calls'}]};
const summary=[{id:'p',label:'P',nodeCount:1,codeProjectId:'p'},{id:'q',label:'Q',nodeCount:2,codeProjectId:'q'}];
/** Читает translate одного вложенного слоя для проверки фактического места на карте. */
function translation(layer){return layer.attributes.transform.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);}
for(const provider of ['topology','flow']){
 const svg=new Svg('svg'),viewport=new Svg('g');viewport.setAttribute('id','viewport');svg.append(viewport);
 const selected=[],view=new GraphView(svg,id=>selected.push(id),()=>{});
 view.colorProjects=[{id:'p',label:'P'},{id:'q',label:'Q'}];view.setDisplayProvider(provider);
 view.renderOverview(summary,[{source:'p',target:'q',weight:2}],null);
 view.camera={x:123,y:234,scale:.77};view.transform();
 const camera={...view.camera},originalChildren=[...viewport.children],originalPositions=JSON.stringify([...view.positions]),originalMap=view.nodeElements;
 const baseBounds=view.geometryBounds(),payload=expansionNeighborhood(graph,'a');
 view.renderExpansion(payload);
 const expansion=view.expansion,child=expansion.child,localBounds=child.geometryBounds();
 assert.deepEqual(view.geometryBounds(),localBounds,'Pinned screen inset must not add phantom world-space area');
 /** Проверяет фактические экранные координаты миникарты после панорамирования/масштаба. */
 const assertInset=()=>{
  const [frameX,frameY,frameScale]=translation(expansion.frame);
  assert.ok(Math.abs(frameX*view.camera.scale+view.camera.x-10)<1e-6);
  assert.ok(Math.abs(frameY*view.camera.scale+view.camera.y-10)<1e-6);
  assert.ok(Math.abs(frameScale*view.camera.scale-1)<1e-6);
  const background=expansion.frame.querySelector('rect');
  assert.equal(background.attributes.width,170);assert.equal(background.attributes.height,132);
  const [x,y,scale]=translation(expansion.context);
  assert.ok(baseBounds.minX*scale+x>=9.99);assert.ok(baseBounds.maxX*scale+x<=160.01);
  assert.ok(baseBounds.minY*scale+y>=25.99);assert.ok(baseBounds.maxY*scale+y<=126.01);
  assert.equal(expansion.frame.querySelector('text').textContent,'Общая карта');
 };
 assertInset();expansion.focus();assertInset();
 view.camera={x:-555,y:700,scale:2.4};view.transform();assertInset();
 expansion.fitLocal();assertInset();
 assert.equal(JSON.stringify([...view.positions]),originalPositions);
 assert.deepEqual([...view.communityElements.keys()],['p','q']);
 assert.deepEqual([...child.nodeElements.keys()].sort(),['a','b','c']);
 for(const item of originalChildren)assert.ok(expansion.context.children.includes(item));
 const markerIds=viewport.querySelectorAll('marker').map(node=>node.attributes.id);
 assert.equal(new Set(markerIds).size,markerIds.length,'Nested renderers must not reuse marker IDs');
 for(const path of [...expansion.local.querySelectorAll('.graph-edge'),...expansion.local.querySelectorAll('.flow-edge')]){
  const marker=viewport.querySelector(path.attributes['marker-end'].slice(4,-1));
  assert.equal(marker.children[0].attributes.fill,path.attributes.stroke);
 }
 view.fit();assertInset();const fitted={...view.camera};
 assert.ok((view.geometryBounds().minX)*fitted.scale+fitted.x>=0);
 const detailBefore=[...expansion.local.children];
 assert.equal(selectInCurrentView(view,'b'),true);
 assert.deepEqual(view.camera,fitted);
 for(let i=0;i<detailBefore.length;i++)assert.equal(expansion.local.children[i],detailBefore[i]);
 child.nodeElements.get('b').group.listeners.click();assert.deepEqual(selected,['b']);
 assert.ok(child.nodeElements.get('a').group.querySelector('.change-ring'));
 // App исключает раскрытые ID из агрегатных счётчиков: A и C здесь представлены деталями.
 assert.equal(view.setSearchMatches(['a','c'],new Map()),2);
 view.renderExpansion(expansionNeighborhood(graph,'b'));
 view.fit();view.clearExpansion();
 assert.deepEqual(view.camera,camera,'Repeated expansion must retain the original collapse camera');
 assert.equal(view.nodeElements,originalMap);
 assert.equal(JSON.stringify([...view.positions]),originalPositions);
 assert.equal(viewport.children.length,originalChildren.length);
 for(let i=0;i<originalChildren.length;i++)assert.equal(viewport.children[i],originalChildren[i]);
 // Even if raw symbols are also in context, represented regex IDs are counted once.
 view.render(graph.nodes,graph.edges,null);view.renderExpansion(payload);
 assert.equal(view.setSearchMatches(['a','b']),2);
 view.clearExpansion();
 // Смена провайдера при раскрытии: новый обзор получает собственную fit-камеру.
 view.renderOverview(summary,[{source:'p',target:'q',weight:2}],null);
 view.fit();view.renderExpansion(payload);view.expansion.focus();
 view.setDisplayProvider(provider==='flow'?'topology':'flow');
 view.renderOverview(summary,[{source:'p',target:'q',weight:2}],null);
 view.fit();const switchedBaseline={...view.camera};
 view.renderExpansion(payload);view.expansion.focus();view.clearExpansion();
 assert.deepEqual(view.camera,switchedBaseline);
}
console.log('Expansion SVG context, marker isolation, selection and exact collapse PASS');
