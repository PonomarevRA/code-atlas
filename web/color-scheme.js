import { requestJson } from './bounded-request.js';
export const COLOR_SCHEME_KEY = 'atlas-color-scheme';

/** Choose only published palettes; a disabled/changed mod falls back to its last selected base. */
export function resolveColorScheme(schemes, choice) {
  return schemes.find(item => item.id === choice?.id)
    || schemes.find(item => item.id === choice?.base) || schemes.find(item => item.id === 'light');
}

/** Catalog, persistence and cross-tab updates never trigger a graph render or change the camera. */
export class ColorSchemeController {
  constructor({ root = document.documentElement, storage = localStorage, events = window, fetcher } = {}) {
    this.root = root; this.storage = storage; this.events = events; this.fetcher = fetcher;
    this.schemes = []; this.listeners = new Set(); this.revision = 0;
    this.handleStorage = event => {
      if (event.key === COLOR_SCHEME_KEY) this.apply(this.read(), false);
      if (event.key === 'atlas-mods-revision') this.refresh().catch(() => {});
    };
    events.addEventListener('storage', this.handleStorage);
  }

  /** Unavailable or corrupt local storage never prevents opening the map. */
  read() {
    try { return JSON.parse(this.storage.getItem(COLOR_SCHEME_KEY) || 'null'); }
    catch { return null; }
  }

  /** Apply attributes rather than inline styles; the trusted stylesheet owns all palette values. */
  apply(choice, remember = true) {
    const scheme = resolveColorScheme(this.schemes, choice);
    if (!scheme) return;
    this.selected = scheme;
    this.root.setAttribute('data-color-scheme', scheme.id);
    this.root.setAttribute('data-color-mode', scheme.base);
    if (remember) {
      try { this.storage.setItem(COLOR_SCHEME_KEY, JSON.stringify({ id: scheme.id, base: scheme.base })); }
      catch { /* Selection still works for this page when persistent storage is disabled. */ }
    }
    for (const listener of this.listeners) listener(scheme);
  }

  /** Load declarations without importing executable mods, including on the recovery management page. */
  async refresh() {
    const revision = ++this.revision;
    const safe = new URLSearchParams(location.search).get('mods') === 'off';
    const catalog = await requestJson('/api/color-schemes' + (safe ? '?safe=true' : ''), {}, { fetcher: this.fetcher });
    if (revision !== this.revision) return catalog;
    this.schemes = catalog.schemes;
    const sheet = document.getElementById('color-scheme-styles');
    if (sheet) sheet.href = '/api/color-schemes/styles.css?' + new URLSearchParams({ safe: String(safe), revision: String(Date.now()) });
    this.apply(this.read(), false);
    return catalog;
  }

  /** Return an unsubscribe function so menu refreshes cannot retain detached controls. */
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }

  /** Release the sole cross-tab listener when an embedded workspace is disposed. */
  dispose() { this.events.removeEventListener('storage', this.handleStorage); this.listeners.clear(); }
}

let controller;
/** One palette controller is shared by the workspace menu and appearance editor on each page. */
export function colorSchemes() {
  if (!controller) {
    controller = new ColorSchemeController();
    controller.ready = controller.refresh().catch(() => {
      // Built-in CSS remains available when the catalog request fails.
      controller.schemes = ['light', 'dark'].map(base => ({ id: base, label: base === 'light' ? 'Светлая' : 'Тёмная', base }));
      controller.apply(controller.read(), false);
    });
  }
  return controller;
}

/** Render a compact labeled picker; selecting it preserves the active card and URL. */
export async function colorSchemePicker(parent) {
  const themes = colorSchemes(); await themes.ready;
  const label = document.createElement('label'); label.className = 'color-scheme-picker';
  label.append(document.createTextNode('Цветовая схема'));
  const select = document.createElement('select'); select.setAttribute('aria-label', 'Цветовая схема');
  const update = () => {
    select.replaceChildren();
    for (const scheme of themes.schemes) {
      const option = document.createElement('option'); option.value = scheme.id;
      option.textContent = scheme.label + (scheme.origin && scheme.origin !== 'builtin' ? ' · мод' : '');
      select.append(option);
    }
    select.value = themes.selected.id;
  };
  update(); select.addEventListener('change', () => themes.apply({ id: select.value }));
  const unsubscribe = themes.subscribe(() => { if (label.isConnected) update(); else unsubscribe(); });
  label.append(select); parent.append(label);
}
