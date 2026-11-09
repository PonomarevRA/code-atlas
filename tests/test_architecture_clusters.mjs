/** Pure map-coordinate architecture grouping contracts; no SVG, browser or network required. */
import assert from 'node:assert/strict';
import {stableArchitectureGeometry} from '../web/architecture-layout.js';
import {nodeCardLayout} from '../web/node-card-layout.js';

/**
 * Build either renderer's footprint, taking flow dimensions from the actual production layout.
 * @param {Array<object>} nodes Visible fixture nodes with explicit semantic kinds.
 * @param {string} renderer topology or flow.
 * @returns {Map|object} Disposable geometry with distinct renderer-owned properties.
 */
function geometry(nodes, renderer='topology') {
  const actual = renderer==='flow'?nodeCardLayout(nodes.map(node=>({...node,system:true})),[]).positions:null;
  const positions = new Map(nodes.map(node => {
    const flow=actual?.get(node.id);
    return [node.id, flow?{...flow,labelWidth:flow.width+24,labelBottom:flow.height/2+16,color:'keep'}
      : {x:0,y:0,r:56,labelWidth:node.long?1200:240,labelBottom:node.long?170:116,color:'keep'}];
  }));
  return renderer==='flow'?{positions,containers:[]}:positions;
}
/**
 * Capture centers independently of renderer-owned sizes and metadata.
 * @param {Map|object} geo The temporary geometry to inspect.
 * @returns {Array} ID-sorted coordinate tuples.
 */
function centers(geo) {return [...(geo instanceof Map?geo:geo.positions)].map(([id,p])=>[id,p.x,p.y]).sort();}
/**
 * Compute the full card body and lower-label footprint.
 * @param {object} p One renderer position.
 * @returns {object} Bounding sides in map coordinates.
 */
function box(p) {
  const w=Math.max(100,p.width||p.w||(p.r||0)*2,p.labelWidth||0);
  const h=Math.max(35,(p.height||p.h||(p.r||0)*2)/2,p.r||0);
  return {left:p.x-w/2,right:p.x+w/2,top:p.y-h,bottom:p.y+Math.max(h,p.labelBottom||0)};
}
/**
 * Detect shared rectangle interior while permitting touching edges.
 * @param {object} a First bounding rectangle.
 * @param {object} b Second bounding rectangle.
 * @returns {boolean} Whether the interiors overlap.
 */
function intersects(a,b) {return a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;}
/**
 * Verify node separation, full labels, heading room and independent semantic regions.
 * @param {Map|object} geo Actual layout result.
 * @param {Array<object>} nodes Visible semantic node definitions.
 * @param {object} options regions=false permits unavoidable existing anchor-region conflicts.
 * @returns {void} Throws an assertion on a violated geometry invariant.
 */
function verify(geo,nodes,{regions=true}={}) {
  const points=geo instanceof Map?geo:geo.positions;
  const entries=[...points];
  for(let a=0;a<entries.length;a++)for(let b=a+1;b<entries.length;b++)assert(!intersects(box(entries[a][1]),box(entries[b][1])),`${entries[a][0]} overlaps ${entries[b][0]}`);
  const clusters=geo.architectureClusters;
  assert.equal(points.architectureClusters,clusters);
  assert.equal(clusters.reduce((sum,c)=>sum+c.nodeIds.length,0),nodes.length);
  for(const c of clusters) {
    assert(c.label&&c.width>=240&&Number.isFinite(c.x)&&Number.isFinite(c.y));
    for(const id of c.nodeIds) {
      const p=box(points.get(id));
      assert(p.left>=c.x+18-1e-8&&p.right<=c.x+c.width-18+1e-8,`${id} horizontal cluster bounds`);
      assert(p.top>=c.y+58-1e-8&&p.bottom<=c.y+c.height-18+1e-8,`${id} full label/headroom`);
    }
  }
  if(regions)for(let a=0;a<clusters.length;a++)for(let b=a+1;b<clusters.length;b++) {
    const x=clusters[a],y=clusters[b];
    assert(!intersects({left:x.x,right:x.x+x.width,top:x.y,bottom:x.y+x.height},{left:y.x,right:y.x+y.width,top:y.y,bottom:y.y+y.height}),`${x.kind} and ${y.kind} regions overlap`);
  }
}
const owners=Array.from({length:17},(_,i)=>({id:'owner-'+i,kind:'project'}));
const resources=['kafka','rabbit','database','http'].map(kind=>({id:kind+'-block',kind,resourceBlock:true}));
for(const renderer of ['topology','flow']) {
  const nodes=[...owners,...resources],registry=new Map(),first=geometry(nodes,renderer);
  const map=first instanceof Map?first:first.positions;
  assert.equal(stableArchitectureGeometry(first,registry,{nodes}),first);
  assert.equal(first instanceof Map?first:first.positions,map);
  assert.deepEqual(new Set(first.architectureClusters.map(c=>c.kind)),new Set(['owners','kafka','rabbit','database','http']));
  verify(first,nodes);
  const shuffled=geometry([...nodes].reverse(),renderer);
  stableArchitectureGeometry(shuffled,new Map(),{nodes:[...nodes].reverse()});
  assert.deepEqual(centers(first),centers(shuffled),'cold grouping independent of payload/Map ordering');
  const before=centers(first),newNodes=[...nodes,...Array.from({length:28},(_,i)=>({id:'table-'+i,kind:'database',long:i===5}))];
  const grown=geometry(newNodes,renderer);
  stableArchitectureGeometry(grown,registry,{nodes:newNodes,pinnedIds:new Set(['owner-2']),previousPositions:map});
  for(const [id,x,y] of before)assert.deepEqual([grown instanceof Map?grown.get(id).x:grown.positions.get(id).x,grown instanceof Map?grown.get(id).y:grown.positions.get(id).y],[x,y],`${id} stable`);
  verify(grown,newNodes);
  assert([...((grown instanceof Map)?grown:grown.positions).values()].every(p=>p.color==='keep'));
}

