/**
 * Atlas-specific contents grouping and disclosure state, independent of document loading.
 */
import {el} from './dom.js';

const GROUPS = [
  {id: 'start', title: 'Начать работу', pages: ['Home', 'Installation', 'Explore-Code', 'Remote-Repositories']},
  {id: 'study', title: 'Разобраться в проекте', pages: ['Walkthrough-Map', 'Code-Health', 'Walkthrough-Agents', 'Walkthrough-Tests', 'Test-Feedback', 'Agents-and-MCP', 'Temporary-Checkout-Maps', 'Position-Dependencies', 'Module-Experiments', 'Data-and-Updates']},
  {id: 'patterns', title: 'Паттерны проектирования', pages: ['Code-Patterns', 'Pattern-Selection', 'Modular-Code', 'MCP-Compatible-Updates'],
    include: page => page.id.startsWith('Pattern-')},
  {id: 'develop', title: 'Развивать Atlas', pages: ['Architecture', 'Chunked-Analysis', 'Development-Guide', 'Mods-and-Wiki', 'Development-and-Releases', 'Wiki-Sync']},
];

/**
 * Assign every guide page once, retaining unknown chapters in a visible fallback.
 * @param {Array<object>} pages Unique bundle page identities in source order.
 * @returns {Array<object>} Task groups in reading order, containing original page objects.
 */
export function groupWikiPages(pages) {
  const remaining = new Map(pages.map(page => [page.id, page]));
  const groups = [];
  for (const definition of GROUPS) {
    const selected = [];
    for (const id of definition.pages) {
      if (remaining.has(id)) selected.push(remaining.get(id));
      remaining.delete(id);
    }
    // Pattern-* is the generator-owned namespace; unrelated future chapters
    // retain the visible fallback instead of mixing with the pattern cards.
    if (definition.include) {
      for (const [id, page] of remaining) {
        if (definition.include(page)) {
          selected.push(page);
          remaining.delete(id);
        }
      }
    }
    if (selected.length) groups.push({id: definition.id, title: definition.title, pages: selected});
  }
  if (remaining.size) groups.push({id: 'other', title: 'Другие материалы', pages: [...remaining.values()]});
  return groups;
}

export class ProjectWikiNavigation {
  /**
   * Bind existing host nodes; API fetching, routing and article focus remain with the reader.
   * @param {object} options Required root/search/contents/count DOM nodes, makeLink factory and getRoute callback.
   * The link factory receives label, section ID and optional mod/page; getRoute returns the current section/mod/page IDs.
   */
  constructor({root, search, contents, count, makeLink, getRoute}) {
    Object.assign(this, {root, search, contents, count, makeLink, getRoute});
    this.window = root.ownerDocument.defaultView;
    this.compact = this.window.matchMedia('(max-width: 1100px)');
    this.groupState = new Map([['other', true]]);
    this.modState = new Map();
    this.expectedOpen = new WeakMap();
    this.controller = new AbortController();
    this.renderController = new AbortController();
    this.syncLayout();
    contents.addEventListener('toggle', () => {
      if (contents.open) this.scheduleReveal();
    }, {signal: this.controller.signal});
    const updateLayout = () => { this.syncLayout(); this.scheduleReveal(); };
    this.compact.addEventListener('change', updateLayout, {signal: this.controller.signal});
    this.window.addEventListener('resize', updateLayout, {signal: this.controller.signal});
    this.observer = new this.window.ResizeObserver(updateLayout);
    this.observer.observe(root.closest('.wiki-sidebar').querySelector('.wiki-sidebar-heading'));
    this.observer.observe(root.closest('.wiki-sidebar').querySelector('.wiki-sidebar-footer'));
  }

  /**
   * Use normal page flow when width or scaled-text vertical space cannot support a sticky contents panel.
   * The font-relative budget is independent of the chosen layout, avoiding observer oscillation.
   */
  syncLayout() {
    const fontSize = parseFloat(this.window.getComputedStyle(this.root.ownerDocument.documentElement).fontSize);
    const compact = this.compact.matches || this.window.innerHeight < fontSize * 40;
    if (compact === this.compactMode) return;
    this.compactMode = compact;
    this.root.closest('.project-wiki').classList.toggle('wiki-compact', compact);
    if (compact && this.contents.contains(this.root.ownerDocument.activeElement)) {
      this.contents.querySelector('summary').focus({preventScroll: true});
    }
    this.setOpen(this.contents, !compact);
  }

