import { el } from './dom.js';

/** Управляет обзором раскрытых методов без перехода на другую схему. */
export class PathToolbar {
  /** Кнопки вызывают renderer; данные анализа используются только для видимых ограничений. */
  constructor(root, { collapse, focus, fit, expand, follow, filterControls, presentation }) {
    this.root = root;
    this.find = name => root.querySelector(`[data-path-toolbar="${name}"]`);
    this.find('collapse').addEventListener('click', collapse);
    this.find('focus').addEventListener('click', focus);
    this.find('fit').addEventListener('click', fit);
    this.find('presentation')?.addEventListener('change', event => presentation?.(event.target.value));
    for (const kind of ['conditions', 'loops']) {
      const checkbox = this.find(kind);
      checkbox?.addEventListener('change', () => filterControls?.({ [kind]: checkbox.checked }));
    }
    this.expand = expand;
    this.follow = follow;
    const relations = this.find('relations');
    if (relations) relations.open = false;
    this.find('expand')?.addEventListener('click', () => this.expand?.());
  }

  /** Показывает прямые raw связи, направления к реальным блокам и результат последнего раскрытия. */
  renderRelations(info) {
    const root = this.find('relations');
    if (!root) return;
    root.hidden = !info;
    root.replaceChildren();
    if (!info) return;
    const summary = el('summary', `Связи выбранного: ${info.selected.label}${info.selected.visible ? '' : ' · блок скрыт'}`, 'path-relations-summary');
    summary.title = 'Направления указаны относительно выбранного блока, а не порядок исполнения.';
    root.append(summary);
    if (!info.selected.visible) root.append(this.relationButton(info.selected));
    for (const [kind, name] of [['incoming', 'Вызывают'], ['outgoing', 'Вызывает']]) {
      const row = el('div', undefined, `path-relations-row path-relations-${kind}`);
      const structural = info[kind].some(item => (item.edges || []).some(edge => !['calls', 'dispatches_to'].includes(edge.relation)));
      const heading = structural ? kind === 'incoming' ? 'Входящие связи' : 'Исходящие связи' : name;
      row.append(el('strong', `${heading} (${info[kind].length})`, 'small'));
      const list = el('div', undefined, 'path-relations-list');
      for (const item of info[kind]) list.append(this.relationButton(item));
      if (!info[kind].length) list.append(el('span', 'нет связей в этой цепочке', 'small muted'));
      row.append(list);
      root.append(row);
    }
    if (info.revealOwner) {
      const status = el('div', `Раскрыт ${info.revealOwner.label}. Новых блоков: ${info.revealed.length}.`, 'path-reveal-status small');
      status.setAttribute('role', 'status');
      const list = el('div', undefined, 'path-relations-list');
      for (const item of info.revealed.slice(0, 6)) list.append(this.relationButton(item));
      if (info.revealed.length > 6) list.append(el('span', `ещё ${info.revealed.length - 6}`, 'small muted'));
      status.append(list);
      root.append(status);
    }
  }

  /** Ссылка сохраняет полное имя и свидетельства в title; скрытая цель раскрывается по явному нажатию. */
  relationButton(item) {
    const block = item.number ? `Блок ${item.number} · ` : '';
    const prefix = item.candidate ? 'Кандидат · ' : '';
    const text = `${item.arrow} ${prefix}${block}${item.label}${item.visible ? '' : ' · раскрыть'}`;
    const button = el('button', text, `path-relation-link${item.candidate ? ' path-relation-candidate' : ''}`);
    button.type = 'button';
    button.setAttribute('data-node-id', item.id);
    const proof = [];
    for (const edge of item.edges || []) {
      const source = edge.source_file || edge.sourceFile || '';
      proof.push(`${edge.relation || 'связь'} · ${edge.confidence || 'уверенность не указана'}${source || edge.sourceLine ? ` · ${source}:${edge.sourceLine || '?'}` : ''}`);
      if (edge.caption) proof.push(edge.caption);
    }
    button.title = `${text}\nРасположение: ${item.label ? item.label + ' — ' : ''}${item.direction || item.label}\n${proof.join('\n')}`;
    button.setAttribute('aria-label', `${text}. ${item.visible ? 'Перейти к блоку, сохранив масштаб.' : 'Раскрыть цепочку и перейти к реализации.'}`);
    button.addEventListener('click', () => this.follow?.(item.id));
    return button;
  }

  /** Показывает размер текущей схемы и отличает скрытые блоки от пределов серверного анализа. */
  render(info, data) {
    this.root.hidden = false;
    this.find('current').textContent = info.label || 'Путь выполнения';
    const noun = data?.rootKind === 'class' || data?.steps?.some(step => step.kind === 'field') ? 'блоков' : 'методов';
    this.find('count').textContent = `${info.visible} из ${info.total} ${noun} · раскрыто ${info.expanded ?? 1}`;
    const expand = this.find('expand');
    if (expand) expand.disabled = typeof this.expand !== 'function' || !info.canExpandAll;
    const limits = [];
    const names = { nodes: 'методы', depth: 'глубина', files: 'файлы', bytes: 'объём исходников', events: 'события',
      arguments: 'аргументы вызовов', parameterFacts: 'параметры и условия', attributeFacts: 'атрибуты', staticData: 'статические данные' };
    for (const [key, limited] of Object.entries(data?.truncation || {})) {
      if (limited) limits.push(names[key] || key);
    }
    const callCoverageLimited = ['nodes', 'depth', 'files', 'bytes', 'events'].some(key => data?.truncation?.[key]);
    this.find('limits').textContent = limits.length
      ? `Анализ ограничен: ${limits.join(', ')}. ${callCoverageLimited ? 'Часть вызовов или условий может отсутствовать.' : 'Часть сведений внутри методов сокращена.'}`
      : 'Статическая схема возможных вызовов. Условия и порядок — из исходника.';
    this.find('limits').classList.toggle('path-analysis-limited', limits.length > 0);
  }

  /** Закрывает управление при возврате к карте кода или смене репозитория. */
  hide() {
    this.root.hidden = true;
    const relations = this.find('relations');
    if (relations) relations.open = false;
    const expand = this.find('expand');
    if (expand) expand.disabled = true;
  }
}
