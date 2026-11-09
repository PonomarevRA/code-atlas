/** Сохраняет семантику сравнений, вложенность ветвей, исходные комментарии и ленивое чтение методов. */
import assert from 'node:assert/strict';
import { conditionText, branchText, armText, documentationTree } from '../web/method-documentation-model.js';
import { MethodDocumentation } from '../web/method-documentation.js';
import { normalizeRoute, readRoute, writeRoute } from '../web/navigation-history.js';
import { branchLabels, renderControlFlow } from '../web/path-annotations.js';

assert.match(conditionText('quantity > 0'), /«quantity» больше «0»/);
assert.match(conditionText('quantity <= limit'), /меньше или равно/);
assert.match(conditionText('item != null'), /не равно null \(задано\)/);
assert.match(conditionText('item is null'), /равно null \(отсутствует\)/);
assert.match(conditionText('item is not null'), /не равно null/);
assert.equal(conditionText('a == "x > 0"'), 'значение «a» равно «"x > 0"»');
assert.equal(conditionText('x == null || (x > 0 && enabled)'), '(значение «x» равно null (отсутствует)) или ((значение «x» больше «0») и («enabled» истинно))');
assert.match(conditionText('(a) && (b)'), /«a» истинно.* и .*«b» истинно/);
for (const expression of ['Check(x > 0)', 'a ? b : c && d', 'x => x > 0', 'x ?? 0', 'x = false', 'x?.Value > 0']) {
  assert.equal(conditionText(expression), `выполнено условие «${expression}»`);
}
assert.match(branchText({kind:'foreach',collection:'items'}), /Для каждого элемента из «items»/);
assert.match(branchText({kind:'while',condition:'pending'}), /Повторять, пока/);
assert.match(branchText({kind:'do',condition:'pending'}), /хотя бы один раз/);
assert.match(branchText({kind:'for',condition:''}), /не задано/);
assert.equal(armText({kind:'switch'},{labels:[{text:'case 1 when allowed',guard:'when allowed'},{text:'case 2 when x > 0',guard:'when x > 0'}]}), 'Вариант «1», если «allowed» истинно или Вариант «2», если значение «x» больше «0»');
assert.equal(armText({kind:'switch'},{label:'default'}),'Для остальных значений');
assert.match(armText({kind:'switch',selector:'amount'},{label:'case > 0'}), /«amount» больше «0»/);
assert.match(armText({kind:'switch',selector:'item'},{label:'case null'}), /равно null/);

const step = {nodeId:'root',label:'Calculate()',line:10,sourceStatus:'parsed',documentation:{summary:'Считает итог.',params:[{name:'items',text:'Элементы.'}],returns:'Итог.'},controlFlow:{branches:[
  {id:'loop',kind:'foreach',line:12,collection:'items',arms:[{id:'body',kind:'body'}]},
  {id:'check',kind:'if',line:13,condition:'item != null',parentArmIds:['body'],arms:[{id:'yes',kind:'true'},{id:'no',kind:'false'}]},
  {id:'choice',kind:'switch',line:16,selector:'item.Kind',parentArmIds:['body','yes'],arms:[{id:'case',kind:'case',label:'case 1'}]},
],terminals:[{kind:'continue',line:15,branchIds:['body','no']},{kind:'return',line:22,text:'return total;',branchIds:[]}]},events:[
  {kind:'comment',text:'/// <summary>Считает итог.</summary>',line:9,branchIds:[]},
  {kind:'comment',text:'// Считаем каждый элемент',line:14,branchIds:['body','yes']},
  {kind:'call',text:'calculator.Apply()',line:17,targetNodeIds:['callee'],branchIds:['body','yes','case']},
  {kind:'call',text:'Unknown()',line:20,branchIds:['missing']},
]};
const before=JSON.stringify(step), tree=documentationTree(step);
assert.equal(tree.items[0].branch.id,'loop');
const check=tree.items[0].arms[0].items[0];
assert.equal(check.branch.id,'check');
assert.equal(check.arms[0].items[0].kind,'comment');
assert.equal(check.arms[0].items[1].branch.id,'choice');
assert.equal(check.arms[0].items[1].arms[0].items[0].event.text,'calculator.Apply()');
assert.equal(check.arms[1].items[0].terminal.kind,'continue');
assert.equal(tree.unplaced.length,1);
assert.equal(tree.items[1].terminal.kind,'return');
assert.equal(JSON.stringify(step),before);
const partial=documentationTree({...step,controlFlow:{branches:[],unsupported:['Переходы catch_clause не моделируются'],terminals:[{kind:'return',line:30,branchIds:[]}]},events:[{kind:'call',text:'Failure()',conditional:true,line:30,branchIds:[]}]});
assert.equal(partial.items.length,0);
assert.equal(partial.unplaced.length,2,'unknown exception branches cannot become unconditional actions');

