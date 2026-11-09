/** DocFX ссылки/разделы, code и опасная разметка проверяются через безопасный DOM. */
import assert from 'node:assert/strict';
import {renderReferenceContent, showReferencePage, xrefIdentity} from '../web/reference-content.js';

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.value = ''; }
  set textContent(value) { this.value = String(value); this.children = []; }
  get textContent() { return this.value + this.children.map(node => node.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.value = ''; this.children = nodes; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  set innerHTML(_) { throw new Error('Unsafe HTML'); }
}
const fields = new Map(['document-content', 'document-text', 'document-raw'].map(id => [id, new Element('div')]));
globalThis.document = {createElement: tag => new Element(tag), createTextNode: text => {const node = new Element('#text'); node.textContent = text; return node;}, getElementById: id => fields.get(id)};
/** Собирает реальные вложенные узлы для проверок ссылок и code fences. */
function nodes(parent, tag) {
  const result = [];
  for (const child of parent.children) { if (child.tag === tag) result.push(child); result.push(...nodes(child, tag)); }
  return result;
}
assert.deepEqual(xrefIdentity('System.Linq.Enumerable.Select*?displayProperty=nameWithType'), {uid:'System.Linq.Enumerable.Select', label:'Enumerable.Select'});
assert.deepEqual(xrefIdentity('M:Acme.Run(System.Int32)?displayProperty=name', 'Run(int)'), {uid:'Acme.Run(System.Int32)', label:'Run(int)'});
assert.equal(xrefIdentity('Acme.Run(System.Predicate%7B`0%7D)').uid, 'Acme.Run(System.Predicate{`0})');
const text = '## Remarks\n\nSee <xref:Acme.Run*?displayProperty=name> and [Array](xref:T:System.Array).\n\n'
  + '## Examples\n\n```csharp\nif (ok)\n{\n    Run(); // <xref:Acme.Code>\n}\n```\n\n'
  + '| API | Cost |\n| --- | --- |\n| `Run` | O(n) |\n\n'
  + '- First\n- Second\n\n<script>attack()</script> <img src="https://evil.test" onerror="attack()">\n\n'
  + '[bad](javascript:attack()) [file](file:///etc/passwd) [source](https://learn.microsoft.com/)\n\n'
  + ':::code language="csharp" source="~/snippets/Run.cs" id="Snippet1":::';
const content = fields.get('document-content'), requests = [];
showReferencePage(text, {messages:{remarks:'Примечания', examples:'Примеры', externalExample:'Внешний пример'}, onReference: uid => requests.push(uid)});
assert.deepEqual(nodes(content, 'h3').map(node => node.textContent), ['Примечания', 'Примеры']);
const references = nodes(content, 'button'); assert.equal(references.length, 2);
assert.equal(references[0].textContent, 'Run'); references[0].listeners.click();
assert.deepEqual(requests, ['Acme.Run']);
assert.equal(nodes(content, 'pre')[0].textContent, 'if (ok)\n{\n    Run(); // <xref:Acme.Code>\n}');
assert.equal(nodes(content, 'table').length, 1); assert.equal(nodes(content, 'li').length, 2);
assert.equal(nodes(content, 'script').length, 0); assert.equal(nodes(content, 'img').length, 0);
assert.equal(nodes(content, 'a').length, 1); assert.equal(nodes(content, 'a')[0].href, 'https://learn.microsoft.com/');
assert.equal(fields.get('document-text').textContent, text); assert.equal(fields.get('document-raw').open, false);
assert.ok(content.textContent.includes('Внешний пример: ~/snippets/Run.cs (csharp)'));
renderReferenceContent(content, '[Run](xref:M:Acme.Run(System.Int32))');
assert.equal(nodes(content, 'code')[0].textContent, 'Run');
renderReferenceContent(content, 'Uses T:System.Predicate`1 and T:System.Collections.Generic.List`1. Then `inline`.', {onReference:uid => requests.push(uid)});
assert.deepEqual(nodes(content, 'button').map(node => node.textContent), ['System.Predicate`1', 'System.Collections.Generic.List`1']);
assert.equal(nodes(content, 'code')[0].textContent, 'inline');
renderReferenceContent(content, 'Summary.\n\nRemarks : Old remarks.\n\nexample : Old example.', {messages:{remarks:'Примечания', examples:'Примеры'}});
assert.deepEqual(nodes(content, 'h3').map(node => node.textContent), ['Примечания', 'Примеры']);
assert.ok(content.textContent.includes('Old remarks.') && content.textContent.includes('Old example.'));
renderReferenceContent(content, '| Flag | Action |\n| --- | --- |\n| A\\|B | Run |');
assert.deepEqual(nodes(content, 'td').map(node => node.textContent), ['A|B', 'Run']);
renderReferenceContent(content, '~~~csharp\n    <xref:Literal>\n    tail');
assert.equal(nodes(content, 'pre')[0].textContent, '    <xref:Literal>\n    tail');
showReferencePage('public class Plain {}', {enabled:false});
assert.equal(content.hidden, true); assert.equal(fields.get('document-raw').open, true);
console.log('reference content: sections, local xref, literal code, safety and raw pages PASS');
