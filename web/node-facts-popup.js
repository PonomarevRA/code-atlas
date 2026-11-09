import { NodeDescriptionViewer } from './node-description.js';

/** Закреплённое окно фактов читает один узел и не меняет выбор, уровень карты или камеру. */
export class NodeFactsPopup {
  /** Отдельный viewer использует свой контекст и отмену запросов независимо от выбранного узла карты. */
  constructor(root, { onSource = () => {}, fetcher } = {}) {
    this.root = root;
    this.context = {};
    this.viewer = new NodeDescriptionViewer(root, () => this.context, { onSource, fetcher, scrollOnRender: false });
    root.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); this.close(); }
    });
    root.querySelector('[data-description="close"]').addEventListener('click', () => this.restoreFocus());
  }
  /** Новый контекст отменяет старый запрос; фокус попадает в доступное с клавиатуры окно. */
  open(context, opener = null) {
    // Повторное открытие из окна сохраняет исходную карточку для Escape.
    if (opener) this.returnFocus = opener;
    else if (!this.viewer.opened || !this.root.contains(document.activeElement)) this.returnFocus = document.activeElement;
    this.context = { ...context };
    const pending = this.viewer.open();
    this.root.querySelector('[data-description="close"]').focus?.({ preventScroll: true });
    return pending;
  }
  /** При смене репозитория или снимка нельзя показывать описание прежнего checkout. */
  sync(projectId, snapshotId) {
    if (this.context.projectId !== projectId || this.context.snapshotId !== snapshotId) this.close(false);
  }
  /** Закрывает запрос и по желанию возвращает фокус на карточку без прокрутки. */
  close(restore = true) { this.viewer.close(); if (restore) this.restoreFocus(); }
  /** Удалённая при перерисовке карточка не получает фокус. */
  restoreFocus() { if (this.returnFocus?.isConnected) this.returnFocus.focus?.({ preventScroll: true }); }
}
