/** Фиксированные подписи работы; URL, ID, запросы и пути исходников не выводятся. */
const labels = {
  data: {ru:'Загрузка данных…', en:'Loading data…'},
  graph: {ru:'Загрузка карты…', en:'Loading map…'},
  node: {ru:'Загрузка сведений об узле…', en:'Loading node details…'},
  source: {ru:'Чтение исходника…', en:'Loading source…'},
  wiki: {ru:'Загрузка wiki…', en:'Loading wiki…'},
  docs: {ru:'Загрузка документации…', en:'Loading documentation…'},
  search: {ru:'Поиск…', en:'Searching…'},
  projects: {ru:'Загрузка проектов…', en:'Loading projects…'},
};

/**
 * Выбирает подпись JSON-запроса; null означает тихое наблюдение статуса.
 *
 * url принимается только для выбора известной категории. options.method
 * отличает действие POST от периодического GET. Потоки активности, health
 * и опросы заданий не считаются самостоятельной пользовательской загрузкой.
 */
export function requestLoadingLabel(url, options = {}) {
  let path;
  try { path = new URL(String(url), 'http://atlas.local').pathname; }
  catch { return labels.data; }
  const method = String(options.method || 'GET').toUpperCase();
  if (method === 'GET' && (/\/(?:jobs|documentation-jobs)\//.test(path)
      || /\/(?:code-health\/refresh|documentation-index|semantic-enrichment|health)$/.test(path)
      || /\/(?:agent-activity|usage)(?:\/|$)/.test(path))) return null;
  if (/wiki/.test(path)) return labels.wiki;
  if (/\/(?:graph|architecture|map-overview|map-group)(?:\/|$)/.test(path)) return labels.graph;
  if (/\/(?:source|source-search)(?:\/|$)/.test(path)) return labels.source;
  if (/\/(?:node-description|node-facts|node-attributes|method-flow|ast)(?:\/|$)/.test(path)) return labels.node;
  if (/search|query/.test(path)) return labels.search;
  if (/documentation|documents|libraries|references/.test(path)) return labels.docs;
  if (/\/projects$/.test(path)) return labels.projects;
  return labels.data;
}
