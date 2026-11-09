import {architectureResourceKind} from './architecture-resource-style.js';

const GAP = 36, GROUP_GAP = 84, PADDING = 18, HEADING = 40;
// The longest static16px heading measures196px in the host;240 preserves both18px insets.
const MIN_CLUSTER_WIDTH = 240;
const KINDS = ['owners', 'kafka', 'rabbit', 'database', 'http', 'other'];
const LABELS = {owners: 'Проекты и репозитории', kafka: 'Kafka', rabbit: 'RabbitMQ', database: 'Базы данных', http: 'HTTP', other: 'Пакеты и прочее'};

/**
 * Измеряет всю карточку с асимметричной нижней подписью, не меняя размеры renderer.
 * @param {object} point Геометрия карточки/круга, где labelBottom отсчитывается от центра вниз.
 * @returns {object} Ширина и расстояния above/below от сохранённого центра.
 */
function measure(point) {
  const radius = Number(point.r) || 0;
  const width = Math.max(100, Number(point.width || point.w) || radius * 2, Number(point.labelWidth) || 0);
  const halfHeight = Math.max(35, (Number(point.height || point.h) || radius * 2) / 2);
  const above = Math.max(halfHeight, radius), below = Math.max(halfHeight, radius, Number(point.labelBottom) || 0);
  return {width, above, below, height: above + below};
}

/**
 * Переводит центр и полные размеры подписи в прямоугольник занятости.
 * @param {object} point Сохранённый центр с актуальными либо legacy симметричными размерами.
 * @returns {object} Координаты сторон прямоугольника, без экранных/DOM преобразований.
 */
function bounds(point) {
  return {left: point.x - point.width / 2, right: point.x + point.width / 2,
    top: point.y - (point.above ?? point.height / 2), bottom: point.y + (point.below ?? point.height / 2)};
}

/**
 * Проверяет пересечение с заданным минимальным интервалом между прямоугольниками.
 * @param {object} a Первый прямоугольник в координатах карты.
 * @param {object} b Второй прямоугольник в координатах карты.
 * @param {number} gap Требуемый свободный интервал;0 проверяет сами области.
 * @returns {boolean} True, когда области пересекаются или расположены теснее gap.
 */
function intersects(a, b, gap = 0) {
  return a.left < b.right + gap && a.right + gap > b.left && a.top < b.bottom + gap && a.bottom + gap > b.top;
}

/**
 * Обводит узлы острова с местом под заголовок и боковыми отступами.
 * @param {Array<object>} points Центры с полными размерами, принадлежащие одному типу.
 * @returns {object} Граница кластера с >=40 единицами под заголовок и18 единицами отступа.
 */
function region(points) {
  const boxes = points.map(bounds);
  const left = Math.min(...boxes.map(box => box.left)) - PADDING;
  return {left, right: Math.max(left + MIN_CLUSTER_WIDTH, Math.max(...boxes.map(box => box.right)) + PADDING),
    top: Math.min(...boxes.map(box => box.top)) - HEADING - PADDING,
    bottom: Math.max(...boxes.map(box => box.bottom)) + PADDING};
}

/**
 * Вычисляет компактные начальные области: владельцы слева, ресурсы в двух колонках справа.
 * @param {Map<string,Array<object>>} groups Видимые узлы, сгруппированные по семантическому типу.
 * @returns {Map<string,object>} Начальная сетка каждого имеющегося типа; отсутствующие типы не резервируют пустоты.
 */
function initialGrids(groups) {
  const grids = new Map();
  for (const [kind, members] of groups) {
    const width = Math.max(...members.map(item => item.width));
    const above = Math.max(...members.map(item => item.above)), below = Math.max(...members.map(item => item.below));
    const columns = Math.min(kind === 'owners' ? 4 : 3, Math.ceil(Math.sqrt(members.length)));
    grids.set(kind, {left: 0, top: 0, width, above, below, columns,
      extentWidth: Math.max(MIN_CLUSTER_WIDTH, columns * width + (columns - 1) * GAP + PADDING * 2),
      extentHeight: Math.ceil(members.length / columns) * (above + below + GAP) - GAP + HEADING + PADDING * 2});
  }
  const owners = grids.get('owners');
  const resourceLeft = owners ? owners.extentWidth + GROUP_GAP : 0;
  const resources = KINDS.slice(1).filter(kind => grids.has(kind));
  const columnWidths = [];
  for (const [index, kind] of resources.entries()) {
    const column = Math.floor(index / 2);
    columnWidths[column] = Math.max(columnWidths[column] || 0, grids.get(kind).extentWidth);
  }
  for (const [index, kind] of resources.entries()) {
    const grid = grids.get(kind);
    grid.left = resourceLeft;
    for (let column = 0; column < Math.floor(index / 2); column++) grid.left += columnWidths[column] + GROUP_GAP;
    if (index % 2) grid.top = grids.get(resources[index - 1]).extentHeight + GROUP_GAP;
  }
  return grids;
}

