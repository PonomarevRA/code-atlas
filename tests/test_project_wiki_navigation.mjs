/** Verify task grouping against the actual guide, including future custom chapters. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {groupWikiPages} from '../web/project-wiki-navigation.js';

const bundle = JSON.parse(await readFile(new URL('../web/project-wiki.json', import.meta.url)));
const pages = [...bundle.pages, {id: 'Future-Chapter', title: 'Custom material'}];
const before = JSON.stringify(pages);
const grouped = groupWikiPages(pages);
const ids = grouped.flatMap(group => group.pages.map(page => page.id));
assert.equal(ids.length, pages.length);
assert.equal(new Set(ids).size, pages.length);
assert.deepEqual(new Set(ids), new Set(pages.map(page => page.id)));
assert.equal(grouped.at(-1).id, 'other');
assert.equal(grouped.at(-1).pages[0], pages.at(-1));
assert.equal(grouped[0].pages[0].id, 'Home');
assert.equal(grouped.find(group => group.id === 'develop').pages.find(page => page.id === 'Mods-and-Wiki'), pages.find(page => page.id === 'Mods-and-Wiki'));
assert.equal(JSON.stringify(pages), before);
assert.deepEqual(groupWikiPages([]), []);
console.log('Wiki task groups: exact coverage, stable identity/order and unknown chapter fallback PASS');
