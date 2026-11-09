/** Подключение создаёт только текст/скачивание; ответы привязаны к выбору и ревизии. */
import assert from 'node:assert/strict';
import {AgentSetupPanel,agentDownloadPath} from '../web/agent-setup.js';
class Element{
 constructor(){this.listeners={};this.value='';this.hidden=false;this.disabled=false;this.attributes={};}
 addEventListener(name,callback){this.listeners[name]=callback;}
 setAttribute(name,value){this.attributes[name]=value;}
 removeAttribute(name){delete this.attributes[name];delete this[name];}
 focus(){this.focused=true;}
 select(){this.selected=true;}
 set innerHTML(value){throw new Error('Unsafe HTML');}
}
const names=['context','status','config','codex-config','copy','copy-codex','configuration','retry','download-config','download-codex','download-skill','download-zip','warning'];
const fields=Object.fromEntries(names.map(name=>[name,new Element()]));
const root=new Element();root.open=false;root.querySelector=selector=>fields[selector.match(/"([^\"]+)"/)[1]];
let context={projectId:'one',snapshotId:'s1'};const requests=[],copied=[];
const panel=new AgentSetupPanel(root,{getContext:()=>context,fetcher:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve})),clipboard:{writeText:async text=>copied.push(text)}});
assert.equal(requests.length,0);assert.equal(fields.copy.disabled,true);
function data(id='one'){return {project:id?{id,name:'<b>Project</b>',path:'/local',status:'completed'}:null,mcpConfig:{mcpServers:{atlas:{command:'/runtime/python',args:['--project-id',id]}}},codexConfig:'project = "'+id+'"',serverUrl:'http://127.0.0.1:8877',downloads:{config:'/api/agent-setup/config?projectId='+id,codex:'/api/agent-setup/config?format=toml&projectId='+id,skill:'/api/agent-setup/skill',zip:'/api/agent-setup/skill.zip'},warning:'No editor writes'};}
const first=panel.load();assert.equal(requests[0].url,'/api/agent-setup?projectId=one');
requests[0].resolve({ok:true,json:async()=>data()});await first;
assert.equal(fields.copy.disabled,false);assert.equal(fields['download-config'].hidden,false);
assert.deepEqual(JSON.parse(fields.config.value),data().mcpConfig);
assert.equal(fields['codex-config'].value,data().codexConfig);
assert.equal(fields['copy-codex'].disabled,false);
assert.equal(fields.configuration.attributes['aria-busy'],'false');
assert.match(fields.status.textContent,/Подключение проверьте в клиенте/);
await panel.copy('codex-config');
assert.equal(copied[0],data().codexConfig);
assert.equal(fields['download-codex'].href,data().downloads.codex);
await panel.copy();assert.equal(copied.length,2);assert.ok(fields.status.textContent.includes('mcpServers'));
// Неудачное обновление сразу убирает предыдущую готовую конфигурацию.
const failed=panel.load();assert.equal(fields.configuration.attributes['aria-busy'],'true');assert.equal(fields['copy-codex'].disabled,true);assert.equal(fields.config.value,'');assert.equal(fields.copy.disabled,true);
assert.equal(fields['codex-config'].value,'');
assert.equal(fields['download-codex'].hidden,true);
requests[1].resolve({ok:false,status:503,json:async()=>({error:'unavailable'})});await failed;
assert.equal(fields.status.textContent,'unavailable');assert.equal(fields['download-config'].hidden,true);
// Игнорирующий AbortController старый ответ не возвращает проект one после выбора two.
const stale=panel.load();context={projectId:'two',snapshotId:'s2'};panel.sync();
assert.equal(requests[2].options.signal.aborted,true);
requests[2].resolve({ok:true,json:async()=>data('one')});await stale;
assert.equal(fields.config.value,'');assert.equal(fields.copy.disabled,true);
assert.equal(fields['codex-config'].value,'');
const fresh=panel.load();requests[3].resolve({ok:true,json:async()=>data('two')});await fresh;
assert.equal(JSON.parse(fields.config.value).mcpServers.atlas.args[1],'two');
assert.equal(fields['codex-config'].value,data('two').codexConfig);
// Обновление snapshot даже того же проекта инвалидирует видимый JSON.
context={projectId:'two',snapshotId:'s3'};panel.sync();assert.equal(fields.config.value,'');
context={projectId:null,snapshotId:null};panel.sync();const general=panel.load();assert.equal(requests[4].url,'/api/agent-setup');requests[4].resolve({ok:true,json:async()=>data(null)});await general;assert.ok(fields.context.textContent.includes('Все сохранённые'));
for(const unsafe of ['https://evil.invalid/x','//evil.invalid','/api/agent-setup/../../secret','/api/agent-setup/skill/evil','javascript:alert(1)'])assert.equal(agentDownloadPath(unsafe),null);
assert.equal(agentDownloadPath('/api/agent-setup/skill.zip'),'/api/agent-setup/skill.zip');
console.log('Agent setup explicit copy, safe downloads, failures and stale selection PASS');
