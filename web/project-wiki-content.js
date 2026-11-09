/**
 * Text-only author/mod Markdown and catalogue-scoped links; no document HTML executes.
 */
import {el} from './dom.js';

/**
 * Build a stable guide route while retaining only the host's selected repository context.
 * @param {string} pageId Bundled section ID.
 * @param {object} options Optional mod/page IDs, literal anchor and URL query context.
 * @returns {string} Same-origin wiki path; only the repo context is retained.
 */
export function projectWikiHref(pageId, {modId, modPageId, fragment, context} = {}) {
  const query = new URLSearchParams({page: pageId});
  const repo = new URLSearchParams(context || '').get('repo');
  if (repo) query.set('repo', repo);
  if (modId) query.set('mod', modId);
  if (modPageId) query.set('modPage', modPageId);
  return `/wiki.html?${query}${fragment ? '#' + encodeURIComponent(fragment) : ''}`;
}

/**
 * Give headings deterministic local anchors without colliding with the surrounding shell IDs.
 * @param {string} value Visible heading or Markdown fragment text.
 * @returns {string} Unicode-safe DOM ID with the wiki-heading prefix.
 */
export function wikiHeadingId(value) {
  const slug = String(value).normalize('NFKC').toLocaleLowerCase('ru')
    .replace(/[^\p{L}\p{N}\s_-]/gu, '').trim().replace(/\s+/g, '-');
  return 'wiki-heading-' + (slug || 'section');
}

/**
 * Resolve a relative document path within one mod root; escapes and URL queries stay plain text.
 * @param {string} currentPath Current catalog document path relative to the mod root.
 * @param {string} target Relative Markdown destination, without a fragment.
 * @returns {string|null} Normalized in-root path, or null for invalid/escaping input.
 */
function relativeDocumentPath(currentPath, target) {
  let decoded;
  try { decoded = decodeURIComponent(target); } catch { return null; }
  if (!decoded || /[\\\u0000-\u001f?#]/.test(decoded) || decoded.startsWith('/')) return null;
  const parts = String(currentPath || '').split('/').slice(0, -1);
  for (const part of decoded.split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') { if (!parts.length) return null; parts.pop(); }
    else parts.push(part);
  }
  return parts.join('/');
}

/**
 * Permit known same-mod paths, exact bundled page IDs, local anchors or credential-free HTTP(S).
 * @param {string} target Markdown link destination.
 * @param {object|Set<string>} options Catalog scope/current path, or legacy bundled page IDs.
 * @returns {{href:string, external:boolean}|null} Allowed destination, or null for plain text.
 */
export function resolveProjectWikiLink(target, options) {
  const configuration = options instanceof Set ? {pages: options} : options || {};
  const pages = configuration.pages || new Set();
  const {mod, currentPath, sectionId, context} = configuration;
  if (/^https?:\/\//i.test(target)) {
    try {
      const url = new URL(target);
      if (url.username || url.password) return null;
      return {href: url.href, external: true};
    } catch { return null; }
  }
  const separator = target.indexOf('#');
  const path = separator < 0 ? target : target.slice(0, separator);
  let fragment = separator < 0 ? '' : target.slice(separator + 1);
  try { fragment = decodeURIComponent(fragment); } catch { return null; }
  if (/[\u0000-\u001f]/.test(fragment)) return null;
  if (!path && fragment && sectionId) {
    return {href: projectWikiHref(sectionId, {modId: mod?.id, modPageId: configuration.pageId,
      fragment: wikiHeadingId(fragment), context}), external: false};
  }
  if (mod) {
    const resolved = relativeDocumentPath(currentPath, path);
    const page = (mod.pages || []).find(item => item.path === resolved);
    if (!page) return null;
    return {href: projectWikiHref(mod.section, {modId: mod.id, modPageId: page.id,
      fragment: fragment ? wikiHeadingId(fragment) : '', context}), external: false};
  }
  const pageId = path.endsWith('.md') ? path.slice(0, -3) : null;
  if (!pages.has(pageId)) return null;
  return {href: projectWikiHref(pageId, {fragment: fragment ? wikiHeadingId(fragment) : '', context}), external: false};
}

/**
 * Converts only explicit prose tokens; unknown markup and HTML remain ordinary text.
 * @param {HTMLElement} parent Element receiving text and safe inline elements.
 * @param {string} text One prose segment without block formatting.
 * @param {object|Set<string>} options Link scope accepted by resolveProjectWikiLink.
 * @returns {void} Appends content; provider HTML is never parsed.
 */
function appendInline(parent, text, options) {
  const tokens = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^\s)]+\))/g;
  let start = 0;
  for (const match of text.matchAll(tokens)) {
    parent.append(document.createTextNode(text.slice(start, match.index)));
    const token = match[0];
    if (token.startsWith('`')) parent.append(el('code', token.slice(1, -1)));
    else if (token.startsWith('**')) parent.append(el('strong', token.slice(2, -2)));
    else {
      const [, caption, target] = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const destination = resolveProjectWikiLink(target, options);
      if (destination) {
        const link = el('a', caption);
        link.href = destination.href;
        if (destination.external) { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
        parent.append(link);
      } else parent.append(document.createTextNode(token));
    }
    start = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(start)));
}

