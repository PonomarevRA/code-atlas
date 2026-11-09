import { el } from './dom.js';

const dimensions = {design:'дизайн и связность', naming:'смысл имён', contracts:'контракты',
  correctness:'корректность', tests:'качество тестов', 'comment-accuracy':'точность комментариев'};
const rules = {decisions:'число решений', nesting:'вложенность', size:'размер метода', booleanOperands:'плотность условия', mixedBooleans:'смешанные условия'};

/** Render versioned syntax evidence safely; a high R never becomes a human review approval. */
export function renderReadability(host, health) {
  const profile = health?.readability;
  if (!profile) {
    host.append(el('p', 'Локальная читаемость R не измерена в этом снимке. Измерьте текущий код или обновите карту.', 'small muted'));
    return;
  }
  if (profile.status !== 'complete') host.append(el('p', `Проверка локальной читаемости не завершена: ${profile.reason || profile.status || 'состояние неизвестно'}. Пустой список рекомендаций не означает PASS.`, 'small muted'));
  const section = el('section', undefined, 'readability-profile');
  section.append(el('h4', `Локальная читаемость R: ${profile.score ?? '?'} / 100`));
  section.append(el('p', `${profile.version} · ${profile.status}. Проектная эвристика, без эмпирической калибровки. Смысловое ревью не выполнено.`, 'small muted'));
  for (const item of profile.penalties || []) {
    if (item.penalty > 0) section.append(el('p', `${rules[item.rule] || item.rule}: ${item.observed}, ориентир ${item.free}, штраф ${item.appliedPenalty ?? item.penalty} (сигнал ${item.penalty}).`, 'small'));
  }
  section.append(el('p', 'Связанные сигналы вложенности и ветвлений, а также плотности и смешивания условий учитываются по максимуму в каждой группе.', 'small muted'));
  const tokens = profile.halstead;
  if (tokens) section.append(el('p', `Halstead (${tokens.version}, ${tokens.status}): объём V ${tokens.volume ?? '?'}, трудность D ${tokens.difficulty ?? '?'}, усилие E ${tokens.effort ?? '?'}. E не означает секунды или число ошибок.`, 'small'));
  const pending = (profile.unverifiedDimensions || Object.keys(dimensions)).map(key => dimensions[key] || key);
  section.append(el('p', `Проверить вручную: ${pending.join(', ')}. Высокий R и отсутствие предупреждений не подтверждают понятность или правильность.`, 'small muted'));
  host.append(section);
}
