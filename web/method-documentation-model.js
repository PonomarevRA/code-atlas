import { declarationSummary } from './card-documentation.js';

/** Находит операторы верхнего уровня, не разделяя строки, скобки или вложенные вызовы. */
function operators(expression, choices) {
  const found = [];
  let depth = 0, quote = '';
  for (let i = 0; i < expression.length; i++) {
    const character = expression[i];
    if (quote) {
      if (character === '\\') i++;
      else if (character === quote) quote = '';
      continue;
    }
    if (character === '"' || character === "'") { quote = character; continue; }
    if ('([{'.includes(character)) depth++;
    else if (')]}'.includes(character)) depth--;
    else if (depth === 0) {
      const operator = choices.find(value => expression.startsWith(value, i));
      if (operator) { found.push({ index: i, operator }); i += operator.length - 1; }
    }
  }
  return found;
}

/** Описывает только однозначные формы; сложное выражение остаётся точной цитатой кода. */
export function conditionText(value) {
  const expression = String(value || '').trim();
  if (!expression) return 'условие не сопоставлено';
  // Скобки снимаются только когда окружают всё выражение.
  if (expression.startsWith('(') && expression.endsWith(')')) {
    const tail = expression.slice(1, -1);
    if (balanced(tail)) return conditionText(tail);
  }
  for (const part of operators(expression, ['?', '=>', '='])) {
    const assignment = part.operator === '=' && !['=', '!', '>', '<'].includes(expression[part.index - 1])
      && expression[part.index + 1] !== '=';
    if (part.operator !== '=' || assignment) return `выполнено условие «${expression}»`;
  }
  for (const operator of ['||', '&&']) {
    const parts = operators(expression, [operator]);
    if (!parts.length) continue;
    const descriptions = [];
    let start = 0;
    for (const part of parts) {
      descriptions.push(`(${conditionText(expression.slice(start, part.index))})`);
      start = part.index + part.operator.length;
    }
    descriptions.push(`(${conditionText(expression.slice(start))})`);
    return descriptions.join(operator === '||' ? ' или ' : ' и ');
  }
  const name = '[A-Za-z_][\\w.]*';
  const nullCheck = expression.match(new RegExp(`^(${name})\\s*(==|!=|is not|is)\\s+null$`))
    || expression.match(new RegExp(`^(${name})\\s*(==|!=)\\s*null$`));
  if (nullCheck) return `значение «${nullCheck[1]}» ${['!=', 'is not'].includes(nullCheck[2]) ? 'не равно null (задано)' : 'равно null (отсутствует)'}`;
  const comparisons = operators(expression, ['>=', '<=', '==', '!=', '>', '<']);
  if (comparisons.length === 1) {
    const { index, operator } = comparisons[0];
    const left = expression.slice(0, index).trim(), right = expression.slice(index + operator.length).trim();
    const operand = /^(?:[A-Za-z_][\w.]*|-?\d+(?:\.\d+)?[mMdDfFlL]?|true|false|null|"[^"\\]*")$/;
    if (operand.test(left) && operand.test(right)) {
      const words = { '>': 'больше', '<': 'меньше', '>=': 'больше или равно', '<=': 'меньше или равно', '==': 'равно', '!=': 'не равно' };
      return `значение «${left}» ${words[operator]} «${right}»`;
    }
  }
  if (new RegExp(`^${name}$`).test(expression)) return `«${expression}» истинно`;
  if (new RegExp(`^!${name}$`).test(expression)) return `«${expression.slice(1)}» ложно`;
  return `выполнено условие «${expression}»`;
}

/** Проверяет внешние скобки без изменения выражений со строковыми литералами. */
function balanced(expression) {
  let depth = 0, quote = '';
  for (let i = 0; i < expression.length; i++) {
    const c = expression[i];
    if (quote) { if (c === '\\') i++; else if (c === quote) quote = ''; continue; }
    if (c === '"' || c === "'") quote = c;
    else if (c === '(') depth++;
    else if (c === ')' && --depth < 0) return false;
  }
  return depth === 0 && !quote;
}

/** Заголовки ветвей описывают правила исполнения, не вычисляя значения или число итераций. */
export function branchText(branch) {
  if (['if', 'conditional'].includes(branch.kind)) return `Если ${conditionText(branch.condition)}`;
  if (branch.kind.startsWith('switch')) return `Выбрать вариант по значению «${branch.selector || '?'}»`;
  if (branch.kind === 'foreach') return `Для каждого элемента из «${branch.collection || branch.header || '?'}» выполнить`;
  if (branch.kind === 'do') return `Выполнить хотя бы один раз, затем повторять, пока ${conditionText(branch.condition)}`;
  if (branch.kind === 'for' && !branch.condition) return 'Повторять цикл; условие завершения в заголовке не задано';
  return `Повторять, пока ${conditionText(branch.condition)}`;
}