class Element {
  constructor(tag) {this.tagName=tag.toUpperCase();this.children=[];this.listeners={};}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children=nodes;}
  addEventListener(event,callback) {this.listeners[event]=callback;}
  set innerHTML(value) {throw new Error('Unsafe HTML');}
}
globalThis.document={createElement:tag=>new Element(tag)};
const fields=new Map(['diagram','root','method','body','title','status'].map(n=>[n,new Element('div')]));
const root={hidden:true,querySelector:s=>fields.get(s.match(/"(.*?)"/)[1])};
const selections=[], sources=[];
const viewer=new MethodDocumentation(root,{onSelect:id=>selections.push(id),onSource:(id,line)=>sources.push([id,line]),onDiagram:()=>{}});
const callee={nodeId:'callee',label:'Apply()',typeContext:{name:'Calculator'},documentation:{summary:'<img src=x onerror=attack()> Применяет правило.'},controlFlow:{branches:[]}};
const data={rootNodeId:'root',steps:[step,callee],edges:[]};
const text=node=>[node.textContent||'',...node.children.map(n=>typeof n==='string'?n:text(n))].join(' ');
viewer.display(data,'root',true);
const rendered=text(fields.get('body'));
assert.match(rendered,/Считает итог/); assert.match(rendered,/Для каждого элемента/);
assert.match(rendered,/Если значение «item» не равно null/); assert.match(rendered,/Иначе/);
assert.match(rendered,/Считаем каждый элемент/); assert.match(rendered,/<img src=x onerror=attack\(\)>/);
assert.match(rendered,/Принадлежность к ветви не сопоставлена/);
assert.ok(!rendered.includes('<summary>'));
const bodyBefore=fields.get('body').children;
viewer.display(data,'root',true); assert.equal(fields.get('body').children,bodyBefore,'unchanged selections reuse the document');
viewer.display(data,'callee',true); assert.match(text(fields.get('body')),/Применяет правило/); assert.ok(!text(fields.get('body')).includes('Считает итог'));
viewer.display(data,'absent',true); assert.match(fields.get('title').textContent,/отсутствует/); assert.ok(!text(fields.get('body')).includes('Применяет правило'));
const dispatchData={rootNodeId:'root',steps:[step,callee],edges:[{source:'root',target:'callee',relation:'dispatches_to',confidence:'candidate'}]};
viewer.display(dispatchData,'root',true); assert.match(text(fields.get('body')),/Возможная реализация/);
assert.match(text(viewer.targetButton('callee','candidate')),/Возможная цель вызова/);
viewer.display(null,null,false); assert.equal(root.hidden,true);

const conditionalStep = {nodeId:'conditional',label:'Choose()',line:1,controlFlow:{branches:[
  {id:'ternary',kind:'conditional',line:1,condition:'item != null',arms:[
    {id:'yes',kind:'true',expression:'item'}, {id:'no',kind:'false',expression:'null'},
  ]},
  {id:'nested',kind:'conditional',line:1,condition:'ready',parentArmIds:['yes'],arms:[
    {id:'nested:yes',kind:'true',expression:'Value()'}, {id:'nested:no',kind:'false',expression:'0'},
  ]},
],terminals:[]},events:[{kind:'call',text:'Value()',line:1,branchIds:['yes','nested:yes']}]};
const conditionalTree=documentationTree(conditionalStep);
assert.equal(conditionalTree.items[0].arms[0].items[0].branch.id,'nested');
assert.equal(conditionalTree.items[0].arms[0].items[0].arms[0].items[0].event.text,'Value()');
viewer.display({rootNodeId:'conditional',steps:[conditionalStep],edges:[]},'conditional',true);
assert.match(text(fields.get('body')),/Если значение «item» не равно null/);
assert.match(text(fields.get('body')),/Иначе.*Используется значение выражения:.*null/);
assert.match(text(fields.get('body')),/Если «ready» истинно/);
assert.deepEqual(branchLabels(conditionalStep.controlFlow,['no']),['?: (item != null) — иначе: null']);
assert.match(text(renderControlFlow(conditionalStep.controlFlow)), /\?:.*item != null.*null/);
assert.equal(conditionalTree.unplaced.length,0);

const route={repo:'repo',path:'root',reading:'documentation',node:'callee'};
assert.equal(readRoute(writeRoute(route,'http://localhost/')).reading,'documentation');
assert.equal(normalizeRoute({...route,path:null}).reading,null);
assert.equal(normalizeRoute({...route,view:'system'}).reading,null);
assert.equal(normalizeRoute({...route,repo:null}).reading,null);
assert.equal(normalizeRoute({...route,reading:'unknown'}).reading,null);
console.log('Method documentation conditions, nested controls, source text, selection and history PASS');