  /**
   * Coalesce geometry updates and reveal only after the latest layout has settled.
   */
  scheduleReveal() {
    this.window.cancelAnimationFrame(this.revealFrame);
    this.revealFrame = this.window.requestAnimationFrame(() => this.revealCurrent());
  }

  /**
   * Mark programmatic disclosure changes so delayed native toggle events cannot overwrite user preferences.
   * @param {HTMLDetailsElement} details Owned disclosure to update.
   * @param {boolean} open Desired visible state; does not change stored manual group/mod state.
   */
  setOpen(details, open) {
    this.expectedOpen.set(details, open);
    details.open = open;
  }

  /**
   * Remember native user toggles while ignoring search/current-route expansion.
   * @param {HTMLDetailsElement} details Newly created group or mod disclosure.
   * @param {Map<string,boolean>} state Bounded manual state for that disclosure kind.
   * @param {string} id Stable group or mod identity.
   */
  watch(details, state, id) {
    // Native toggle events are queued; capture an explicit summary action before a subsequent search can run.
    details.addEventListener('click', event => {
      const summary = event.target.closest?.('summary');
      if (!summary || summary.parentElement !== details || event.defaultPrevented || this.search.value.trim()) return;
      const open = !details.open;
      this.remember(state, id, open);
      this.expectedOpen.set(details, open);
    }, {signal: this.renderController.signal});
    details.addEventListener('toggle', () => {
      const open = details.open;
      if (this.expectedOpen.get(details) === open) return;
      this.expectedOpen.set(details, open);
      if (this.search.value.trim()) return;
      this.remember(state, id, open);
    }, {signal: this.renderController.signal});
  }

  /**
   * Retain bounded manual disclosure choices in most-recent order.
   * @param {Map<string,boolean>} state Group or mod preference map.
   * @param {string} id Stable disclosure identity.
   * @param {boolean} open User-selected open state.
   */
  remember(state, id, open) {
    state.delete(id); state.set(id, open);
    while (state.size > 256) state.delete(state.keys().next().value);
  }

  /**
   * Render guide groups and their existing mod subpages, restoring a surviving navigation focus target.
   * @param {Array<object>} pages Loaded static guide pages; no Markdown is parsed here.
   * @param {Array<object>} mods Validated metadata from the optional wiki catalog.
   */
  render(pages, mods) {
    const active = this.root.contains(this.root.ownerDocument.activeElement)
      ? this.root.ownerDocument.activeElement.dataset.wikiKey : null;
    this.renderController.abort(); this.renderController = new AbortController();
    this.root.replaceChildren();
    const selected = this.getRoute();
    for (const group of groupWikiPages(pages)) {
      const details = el('details', undefined, 'wiki-topic-group'); details.dataset.group = group.id;
      details.dataset.search = group.title.toLocaleLowerCase('ru');
      const summary = el('summary', group.title); summary.dataset.wikiKey = 'group:' + group.id;
      const count = el('span', String(group.pages.length), 'wiki-group-count');
      count.title = 'Главы Atlas'; summary.append(count);
      const items = el('div', undefined, 'wiki-topic-items');
      for (const page of group.pages) {
        const section = el('section', undefined, 'wiki-navigation-section'); section.dataset.page = page.id;
        const link = this.makeLink(page.title, page.id);
        link.dataset.search += ' ' + page.id.toLocaleLowerCase('ru');
        link.dataset.wikiKey = 'page:' + page.id;
        section.append(link);
        for (const mod of mods) {
          if (mod.section !== page.id || !mod.pages.length) continue;
          const disclosure = el('details', undefined, 'wiki-mod-pages'); disclosure.dataset.mod = mod.id;
          const title = el('summary', mod.name); title.dataset.wikiKey = 'mod:' + mod.id;
          title.title = mod.id;
          const children = el('div', undefined, 'wiki-mod-children');
          for (const modPage of mod.pages) {
            const child = this.makeLink(modPage.title, page.id, mod, modPage);
            child.dataset.search += ' ' + mod.id.toLocaleLowerCase('ru');
            child.dataset.wikiKey = 'mod-page:' + mod.id + ':' + modPage.id;
            children.append(child);
          }
          this.setOpen(disclosure, selected.modId === mod.id || this.modState.get(mod.id) === true);
          this.watch(disclosure, this.modState, mod.id);
          disclosure.append(title, children); section.append(disclosure);
        }
        items.append(section);
      }
      this.setOpen(details, group.pages.some(page => page.id === selected.sectionId) || this.groupState.get(group.id) === true);
      this.watch(details, this.groupState, group.id);
      details.append(summary, items); this.root.append(details);
    }
    this.empty = el('p', 'По вашему запросу ничего не найдено.', 'wiki-empty-search');
    this.root.append(this.empty);
    this.pageCount = pages.length;
    this.filter();
    if (active) {
      const replacement = [...this.root.querySelectorAll('[data-wiki-key]')].find(node => node.dataset.wikiKey === active);
      (replacement && !replacement.closest('[hidden]') ? replacement : this.search).focus({preventScroll: true});
    }
    this.revealCurrent();
  }

