import assert from 'node:assert/strict';
import {renderOrganization} from '../web/health-organization.js';
import {nodeHealthText, appendHealthBadge} from '../web/health-badge.js';
import {Svg} from './svg_fixture.mjs';

class TextElement {
  constructor(tag) { this.tag = tag; this.children = []; this.value = ''; }
  set textContent(value) { this.value = value; }
  get textContent() { return this.value + this.children.map(child => child.textContent).join(' '); }
  set innerHTML(_) { throw new Error('Untrusted organization text must not become HTML'); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; this.value = ''; }
}
globalThis.document = {createElement: tag => new TextElement(tag), createElementNS: (_, tag) => new Svg(tag)};
const host = new TextElement('div');
renderOrganization(host);
assert.match(host.textContent, /ещё не измерена/);
renderOrganization(host, {status:'partial', fileCount:1, folderCount:1, assessedFiles:0, unknownFiles:1, score:null,
  files:[{file:'<img src=x onerror=alert(1)>.py', status:'partial', score:null, metrics:{declarations:3,statements:200,outgoingFiles:4}}],
  folders:[{path:'feature', files:1, tinyFiles:0, outgoingFolders:2, depth:4, complete:false}], hotspotsTruncated:true});
assert.match(host.textContent, /неизвестно 1/);
assert.match(host.textContent, /<img src=x/);
assert.match(host.textContent, /не установлен/);
assert.match(host.textContent, /факты неполные/);
assert.match(host.textContent, /выбранные файлы/);
for (const [metric, expected, prefix] of [['organization',82,'O'], ['transparency',71,'T']]) {
  const group = new Svg('g');
  appendHealthBadge(group, {organizationScore:82,transparencyScore:71,version:'atlas-health-v2'}, 10, 20, metric);
  assert.equal(group.querySelector('.code-health-badge').attributes['data-health-score'], expected);
  assert.match(group.textContent, new RegExp(prefix));
  assert.match(nodeHealthText({}, metric), /неизвестно/);
}
console.log('PASS organization scope, unknown facts, untrusted paths and O/T badge identity');

renderOrganization(host, {status:'partial', fileCount:2, folderCount:2, assessedFiles:0, unknownFiles:2, score:null,
  coverage:{local:{complete:2,unknown:0,total:2},cycles:{complete:0,unknown:2,total:2}},
  files:[{file:'safe.py',status:'partial',score:null,metrics:{declarations:1,statements:2,outgoingFiles:0},
    coverage:{local:{status:'complete'},outgoing:{status:'partial',reason:'unresolved-source-dependencies'},
      cycles:{status:'partial',reason:'absence-not-proven-on-partial-graph'}}}]});
assert.match(host.textContent, /размер и ветвления: полные данные 2 \/ 2/);
assert.match(host.textContent, /не разрешены вызовы источника/);
assert.match(host.textContent, /отсутствие цикла не доказано/);
assert.ok(!host.textContent.includes('PASS'));
