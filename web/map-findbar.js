import { centeredMapCamera, findMapItems } from './map-find-scene.js';

/** Leave every editable owner in control, including Find's own input and SVG foreignObject editors. */
function editableTarget(target) {
  for (let node = target; node; node = node.parentElement) {
    const tag = String(node.tagName || '').toLowerCase();
    if (['input', 'textarea', 'select'].includes(tag) || node.isContentEditable) return true;
    const editable = node.getAttribute?.('contenteditable');
    if ((editable != null && editable !== 'false') || node.getAttribute?.('role') === 'textbox') return true;
  }
  return false;
}

/** A modal can own keyboard focus even if the browser reports body as the event target. */
function visibleModal(document) {
  const roots = document?.querySelectorAll?.('[role="dialog"][aria-modal="true"],#source-viewer,#wiki-viewer') || [];
  for (const root of roots) {
    if (root.hidden || root.closest?.('[hidden]')) continue;
    if (!root.getClientRects || root.getClientRects().length) return true;
  }
  return false;
}

/** Compact Find over current-scene cards; repository discovery and map selection remain separate. */
export class MapFindbar {
  /** Bind the supplied controls once; the caller supplies current scene and scope refresh hooks. */
  constructor({ root, trigger, svg, adapter, isAvailable = () => true, onOpen = () => {}, document = svg?.ownerDocument || globalThis.document }) {
    Object.assign(this, { root, trigger, svg, adapter, isAvailable, onOpen, document });
    this.controls = {};
    for (const name of ['input', 'status', 'previous', 'next', 'close']) {
      this.controls[name] = root?.querySelector(`[data-map-find="${name}"]`);
      if (!this.controls[name]) throw new TypeError(`MapFindbar requires ${name}.`);
    }
    this.opened = false; this.matches = []; this.index = -1; this.marked = new Set();
    this.scope = adapter.getScope?.() || ''; this.query = '';
    this.controls.input.maxLength = 200;
    root.hidden = true; root.inert = true;
    this.handlers = {
      trigger: () => this.open(), close: () => this.close(), input: () => this.refresh(),
      previous: () => this.step(-1), next: () => this.step(1),
      inputKey: event => {
        if (event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return;
        if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); if (!event.repeat) this.step(event.shiftKey ? -1 : 1); }
      },
      rootKey: event => {
        if (event.key === 'Escape' && !event.defaultPrevented && !event.altKey && !event.ctrlKey && !event.metaKey) {
          event.preventDefault(); event.stopPropagation(); this.close();
        }
      },
      shortcut: event => this.shortcut(event),
    };
    trigger?.addEventListener('click', this.handlers.trigger);
    this.controls.close.addEventListener('click', this.handlers.close);
    this.controls.input.addEventListener('input', this.handlers.input);
    this.controls.input.addEventListener('keydown', this.handlers.inputKey);
    this.controls.previous.addEventListener('click', this.handlers.previous);
    this.controls.next.addEventListener('click', this.handlers.next);
    root.addEventListener('keydown', this.handlers.rootKey);
    document?.addEventListener('keydown', this.handlers.shortcut);
    this.updateStatus();
  }

  /** Require a visible map; method readers and open dialogs keep their own browser/editor Find. */
  available() {
    if (this.disposed || !this.isAvailable() || visibleModal(this.document)) return false;
    const body = this.document?.body;
    if (body?.classList?.contains('method-workbench-active') || body?.classList?.contains('method-documentation-active')) return false;
    if (this.svg.hidden || this.svg.closest?.('[hidden]')) return false;
    const viewport = this.adapter.getViewport();
    if (!viewport || ![viewport.x, viewport.y, viewport.width, viewport.height].every(Number.isFinite)
      || viewport.width <= 0 || viewport.height <= 0) return false;
    return !this.svg.getClientRects || this.svg.getClientRects().length > 0;
  }

  /** Only the unmodified platform Find shortcut outside editables opens this local bar. */
  shortcut(event) {
    if (event.defaultPrevented || event.repeat || event.isComposing || event.altKey || event.shiftKey) return;
    if (Boolean(event.ctrlKey) === Boolean(event.metaKey) || String(event.key).toLowerCase() !== 'f') return;
    if (editableTarget(event.target) || !this.available()) return;
    event.preventDefault(); this.open();
  }

  /** Opening and typing never pan; retain the original invoker across repeated open commands. */
  open() {
    if (!this.available()) return false;
    if (!this.opened) this.previousFocus = this.document?.activeElement;
    this.onOpen();
    this.opened = true; this.root.hidden = false; this.root.inert = false;
    this.trigger?.setAttribute('aria-expanded', 'true');
    this.refresh();
    this.controls.input.focus?.({ preventScroll: true }); this.controls.input.select?.();
    return true;
  }

  /** Closing removes only Find's classes and preserves the last explicit camera position. */
  close(returnFocus = true) {
    const wasOpened = this.opened;
    this.opened = false; this.root.hidden = true; this.root.inert = true;
    this.trigger?.setAttribute('aria-expanded', 'false'); this.clearMarks();
    this.matches = []; this.index = -1; this.updateStatus();
    const previous = this.previousFocus;
    const visible = previous && (!previous.getClientRects || previous.getClientRects().length);
    const usable = visible && previous.isConnected !== false && !previous.disabled && !previous.closest?.('[hidden],[inert]');
    if (returnFocus && wasOpened) {
      if (usable) previous.focus?.({ preventScroll: true });
      else if (this.available() && this.svg.isConnected !== false) this.svg.focus?.({ preventScroll: true });
    }
    this.previousFocus = null;
  }

  /** Clear detached retained elements too; regex and selected classes are never touched. */
  clearMarks() {
    for (const element of this.marked) element.classList?.remove('map-find-match', 'map-find-current');
    this.marked.clear();
  }

  /** Re-read inventory after scene changes; scope changes drop only this bar's query and cursor. */
  refresh() {
    if (this.disposed) return;
    if (this.opened && !this.available()) { this.close(false); return; }
    const scope = this.adapter.getScope?.() || '';
    if (scope !== this.scope) { this.scope = scope; this.controls.input.value = ''; this.query = ''; this.index = -1; }
    const query = this.controls.input.value || '';
    const current = query === this.query ? this.matches[this.index]?.key : null;
    this.clearMarks(); this.matches = []; this.index = -1; this.truncated = false;
    this.query = query;
    if (this.opened && query) {
      const result = findMapItems(this.adapter.getItems(), query);
      this.matches = result.matches; this.truncated = result.truncated;
      if (current) this.index = this.matches.findIndex(item => item.key === current);
      this.paintMarks();
    }
    this.updateStatus();
  }

  /** Explicit Next/Previous centers a fresh match without fitting, selecting or fetching. */
  step(delta) {
    if (!this.opened || !this.available() || ![1, -1].includes(delta)) return false;
    this.refresh();
    if (!this.matches.length) return false;
    this.index = this.index < 0 ? (delta > 0 ? 0 : this.matches.length - 1)
      : (this.index + delta + this.matches.length) % this.matches.length;
    const camera = centeredMapCamera(this.adapter.getCamera(), this.matches[this.index].center, this.adapter.getViewport());
    this.paintMarks(); this.updateStatus();
    return camera ? this.adapter.setCamera(camera) !== false : false;
  }

  /** CSS markers remain attached to retained offscreen cards until virtualization remounts them. */
  paintMarks() {
    for (let index = 0; index < this.matches.length; index++) {
      const element = this.matches[index].element;
      if (!element) continue;
      element.classList?.add('map-find-match');
      element.classList?.toggle('map-find-current', index === this.index);
      this.marked.add(element);
    }
  }

  /** Count card matches honestly: a cap never becomes a claim that the complete scene was searched. */
  updateStatus() {
    const count = this.matches.length, partial = this.truncated ? ' · показана часть' : '';
    this.controls.status.textContent = !this.query ? 'Карточки текущей карты' : `${this.index < 0 ? 0 : this.index + 1} / ${count}${partial} · карточки текущей карты`;
    this.controls.previous.disabled = this.controls.next.disabled = !count;
  }

  /** Dispose only subscriptions owned by this controller; the graph and activity shell remain alive. */
  dispose() {
    this.close(false); this.disposed = true;
    this.trigger?.removeEventListener('click', this.handlers.trigger);
    this.controls.close.removeEventListener('click', this.handlers.close);
    this.controls.input.removeEventListener('input', this.handlers.input);
    this.controls.input.removeEventListener('keydown', this.handlers.inputKey);
    this.controls.previous.removeEventListener('click', this.handlers.previous);
    this.controls.next.removeEventListener('click', this.handlers.next);
    this.root.removeEventListener('keydown', this.handlers.rootKey);
    this.document?.removeEventListener('keydown', this.handlers.shortcut);
  }
}
