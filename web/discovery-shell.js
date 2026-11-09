/** Сворачивает результаты и параметры; поле поиска остаётся в toolbar вместе со своим запросом. */
export class DiscoveryShell {
  /** onLayout позволяет владельцу карты обновить размер viewport без автоматического fit. */
  constructor(root, toggle, { onLayout = () => {} } = {}) {
    this.root = root;
    this.toggle = toggle;
    this.onLayout = onLayout;
    this.document = root.ownerDocument || globalThis.document;
    this.fields = {};
    for (const [mode, id] of [['code', 'navigation-search'], ['meaning', 'semantic-query'], ['entries', 'ingress-catalog-search']]) {
      const field = this.document?.getElementById?.(id);
      if (!field) continue;
      this.fields[mode] = field;
      // Каталог — отдельный modal: его поиск не раскрывает и не закрывает боковую панель.
      if (mode === 'entries') continue;
      field.addEventListener('focus', () => this.setOpen(true));
      field.addEventListener('input', () => this.setOpen(true));
      field.addEventListener('keydown', event => {
        if (event.key === 'Escape') this.setOpen(false);
      });
    }
    toggle?.addEventListener('click', () => this.setOpen(root.hidden));
    this.setOpen(!root.hidden);
  }

  /** Показывает единственное поле выбранного способа; его значение и listeners не копируются. */
  syncMode(mode) {
    this.mode = this.fields[mode] ? mode : 'code';
    const controls = this.document?.querySelectorAll?.('[data-discovery-mode]') || [];
    for (const control of controls) control.hidden = control.getAttribute('data-discovery-mode') !== this.mode;
    const label = this.document?.getElementById?.('workspace-search-label');
    const field = this.mode === 'entries' ? this.document?.getElementById?.('ingress-catalog-toolbar-open') : this.fields[this.mode];
    if (field) label?.setAttribute('for', field.id);
    if (this.mode !== 'code') {
      const popup = this.document?.getElementById?.('navigation-search-results');
      if (popup) popup.hidden = true;
    }
  }

  /** Меняет видимость параметров; при закрытии возвращает фокус в активное поле или прежнюю кнопку. */
  setOpen(open, { focus = false } = {}) {
    const changed = this.root.hidden === Boolean(open);
    const field = this.mode === 'entries' ? this.document?.getElementById?.('ingress-catalog-toolbar-open') : this.fields[this.mode];
    if (!open && this.root.contains?.(this.document?.activeElement)) (field || this.toggle)?.focus?.({ preventScroll: true });
    this.root.hidden = !open;
    this.root.inert = !open;
    if (!open) {
      const popup = this.document?.getElementById?.('navigation-search-results');
      if (popup) popup.hidden = true;
    }
    this.toggle?.setAttribute('aria-expanded', String(Boolean(open)));
    if (this.toggle) this.toggle.textContent = open ? 'Свернуть поиск' : 'Открыть поиск';
    if (focus && open) {
      (field || this.root.querySelector('section:not([hidden]) input:not([disabled])'))?.focus?.({ preventScroll: true });
    }
    if (changed) this.onLayout({ open: Boolean(open) });
  }

  /** Явная смена способа поиска раскрывает панель; фоновая синхронизация её не открывает. */
  openForMode(options = {}) { this.setOpen(true, options); }
}
