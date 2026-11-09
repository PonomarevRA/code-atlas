import { el } from './dom.js';
import { declarationSummary } from './card-documentation.js';
import { conditionText, commentText } from './method-documentation-model.js';
import { readerCallEvent } from './method-reader-model.js';

/** Read only declaration documentation; a containing type never substitutes for a method contract. */
export function readerContract(description = {}) {
  const documentation = description.documentation || {};
  const docs = new Map((documentation.params || []).map(parameter => [parameter.name, parameter.text]));
  const parameters = [];
  for (const parameter of (description.parameters || []).slice(0, 40)) {
    parameters.push({ name: parameter.name || '', type: parameter.type || '', description: docs.get(parameter.name) || '' });
  }
  // Legacy descriptions may retain parameter comments without a parsed declaration.
  // Keep that evidence visible, explicitly distinguishing it from a declared input.
  if (!Array.isArray(description.parameters)) {
    for (const parameter of (documentation.params || []).slice(0, 40)) {
      parameters.push({ name: parameter.name || '', type: '', description: parameter.text || '', declarationKnown: false });
    }
  }
  return { purpose: declarationSummary(documentation), parameters,
    result: { type: description.returnType || '', description: documentation.returns || '' } };
}

/** Visible input/result types retain missing explanations explicitly instead of guessing domain semantics. */
export function renderReaderContract(container, description, { includePurpose = true } = {}) {
  const contract = readerContract(description);
  const section = el('section', undefined, 'reader-contract');
  if (includePurpose) section.append(el('p', contract.purpose || 'Описание назначения метода отсутствует.', 'reader-purpose'));
  section.append(el('h3', 'Входные данные'));
  if (!contract.parameters.length) section.append(el('p', 'Сопоставленных параметров нет; полнота зависит от разбора объявления.', 'small muted'));
  for (const parameter of contract.parameters) {
    const row = el('p', undefined, 'reader-contract-parameter');
    const type = parameter.declarationKnown === false ? 'объявление не сопоставлено' : parameter.type || 'тип не установлен';
    row.append(el('strong', `${parameter.name || '?'} · ${type}`),
      el('span', ` — ${parameter.description || 'Описание параметра отсутствует.'}`));
    section.append(row);
  }
  section.append(el('h3', 'Результат'), el('p', `${contract.result.type || 'Тип результата не установлен'} — ${contract.result.description || 'Описание результата отсутствует.'}`));
  section.append(el('small', 'Типы взяты из объявления; пояснения — из комментариев метода. Значения не вычислялись.', 'muted'));
  container.append(section);
  return section;
}

/** Quote only a comment adjacent to this exact statement with the same indentation. */
function stepComment(step, description) {
  for (const event of description.events || []) {
    if (event.kind !== 'comment') continue;
    const adjacent = event.line === step.line - 1 && event.column === step.column;
    if (!adjacent || /<\/?[a-z][^>]*>/i.test(event.text || '')) continue;
    const text = commentText(event.text);
    if (text) return text;
  }
  return '';
}

/** Describe the loop header conservatively; complex expressions remain exact source evidence. */
function loopExplanation(step) {
  const header = String(step.label || '').trim();
  if (step.controlKind === 'foreach') {
    const collection = header.match(/^foreach\s*\([\s\S]*?\s+in\s+([\s\S]*)\)$/)?.[1]?.trim();
    return collection ? `Для каждого элемента из «${collection}» выполнить действия внутри цикла.` : 'Для каждого элемента коллекции выполнить действия внутри цикла.';
  }
  const test = step.conditionExpression || header.match(/^(?:while|do\s+while)\s*\(([\s\S]*)\)$/)?.[1];
  if (step.controlKind === 'do') return test
    ? `Выполнить тело хотя бы один раз, затем повторять, пока ${conditionText(test)}.`
    : 'Выполнить тело хотя бы один раз, затем проверить условие повторения.';
  if (step.controlKind === 'for') {
    const plainHeader = header.match(/^for\s*\(([^;]*);([^;]*);([^;]*)\)$/);
    if (plainHeader) {
      const condition = plainHeader[2].trim();
      return condition ? `После начальной подготовки повторять тело, пока ${conditionText(condition)}; затем выполнять обновление.`
        : 'После начальной подготовки повторять тело и выполнять обновление; условие выхода в заголовке не задано.';
    }
    return 'Начальную подготовку, условие повторения и обновление брать из заголовка цикла; сложное выражение сохранено в исходнике.';
  }
  return test ? `Повторять тело, пока ${conditionText(test)}.` : 'Проверять условие перед каждой итерацией; число повторений не установлено.';
}