/** Каждая switch-метка сохраняет собственный when; общие тела объединены через «или». */
export function armText(branch, arm) {
  if (['if', 'conditional'].includes(branch.kind)) return arm.kind === 'false' ? 'Иначе' : 'Тогда';
  if (['for', 'foreach', 'while', 'do'].includes(branch.kind)) return 'В каждой итерации';
  const labels = arm.labels?.length ? arm.labels : [{ text: arm.label || arm.kind, guard: arm.condition }];
  const result = [];
  for (const label of labels) {
    let pattern = String(label.text || '').replace(/^case\s+/, '').trim();
    const guard = String(label.guard || '').replace(/^when\s+/, '').trim();
    if (guard) pattern = pattern.replace(/\s+when\s+[\s\S]*$/, '');
    let text = ['default', '_'].includes(pattern) ? 'Для остальных значений' : `Вариант «${pattern}»`;
    if (branch.selector && !['default', '_'].includes(pattern)) {
      const relational = /^(>=|<=|>|<)\s*-?\d+(?:\.\d+)?$/.test(pattern);
      const scalar = /^(?:[A-Za-z_][\w.]*|-?\d+(?:\.\d+)?|"[^"\\]*")$/.test(pattern);
      if (relational || scalar) {
        const comparison = `${branch.selector} ${relational ? pattern : '== ' + pattern}`;
        text = `Когда ${conditionText(comparison)}`;
      }
    }
    if (guard) text += `, если ${conditionText(guard)}`;
    result.push(text);
  }
  return result.join(' или ');
}

/** Удаляет синтаксис обычного комментария, сохраняя текст автора. */
export function commentText(text) {
  return String(text || '').replace(/^\s*\/\*+|\*\/\s*$/g, '')
    .replace(/^\s*\/\/\/?\s?|^\s*\*\s?/gm, '').trim();
}

/** Собирает вложенные ветви и действия по arm IDs, сохраняя неизвестную принадлежность отдельно. */
export function documentationTree(step) {
  const items = [], unplaced = [], arms = new Map();
  const branches = (step.controlFlow?.branches || []).slice(0, 100);
  const entries = [];
  for (const branch of branches) {
    const item = { kind: 'branch', line: branch.line, branch, arms: [] };
    for (const arm of branch.arms || []) {
      const group = { arm, items: [], depth: (branch.parentArmIds || []).length };
      arms.set(arm.id, group); item.arms.push(group);
    }
    entries.push({ item, parents: branch.parentArmIds || [] });
  }
  for (const event of (step.events || []).slice(0, 160)) {
    if (event.kind === 'comment' && event.line < step.line) continue;
    entries.push({ item: { kind: event.kind, line: event.line, column: event.column, event }, parents: event.branchIds || [] });
  }
  for (const terminal of (step.controlFlow?.terminals || []).slice(0, 100)) {
    entries.push({ item: { kind: 'terminal', line: terminal.line, terminal }, parents: terminal.branchIds || [] });
  }
  for (const { item, parents } of entries) {
    let parent;
    for (const id of parents) {
      const candidate = arms.get(id);
      if (candidate && (!parent || candidate.depth > parent.depth)) parent = candidate;
    }
    const unknownConditional = item.event?.conditional && !parents.length;
    const unknownTerminal = item.kind === 'terminal' && !parents.length
      && step.controlFlow?.unsupported?.some(text => /try_statement|catch_clause|finally_clause/.test(text));
    if (unknownConditional || unknownTerminal || parents.some(id => !arms.has(id))) unplaced.push(item);
    else if (parent) parent.items.push(item);
    else items.push(item);
  }
  const order = { comment: 0, call: 1, log: 1, span: 1, branch: 2, terminal: 3 };
  const compare = (a, b) => (a.line ?? Infinity) - (b.line ?? Infinity)
    || (order[a.kind] ?? 1) - (order[b.kind] ?? 1) || (a.column ?? 0) - (b.column ?? 0);
  items.sort(compare); unplaced.sort(compare);
  for (const arm of arms.values()) arm.items.sort(compare);
  return { items, unplaced, limited: Boolean(step.controlFlow?.truncated)
    || (step.controlFlow?.branches?.length || 0) > 100 || (step.events?.length || 0) > 160 };
}

/** Назначение берётся только из документации конкретного объявления. */
export function methodPurpose(step) { return declarationSummary(step?.documentation); }
