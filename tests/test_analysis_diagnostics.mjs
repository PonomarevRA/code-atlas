/** Unknown diagnostics stay unknown; partial extraction does not suppress valid nodes or build data maps. */
import assert from 'node:assert/strict';
import {analysisDiagnosticsState,analysisIssueCard} from '../web/analysis-diagnostics.js';
const owner={createElement(tag){return {tag,children:[],dataset:{},append(...items){this.children.push(...items);},addEventListener(name,callback){this[name]=callback;}};}};globalThis.document=owner;
assert.equal(analysisDiagnosticsState(null).known,false);assert.equal(analysisDiagnosticsState({}).partial,false);
assert.match(analysisDiagnosticsState({version:1,analysisStatus:'complete'}).text,/не зарегистрированы/);
const partial=analysisDiagnosticsState({version:1,analysisStatus:'partial',failedFileCount:3,processedFileCount:7,sourceFileCount:10});
assert.equal(partial.partial,true);assert.equal(partial.errorCount,null);assert.match(partial.text,/3/);assert.match(partial.text,/7 из 10/);
assert.equal(analysisDiagnosticsState({version:1,analysisStatus:'partial',errorCount:-1}).errorCount,null);
const card=analysisIssueCard({code:'PARSE_FAILED',path:'bad.ts',message:'<script>bad()</script>',alternative:{kind:'data-map',file:'../private.json'}},{projectId:'a'});
assert(card.children.some(node=>node.textContent==='<script>bad()</script>'));assert(!card.children.some(node=>node.tag==='a'));
let selected;const valid=analysisIssueCard({code:'DATA',path:'report.json',message:'Skipped',nodeId:'error:1',alternative:{kind:'data-map',file:'report.json',status:'not-built'}},{projectId:'a',onNode:id=>selected=id});
valid.children.find(node=>node.tag==='button').click();assert.equal(selected,'error:1');assert(valid.children.some(node=>node.href?.startsWith('/data.html?')));
console.log('Partial/unknown analysis diagnostics, literal per-element errors and bounded data alternatives PASS');
