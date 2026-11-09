/** Только геометрия и panelIndex влияют на выбор свободного коридора. */
function routePoint(point) {
  return [point.x, point.y, point.w, point.h, point.panelIndex];
}

/**
 * Кэш одной сцены принадлежит view. При смене snapshot, провайдера или геометрии
 * маршруты пересчитываются. Последовательность вызовов важна: внешние порты заглушек
 * дополняют общую сетку. При изменении последовательности воспроизводим прежний префикс.
 */
export function cachedPathRouter(view, positions, createRouter) {
  const geometry = [];
  for (const [id, point] of positions) geometry.push([id, ...routePoint(point)]);
  const key = JSON.stringify([view.pathSnapshotId, view.pathRootId, view.groupingProvider, geometry]);
  if (view.pathRouteCache?.key !== key) view.pathRouteCache = { key, records: [] };
  const records = view.pathRouteCache.records;
  let cursor = 0, route;
  return (source, target, index) => {
    const args = JSON.stringify([routePoint(source), routePoint(target), index]);
    const saved = records[cursor];
    if (!route && saved?.args === args) {
      cursor += 1;
      return { ...saved.result };
    }
    if (!route) {
      route = createRouter(positions);
      for (let previous = 0; previous < cursor; previous++) {
        const record = records[previous];
        route(record.source, record.target, record.index);
      }
      records.length = cursor;
    }
    const result = route(source, target, index);
    records[cursor++] = { args, source: { ...source }, target: { ...target }, index, result: { ...result } };
    return result;
  };
}
