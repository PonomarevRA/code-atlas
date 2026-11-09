/** Проверяет устойчивые цвета имён независимо от индекса графа и геометрии. */
import assert from 'node:assert/strict';
import {normalizeColorName,nameHash,projectColor,nodeColor,groupColor} from '../web/colors.js';
import {nodeGeometry,communityGeometry} from '../web/layout.js';
assert.equal(normalizeColorName('  CAFÉ  '),'café');
assert.equal(nameHash('Cafe\u0301'),nameHash('Café'));
assert.deepEqual(projectColor('ТЕСТ'),projectColor('тест'));
const projects=[{id:'p1',label:'Расчёты'},{id:'p2',label:'Reporting'}];
const node={id:'unstable:1',label:'Calculate',codeProjectId:'p1',community:'old-id'};
const color=nodeColor(node,projects);
assert.equal(color,nodeColor({...node,id:'new-id',community:'new-id'},[...projects].reverse()));
assert.equal(color,nodeColor(node,[projects[0]]));
assert.equal(color,nodeColor({...node,codeProjectId:'renumbered'},[{id:'renumbered',label:projects[0].label}]));
assert.notEqual(color,nodeColor({...node,label:'Validate'},projects));
assert.equal(groupColor({id:'1',label:'Calculate'}),groupColor({id:'900',label:'Calculate'}));
assert.equal(groupColor({id:'fragment',label:'decorated',originalCommunityLabel:'Calculate',codeProjectId:'p1'},projects),color);
assert.equal(nodeColor({label:'Cafe\u0301',codeProjectId:'p1'},projects),nodeColor({label:'Café',codeProjectId:'p1'},projects));
/** Разбирает HSL компоненты для проверки семейства и контраста. */
function hsl(text){return text.match(/[\d.]+/g).map(Number);}
const [hue,saturation,lightness]=hsl(color),base=projectColor(projects[0].label).hue;
assert.ok(Math.min((hue-base+360)%360,(base-hue+360)%360)<=18);
assert.ok(saturation>=48&&saturation<=65);assert.ok(lightness>=36&&lightness<=47);
assert.deepEqual(projectColor('arbitrary',{unassigned:true}),projectColor('another',{unassigned:true}));
assert.ok(hsl(nodeColor({label:'Unknown'},projects))[1]<=12);
/** Возвращает относительную яркость sRGB из HSL без браузерной зависимости. */
function luminance(text){
  const[h,s0,l0]=hsl(text),s=s0/100,l=l0/100,a=s*Math.min(l,1-l);
  const channel=n=>{const k=(n+h/30)%12,v=l-a*Math.max(-1,Math.min(k-3,9-k,1));return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;};
  return .2126*channel(0)+.7152*channel(8)+.0722*channel(4);
}
for(let index=0;index<360;index++){
  const palette=projectColor(`Project ${index}`);
  assert.ok((luminance(palette.fill)+.05)/(luminance(palette.text)+.05)>=4.5);
}
const nodes=[node,{id:'b',label:'Read',community:'x',codeProjectId:'p2'}],edges=[{source:node.id,target:'b'}];
const before=JSON.stringify({nodes,edges,projects});
const geometry=nodeGeometry(nodes,[],edges,null,null,projects);
const renamed=nodeGeometry(nodes.map(item=>({...item,label:'Changed '+item.label})),[],edges,null,null,projects.map(item=>({...item,label:'New '+item.label})));
for(const item of nodes){const a=geometry.get(item.id),b=renamed.get(item.id);assert.deepEqual([a.x,a.y,a.r],[b.x,b.y,b.r]);}
assert.equal(JSON.stringify({nodes,edges,projects}),before);
console.log('Name colors PASS');
