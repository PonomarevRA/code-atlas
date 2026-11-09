const FOCUSABLE = 'button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])';

/** Держит клавиатуру внутри открытого диалога и возвращает исходный фокус при закрытии. */
export class DialogFocus {
  /** Содержимое и async-загрузки принадлежат просмотрщику; shell управляет только фокусом. */
  constructor(root, { onClose, initialFocus } = {}) {
    this.root = root;
    this.document = root.ownerDocument || globalThis.document;
    this.onClose = onClose;
    this.initialFocus = initialFocus;
    this.background = [];
    this.keydown = event => this.handleKey(event);
    root.addEventListener?.('keydown', this.keydown);
  }

  /** Запоминает trigger один раз; повторное открытие при выборе другой строки его не заменяет. */
  open() {
    if (this.opened) return;
    this.opened = true;
    this.previous = this.document?.activeElement;
    let branch = this.root;
    const body = this.document?.body;
    while (branch.parentElement && branch.parentElement !== body) branch = branch.parentElement;
    for (const element of body?.children || []) {
      if (element === branch || ['SCRIPT', 'STYLE'].includes(element.tagName)) continue;
      this.background.push({ element, inert: element.inert });
      element.inert = true;
    }
    const initial = this.initialFocus?.() || this.controls()[0] || this.root;
    initial.focus?.({ preventScroll: true });
  }

  /** Возвращает только доступные, видимые элементы; состояние кнопок может меняться после HTTP. */
  controls() {
    const result = [];
    for (const element of this.root.querySelectorAll?.(FOCUSABLE) || []) {
      if (element.disabled || element.closest?.('[hidden],[inert]')) continue;
      if (element.getClientRects && !element.getClientRects().length) continue;
      result.push(element);
    }
    return result;
  }

  /** Escape закрывает только этот диалог; Tab циклически проходит его актуальные controls. */
  handleKey(event) {
    if (!this.opened || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopPropagation(); this.onClose?.();
      return;
    }
    if (event.key !== 'Tab') return;
    const controls = this.controls();
    const first = controls[0] || this.root;
    const last = controls.at(-1) || this.root;
    const active = this.document?.activeElement;
    if (!controls.length || !controls.includes(active) || (event.shiftKey ? active === first : active === last)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus?.({ preventScroll: true });
    }
  }

  /** Восстанавливает прежний inert, затем trigger; скрытый trigger не получает фокус. */
  close() {
    if (!this.opened) return;
    this.opened = false;
    for (const { element, inert } of this.background) element.inert = inert;
    this.background = [];
    if (this.previous?.isConnected !== false && !this.previous?.closest?.('[hidden],[inert]')) {
      this.previous?.focus?.({ preventScroll: true });
    }
    this.previous = null;
  }
}
