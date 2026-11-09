/** Общий учёт работы Atlas без DOM, таймеров и перехвата fetch. */
const active = new Map();
const subscribers = new Set();
const MAX_DETAILS = 128;
let overflow = 0;

/**
 * Ограничивает подпись операции; переданные хостом переводы остаются обычным текстом.
 */
function cleanLabel(label) {
  if (label && typeof label === 'object') return {ru: cleanLabel(String(label.ru || '')), en: cleanLabel(String(label.en || label.ru || ''))};
  return String(label || 'Загрузка данных…').replace(/[\x00-\x1f]/g, ' ').slice(0, 160);
}

/**
 * Возвращает отдельный снимок счётчика и последней активной подписи без внутренних токенов.
 */
export function loadingSnapshot() {
  const details = [...active.values()];
  const label = details.at(-1) || {ru:'Загрузка данных…', en:'Loading data…'};
  return {count: active.size + overflow, label: typeof label === 'string' ? label : {...label}};
}

/**
 * Оповещает только подписчиков текущего документа; ошибка одного представления не ломает запрос.
 */
function publish() {
  const snapshot = loadingSnapshot();
  for (const listener of subscribers) {
    try { listener(snapshot); } catch { /* Индикация не должна менять результат операции. */ }
  }
}

/**
 * Регистрирует работу и возвращает идемпотентное освобождение с update(label).
 *
 * label — подпись хоста либо словарь ru/en. Освобождение вызывается в finally;
 * update меняет только подпись активной операции. Детали ограничены128 записями,
 * дополнительные операции учитываются счётчиком до своего завершения.
 */
export function beginLoading(label) {
  const token = active.size < MAX_DETAILS ? Symbol('loading') : null;
  let released = false;
  if (token) active.set(token, cleanLabel(label));
  else overflow++;
  publish();
  const release = () => {
    if (released) return;
    released = true;
    if (token) active.delete(token);
    else overflow--;
    publish();
  };
  release.update = value => {
    if (released || !token) return;
    active.set(token, cleanLabel(value));
    publish();
  };
  return release;
}

/**
 * Подписывает представление и сразу передаёт ему текущее состояние.
 *
 * listener получает {count,label}; возвращаемая функция удаляет подписку.
 * Представление само владеет задержкой показа и своим DOM.
 */
export function subscribeLoading(listener) {
  if (typeof listener !== 'function') throw new TypeError('Ожидается подписчик состояния');
  subscribers.add(listener);
  try { listener(loadingSnapshot()); }
  catch (error) { subscribers.delete(listener); throw error; }
  return () => subscribers.delete(listener);
}

/**
 * Выполняет operation до результата/ошибки, освобождая только свой токен в finally.
 */
export async function withLoading(label, operation) {
  const release = label ? beginLoading(label) : () => {};
  try { return await operation(); }
  finally { release(); }
}