/**
 * Проверяет новое место вместе с будущей рамкой, не перемещая чужие или закреплённые центры.
 * @param {object} candidate Новый центр с полными размерами и семантическим kind.
 * @param {?object} island Существующий остров того же типа либо null.
 * @param {Map} occupied Все сохранённые и уже размещённые текущие позиции, включая невидимый кеш.
 * @param {Array<object>} islands Уже образованные видимые области всех типов.
 * @returns {boolean} True, если узел и заголовок его области не перекрывают чужие факты/рамки.
 */
function fits(candidate, island, occupied, islands) {
  const box = bounds(candidate);
  for (const peer of occupied.values()) if (intersects(box, bounds(peer), GAP)) return false;
  const shell = region(island ? [...island.points, candidate] : [candidate]);
  for (const peer of occupied.values()) {
    if (peer.kind !== candidate.kind && intersects(shell, bounds(peer), PADDING)) return false;
  }
  for (const peer of islands) {
    if (peer !== island && peer.kind !== candidate.kind && intersects(shell, peer.box, GAP)) return false;
  }
  return true;
}

/**
 * Добавляет узел к острову либо начинает отдельный остров без потери семантической метки.
 * @param {Array<object>} islands Изменяемый список областей текущего кадра.
 * @param {object} item Размещённый узел с id/kind/центром/размерами.
 * @param {?object} island Допустимая существующая область либо null для отдельной рамки.
 * @returns {void} Рамка пересчитывается только из её собственных узлов.
 */
function addToIsland(islands, item, island = null) {
  if (!island) { island = {kind: item.kind, points: []}; islands.push(island); }
  island.points.push(item); island.box = region(island.points);
}

/**
 * Находит остров старого anchor, рамка которого не захватит узлы/заголовки другого типа.
 * @param {object} item Неподвижный узел с полными размерами.
 * @param {Array<object>} islands Уже образованные видимые острова.
 * @param {Map} occupied Все старые и текущие anchors, восстановленные до размещения новых узлов.
 * @returns {?object} Допустимый остров того же типа либо null для отдельной рамки.
 */
function matchingIsland(item, islands, occupied) {
  for (const island of islands) {
    if (island.kind !== item.kind) continue;
    const shell = region([...island.points, item]);
    let blocked = false;
    for (const peer of occupied.values()) {
      if (peer.kind !== item.kind && intersects(shell, bounds(peer), PADDING)) { blocked = true; break; }
    }
    if (blocked) continue;
    for (const peer of islands) {
      if (peer.kind !== item.kind && intersects(shell, peer.box, GAP)) { blocked = true; break; }
    }
    if (!blocked) return island;
  }
  return null;
}

/**
 * Размещает архитектуру по типам, сохраняя кешированные центры и до24 видимых/скрытых pins.
 * @param {Map|object} geometry Временная Map либо {positions,containers}; identity не меняется.
 * @param {Map} registry До240 сохранённых центров; дополнительно pinnedPositions хранит до24 pin-центров/размеров в том же registry lifetime.
 * @param {object} context Текущие nodes, pinnedIds и optional предыдущая same-layout positions Map.
 * @returns {Map|object} Тот же geometry с architectureClusters на нём и исходной positions Map.
 * Старые перемешанные anchors могут требовать несколько островов одного типа вместо общего перекрывающего бокса.
 */