// Known full labels are asymmetric: a lower label must never overlap the next card or a heading.
const longNodes=[{id:'a',kind:'project',long:true},{id:'b',kind:'project',long:true},{id:'c',kind:'database',long:true},{id:'d',kind:'http',long:true}];
const long=geometry(longNodes);stableArchitectureGeometry(long,new Map(),{nodes:longNodes});verify(long,longNodes);

// Existing cross-type anchors keep their exact centers; disjoint same-kind islands avoid a box across the middle owner.
const old=new Map([['left',{x:0,y:0,width:240,height:172,above:56,below:116,clusterKind:'database'}],
 ['middle',{x:500,y:0,width:240,height:172,above:56,below:116,clusterKind:'owners'}],
 ['right',{x:1000,y:0,width:240,height:172,above:56,below:116,clusterKind:'database'}]]);
const oldNodes=[{id:'left',kind:'database'},{id:'middle',kind:'project'},{id:'right',kind:'database'}],anchors=geometry(oldNodes);
stableArchitectureGeometry(anchors,old,{nodes:oldNodes});
assert.deepEqual(centers(anchors),[['left',0,0],['middle',500,0],['right',1000,0]]);
assert.equal(anchors.architectureClusters.filter(c=>c.kind==='database').length,2);verify(anchors,oldNodes);

// Full registry still reserves every cached anchor and every current uncached position.
const full=new Map(Array.from({length:240},(_,i)=>['cached-'+i,{x:i*350,y:0,width:240,height:172,above:56,below:116,clusterKind:'owners'}]));
const overflowNodes=Array.from({length:240},(_,i)=>({id:'new-'+i,kind:['kafka','rabbit','database','http'][i%4]}));
const overflow=geometry(overflowNodes,'flow');stableArchitectureGeometry(overflow,full,{nodes:overflowNodes});
assert.equal(full.size,240);verify(overflow,overflowNodes);
const pinned=new Set(['new-3']);const next=geometry(overflowNodes,'flow');
stableArchitectureGeometry(next,full,{nodes:overflowNodes,pinnedIds:pinned,previousPositions:overflow.positions});
assert.deepEqual([next.positions.get('new-3').x,next.positions.get('new-3').y],[overflow.positions.get('new-3').x,overflow.positions.get('new-3').y]);
assert.equal(full.size,240);verify(next,overflowNodes);
console.log('Architecture semantic cluster geometry PASS (both renderer footprints, stable anchors, long labels,240registry+240frame,pins,islands)');

const miscellaneous=[{id:'external',kind:'external-project'},{id:'package',kind:'package'},{id:'unknown',kind:'other'},{id:'http',kind:'http'}];
const misc=geometry(miscellaneous,'flow');stableArchitectureGeometry(misc,new Map(),{nodes:miscellaneous});verify(misc,miscellaneous);
assert.deepEqual(misc.architectureClusters.find(c=>c.kind==='owners').nodeIds,['external']);
assert.deepEqual(misc.architectureClusters.find(c=>c.kind==='other').nodeIds,['package','unknown']);
console.log('External projects retain owner meaning; packages/unknown nodes use neutral other grouping PASS');

// A pinned node outside the full registry returns to its exact center after a filter hides it.
const pinnedCenter=[next.positions.get('new-3').x,next.positions.get('new-3').y];
const hiddenNodes=overflowNodes.filter(node=>node.id!=='new-3'),hidden=geometry(hiddenNodes,'flow');
stableArchitectureGeometry(hidden,full,{nodes:hiddenNodes,pinnedIds:pinned,previousPositions:next.positions});
assert.equal(full.pinnedPositions.size,1);
const returned=geometry(overflowNodes,'flow');
stableArchitectureGeometry(returned,full,{nodes:overflowNodes,pinnedIds:pinned,previousPositions:hidden.positions});
assert.deepEqual([returned.positions.get('new-3').x,returned.positions.get('new-3').y],pinnedCenter);
assert.equal(full.size,240);verify(returned,overflowNodes);
stableArchitectureGeometry(geometry(hiddenNodes,'flow'),full,{nodes:hiddenNodes,pinnedIds:new Set()});
assert.equal(full.pinnedPositions.size,0,'unpin prunes the extra registry-lifetime metadata');
const tooManyPins=new Set(overflowNodes.slice(0,40).map(node=>node.id));
stableArchitectureGeometry(geometry(overflowNodes,'flow'),full,{nodes:overflowNodes,pinnedIds:tooManyPins});
assert.equal(full.pinnedPositions.size,24,'independent layout helper enforces the24pin metadata bound');
console.log('Hidden overflow pin restoration and bounded24 registry pin metadata PASS');

// A filtered single short owner must still contain the full static16px heading and side padding.
const shortNodes=[{id:'short-owner',kind:'project'},{id:'short-db',kind:'database'}];
const shortGeometry=new Map(shortNodes.map(node=>[node.id,{x:0,y:0,r:24,labelWidth:80,labelBottom:42}]));
stableArchitectureGeometry(shortGeometry,new Map(),{nodes:shortNodes});verify(shortGeometry,shortNodes);
const ownerHeading=shortGeometry.architectureClusters.find(cluster=>cluster.kind==='owners');
assert(ownerHeading.width>=195.86+18*2,'actual longest static heading plus left/rightpadding');
console.log('Filtered short owner retains full240px heading region and group start spacing PASS');
