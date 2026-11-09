import { el } from './dom.js';
import { declarationSummary } from './card-documentation.js';
import { documentationTree, branchText, armText, commentText, methodPurpose } from './method-documentation-model.js';
import { renderReaderSteps } from './method-reader-steps.js';
import { renderReaderContract } from './reading-explanations.js';
import { renderReadingProvenance } from './reading-provenance.js';

/** Читаемый режим одного метода: документация автора и вложенные действия без загрузки всей цепочки в DOM. */
export class MethodDocumentation {
  /** Переходы выбирают реальные методы, а кнопки исходника сохраняют карту и её камеру. */
  constructor(root, { onSelect, onSource, onDiagram, onRefreshMap }) {
    this.root = root;
    this.onSelect = onSelect;
    this.onSource = onSource;
    this.onRefreshMap = onRefreshMap;
    this.find = name => root.querySelector(`[data-method-doc="${name}"]`);
    this.find('diagram').addEventListener('click', onDiagram);
    this.find('method').addEventListener('change', () => this.onSelect(this.find('method').value));
    this.find('root').addEventListener('click', () => this.onSelect(this.data.rootNodeId));
  }

  /** Кэширует только текущий ответ анализа; выбор метода и фильтры не пересоздают индекс шагов. */
  display(data, selected, active) {
    this.root.hidden = !active || !data;
    if (this.root.hidden) return;
    if (this.data !== data) {
      this.data = data;
      this.steps = new Map((data.steps || []).map(step => [step.nodeId, step]));
      this.dispatch = new Map();
      for (const edge of data.edges || []) {
        if (edge.relation !== 'dispatches_to') continue;
        if (!this.dispatch.has(edge.source)) this.dispatch.set(edge.source, new Set());
        this.dispatch.get(edge.source).add(edge.target);
      }
      const select = this.find('method');
      select.replaceChildren();
      for (const step of this.steps.values()) {
        const option = el('option', `${step.typeContext?.name ? step.typeContext.name + ' · ' : ''}${step.label || step.nodeId}`);
        option.value = step.nodeId; select.append(option);
      }
      this.selected = null;
    }
    const id = selected || data.rootNodeId;
    if (id === this.selected) return;
    this.selected = id;
    this.find('method').value = id;
    this.root.scrollTop = 0;
    this.render(this.steps.get(id));
  }

  /** Показывает точную строку выражения и даёт переход к её исходнику. */
  sourceDetails(step, line, expression) {
    const details = el('details', undefined, 'method-doc-source');
    details.append(el('summary', `Исходник · строка ${line || step.line || '?'}`));
    if (expression) details.append(el('pre', String(expression)));
    const button = el('button', 'Открыть исходник');
    button.type = 'button';
    button.addEventListener('click', () => this.onSource(step.nodeId, line || step.line));
    details.append(button);
    return details;
  }

  /** Реализации всегда подписаны как возможные; назначения берутся из собственных комментариев. */
  targetButton(id, kind = 'call') {
    const target = this.steps.get(id);
    const prefix = { candidate: 'Возможная цель вызова · ', implementation: 'Возможная реализация · ' }[kind] || '';
    const label = `${prefix}${target?.typeContext?.name || ''}${target?.typeContext?.name ? ' · ' : ''}${target?.label || id}`;
    const button = el('button', label, 'method-doc-call');
    button.type = 'button'; button.disabled = !target;
    button.addEventListener('click', () => this.onSelect(id));
    return button;
  }

