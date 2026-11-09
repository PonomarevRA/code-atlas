/** Data recovery remains project-scoped, discovery-confirmed and explicit; links do not build maps. */
import assert from 'node:assert/strict';
import {relativeDataFile,dataFileURL,discoveredDataSelection} from '../web/data-file-navigation.js';
for(const value of ['',null,{},'../file.json','/root/file.json','x/../file.json','C:/file.json','javascript:alert(1)','x\\file.json','a//file.json','a\nfile.json']){
  assert.equal(relativeDataFile(value),null);assert.equal(dataFileURL('project',value),null);
}
const file='reports/Отчёт & map=foreign.json',url=new URL(dataFileURL('project',file),'http://127.0.0.1');
assert.equal(url.pathname,'/data.html');assert.equal(url.searchParams.get('project'),'project');assert.equal(url.searchParams.get('file'),file);assert.equal(url.searchParams.get('map'),null);
assert.equal(new URL(dataFileURL('project',file,'ready-map'),'http://127.0.0.1').searchParams.get('map'),'ready-map');
const discovered=[{path:'report.json'},{path:'other.json'}];
assert.equal(discoveredDataSelection(discovered,'report.json'),'report.json');
assert.equal(discoveredDataSelection(discovered,'absent.json'),null);assert.equal(discoveredDataSelection(discovered,null),null);
console.log('Data-file links and discovery-confirmed single selection PASS');
