/** Переводит геометрию обычной карты в независимые прямоугольники заголовков. */
export function inlineBaseBounds(positions) {
  const result = new Map();
  for (const [id, point] of positions || []) {
    const box = point.headerBounds || point.panelBounds || point.cardBounds;
    let bounds;
    if (box) {
      bounds = { x: box.x ?? box.left, y: box.y ?? box.top,
        width: box.width ?? box.right - box.left, height: box.height ?? box.bottom - box.top };
    } else if (point.width && point.height) {
      // Flow и raw topology используют центр прямоугольника, даже если имеют labelBottom.
      bounds = { x: point.x - point.width / 2, y: point.y - point.height / 2,
        width: point.width, height: point.height };
    } else {
      const width = point.labelWidth || Math.max(160, (point.r || 0) * 2);
      const height = (point.r || 0) + (point.labelBottom || point.r || 112);
      bounds = { x: point.x - width / 2, y: point.y - (point.r || 0), width, height };
    }
    if (Object.values(bounds).every(Number.isFinite)) {
      if (point.cycleId) bounds.cycleId = point.cycleId;
      result.set(String(id), bounds);
    }
  }
  return result;
}

/** Направление разделения берётся из исходной карты, а не из размеров раскрытых рамок. */
function separation(a, b) {
  const gapX = Math.max(b.x - a.x - a.width, a.x - b.x - b.width);
  const gapY = Math.max(b.y - a.y - a.height, a.y - b.y - b.height);
  let axis;
  if (gapX >= 0 && gapY < 0) axis = 'x';
  else if (gapY >= 0 && gapX < 0) axis = 'y';
  else axis = gapX / Math.max(a.width, b.width) > gapY / Math.max(a.height, b.height) ? 'x' : 'y';
  return { axis, gap: Math.max(0, Math.min(40, axis === 'x' ? gapX : gapY)) };
}

/**
 * Расширяет свободное место без упаковки в новую сетку.
 * Каждая пара сохраняет исходное направление: слева/справа или сверху/снизу.
 * Ограничения ацикличны по исходной координате, поэтому один проход на ось
 * даёт минимальные сдвиги вправо/вниз; сворачивание возвращает исходные места.
 * gapX/gapY задают минимальный свободный промежуток в пикселях; по умолчанию сохраняется прежний зазор.
 * O(n²), n ограничен количеством видимых корневых карточек.
 */
export function reserveInlineSpace(boxes, baseline, { gapX = 0, gapY = 0 } = {}) {
  const ids = boxes.map(box => box.id).filter(id => baseline.has(id));
  const sizes = new Map(boxes.map(box => [box.id, box]));
  const points = new Map(ids.map(id => [id, { ...baseline.get(id) }]));
  const constraints = { x: [], y: [] };
  for (let first = 0; first < ids.length; first++) {
    for (let second = first + 1; second < ids.length; second++) {
      const a = ids[first], b = ids[second];
      const rule = separation(baseline.get(a), baseline.get(b));
      const before = baseline.get(a)[rule.axis] < baseline.get(b)[rule.axis]
        || baseline.get(a)[rule.axis] === baseline.get(b)[rule.axis] && String(a).localeCompare(String(b), 'en') < 0;
      const minimum = rule.axis === 'x' ? gapX : gapY;
      constraints[rule.axis].push({ source: before ? a : b, target: before ? b : a, gap: Math.max(rule.gap, minimum) });
    }
  }
  for (const axis of ['x', 'y']) {
    const ordered = [...ids].sort((a, b) => baseline.get(a)[axis] - baseline.get(b)[axis] || String(a).localeCompare(String(b), 'en'));
    const incoming = new Map(ids.map(id => [id, []]));
    for (const rule of constraints[axis]) incoming.get(rule.target).push(rule);
    const dimension = axis === 'x' ? 'width' : 'height';
    for (const id of ordered) {
      for (const rule of incoming.get(id)) {
        const source = points.get(rule.source);
        points.get(id)[axis] = Math.max(points.get(id)[axis], source[axis] + sizes.get(rule.source)[dimension] + rule.gap);
      }
    }
  }
  return points;
}