/**
 * Renders one complete bounded page with headings, lists and fences, without innerHTML.
 * @param {HTMLElement} container Article whose children will be replaced.
 * @param {string} text Markdown text; callers bound provider pages to 64 KiB.
 * @param {object|Set<string>} options Same catalog scope accepted by resolveProjectWikiLink.
 * @returns {void} Unknown markup and unsafe link destinations remain literal text.
 */
export function renderProjectWiki(container, text, options) {
  container.replaceChildren();
  const lines = String(text).split('\n');
  const reservedAnchors = new Set();
  const emittedAnchors = new Set();
  let insideFence = false;
  // Reserve author headings first so a repeated heading cannot consume another heading's natural ID.
  for (const line of lines) {
    if (/^```/.test(line)) { insideFence = !insideFence; continue; }
    const heading = !insideFence && /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) reservedAnchors.add(wikiHeadingId(heading[2]));
  }
  let paragraph = [], code = [], fenced = false, list = null;
  const flush = () => {
    if (paragraph.length) {
      const node = el('p'); appendInline(node, paragraph.join(' '), options); container.append(node);
    }
    paragraph = []; list = null;
  };
  for (const line of lines) {
    if (/^```/.test(line)) {
      flush();
      if (fenced) { const pre = el('pre'); pre.append(el('code', code.join('\n'))); container.append(pre); code = []; }
      fenced = !fenced; continue;
    }
    if (fenced) { code.push(line); continue; }
    if (!line.trim()) { flush(); continue; }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      flush(); const node = el(`h${heading[1].length}`);
      const anchor = wikiHeadingId(heading[2]);
      let candidate = anchor;
      let occurrence = 1;
      while (emittedAnchors.has(candidate) || (candidate !== anchor && reservedAnchors.has(candidate))) {
        candidate = `${anchor}-${++occurrence}`;
      }
      emittedAnchors.add(candidate); node.id = candidate;
      appendInline(node, heading[2], options); container.append(node); continue;
    }
    const item = /^(?:([-*])|(\d+)\.)\s+(.+)$/.exec(line);
    if (item) {
      if (paragraph.length) flush();
      const kind = item[2] ? 'OL' : 'UL';
      if (!list || list.tagName !== kind) { list = el(kind.toLowerCase()); container.append(list); }
      const node = el('li'); appendInline(node, item[3], options); list.append(node); continue;
    }
    list = null; paragraph.push(line.trim());
  }
  flush();
  if (code.length) { const pre = el('pre'); pre.append(el('code', code.join('\n'))); container.append(pre); }
}
