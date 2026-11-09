import {beginLoading} from './loading-state.js';
import {requestLoadingLabel} from './loading-labels.js';

/**
 * Ограничивает ожидание ответа и JSON; отмена прекращает также локальное ожидание.
 *
 * url/options сохраняют fetch-контракт; timeoutMs ограничивает весь ответ вместе
 * с JSON. fetcher допускает тестовый транспорт. activity=false выключает только
 * индикацию, строка/словарь задаёт подпись; иначе используется категория URL.
 * Возвращает JSON или исходную ошибку/ошибку формата; finally освобождает токен.
 */
export async function requestJson(url, options = {}, { timeoutMs = 30000, fetcher = (...args) => fetch(...args), activity } = {}) {
  const label = activity === false ? null : activity || requestLoadingLabel(url, options);
  const release = label ? beginLoading(label) : () => {};
  const controller = new AbortController();
  let timer;
  let cancel;
  const deadline = new Promise((_, reject) => {
    cancel = () => {
      controller.abort();
      reject(new DOMException('Переход отменён', 'AbortError'));
    };
    timer = setTimeout(() => {
      controller.abort();
      const error = new Error('Сервер долго отвечает. Повторите запрос; карта остаётся доступной.');
      error.name = 'TimeoutError';
      reject(error);
    }, timeoutMs);
    options.signal?.addEventListener('abort', cancel, { once: true });
    if (options.signal?.aborted) cancel();
  });
  try {
    const operation = async () => {
      const response = await fetcher(url, { ...options, signal: controller.signal });
      let data;
      try {
        data = await response.json();
      } catch (cause) {
        // Cancellation belongs to the navigation/deadline; keep its original semantics.
        if (controller.signal.aborted || cause?.name === 'AbortError') throw cause;
        const error = new Error(response.ok
          ? 'Сервер вернул непонятный ответ. Повторите запрос; карта остаётся доступной.'
          : `Ошибка сервера: ${response.status}. Повторите запрос.`);
        error.name = response.ok ? 'ResponseFormatError' : 'Error';
        error.status = response.status;
        throw error;
      }
      if (!response.ok) {
        let message = `Ошибка сервера: ${response.status}`;
        if (typeof data?.error === 'string') message = data.error;
        else if (typeof data?.detail === 'string') message = data.detail;
        const error = new Error(message);
        error.status = response.status;
        if (typeof data?.code === 'string') error.code = data.code;
        if (['unrecognized-snapshot-file','unrecognized-snapshot-directory'].includes(data?.reason)) error.reason = data.reason;
        if (typeof data?.jobId === 'string' && /^[a-f0-9]{32}$/.test(data.jobId)) error.jobId = data.jobId;
        throw error;
      }
      return data;
    };
    return await Promise.race([operation(), deadline]);
  } finally {
    release();
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', cancel);
  }
}