  /** Вызов описывается комментарием цели; несопоставленный вызов остаётся буквальным именем. */
  renderEvent(item, step) {
    const event = item.event;
    const row = el('article', undefined, `method-doc-action method-doc-${item.kind}`);
    if (item.kind === 'comment') {
      row.append(el('small', 'Комментарий из кода'), el('p', commentText(event.text)));
    } else {
      const names = { log: 'Записать сообщение в журнал', span: 'Событие трассировки', call: 'Выполнить вызов', 'data-write': 'Записать значение словаря' };
      row.append(el('strong', names[item.kind] || 'Действие из исходника'));
      if (event.awaited) row.append(el('span', ' · дождаться результата', 'small muted'));
      const targets = [...new Set(event.targetNodeIds || [])];
      if (!targets.length) {
        const name = item.kind === 'call' && event.callee ? `${event.callee}()` : event.text;
        row.append(el('p', String(name || 'Цель вызова не сопоставлена')));
        if (item.kind === 'call' && event.callee) {
          const reference = el('a', 'Найти в языковом справочнике');
          reference.href = `/language-docs.html?q=${encodeURIComponent(event.callee)}`;
          reference.target = '_blank'; reference.rel = 'noopener noreferrer';
          row.append(reference);
        }
      }
      for (const id of targets) {
        const target = this.steps.get(id), purpose = methodPurpose(target);
        const kind = event.confidence === 'candidate' || targets.length > 1 ? 'candidate' : 'call';
        row.append(this.targetButton(id, kind));
        if (purpose) row.append(el('p', purpose, 'method-doc-purpose'));
      }
    }
    row.append(this.sourceDetails(step, item.line, event.text));
    return row;
  }

  /** Вложенность следует только известным arm IDs; ограничитель защищает DOM от большого ответа. */
  renderItems(items, container, step, depth = 0) {
    for (const item of items) {
      if (this.remaining-- <= 0 || depth > 40) {
        container.append(el('p', 'Продолжение ограничено. Откройте исходник метода.', 'small muted'));
        return;
      }
      if (item.kind === 'branch') {
        const branch = item.branch;
        const block = el('section', undefined, 'method-doc-branch');
        block.append(el('h3', branchText(branch)), this.sourceDetails(step, branch.line, branch.header || branch.condition || branch.selector));
        for (const group of item.arms) {
          const arm = el('div', undefined, 'method-doc-arm');
          arm.append(el('h4', armText(branch, group.arm)));
          if (branch.kind === 'conditional' && group.arm.expression) {
            arm.append(el('p', 'Используется значение выражения:'), el('code', group.arm.expression));
          }
          if (!group.items.length && !group.arm.expression) arm.append(el('p', 'Сопоставленных вызовов или комментариев в этой ветви нет. Тело может содержать другие действия.', 'small muted'));
          this.renderItems(group.items, arm, step, depth + 1);
          block.append(arm);
        }
        container.append(block);
      } else if (item.kind === 'terminal') {
        const names = { return: 'Вернуть результат', throw: 'Выбросить исключение', break: 'Прервать ближайший цикл или switch', continue: 'Перейти к следующей итерации цикла' };
        const row = el('article', undefined, 'method-doc-action method-doc-terminal');
        row.append(el('strong', names[item.terminal.kind] || item.terminal.kind), this.sourceDetails(step, item.line, item.terminal.text));
        container.append(row);
      } else container.append(this.renderEvent(item, step));
    }
  }

  /** Preserve every possible target and the existing explicit selection action within an evaluation step. */
  renderCallTargets(row, event) {
    const targets = [...new Set([...(event.targetNodeIds || []), ...(event.implementationTargetIds || [])])];
    for (const id of targets) {
      const kind = event.confidence === 'candidate' || targets.length > 1 ? 'candidate' : 'call';
      row.append(this.targetButton(id, kind));
      const purpose = methodPurpose(this.steps.get(id));
      if (purpose) row.append(el('p', purpose, 'method-doc-purpose'));
    }
  }

