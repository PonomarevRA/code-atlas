/** Символьный поиск: явно выбранная область, стабильная пагинация и callbacks без фильтрации карты. */
import assert from 'node:assert/strict';
import { createNavigationSearch, rankNavigationMatches } from '../web/navigation-search.js';

class Element {
  constructor() { this.value = ''; this.children = []; this.listeners = {}; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  focus() { document.activeElement = this; }
}
const ids = ['navigation-search', 'navigation-search-scope', 'navigation-count', 'navigation-results', 'navigation-more', 'navigation-search-results'];
const fields = new Map(ids.map(id => [id, new Element()]));
globalThis.document = { getElementById: id => fields.get(id), createElement: () => new Element() };
const nodes = Array.from({ length: 55 }, (_, index) => ({ id: `n${index}`, label: 'Calculator', file: `src/${index}.cs` }));
let generation = 'snapshot-a';
const selected = [], scopes = [];
let searches = 0, visibilityChanges = 0;
const search = createNavigationSearch({
  model: { search: (query, rows) => { searches++; return rows.filter(node => node.label.includes(query)); } },
  getNodes: scope => { scopes.push(scope); return (scope === 'current' ? nodes.slice(0, 3) : nodes).map(node => ({ ...node })); },
  getScopeKey: scope => `${generation}:${scope}`,
  getProjects: () => [], onSelect: (id, options) => selected.push({ id, options }),
  onVisibilityChange: () => visibilityChanges++,
});
fields.get('navigation-search').value = 'Calc';
search.refresh();
assert.equal(scopes.at(-1), 'all', 'entire repository is default');
assert.equal(fields.get('navigation-results').children.length, 20);
assert.ok(fields.get('navigation-count').textContent.includes('во всём репозитории'));
fields.get('navigation-more').listeners.click();
assert.equal(fields.get('navigation-results').children.length, 40);
search.refresh();
assert.equal(fields.get('navigation-results').children.length, 40, 'new arrays during draw preserve More');
assert.equal(searches, 1);
fields.get('navigation-results').children[0].listeners.click();
assert.deepEqual(selected.at(-1), { id: 'n0', options: { scope: 'all' } });
fields.get('navigation-search-scope').value = 'current';
fields.get('navigation-search-scope').listeners.change();
assert.equal(fields.get('navigation-results').children.length, 3);
assert.equal(fields.get('navigation-more').hidden, true);
assert.ok(fields.get('navigation-count').textContent.includes('в текущем фильтре'));
fields.get('navigation-results').children[0].listeners.click();
assert.equal(selected.at(-1).options.scope, 'current');
fields.get('navigation-search-scope').value = 'all';
search.refresh();
fields.get('navigation-more').listeners.click();
generation = 'snapshot-b'; search.refresh();
assert.equal(fields.get('navigation-results').children.length, 20, 'different snapshot resets pagination');
fields.get('navigation-search').value = '';
fields.get('navigation-search').listeners.input();
assert.equal(fields.get('navigation-results').children.length, 0);
assert.equal(selected.length, 2, 'typing does not select or navigate');
fields.get('navigation-search').listeners.input();
assert.equal(fields.get('navigation-search-results').hidden, false);
fields.get('navigation-search').listeners.keydown({ key: 'Escape' });
assert.equal(fields.get('navigation-search-results').hidden, true);
assert.equal(visibilityChanges, 1, 'Escape persists popup visibility through replace-only callback');

// Older callers have neither scope control nor getScopeKey; stable content is sufficient.
fields.delete('navigation-search-scope');
const compatible = createNavigationSearch({ model: { search: (_, rows) => rows }, getNodes: () => [...nodes], getProjects: () => [], onSelect: id => selected.push(id) });
fields.get('navigation-search').value = 'Calc';
compatible.refresh();
fields.get('navigation-more').listeners.click();
compatible.refresh();
assert.equal(fields.get('navigation-results').children.length, 40);
console.log('Navigation search explicit scope and stable pagination PASS');

// Ranking is stable and preserves matches: a filename must not outrank an exact callable name.
const rankedNodes = [
  { id:'path', label:'.Calculate()', file:'src/CostChange.cs' },
  { id:'contains', label:'PifCostChange' },
  { id:'prefix', label:'CostChangeTotal' },
  { id:'exact', label:'.CostChange()' },
  { id:'prefix2', label:'CostChangeService' },
];
assert.deepEqual(rankNavigationMatches('CostChange', rankedNodes).map(row=>row.id), ['exact','prefix','prefix2','contains','path']);
assert.equal(rankedNodes[0].id,'path','ranking must not mutate graph arrays');
fields.get('navigation-search').listeners.keydown({key:'ArrowDown'});
assert.equal(document.activeElement,fields.get('navigation-results').children[0]);
document.activeElement.listeners.keydown({key:'ArrowDown'});
assert.equal(document.activeElement,fields.get('navigation-results').children[1]);
document.activeElement.listeners.keydown({key:'Escape'});
assert.equal(document.activeElement,fields.get('navigation-search'));
assert.equal(fields.get('navigation-search-results').hidden,true);
const beforeKeyboardSelect=selected.length;
fields.get('navigation-search').listeners.keydown({key:'Enter'});
assert.equal(selected.length,beforeKeyboardSelect+1);
fields.get('navigation-search').listeners.keydown({key:'Enter',repeat:true});
assert.equal(selected.length,beforeKeyboardSelect+1,'held Enter should not make duplicate transitions');
console.log('Navigation search ranking and keyboard PASS');