/** Preserve source operators and evaluation order; a neutral phrase is preferable to an invented business rule. */
export function readerStepExplanation(step, description = {}) {
  const event = step.kind === 'call' ? readerCallEvent(description, step) : null;
  const exactExpression = step.sourceStatement || event?.sourceExpression || step.expression || step.label || '';
  let text = '', source = 'code';
  if (step.kind === 'branch') {
    const condition = step.conditionExpression || step.label || '';
    if (step.controlKind === 'try') text = 'Выполнить основной блок; при исключении перейти к подходящей обработке.';
    else if (step.controlKind?.startsWith('switch')) text = `Выбрать подходящий вариант по значению «${condition}».`;
    else if (step.controlKind === '??') text = `Если значение «${condition}» равно null, вычислить правую часть; иначе сохранить левое значение.`;
    else if (['&&', '||'].includes(step.controlKind)) {
      const missingCondition = !String(condition).trim() || ['null', 'undefined'].includes(String(condition).trim());
      if (missingCondition) {
        text = step.controlKind === '&&' ? 'Вычислить правую часть только если результат проверки слева истинен.'
          : 'Вычислить правую часть только если результат проверки слева ложен.';
        source = 'unknown';
      } else if (step.controlKind === '&&') text = `Вычислить правую часть только если ${conditionText(condition)}.`;
      else text = `Вычислить правую часть только если левое условие «${condition}» ложно.`;
    }
    else text = `Если ${conditionText(condition)}, выполнить действия ветви «Тогда»; иначе перейти к ветви «Иначе».`;
  } else if (step.kind === 'loop') text = loopExplanation(step);
  else if (step.kind === 'call') {
    const name = event?.callee || String(step.label || '').match(/([\w.]+)\s*(?:<[^<>]*>)?\s*\(/)?.[1];
    text = name ? `Вызвать «${name}» после вычисления его аргументов.` : 'Вызвать операцию после вычисления её аргументов.';
  } else if (step.kind === 'await') text = 'Дождаться завершения уже запущенной асинхронной операции.';
  else if (step.kind === 'return') text = step.returnsValue === false || /^return\s*;\s*$/.test(step.label || '')
    ? 'Завершить этот метод без возвращаемого значения.' : 'Вернуть вычисленный результат и завершить этот метод.';
  else if (step.kind === 'throw') text = 'Передать исключение ближайшему подходящему обработчику; обычное продолжение здесь прекращается.';
  else if (step.kind === 'continue') text = 'Перейти к следующей итерации ближайшего цикла.';
  else if (step.kind === 'break') text = 'Выйти из ближайшего цикла или switch и продолжить после него.';
  else if (step.kind === 'value') text = step.reuseResult ? 'Использовать уже вычисленное значение.' : 'Использовать значение из исходного выражения.';
  else if (step.kind === 'operation' && step.operationKind === 'declaration') text = 'Подготовить локальную переменную из указанного значения или результата.';
  else if (step.kind === 'operation') text = 'Выполнить действие из исходника. Описание этого правила отсутствует.';
  else { text = 'Продолжение или смысл операции не установлены; проверьте исходник.'; source = 'unknown'; }
  const comment = stepComment(step, description);
  return { text, source, exactExpression, sourceTruncated: Boolean(event?.sourceExpressionTruncated),
    comment, commentSource: comment ? 'comment' : null };
}
