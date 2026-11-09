/** Открытое пересечение включает линии нулевой толщины внутри прямоугольника, но не его край. */
function overlaps(a, b) {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}

/** Оболочка карточки в координатах SVG; исходный объект остаётся значением индекса. */
export function cardRectangle(card) {
  return { left: card.x - card.w / 2, right: card.x + card.w / 2,
    top: card.y - card.h / 2, bottom: card.y + card.h / 2 };
}

/** Строит сбалансированное дерево оболочек один раз, без изменения порядка входных объектов. */
function buildTree(items) {
  if (!items.length) return null;
  const bounds = { left: Infinity, right: -Infinity, top: Infinity, bottom: -Infinity };
  for (const { box } of items) {
    bounds.left = Math.min(bounds.left, box.left); bounds.right = Math.max(bounds.right, box.right);
    bounds.top = Math.min(bounds.top, box.top); bounds.bottom = Math.max(bounds.bottom, box.bottom);
  }
  if (items.length <= 8) return { bounds, items };
  const horizontal = bounds.right - bounds.left >= bounds.bottom - bounds.top;
  items.sort((a, b) => horizontal ? a.box.left + a.box.right - b.box.left - b.box.right
    : a.box.top + a.box.bottom - b.box.top - b.box.bottom);
  const middle = Math.floor(items.length / 2);
  return { bounds, children: [buildTree(items.slice(0, middle)), buildTree(items.slice(middle))] };
}

/**
 * Отбирает только близкие препятствия; точная проверка кривой остаётся у маршрутизатора.
 * Координаты объектов неизменны в течение одной сцены. Новые подписи добавляются отдельно.
 */
export class RectangleIndex {
  constructor(values, rectangle = value => value) {
    this.rectangle = rectangle;
    this.root = buildTree(Array.from(values, value => ({ value, box: rectangle(value) })));
    this.added = [];
  }

  /** Учитывает новую подпись немедленно; небольшая очередь периодически объединяется в дерево. */
  add(value) {
    this.added.push({ value, box: this.rectangle(value) });
    if (this.added.length < 64) return;
    const items = [...this.added], pending = this.root ? [this.root] : [];
    while (pending.length) {
      const node = pending.pop();
      if (node.items) items.push(...node.items);
      else pending.push(...node.children);
    }
    this.root = buildTree(items);
    this.added = [];
  }

  /** Возвращает кандидатов без пропуска открытых пересечений, включая горизонтальные и вертикальные линии. */
  query(box) {
    const result = [], pending = this.root ? [this.root] : [];
    while (pending.length) {
      const node = pending.pop();
      if (!overlaps(box, node.bounds)) continue;
      if (node.items) {
        for (const item of node.items) if (overlaps(box, item.box)) result.push(item.value);
      } else pending.push(...node.children);
    }
    for (const item of this.added) if (overlaps(box, item.box)) result.push(item.value);
    return result;
  }
}
