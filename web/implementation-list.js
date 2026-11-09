import { el } from './dom.js';

/** A bounded snapshot list explains alternatives without expanding every implementation body. */
export class ImplementationList {
  constructor(root, { onSource }) {
    this.root = root; this.onSource = onSource;
    root.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); this.close(); } });
    root.querySelector('[data-implementations="close"]').addEventListener('click', () => this.close());
  }
  /** Names are snapshot candidates; DI/runtime selection remains explicitly unknown. */
  open(node, context, opener) {
    this.context = context; this.opener = opener; this.root.hidden = false;
    const count = `${node.candidateCountExact === false ? 'не менее ' : ''}${node.candidateCount}`;
    this.root.querySelector('[data-implementations="title"]').textContent = `Возможные реализации · ${count}`;
    const body = this.root.querySelector('[data-implementations="body"]'); body.replaceChildren();
    body.append(el('p', `${node.label}. Фактический выбор и число экземпляров определяются при выполнении.`, 'small muted'));
    const items = node.implementationItems || [];
    const headings = { source: 'В исходниках проекта', delegate: 'Переадресуют вызов', tests: 'В тестовых исходниках' };
    for (const scope of ['source', 'delegate', 'tests']) {
      const members = items.filter(item => (item.scope || 'source') === scope);
      if (!members.length) continue;
      body.append(el('h3', `${headings[scope]} · ${members.length}`));
      for (const item of members) {
        const button = el('button', item.label, 'implementation-list-item');
        button.type = 'button'; button.title = `${item.qualifiedLabel || item.label}\n${item.file || ''}:${item.line || '?'}`;
        button.setAttribute('data-implementation-id', item.id);
        button.addEventListener('click', () => { this.close(false); this.onSource(item.id, item.line); });
        body.append(button);
      }
    }
    if (node.implementationListLimited) body.append(el('p', `Показано ${node.implementationItems.length} из ${node.candidateCount}; список ограничен.`, 'small muted'));
    this.root.querySelector('[data-implementations="close"]').focus?.({ preventScroll: true });
  }
  /** Changing a snapshot closes stale facts; camera and map selection stay untouched. */
  sync(projectId, snapshotId) {
    if (this.context && (this.context.projectId !== projectId || this.context.snapshotId !== snapshotId)) this.close(false);
  }
  /** Source navigation may remove the opener; restore focus only while it remains connected. */
  close(restore = true) { this.root.hidden = true; if (restore && this.opener?.isConnected) this.opener.focus?.({ preventScroll: true }); }
}
