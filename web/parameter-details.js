import { el } from './dom.js';

/** Формальные параметры, условия и аргументы показываются как текст исходника, без вычисления значений. */
export function renderParameterDetails(data, onSource = () => {}) {
  const section = el('section', undefined, 'parameter-details');
  section.append(el('h3', 'Параметры → условия → вызовы'));
  const facts = data.parameterFacts;
  const parameters = facts?.parameters || data.parameters || [];
  if (!parameters.length) section.append(el('p', 'Формальные параметры не найдены в доступном объявлении.', 'small muted'));
  for (const parameter of parameters) {
    const row = el('div', undefined, 'parameter-row');
    row.append(el('strong', parameter.name), el('code', `${parameter.modifier ? `${parameter.modifier} ` : ''}${parameter.type || 'тип не извлечён'}${parameter.defaultValue ? ` = ${parameter.defaultValue}` : ''}`));
    const comment = data.documentation?.params?.find(item => item.name === parameter.name)?.text;
    if (comment) row.append(el('p', comment));
    const conditions = (facts?.conditions || []).filter(item => item.parameterReferences?.includes(parameter.name));
    for (const condition of conditions) {
      const link = sourceButton(`${condition.kind} · строка ${condition.line}`, data.nodeId, condition.line, onSource);
      row.append(link, el('pre', condition.expression, 'parameter-expression'));
    }
    if (facts && !conditions.length) row.append(el('p', facts.truncated ? 'В доступной части условий прямые упоминания не найдены.' : 'Прямые упоминания в условиях не найдены. Параметр может влиять через другие переменные или методы.', 'small muted'));
    section.append(row);
  }
  appendIncomingCalls(section, data, onSource);
  const calls = (data.events || []).filter(event => event.arguments?.length || event.argumentKind);
  if (calls.length) section.append(el('h4', 'Что передаётся в вызываемые методы'));
  for (const event of calls.slice(0, 40)) {
    const row = el('details', undefined, 'parameter-call');
    row.append(el('summary', `${event.callee || event.text || event.kind} · строка ${event.line}${event.confidence === 'candidate' ? ' · возможная цель' : ''}`));
    appendArguments(row, event.arguments);
    if (event.argumentsTruncated) row.append(el('p', 'Список аргументов ограничен.', 'small muted'));
    row.append(sourceButton('Место вызова в исходнике', data.nodeId, event.line, onSource));
    section.append(row);
  }
  if (calls.length > 40) section.append(el('p', `Показано 40 из ${calls.length} мест вызова.`, 'small muted'));
  if (facts?.truncated) section.append(el('p', 'Извлечение параметров и условий ограничено; показана часть исходника.', 'small muted'));
  section.append(el('p', facts?.warning || 'Показаны выражения текущего исходника. Значения параметров, транзитивное влияние и выбранная при запуске ветка не вычисляются.', 'small muted'));
  return section;
}

/** Входящие места вызова раскрываются отдельно, чтобы длинный список не заслонял условия параметров. */
function appendIncomingCalls(section, data, onSource) {
  const incoming = data.incomingCallSites || [];
  const limited = data.incomingCallSitesTruncated || data.truncation?.incomingCallSites;
  if (!incoming.length && !limited) return;
  const list = el('details', undefined, 'parameter-incoming');
  list.append(el('summary', `С какими аргументами вызывают этот метод · ${incoming.length} мест${limited ? ' · частично' : ''}`));
  for (const site of incoming) {
    const row = el('details', undefined, 'parameter-call');
    row.append(el('summary', `${site.callerLabel || site.callerNodeId} · строка ${site.line}${site.confidence === 'candidate' ? ' · возможная цель' : ''}`));
    appendArguments(row, site.arguments);
    if (site.argumentsTruncated) row.append(el('p', 'Аргументы этого вызова показаны частично.', 'small muted'));
    row.append(sourceButton('Исходник вызывающего метода', site.callerNodeId, site.line, onSource));
    list.append(row);
  }
  if (limited) list.append(el('p', 'Места входящих вызовов показаны частично. Сопоставление цели опирается на связи снимка.', 'small muted'));
  section.append(list);
}

/** Позиция аргумента не выдаётся за доказанное сопоставление с параметром перегруженного метода. */
function appendArguments(row, argumentsList = []) {
  for (const argument of argumentsList) {
    row.append(el('p', `${argument.name ? `${argument.name}:` : `Аргумент ${argument.position}`} ${argument.modifier || ''}`), el('pre', argument.expression, 'parameter-expression'));
    if (argument.parameterReferences?.length) row.append(el('p', `Использует параметры вызывающего метода: ${argument.parameterReferences.join(', ')}`, 'small muted'));
  }
}

/** Явный переход к строке сохраняет связь факта с исходным символом. */
function sourceButton(label, nodeId, line, onSource) {
  const button = el('button', label, 'parameter-source');
  button.addEventListener('click', () => onSource(nodeId, line));
  return button;
}
