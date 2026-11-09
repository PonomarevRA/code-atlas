import { el } from './dom.js';
/** Общие case-ветви сохраняют отдельные guard каждой метки; варианты объединяются через ИЛИ. */
function armText(arm) {
  if (!arm.labels?.length) return `${arm.label || arm.kind}${arm.condition ? ` · условие ${arm.condition}` : ''}`;
  const labels = [];
  for (const label of arm.labels) {
    const text = String(label.text || 'case');
    labels.push(`${text}${label.guard && !text.includes(label.guard) ? ` · ${label.guard}` : ''}`);
  }
  return labels.join(' ИЛИ ');
}
/** Возвращает точные подписи ветвей исходника без вычисления их истинности. */
export function branchLabels(controlFlow, branchIds = []) {
  const arms = new Map();
  for (const branch of controlFlow?.branches || []) for (const arm of branch.arms || []) {
    let text;
    if (branch.kind === 'if') text = `if (${branch.condition || '?'}) — ${arm.kind === 'true' ? 'истина' : arm.kind === 'false' ? 'ложь / else' : arm.label}`;
    else if (branch.kind === 'conditional') text = `?: (${branch.condition || '?'}) — ${arm.kind === 'true' ? 'истина' : 'иначе'}: ${arm.expression || '?'}`;
    else if (['for', 'foreach', 'while', 'do'].includes(branch.kind)) text = `${branch.header || `${branch.kind}${branch.condition ? ` (${branch.condition})` : ''}`} — тело`;
    else text = `switch (${branch.selector || '?'}) — ${armText(arm)}`;
    arms.set(arm.id, text);
  }
  const result = [];
  for (const id of new Set(branchIds)) result.push(arms.get(id) || 'Условие ветви не сопоставлено');
  return result;
}
/** Показывает ветвления отдельно от порядка выполнения; терминалы остаются исходными фактами. */
export function renderControlFlow(controlFlow) {
  const branches = controlFlow?.branches || [];
  const terminals = controlFlow?.terminals || [];
  const details = el('details', undefined, 'path-control-flow');
  details.append(el('summary', `Условия и переходы · ${branches.length} ветвлений / ${terminals.length} завершений`));
  if (!controlFlow) { details.append(el('p', 'Структура управления для этого символа недоступна.', 'small muted')); return details; }
  const list = el('div', undefined, 'path-control-list');
  for (const branch of branches.slice(0, 100)) {
    const section = el('article', undefined, 'path-branch');
    section.append(el('strong', `${branch.kind === 'conditional' ? '?:' : branch.kind} · строка ${branch.line || '?'}`), el('pre', String(branch.header || branch.condition || branch.selector || 'Условие не сопоставлено')));
    const parents = branchLabels(controlFlow, branch.parentArmIds || branch.branchIds || (branch.parentArmId ? [branch.parentArmId] : []));
    if (parents.length) section.append(el('p', `Внутри: ${parents.join(' → ')}`, 'small muted'));
    for (const arm of branch.arms || []) {
      section.append(el('p', `${arm.kind === 'true' ? 'Истина' : arm.kind === 'false' ? 'Ложь / else' : armText(arm)} · строки ${arm.line || '?'}–${arm.endLine || '?'}`));
      if (arm.expression) section.append(el('pre', arm.expression));
    }
    list.append(section);
  }
  for (const terminal of terminals.slice(0, 100)) {
    const section = el('article', undefined, 'path-branch');
    const labels = branchLabels(controlFlow, terminal.branchIds);
    section.append(el('strong', `${terminal.kind} · строка ${terminal.line || '?'}`), el('pre', String(terminal.text || terminal.kind)));
    if (labels.length) section.append(el('p', labels.join(' → '), 'small muted'));
    list.append(section);
  }
  details.append(list);
  if (controlFlow.truncated || branches.length > 100 || terminals.length > 100) details.append(el('p', 'Структура ограничена; показаны не все ветвления/переходы.', 'small muted'));
  for (const value of controlFlow.unsupported || []) details.append(el('p', `Не разобрано: ${String(value)}`, 'small muted'));
  return details;
}
/** Выводит тексты документации как цитаты исходника, отдельно от программно собранных фактов. */
export function renderDocumentation(documentation, title = 'Документация из исходника') {
  const section = el('details', undefined, 'node-documentation');
  section.append(el('summary', title));
  if (!documentation) { section.append(el('p', 'Документация не получена.', 'small muted')); return section; }
  let populated = false;
  for (const [field, label] of [['summary', 'XML summary'], ['remarks', 'XML remarks'], ['returns', 'XML returns']]) {
    if (!documentation[field]) continue;
    populated = true;
    section.append(el('strong', label), el('pre', String(documentation[field])));
  }
  for (const parameter of documentation.params || []) { populated = true; section.append(el('strong', `XML param: ${parameter.name}`), el('pre', String(parameter.text || ''))); }
  for (const comment of (documentation.comments || []).slice(0, 100)) { populated = true; section.append(el('strong', `Комментарий · строка ${comment.line || '?'}`), el('pre', String(comment.text || ''))); }
  if (!populated) section.append(el('p', 'XML-документация и комментарии для символа не найдены.', 'small muted'));
  if (documentation.truncated || (documentation.comments?.length || 0) > 100) section.append(el('p', 'Тексты документации ограничены по объёму.', 'small muted'));
  if (documentation.unsupported) section.append(el('p', 'Часть документации не поддержана анализатором.', 'small muted'));
  return section;
}
