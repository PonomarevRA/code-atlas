import { el } from './dom.js';
import { codeTokens, CODE_COLORS } from './code-text.js';
import { readerCallEvent } from './method-reader-model.js';
import { readerStepExplanation } from './reading-explanations.js';

/** Source tokens remain inert text; HTML and templates from a repository never execute. */
export function readerCode(value) {
  const code = el('code', undefined, 'reader-code');
  for (const token of codeTokens(String(value || ''))) {
    const span = el('span', token.text); span.style.color = CODE_COLORS[token.kind]; code.append(span);
  }
  return code;
}

/** Keep source-order AST regions explicit instead of projecting every call into a flat dependency list. */
export function renderReaderSteps(container, steps, description, { onCall, onSource, renderCallTargets, budget = { remaining: 500 }, depth = 0, preview = false }) {
  const names = { operation: 'Действие', call: 'Вызов', branch: 'Условие', loop: 'Цикл', await: 'Дождаться результата',
    return: 'Вернуть результат', throw: 'Исключение', continue: 'Следующая итерация', break: 'Выход из цикла / switch',
    value: 'Значение', unknown: 'Продолжение не установлено' };
  for (const step of steps || []) {
    if (budget.remaining-- <= 0 || depth > 30) { container.append(el('p', 'Показаны не все шаги. Откройте исходник.', 'reader-notice')); return; }
    const row = el('article', undefined, `reader-step reader-step-${step.kind}`);
    row.setAttribute('data-kind', step.kind);
    const label = el('span', `${names[step.kind] || step.kind}${step.controlKind ? ' · ' + step.controlKind : ''}`, 'reader-step-kind');
    const location = el('button', `Строка ${step.line || '?'}`, 'reader-source-link'); location.type = 'button';
    location.addEventListener('click', () => onSource(description.nodeId, step.line));
    row.append(label, location);
    const explanation = readerStepExplanation(step, description);
    const narrative = el('p', explanation.text, 'reader-explanation');
    narrative.setAttribute('data-explanation-source', explanation.source); row.append(narrative);
    if (explanation.comment) row.append(el('p', `Комментарий исходника: ${explanation.comment}`, 'reader-rule-comment'));
    if (explanation.exactExpression) {
      const source = el('details', undefined, 'reader-exact-source');
      source.append(el('summary', 'Как проверяется в коде'), readerCode(explanation.exactExpression),
        el('small', explanation.sourceTruncated ? 'Фрагмент усечён. Полный исходник доступен по ссылке на строку.'
          : 'Сохранённый фрагмент; полный исходник доступен по ссылке на строку.', 'muted')); row.append(source);
    }
    if (step.kind === 'call') {
      const event = readerCallEvent(description, step);
      if (event?.targetNodeIds?.length || event?.implementationTargetIds?.length) {
        if (renderCallTargets) renderCallTargets(row, event, step, description);
        else {
          const read = el('button', 'Читать вызов →', 'reader-call-button'); read.type = 'button';
          read.setAttribute('data-reader-action', 'call');
          read.addEventListener('click', () => onCall(event, step, description)); row.append(read);
        }
      } else row.append(el('small', 'Цель вызова не сопоставлена; имя сохранено из исходника.', 'muted'));
    }
    if (step.conditionResult) row.append(el('small', 'Используется уже вычисленный результат условия.', 'muted'));
    if (!preview && step.kind === 'branch') {
      if (step.incomplete) row.append(el('p', 'Переходы разобраны не полностью.', 'reader-notice'));
      for (const arm of step.arms || []) {
        const region = el('section', undefined, 'reader-arm'); region.setAttribute('data-arm-label', arm.label || '');
        let armLabel = arm.label;
        if (['if', 'conditional', '?.'].includes(step.controlKind)) {
          if (arm.label === 'истина') armLabel = 'Тогда';
          else if (arm.label === 'иначе') armLabel = 'Иначе';
        }
        region.append(el('h4', armLabel || 'Вариант'));
        if (arm.exception) region.append(el('small', 'Возможная обработка исключения; не следующий обычный шаг.', 'muted'));
        if (!arm.steps?.length) region.append(el('p', arm.emptyReason === 'not-extracted'
          ? 'Тело ветви не извлечено.' : 'Ветвь пропускает действия и передаёт управление дальше.', 'small muted'));
        renderReaderSteps(region, arm.steps, description, { onCall, onSource, renderCallTargets, budget, depth: depth + 1 }); row.append(region);
      }
    }
    if (!preview && step.kind === 'loop') {
      const groups = step.controlKind === 'do'
        ? [['Тело итерации', step.body], ['Проверка после тела', step.condition], ['Следующая итерация', step.update]]
        : [['Проверка перед итерацией', step.condition], ['Тело итерации', step.body], ['Обновление перед следующей проверкой', step.update]];
      for (const [heading, inner] of groups) {
        if (!inner?.length && heading !== 'Тело итерации') continue;
        const region = el('section', undefined, 'reader-arm'); region.setAttribute('data-arm-label', heading);
        region.append(el('h4', heading));
        if (!inner?.length) region.append(el('p', step.bodyEmptyReason === 'not-extracted' ? 'Тело цикла не извлечено.' : 'Сопоставленных действий в теле нет.', 'small muted'));
        renderReaderSteps(region, inner, description, { onCall, onSource, renderCallTargets, budget, depth: depth + 1 }); row.append(region);
      }
      row.append(el('small', 'Число повторений не вычисляется; continue возвращает к следующей итерации, break выходит из ближайшего цикла или switch.', 'muted'));
    }
    if (preview && ['branch', 'loop'].includes(step.kind)) row.append(el('small', 'Вложенные ветви и действия — в полном чтении метода.', 'muted'));
    container.append(row);
  }
}
