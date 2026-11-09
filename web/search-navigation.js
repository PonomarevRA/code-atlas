/** Загрузка raw-графа по результату поиска с проверкой репозитория и снимка после await. */
export function createSearchNavigation({ getScope, getNode, ensureGraph, navigate, onError = () => {} }) {
  let revision = 0;
  /** Позволяет открыть результат из «Систем», не возвращая поздний ответ в другой репозиторий. */
  async function select(id, { signal } = {}) {
    const requested = ++revision, scope = { ...getScope() };
    if (signal?.aborted || !scope.projectId || !scope.snapshotId) return false;
    try {
      await ensureGraph(scope);
      const current = getScope();
      if (signal?.aborted || requested !== revision || current.projectId !== scope.projectId || current.snapshotId !== scope.snapshotId) return false;
      const node = getNode(id);
      if (!node) { onError('Символ отсутствует в текущем снимке. Обновите результаты поиска.'); return false; }
      navigate(node);
      return true;
    } catch (error) {
      const current = getScope();
      if (!signal?.aborted && requested === revision && current.projectId === scope.projectId && current.snapshotId === scope.snapshotId) onError(error.message);
      return false;
    }
  }
  /** Другой переход или Browser Back отменяет намерение, даже если snapshot остался тем же. */
  function cancel() { revision++; }
  return { select, cancel };
}
