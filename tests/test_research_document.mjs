/** Untrusted research prose stays text; source fences and indentation remain readable. */
import assert from 'node:assert/strict';
import { renderResearchDocument } from '../web/research-document.js';
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}
globalThis.document = { createElement: tag => new Element(tag), createTextNode: text => ({textContent:text}) };
const root = new Element('article');
renderResearchDocument(root, '# Local facts\n\\<img src=x onerror=bad\\>\n  - nested\n````\n```\n<script>literal</script>\n````\nDone');
assert.equal(root.children[0].tag, 'h2');
assert.equal(root.children[1].textContent, '<img src=x onerror=bad>');
assert.equal(root.children[2].textContent, '  - nested');
assert.equal(root.children[3].tag, 'pre');
assert.equal(root.children[3].children.map(item => item.textContent).join(''), '```\n<script>literal</script>\n');
assert.equal(root.children[4].textContent, 'Done');
assert.ok(root.children.every(item => !['img','script','iframe'].includes(item.tag)));
console.log('Research text-only rendering PASS');