export function layoutArchitectureClusters(geometry, registry, {nodes, pinnedIds = new Set(), previousPositions = null}) {
  const positions = geometry instanceof Map ? geometry : geometry.positions;
  const pins = new Set([...pinnedIds].slice(0, 24));
  const pinnedPositions = registry.pinnedPositions instanceof Map ? registry.pinnedPositions : new Map();
  registry.pinnedPositions = pinnedPositions;
  for (const id of pinnedPositions.keys()) if (!pins.has(id)) pinnedPositions.delete(id);
  const definitions = new Map(nodes.map(node => [String(node.id), node]));
  const items = [];
  for (const [id, point] of positions) {
    const node = definitions.get(String(id));
    const owner = ['project', 'code-scope', 'external-project'].includes(node?.kind);
    items.push({id: String(id), point, kind: architectureResourceKind(node) || (owner ? 'owners' : 'other'), ...measure(point)});
  }
  items.sort((a, b) => KINDS.indexOf(a.kind) - KINDS.indexOf(b.kind) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const occupied = new Map();
  for (const [id, cached] of registry) occupied.set(String(id), {...cached, kind: cached.clusterKind || null});
  // Hidden pins reserve their last known body/label footprint without evicting any ordinary cached center.
  for (const [id, anchor] of pinnedPositions) {
    if (!occupied.has(id)) occupied.set(id, {...anchor, kind: anchor.clusterKind || null});
  }
  const fixed = [], pending = [], groups = new Map();
  for (const item of items) {
    if (!groups.has(item.kind)) groups.set(item.kind, []);
    groups.get(item.kind).push(item);
    const cached = registry.get(item.id);
    const anchor = cached || (pins.has(item.id) && (pinnedPositions.get(item.id) || previousPositions?.get(item.id)));
    if (anchor) {
      item.x = anchor.x; item.y = anchor.y;
      item.point.x = item.x; item.point.y = item.y;
      const saved = {...anchor, x: item.x, y: item.y,
        width: Math.max(item.width, cached?.width || 0), above: Math.max(item.above, cached?.above || 0),
        below: Math.max(item.below, cached?.below || 0), clusterKind: item.kind};
      saved.height = saved.above + saved.below;
      if (cached || registry.size < 240) registry.set(item.id, saved);
      if (pins.has(item.id)) pinnedPositions.set(item.id, {x: saved.x, y: saved.y, width: saved.width,
        height: saved.height, above: saved.above, below: saved.below, clusterKind: item.kind});
      occupied.set(item.id, {...saved, kind: item.kind}); fixed.push(item);
    } else pending.push(item);
  }
  const islands = [];
  // Restore every anchor before considering new nodes; collection order cannot hide a later fixed obstacle.
  for (const item of fixed) {
    addToIsland(islands, item, matchingIsland(item, islands, occupied));
  }
  const grids = initialGrids(groups);
  for (const item of pending) {
    const grid = grids.get(item.kind);
    let chosen = null, selectedIsland = null;
    const candidates = islands.filter(island => island.kind === item.kind);
    if (!candidates.length) candidates.push(null);
    let attempts = 0;
    for (const island of candidates) {
      const left = island?.box.left ?? grid.left, top = island?.box.top ?? grid.top;
      // The budget covers all islands together, so interleaved cached anchors cannot multiply the search.
      for (let slot = 0; attempts < 480; slot++, attempts++) {
        const candidate = {...item, x: left + PADDING + grid.width / 2 + slot % grid.columns * (grid.width + GAP),
          y: top + HEADING + PADDING + grid.above + Math.floor(slot / grid.columns) * (grid.above + grid.below + GAP)};
        if (fits(candidate, island, occupied, islands)) { chosen = candidate; selectedIsland = island; break; }
      }
      if (chosen) break;
    }
    if (!chosen) {
      // A new disjoint island avoids forcing a box across old anchors from another semantic type.
      const right = Math.max(0, ...[...occupied.values()].map(point => bounds(point).right), ...islands.map(island => island.box.right));
      chosen = {...item, x: right + GROUP_GAP + PADDING + item.width / 2, y: HEADING + PADDING + item.above};
    }
    item.point.x = chosen.x; item.point.y = chosen.y;
    occupied.set(item.id, chosen);
    const saved = {x: chosen.x, y: chosen.y, width: item.width,
      height: item.height, above: item.above, below: item.below, clusterKind: item.kind};
    if (registry.size < 240) registry.set(item.id, saved);
    if (pins.has(item.id)) pinnedPositions.set(item.id, saved);
    addToIsland(islands, chosen, selectedIsland);
  }
  const clusters = islands.map(island => ({kind: island.kind, label: LABELS[island.kind],
    x: island.box.left, y: island.box.top, width: island.box.right - island.box.left,
    height: island.box.bottom - island.box.top, nodeIds: island.points.map(point => point.id).sort()}));
  clusters.sort((a, b) => KINDS.indexOf(a.kind) - KINDS.indexOf(b.kind) || a.x - b.x || a.y - b.y);
  positions.architectureClusters = clusters;
  geometry.architectureClusters = clusters;
  return geometry;
}
