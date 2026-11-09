/** Bundled author guide: real navigation, code-safe rendering and source/package consistency. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {renderProjectWiki} from '../web/project-wiki-content.js';
class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  set innerHTML(_) { throw Error('Unsafe HTML'); }
}
globalThis.document = {createElement: tag => new Element(tag), createTextNode: value => ({textContent: value, children: []})};
const all = node => [node, ...node.children.flatMap(all)];
const root = new Element('article');
renderProjectWiki(root, '# Start\n\n[Read](Home.md) [bad](javascript:alert) <script>alert(1)</script>\n\n- one\n- two\n\n```js\n<img src=x onerror=alert(1)>\n```', new Set(['Home']));
assert.equal(all(root).filter(item => item.tagName === 'A').length, 1);
assert.equal(all(root).find(item => item.tagName === 'A').href, '/wiki.html?page=Home');
assert.equal(all(root).filter(item => item.tagName === 'SCRIPT' || item.tagName === 'IMG').length, 0);
assert.equal(all(root).filter(item => item.tagName === 'LI').length, 2);
assert.equal(all(root).find(item => item.tagName === 'CODE').textContent, '<img src=x onerror=alert(1)>');
const bundle = JSON.parse(await readFile(new URL('../web/project-wiki.json', import.meta.url)));
const names = new Set(bundle.pages.map(page => page.id));
assert.equal(names.size, bundle.pages.length);
for (const id of ['Home', 'Architecture', 'Walkthrough-Map', 'Walkthrough-Agents', 'Walkthrough-Tests', 'Development-Guide']) assert(names.has(id));
for (const page of bundle.pages) {
  assert.equal(page.text, await readFile(new URL('../' + page.source, import.meta.url), 'utf8'));
  for (const link of page.text.matchAll(/\]\(([A-Za-z][A-Za-z0-9-]*)\.md\)/g)) assert(names.has(link[1]), `Missing page ${link[1]}`);
  renderProjectWiki(root, page.text, names);
  assert(all(root).some(item => item.tagName === 'H1'));
}
console.log('Project wiki: bundled sources, chapter links, text-only rendering and four examples PASS');
