/** Ограниченные настройки каталога по репозиторию; исходники, наборы данных и снимки сюда не записываются. */
const kinds = new Set(['', 'test', 'http', 'mcp', 'kafka', 'rabbit']);
const scopes = new Set(['all', 'favorites', 'recent']);
const prefix = 'code-atlas-entry-catalog-v1:';

/** Проверяет ID и ограничивает списки до записи и после чтения непроверенного localStorage. */
function ids(values, limit) {
  const result = [];
  for (const value of Array.isArray(values) ? values : []) {
    if (typeof value !== 'string' || !value || value.length > 1000 || result.includes(value)) continue;
    result.push(value);
    if (result.length === limit) break;
  }
  return result;
}

/** Возвращает только известные поля, сохраняя явное отсутствие выбора типа. */
export function normalizeEntryPreferences(value = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) value = {};
  return {kind: kinds.has(value.kind) ? value.kind : null, query: typeof value.query === 'string' ? value.query.slice(0, 500) : '',
    page: Number.isSafeInteger(value.page) && value.page >= 0 ? Math.min(value.page, 100000) : 0,
    scope: scopes.has(value.scope) ? value.scope : 'all', selectedId: ids([value.selectedId], 1)[0] || null,
    favorites: ids(value.favorites, 30), recent: ids(value.recent, 8)};
}

/** Недоступное хранилище не мешает изучению карты; записи разных репозиториев изолированы. */
export function createEntryMemory(storage = undefined) {
  const fallback = new Map();
  const getStorage = () => storage === undefined ? globalThis.localStorage : storage;
  return {
    read(projectId) {
      if (!projectId) return normalizeEntryPreferences();
      try {
        const saved = getStorage()?.getItem(prefix + projectId);
        return normalizeEntryPreferences(saved ? JSON.parse(saved) : fallback.get(projectId));
      }
      catch { return normalizeEntryPreferences(fallback.get(projectId)); }
    },
    write(projectId, preferences) {
      if (!projectId) return;
      const value = normalizeEntryPreferences(preferences);
      fallback.set(projectId, value);
      try { getStorage()?.setItem(prefix + projectId, JSON.stringify(value)); } catch { /* Приватный режим: настройки живут до закрытия окна. */ }
    },
  };
}
