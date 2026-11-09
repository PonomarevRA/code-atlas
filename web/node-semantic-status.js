import {requestJson} from './bounded-request.js';
import {beginLoading} from './loading-state.js';
import { el } from './dom.js';

/** Приложение само уточняет узел; агент не нужен ни для запуска, ни для просмотра состояния. */
export function renderNodeSemanticStatus(context, initial, { fetcher, signal, isCurrent }) {
  const root = el('details');
  root.append(el('summary', 'Локальные движки анализа'));
  const status = el('p');
  const action = el('button', 'Уточнить выбранный узел');
  action.type = 'button';
  root.append(status, action, el('p', 'Доступность проверяется при работе с картой, обычно раз в сутки. Связи остаются статическими; неполный анализ не означает PASS.', 'small muted'));
  const labels = { available: 'доступен', missing: 'не найден', incompatible: 'неподходящий контекст', failed: 'ошибка', checking: 'проверяется', unchecked: 'не проверен', disabled: 'выключен' };

  /** Состояние доступности отделено от наличия результата для выбранного символа. */
  function show(data) {
    const providers = (data?.providers || []).slice(0, 64);
    status.textContent = providers.length ? providers.map(item => `${item.language}: ${labels[item.availability?.status] || 'не проверен'} · ${item.enrichment || 'baseline'}`).join('; ') : 'Используется текущий анализ Atlas.';
  }
  show(initial);
  action.addEventListener('click', async () => {
    action.disabled = true;
    const release = beginLoading({ru:'Уточнение выбранного узла…', en:'Analyzing selected node…'});
    const path = `/api/projects/${encodeURIComponent(context.projectId)}/semantic-enrichment`;
    const current = () => !signal?.aborted && isCurrent();
    try {
      let data = await requestJson(path, {
        method: 'POST', headers: {'Content-Type':'application/json'}, signal,
        body: JSON.stringify({snapshotId:context.snapshotId, nodeIds:[context.nodeId]}),
      }, {fetcher, activity:false});
      if (!current()) return;
      show(data);
      // Observe only this explicit action; closing/changing the node cancels the
      // wait. A finite observation window does not claim to cancel server work.
      const deadline = performance.now() + 60000;
      while (['queued', 'running'].includes(data.enrichment) && performance.now() < deadline) {
        await waitForSemanticStatus(signal);
        if (!current()) return;
        data = await requestJson(path + '?' + new URLSearchParams({snapshotId:context.snapshotId}),
          {signal}, {fetcher, timeoutMs:15000, activity:false});
        if (!current()) return;
        show(data);
      }
      if (['queued', 'running'].includes(data.enrichment)) {
        status.append(el('span', ' · Анализ ещё продолжается; откройте описание повторно для проверки.'));
      } else status.append(el('span', ' · Для чтения новых фактов откройте описание повторно.'));
    } catch (error) {
      if (current()) status.textContent = error.message;
    } finally {
      release();
      if (current()) action.disabled = false;
    }
  });
  return root;
}

/**
 * Ждёт следующий явный status-check, освобождая таймер и abort-подписку.
 *
 * signal принадлежит открытому описанию узла. Отмена отвергает ожидание с
 * AbortError; после закрытия панели новые GET больше не планируются.
 */
function waitForSemanticStatus(signal) {
  return new Promise((resolve, reject) => {
    let timer;
    const cleanup = () => {clearTimeout(timer); signal?.removeEventListener('abort', abort);};
    const abort = () => {cleanup(); reject(new DOMException('Переход отменён', 'AbortError'));};
    timer = setTimeout(() => {cleanup(); resolve();}, 750);
    signal?.addEventListener('abort', abort, {once:true});
    if (signal?.aborted) abort();
  });
}
