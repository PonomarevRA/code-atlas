const KEY = 'atlas-activity-workspace-v3';
const DEFAULTS = { left: 'repository', right: 'details' };

/** Selects retained content blocks; WorkspacePanels remains the only owner of outer visibility. */
export class ActivitySidebar {
  /** Blocks provide their existing element/button and side; no graph or data dependencies are accepted. */
  constructor({ root, roots = [root], blocks, panels, storage, onActivate = () => {} }) {
    Object.assign(this, { root, roots, blocks, panels, onActivate });
    this.document = root.ownerDocument; this.selected = { ...DEFAULTS }; this.saved = { ...DEFAULTS };
    try { this.storage = storage === undefined ? this.document.defaultView.localStorage : storage; } catch { this.storage = null; }
    this.read(); this.callbacks = new Map();
    for (const [id, block] of Object.entries(blocks)) {
      const callback = () => this.activate(id, { toggle: true });
      this.callbacks.set(id, callback); block.button.addEventListener('click', callback);
      block.button.setAttribute('aria-controls', block.element.id);
    }
    this.keys = event => {
      if (event.altKey || event.ctrlKey || event.metaKey || !['ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
      // Each edge has its own navigation group; arrow keys never jump across the map.
      const region = this.roots.find(item => item.contains(this.document.activeElement));
      const buttons = Object.values(this.blocks).map(block => block.button).filter(button => region?.contains(button));
      const current = buttons.indexOf(this.document.activeElement);
      if (current < 0) return;
      event.preventDefault();
      let next = current + (event.key === 'ArrowUp' ? -1 : 1);
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = buttons.length - 1;
      buttons[(next + buttons.length) % buttons.length].focus({ preventScroll: true });
    };
    for (const region of roots) region.addEventListener('keydown', this.keys);
    this.syncLayout();
  }
  /** Restore per-side choices; migrate a v2 Details/Tools selection to its matching right side. */
  read() {
    try {
      const text = this.storage?.getItem(KEY) ?? this.storage?.getItem('atlas-activity-workspace-v2') ?? this.storage?.getItem('atlas-activity-workspace-v1');
      if (!text || text.length > 512) return;
      const value = JSON.parse(text);
      if (![1, 2, 3].includes(value.version)) return;
      if (value.version === 2) {
        const block = this.blocks[value.left];
        if (block && ['left','right'].includes(block.side)) this.selected[block.side] = value.left;
      } else {
        for (const side of ['left','right']) if (this.blocks[value[side]]?.side === side) this.selected[side] = value[side];
      }
      this.saved = { ...this.selected };
    } catch { /* Private storage and old settings retain usable defaults. */ }
  }
  /** A switch hides sibling blocks without replacing their DOM or form state. */
  syncLayout() {
    if (this.disposed) return;
    for (const [id, block] of Object.entries(this.blocks)) {
      const active = this.selected[block.side] === id;
      if (!active && block.element.contains(this.document.activeElement)) block.button.focus({ preventScroll: true });
      block.element.hidden = !active; block.element.inert = !active;
      block.element.setAttribute('aria-hidden', String(!active));
      const heading = block.element.getAttribute('aria-labelledby');
      if (active && heading) this.panels.panels[block.side].setAttribute('aria-labelledby', heading);
      const open = active && this.panels.state[block.side];
      block.button.setAttribute('aria-expanded', String(open));
      block.button.setAttribute('aria-pressed', String(open));
      block.button.classList.toggle('activity-active', open);
    }
    for (const side of ['left','right']) this.panels.focusTargets[side] = this.blocks[this.selected[side]].button;
  }
  /** Explicit activation may collapse an already visible block; programmatic reveal never toggles it shut. */
  activate(id, { remember = true, focus = false, toggle = false } = {}) {
    const block = this.blocks[id];
    if (!block || this.disposed) return false;
    const close = toggle && this.selected[block.side] === id && this.panels.state[block.side];
    this.selected[block.side] = id; this.syncLayout();
    this.panels.setOpen(block.side, !close, { remember });
    if (remember) {
      this.saved[block.side] = id;
      try { this.storage?.setItem(KEY, JSON.stringify({ version: 3, ...this.saved })); } catch { /* Selection works without persistence. */ }
    }
    if (!close) this.onActivate(id, { remember, focus });
    this.syncLayout();
    if (focus && !close) {
      const control = block.element.querySelector('input:not([disabled]),select:not([disabled]),button:not([disabled])');
      control?.focus({ preventScroll: true });
    }
    return true;
  }
  /** A contextual Details reveal does not overwrite the user's saved Tools preference. */
  reveal(id) { return this.activate(id, { remember: false }); }
  /** Selection updates all contextual right blocks while preserving the user's active Health/Details/Actions tab. */
  revealSelection() {
    const id = this.selected.right;
    return this.reveal(this.blocks[id]?.side === 'right' ? id : 'details');
  }
  /** Release shell subscriptions without touching map, filters or the retained blocks. */
  dispose() {
    this.disposed = true;
    for (const [id, callback] of this.callbacks) this.blocks[id].button.removeEventListener('click', callback);
    for (const region of this.roots) region.removeEventListener('keydown', this.keys);
  }
}