  /** Страница сохраняет назначение, входы/результат и ветвления; неопознанное поведение не дополняется догадками. */
  render(step) {
    const body = this.find('body'); body.replaceChildren();
    body.setAttribute?.('tabindex', '-1');
    if (!step) {
      this.find('title').textContent = 'Метод отсутствует в этой цепочке';
      this.find('status').textContent = 'Выберите метод из списка или откройте его документацию из панели узла.';
      body.append(el('p', 'Выбранный метод отсутствует в этом анализе.'));
      return;
    }
    this.find('title').textContent = step.label || step.nodeId;
    const purpose = methodPurpose(step);
    body.append(el('p', purpose || 'Комментарий о назначении метода не найден.', 'method-doc-intro'));
    const owner = declarationSummary(step.typeContext?.documentation);
    if (owner) body.append(el('p', `${step.typeContext.name}: ${owner}`, 'method-doc-owner'));
    if (step.documentation?.remarks) body.append(el('p', step.documentation.remarks));
    renderReaderContract(body, step, { includePurpose: false });
    renderReadingProvenance(body, { ...step, snapshotId: this.data.snapshotId }, {
      onContinue: () => this.find('body').focus?.(), onRefresh: this.onRefreshMap });
    const parsedFlow = step.methodFlow?.status === 'parsed';
    const unsupported = parsedFlow ? step.methodFlow.unsupported || [] : step.controlFlow?.unsupported || [];
    if (unsupported.length) {
      const messages = [];
      if (unsupported.some(text => /try_statement|catch_clause|finally_clause/.test(text))) messages.push('Обработка исключений разобрана частично: проверьте try/catch/finally в исходнике. Действия без известной ветви показаны отдельно.');
      if (unsupported.some(text => /lambda|Вложенные объявления/.test(text))) messages.push('Вложенные функции: момент их вызова не определён. Показано расположение в исходнике.');
      if (unsupported.some(text => /conditional_expression|goto_statement/.test(text))) messages.push('Часть условных переходов не сопоставлена. Проверьте исходник.');
      if (!messages.length) messages.push('Часть переходов не разобрана. Проверьте исходник.');
      for (const message of messages) body.append(el('p', message, 'method-doc-notice'));
    }
    body.append(el('h2', 'Как работает метод'));
    let flowLimited = false;
    if (parsedFlow) {
      const sequence = el('div', undefined, 'method-doc-evaluation');
      renderReaderSteps(sequence, step.methodFlow.steps, step, { onSource: this.onSource,
        renderCallTargets: (row, event) => this.renderCallTargets(row, event) });
      body.append(sequence);
      flowLimited = Boolean(step.methodFlow.truncated);
      if (!step.methodFlow.steps?.length) body.append(el('p', 'Сопоставленные шаги не извлечены. Проверьте исходник метода.', 'small muted'));
    } else {
      const tree = documentationTree(step); this.remaining = 500;
      this.renderItems(tree.items, body, step);
      flowLimited = tree.limited;
      if (!tree.items.length) body.append(el('p', step.sourceStatus === 'parsed'
        ? 'Сопоставленные действия не найдены. Назначение приведено выше; детали доступны в исходнике.'
        : 'Тело метода недоступно в анализе.', 'small muted'));
      if (tree.unplaced.length) {
        const other = el('section', undefined, 'method-doc-unplaced');
        other.append(el('h3', 'Принадлежность к ветви не сопоставлена'));
        this.renderItems(tree.unplaced, other, step); body.append(other);
      }
      body.append(el('p', 'Порядок вычисления выражений не разобран; показано лексическое дерево вызовов.', 'small muted'));
    }
    const candidates = this.dispatch.get(step.nodeId);
    if (candidates?.size) {
      const section = el('section', undefined, 'method-doc-implementations');
      section.append(el('h2', 'Возможные реализации'));
      for (const id of candidates) {
        section.append(this.targetButton(id, 'implementation'));
        const text = methodPurpose(this.steps.get(id));
        if (text) section.append(el('p', text));
      }
      body.append(section);
    }
    const limited = flowLimited || step.documentation?.truncated || Object.values(this.data.truncation || {}).some(Boolean);
    if (limited) body.append(el('p', 'Анализ ограничен: часть действий или документации может отсутствовать.', 'method-doc-notice'));
    body.append(this.sourceDetails(step, step.line, step.signature));
    this.find('status').textContent = parsedFlow
      ? 'Комментарии — из кода. Порядок вычисления — из статического разбора выражений; это не runtime-трасса. Значения условий и число повторений не вычисляются.'
      : 'Комментарии — из кода. Показано лексическое дерево вызовов, условий и переходов; порядок вычисления выражений не установлен.';
  }
}
