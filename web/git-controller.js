import { $, el } from './dom.js';
/** Контроллер сравнения ревизий; проверка снимка и состояния централизована в одном запросе. */
export function createGitController({ state, api, applyFilters, controls }) {
  /** Сбрасывает сравнение при смене индекса; отдельный revision отменяет запоздавшие ответы. */
  function clearComparison(redraw = true) {
    state.compareRevision++;
    state.comparing = false;
    state.comparison = null;
    $('only-changed').checked = false;
    $('comparison-output').replaceChildren(el('p', 'Git checkout не изменяется. На карте подсвечиваются узлы изменённых файлов, не отдельные строки. Сравнение показывает, что сдвинулось между двумя ревизиями.', 'small muted'));
    if (redraw) { applyFilters(); controls(); }
  }
  /** Выводит полный список файлов, включая удалённые и отсутствующие в индексе. */
  function renderComparison(comparison) {
    const output = $('comparison-output');
    output.replaceChildren(el('p', `${comparison.totalChangedFiles} файлов · ${comparison.matchedNodeCount} узлов изменённых файлов · ${comparison.unmatchedFileCount} файлов без узлов в индексе`, 'small'));
    output.append(el('p', `${comparison.baseRef} → ${comparison.targetRef} · ${comparison.mode}`, 'small mono'));
    const commits = `База: ${comparison.baseCommit}\nЦель: ${comparison.targetCommit}\nБаза сравнения: ${comparison.comparisonBase}`;
    output.append(el('pre', commits, 'compare-commits'));
    output.append(el('p', comparison.warning || 'Сохранённый граф может отличаться от сравниваемых ревизий. Точность сопоставления — файл, не символ.', 'change-note'));
    const list = el('div', undefined, 'changed-files');
    for (const file of comparison.files) {
      const row = el('div', undefined, 'changed-file');
      row.append(el('strong', file.status, 'change-status'), el('span', `${file.oldPath ? `${file.oldPath} → ` : ''}${file.path}`), el('small', file.nodeIds?.length ? `${file.nodeIds.length} узлов в индексе` : 'Нет узлов в сохранённом индексе', 'muted'));
      list.append(row);
    }
    output.append(list);
  }
  /** Запрашивает явное сравнение refs; смена репозитория или очистка отменяет применение ответа. */
  async function compareRefs(event) {
    event.preventDefault();
    if (!state.project || state.comparing || state.loading || state.job) return;
    const revision = ++state.compareRevision;
    const projectId = state.project.id;
    state.comparing = true;
    controls();
    $('comparison-output').replaceChildren(el('p', 'Сравниваем Git refs…', 'small muted'));
    try {
      const result = await api(`/projects/${encodeURIComponent(projectId)}/compare`, { baseRef: $('base-ref').value.trim(), targetRef: $('target-ref').value.trim(), mode: $('compare-mode').value });
      if (revision !== state.compareRevision || state.project?.id !== projectId) return;
      if (result.snapshotId !== state.graph.metadata?.snapshotId) {
        clearComparison();
        $('comparison-output').replaceChildren(el('p', 'Индекс изменился или карта ещё загружается. Перезагрузите карту и повторите сравнение.', 'error-text'));
        return;
      }
      state.comparison = result;
      renderComparison(result);
      applyFilters();
    } catch (error) {
      if (revision !== state.compareRevision) return;
      $('comparison-output').replaceChildren(el('p', error.message, 'error-text'));
      if (state.comparison) $('comparison-output').append(el('p', `Подсветка предыдущего сравнения сохранена: ${state.comparison.baseRef} → ${state.comparison.targetRef}.`, 'small'));
    } finally {
      if (revision === state.compareRevision) { state.comparing = false; controls(); }
    }
  }
  $('compare-form').addEventListener('submit', compareRefs);
  $('compare-clear').addEventListener('click', () => clearComparison());
  $('only-changed').addEventListener('change', applyFilters);

  return { clear: clearComparison };
}
