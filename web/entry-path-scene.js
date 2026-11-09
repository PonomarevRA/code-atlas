import { buildPathScope } from './path-scope.js';
import { buildPathScene } from './path-scene.js';
import { localPathExpansion } from './path-local-expansion.js';

/**
 * Горизонтальный путь резервирует только видимые тела, без пустот для скрытых потомков.
 * Фильтры и выбор используют ту же геометрию; раскрытие освобождает место рядом с карточкой.
 * Старый входной Flow сохраняет полное резервирование. Номера методов стабильны в пути.
 */
export function buildEntryPathScene(view, scope, selected) {
  const { nodes, edges } = view.pathData;
  let reserved = view.entryPathLayout;
  const compact = Boolean(view.pathReading) || view.groupingProvider === 'natural';
  const detailKey = compact ? JSON.stringify([scope.nodes.map(node => node.id), [...scope.expandedIds].sort()]) : '';
  const samePath = reserved && reserved.nodes === nodes && reserved.edges === edges
    && reserved.rootId === scope.focusId && reserved.snapshotId === view.pathSnapshotId;
  if (!reserved || reserved.nodes !== nodes || reserved.edges !== edges
      || reserved.reading !== Boolean(view.pathReading)
      || reserved.natural !== (view.groupingProvider === 'natural')
      || reserved.rootId !== scope.focusId || reserved.snapshotId !== view.pathSnapshotId || reserved.detailKey !== detailKey) {
    const layoutScope = compact ? scope : buildPathScope(nodes, edges, {
      rootId: scope.focusId, expandedIds: view.reachablePathNodeIds(),
    });
    const geometryScene = buildPathScene(layoutScope, { flowPath: true, flatFlow: true, reading: Boolean(view.pathReading), natural: view.groupingProvider === 'natural' });
    const sameProvider = samePath && reserved.reading === Boolean(view.pathReading)
      && reserved.natural === (view.groupingProvider === 'natural');
    const history = sameProvider && reserved.history ? reserved.history : new Map();
    let positions = history?.get(detailKey) || geometryScene.positions;
    if (compact && sameProvider && view.pathExpansionAnchor) {
      // После загрузки сохранённого вида его общая геометрия ещё не была локальным раскрытием.
      // Любое новое нажатие строится от текущих соседей, а журнал используется для сворачивания.
      positions = localPathExpansion(geometryScene, reserved.positions, view.pathExpansionAnchor.ownerId);
    }
    // Ограниченный журнал возвращает точные позиции при сворачивании, без накопления всех состояний пути.
    history.set(detailKey, positions);
    if (history.size > 16) history.delete(history.keys().next().value);
    const methodNumbers = samePath ? new Map(reserved.methodNumbers) : new Map();
    for (const id of geometryScene.methodNumbers.keys()) if (!methodNumbers.has(id)) methodNumbers.set(id, methodNumbers.size + 1);
    reserved = { nodes, edges, rootId: scope.focusId, snapshotId: view.pathSnapshotId,
      detailKey, reading: Boolean(view.pathReading), natural: view.groupingProvider === 'natural', positions, methodNumbers, history };
    view.entryPathLayout = reserved;
  }
  const scene = buildPathScene(scope, {
    flowPath: true, flatFlow: true, flowPositions: reserved.positions, controlFilters: view.pathControlFilters, selected,
  });
  scene.methodNumbers = reserved.methodNumbers;
  return scene;
}