  /**
   * Filter links locally; clearing search restores manual disclosure state and the current route.
   * @param {object} options Set expandContents for direct user input, never for API refresh renders.
   */
  filter({expandContents = false} = {}) {
    const query = this.search.value.trim().toLocaleLowerCase('ru');
    if (expandContents && query) this.setOpen(this.contents, true);
    if (!this.empty) return;
    const selected = this.getRoute();
    let visible = 0;
    const links = this.root.querySelectorAll('a');
    for (const group of this.root.querySelectorAll('.wiki-topic-group')) {
      const groupMatch = query && group.dataset.search.includes(query);
      for (const link of group.querySelectorAll('a')) {
        link.hidden = !groupMatch && !link.dataset.search.includes(query);
        if (!link.hidden) visible++;
      }
      for (const mod of group.querySelectorAll('.wiki-mod-pages')) {
        mod.hidden = ![...mod.querySelectorAll('a')].some(link => !link.hidden);
        this.setOpen(mod, query ? !mod.hidden : selected.modId === mod.dataset.mod || this.modState.get(mod.dataset.mod) === true);
      }
      for (const section of group.querySelectorAll('.wiki-navigation-section')) {
        section.hidden = ![...section.querySelectorAll('a')].some(link => !link.hidden);
      }
      group.hidden = ![...group.querySelectorAll('a')].some(link => !link.hidden);
      const current = [...group.querySelectorAll('.wiki-navigation-section')].some(section => section.dataset.page === selected.sectionId);
      this.setOpen(group, query ? !group.hidden : current || this.groupState.get(group.dataset.group) === true);
    }
    this.empty.hidden = visible > 0;
    this.count.textContent = query ? `Страниц найдено: ${visible} из ${links.length}`
      : `Главы Atlas: ${this.pageCount} · Страницы модов: ${links.length - this.pageCount}`;
  }

  /**
   * Reveal the selected link inside the contents scroller without moving the article or stealing focus.
   */
  revealCurrent() {
    const current = this.root.querySelector('a[aria-current="page"]');
    if (!current || current.closest('[hidden]') || !this.contents.open) return;
    const scroller = this.compactMode ? this.root : this.root.closest('.wiki-sidebar-content');
    if (!scroller || this.root.contains(this.root.ownerDocument.activeElement)) return;
    const box = scroller.getBoundingClientRect(), target = current.getBoundingClientRect();
    if (target.top < box.top) scroller.scrollTop += target.top - box.top;
    else if (target.bottom > box.bottom) scroller.scrollTop += target.bottom - box.bottom;
  }

  /**
   * Close compact contents after explicit navigation; the reader owns article focus.
   */
  afterNavigate() {
    if (this.compactMode) this.setOpen(this.contents, false);
  }

  /**
   * Release owned listeners and bounded disclosure state when the reader leaves.
   */
  dispose() {
    this.controller.abort(); this.renderController.abort();
    this.observer.disconnect();
    this.window.cancelAnimationFrame(this.revealFrame);
    this.groupState.clear(); this.modState.clear();
  }
}
