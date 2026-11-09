import assert from 'node:assert/strict';
import {projectWikiHref, resolveProjectWikiLink, renderProjectWiki, wikiHeadingId} from '../web/project-wiki-content.js';
class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  set innerHTML(_){throw Error('Unsafe HTML');}
}
globalThis.document={createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text,children:[]})};
const mod={id:'custom-reader',section:'Explore-Code',pages:[{id:'overview',path:'wiki/Overview.md'},{id:'detail',path:'wiki/nested/Detail.MD'}]};
const options={mod,currentPath:'wiki/Overview.md',pageId:'overview',sectionId:'Explore-Code',context:'?repo=selected&secret=discard'};
assert.equal(projectWikiHref('Home',{context:options.context}),'/wiki.html?page=Home&repo=selected');
assert.equal(resolveProjectWikiLink('nested/Detail.MD#Точное имя',options).href,'/wiki.html?page=Explore-Code&repo=selected&mod=custom-reader&modPage=detail#wiki-heading-%D1%82%D0%BE%D1%87%D0%BD%D0%BE%D0%B5-%D0%B8%D0%BC%D1%8F');
assert.equal(resolveProjectWikiLink('../Overview.md',{...options,currentPath:'wiki/nested/Detail.MD'}).href,'/wiki.html?page=Explore-Code&repo=selected&mod=custom-reader&modPage=overview');
for(const target of ['../../outside.md','/wiki/Overview.md','javascript:alert(1)','data:text/html,bad','file:///etc/passwd','nested/Missing.md','https://user:secret@example.test/docs','nested/%2e%2e/%2e%2e/outside.md'])assert.equal(resolveProjectWikiLink(target,options),null,target);
assert.equal(resolveProjectWikiLink('https://example.test/docs',options).external,true);
assert.equal(resolveProjectWikiLink('Home.md',new Set(['Home'])).href,'/wiki.html?page=Home');
const root=new Element('article');
renderProjectWiki(root,'# Example\n\n[Next](nested/Detail.MD) [Bad](javascript:alert) <img src=x onerror=bad>\n\n## Example\n\n```html\n<script>bad()</script>\n```',options);
const all=node=>[node,...node.children.flatMap(all)];
assert.equal(all(root).filter(n=>n.tagName==='A').length,1);
assert.equal(all(root).filter(n=>n.tagName==='SCRIPT'||n.tagName==='IMG').length,0);
assert.equal(all(root).find(n=>n.tagName==='H1').id,wikiHeadingId('Example'));
assert.equal(all(root).find(n=>n.tagName==='H2').id,wikiHeadingId('Example')+'-2');
for (const titles of [['Example','Example','Example-2'],['Example-2','Example','Example']]) {
  renderProjectWiki(root,titles.map(title=>'# '+title).join('\n'),options);
  const headings = all(root).filter(node=>node.tagName==='H1');
  assert.equal(new Set(headings.map(node=>node.id)).size,3);
  assert.equal(headings[titles.indexOf('Example-2')].id,'wiki-heading-example-2');
}
console.log('Mod wiki scoped links, uppercase.MD, anchors and text-only Markdown PASS');

