import { createIngressCatalog } from './ingress-catalog.js';
import { $, el } from './dom.js';
import { ingressOverlay } from './ingress.js';
import { requestJson } from './bounded-request.js';
import { appendTestEntryDetails } from './test-entry-details.js';

/** Captured declarations win duplicate IDs; live transport entries remain available alongside them. */
export function mergeIngressCatalogs(captured, full) {
  const entries = new Map();
  for (const entry of full.entries || []) entries.set(entry.id, entry);
  for (const entry of captured.entries || []) entries.set(entry.id, entry);
  return { ...full, entries: [...entries.values()], testDiscovery: captured.testDiscovery || full.testDiscovery,
    entrypointDiscovery: captured.entrypointDiscovery || full.entrypointDiscovery, capturedCoverage: captured.capturedCoverage };
}

/** Контроллер производных входов: запрос, пагинация и детали; state остаётся у приложения. */
export function createIngressController({ state, view, draw, controls, filteredNodes, selectNode, analyzePath, describeNode, onReady,
  navigateNode = selectNode, onSource = () => {}, revealDetails = () => {}, request = requestJson }) {
  let active = null;
  let loadedScope = null;
  const catalog = createIngressCatalog({ state, filteredNodes, selectNode, selectEvidence: selectIngress,
    load: () => loadIngress(), retry: () => loadIngress({ force: true }), analyzePath, describeNode });
  /** Share a request for the same project generation and allow the full scan to follow saved entries. */
  function loadIngress({ capturedOnly = false, force = false } = {}) {
    const ingress = state.ingress;
    const projectId = state.project?.id;
    const snapshotId = state.graph.metadata?.snapshotId;
    if (!projectId || !snapshotId) return Promise.resolve();
    const scope = JSON.stringify([projectId, snapshotId]);
    if (loadedScope !== scope) {
      ingress.controller?.abort();
      ingress.data = null;
      ingress.status = 'idle';
      ingress.infrastructureStatus = null;
      loadedScope = scope;
    }
    if (active?.scope === scope && active.revision === ingress.revision) {
      const current = active;
      if (capturedOnly || !active.capturedOnly) return active.promise;
      return active.promise.then(() => {
        if (loadedScope === scope && current.revision === ingress.revision) return loadIngress({ force });
      });
    }
    if (!force && (ingress.infrastructureStatus === 'ready' || (capturedOnly && ingress.status !== 'idle'))) return Promise.resolve();
    const request = { scope, capturedOnly, revision: ingress.revision + 1, promise: null };
    active = request;
    request.promise = fetchIngress(capturedOnly).finally(() => {
      if (active === request) active = null;
    });
    return request.promise;
  }
  /** Читает отдельный индекс входов; отказ не блокирует основную карту, устаревший ответ отбрасывается. */
  async function fetchIngress(capturedOnly) {
    const projectId = state.project?.id;
    const snapshotId = state.graph.metadata?.snapshotId;
    if (!projectId || !snapshotId) return;
    state.ingress.controller?.abort();
    const controller = new AbortController();
    state.ingress.controller = controller;
    const revision = ++state.ingress.revision;
    let captured = state.ingress.data;
    state.ingress.status = captured ? 'ready' : 'loading';
    state.ingress.infrastructureStatus = 'pending';
    state.ingress.message = 'Ищем тест-кейсы, входящие API и сообщения…';
    catalog.render();
    const endpoint = `/api/projects/${encodeURIComponent(projectId)}/entrypoints?snapshotId=${encodeURIComponent(snapshotId)}`;
    const isCurrent = () => revision === state.ingress.revision && state.project?.id === projectId && state.graph.metadata?.snapshotId === snapshotId;
    /** Publishing one successful stage keeps test discovery independent of infrastructure scans. */
    function publish(data, infrastructureStatus) {
      state.ingress.data = data;
      state.ingress.status = 'ready';
      state.ingress.infrastructureStatus = infrastructureStatus;
      state.ingress.message = data.warning || 'Статические свидетельства; производитель сообщения может быть неизвестен.';
      if (infrastructureStatus === 'pending') state.ingress.message += ' Прочие протоколы ещё загружаются; их количество неизвестно.';
      if (data.coverage?.scope !== 'captured-only' && data.coverage) state.ingress.message += ` Проверено файлов: ${data.coverage.readFiles} из ${data.coverage.candidateFiles}${data.coverage.limited ? ' · достигнут лимит поиска' : ''}.`;
      draw(false);
      catalog.render();
      onReady?.();
    }
    try {
      const hasCaptured = state.graph.metadata?.entrypointDiscovery || state.graph.metadata?.testDiscovery?.coverage?.testEntries > 0;
      if (hasCaptured && !captured) {
        try {
          const data = await request(`${endpoint}&capturedOnly=true`, { signal: controller.signal });
          if (!isCurrent()) return;
          if (data.snapshotId !== snapshotId) throw new Error('Индекс изменился. Перезагрузите карту для точек входа.');
          captured = data;
          publish(data, 'pending');
        } catch (error) {
          if (error.name === 'AbortError' || !isCurrent()) return;
          // A missing old-server capture endpoint must not remove the existing full catalogue.
          state.ingress.message = `Сохранённые входы недоступны: ${error.message}.`;
          catalog.render();
        }
      }
      if (capturedOnly) {
        if (!captured) {
          state.ingress.status = 'idle';
          state.ingress.infrastructureStatus = null;
        }
        catalog.render();
        return;
      }
      const full = await request(endpoint, { signal: controller.signal });
      if (!isCurrent()) return;
      if (full.snapshotId !== snapshotId) throw new Error('Индекс изменился. Перезагрузите карту для точек входа.');
      publish(captured ? mergeIngressCatalogs(captured, full) : full, 'ready');
    } catch (error) {
      if (error.name === 'AbortError' || !isCurrent()) return;
      if (captured) {
        state.ingress.status = 'ready';
        state.ingress.infrastructureStatus = 'error';
        state.ingress.message = `Объявления снимка доступны. Прочие протоколы не проверены: ${error.message}`;
      } else {
        state.ingress.status = 'error';
        state.ingress.message = `Точки входа недоступны: ${error.message}`;
      }
      $('ingress-status').textContent = state.ingress.message;
      catalog.render();
    }
  }

  /** Рисует отдельные производные входы и список; факты/числа исходного графа не меняются. */
  function renderIngress(architecture) {
    if (!$('show-ingress').checked) return;
    if (state.ingress.status === 'idle' || state.ingress.infrastructureStatus === 'pending') {
      loadIngress();
    }
    if (state.ingress.status !== 'ready') {
      $('ingress-count').textContent = state.ingress.status === 'loading' ? '· индекс загружается' : '· индекс недоступен';
      $('ingress-status').textContent = state.ingress.message;
      $('ingress-list').replaceChildren();
      $('ingress-page').textContent = '—';
      $('ingress-previous').disabled = true;
      $('ingress-next').disabled = true;
      return;
    }
    const overlay = ingressOverlay(state.ingress.data?.entries || [], architecture, { ...state.architecture, kind: $('ingress-kind').value, page: state.ingress.page });
    state.ingress.page = overlay.page;
    view.renderIngress(overlay.entries, selectIngress);
    const mapped = overlay.entries.filter(entry => entry.targets.length).length;
    $('ingress-count').textContent = `· ${mapped} на карте / ${overlay.total} в области`;
    $('ingress-status').textContent = `${state.ingress.message} Входы и пунктирные стрелки показаны отдельно от фактов графа. Без сопоставленного обработчика стрелка не рисуется.`;
    $('ingress-page').textContent = ` ${overlay.page + 1} / ${overlay.pageCount} · до 6 входов на странице `;
    $('ingress-previous').disabled = overlay.page === 0;
    $('ingress-next').disabled = overlay.page + 1 >= overlay.pageCount;
    $('ingress-list').replaceChildren();
    for (const entry of overlay.entries) {
      const button = el('button', `${entry.kind.toUpperCase()} · ${entry.label}${entry.targets.length ? '' : ' · обработчик не сопоставлен'}`);
      button.addEventListener('click', () => selectIngress(entry)); $('ingress-list').append(button);
    }
  }
  /** Показывает происхождение входа, не выбирая синтетический id как символ для MCP/исходника. */
  async function selectIngress(entry) {
    state.selected = null;
    const selectionRevision = ++state.answerRevision;
    $('answer').replaceChildren();
    controls();
    const panel = $('selection');
    if (entry.kind === 'test' && entry.detailsAvailable) {
      const projectId = state.project?.id, snapshotId = state.graph.metadata?.snapshotId;
      panel.replaceChildren(el('h2', entry.label), el('p', 'Читаем данные и подготовку выбранного теста…', 'small'));
      revealDetails();
      try {
        const payload = await request(`/api/projects/${encodeURIComponent(projectId)}/graph-node`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ nodeId: entry.handlerNodeIds[0], snapshotId }),
        });
        if (selectionRevision !== state.answerRevision || state.project?.id !== projectId || state.graph.metadata?.snapshotId !== snapshotId) return;
        entry = payload.node?.testFacts
          ? { ...payload.node.testFacts, detailTruncated: Boolean(payload.truncated) }
          : { ...entry, detailTruncated: true, detailWarning: 'Подробные факты не вошли в ответ. Схема и исходник остаются доступны.' };
      } catch (error) {
        if (selectionRevision !== state.answerRevision || state.project?.id !== projectId || state.graph.metadata?.snapshotId !== snapshotId) return;
        entry = { ...entry, detailTruncated: true, detailWarning: `Подробности недоступны: ${error.message}` };
      }
    }
    panel.replaceChildren(el('h2', entry.label), el('span', entry.kind === 'test' ? 'Тест-кейс · точка входа' : `${entry.kind.toUpperCase()} · точка входа`, 'pill'));
    if (entry.kind === 'test') {
      const projectId = state.project?.id, snapshotId = state.graph.metadata?.snapshotId;
      const isCurrent = () => selectionRevision === state.answerRevision && projectId === state.project?.id && snapshotId === state.graph.metadata?.snapshotId;
      const loadRows = async (sourceId, cursor) => {
        const params = new URLSearchParams({ nodeId: entry.handlerNodeIds[0], snapshotId, sourceId, limit: '20' });
        if (cursor) params.set('cursor', cursor);
        const page = await request(`/api/projects/${encodeURIComponent(projectId)}/test-datasets?${params}`);
        if (page.snapshotId !== snapshotId || page.nodeId !== entry.handlerNodeIds[0] || page.sourceId !== sourceId) throw new Error('Строки относятся к другому тесту или снимку. Обновите карту.');
        return page;
      };
      appendTestEntryDetails(panel, entry, { onNode: navigateNode, onSource, loadRows, isCurrent });
    }
    else panel.append(el('p', `Откуда: ${entry.origin || 'Источник не установлен'}`));
    for (const [field, label] of [['httpMethod', 'HTTP'], ['route', 'Маршрут'], ['topic', 'Топик'], ['queue', 'Очередь'], ['mcpType', 'Тип MCP'], ['operationName', 'Операция MCP'], ['uri', 'URI ресурса']]) if (entry[field]) panel.append(el('p', `${label}: ${entry[field]}`));
    panel.append(el('p', `Основание: ${['static', 'source-declaration'].includes(entry.confidence) ? 'статические свидетельства' : 'неполное сопоставление'}. ${entry.sourceKind === 'snapshot-captured-source' ? 'Сохранённый снимок' : 'Текущая рабочая копия'}, не трасса выполнения.`, 'small muted'));
    if (entry.configFile) panel.append(el('p', `Конфигурация: ${entry.configFile}`, 'small muted'));
    for (const evidence of entry.evidence || []) panel.append(el('code', `${evidence.file}:${evidence.line || '?'}`, 'source'));
    for (const unresolved of entry.unresolved || []) panel.append(el('p', String(unresolved), 'small muted'));
    if (['kafka', 'rabbit'].includes(entry.kind)) panel.append(el('p', 'Производитель сообщения не определяется только по обработчику.', 'small muted'));
    const available = new Set(filteredNodes(false).map(node => node.id));
    for (const id of entry.handlerNodeIds || []) {
      const node = state.nodes.get(id); if (!node) continue;
      const button = el('button', `Открыть обработчик: ${node.label || id}`); button.disabled = !available.has(id);
      button.title = available.has(id) ? 'Выбрать реальный символ; затем доступен исходник и MCP' : 'Обработчик исключён текущими фильтрами';
      button.addEventListener('click', () => selectNode(id)); panel.append(button);
      if (entry.kind === 'test') {
        const source = el('button', 'Открыть исходник теста');
        source.addEventListener('click', () => onSource(id, entry.line || node.line));
        panel.append(source);
      }
      const path = el('button', `Путь от входа: ${node.label || id}`);
      path.type = 'button';
      path.disabled = !available.has(id);
      path.addEventListener('click', () => analyzePath?.(id, entry));
      panel.append(path);
    }
    revealDetails();
  }
  $('show-ingress').addEventListener('change', () => draw(true));
  $('ingress-kind').addEventListener('change', () => { state.ingress.page = 0; draw(true); });
  $('ingress-previous').addEventListener('click', () => { state.ingress.page--; draw(true); });
  $('ingress-next').addEventListener('click', () => { state.ingress.page++; draw(true); });

  return { load: loadIngress, render: renderIngress, select: selectIngress, catalog };
}
