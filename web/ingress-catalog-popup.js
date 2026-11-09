/** Один просторный modal-каталог; native dialog обеспечивает Escape и клавиатурную изоляцию. */
export class IngressCatalogPopup {
  constructor(dialog, search) {
    this.dialog = dialog; this.search = search;
    dialog?.addEventListener('click', event => { if (event.target === dialog) this.close(); });
  }
  /** Открывает только по действию пользователя; поиск получает фокус без автоматического запуска входа. */
  open() {
    if (!this.dialog || this.dialog.open) return;
    this.dialog.showModal?.();
    this.search?.focus?.({preventScroll: true});
  }
  /** Закрывает окно перед переходом к карте; браузер восстанавливает предшествующий фокус. */
  close() { if (this.dialog?.open) this.dialog.close(); }
}
