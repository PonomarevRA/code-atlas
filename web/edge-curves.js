/**
 * Две опоры на 1/3 и 2/3 пролёта. Боковые порты задают горизонтальные
 * касательные, верхние/нижние — вертикальные, свободные связи следуют прямой.
 * offset смещает обе опоры для общего коридора; промежуточного узла в центре нет.
 */
export function connectionControls(from, to, { axis = 'horizontal', offset = { x: 0, y: 0 } } = {}) {
  const dx = to.x - from.x, dy = to.y - from.y;
  const first = { x: from.x + dx / 3, y: from.y + dy / 3 };
  const second = { x: from.x + 2 * dx / 3, y: from.y + 2 * dy / 3 };
  if (axis === 'horizontal') { first.y = from.y; second.y = to.y; }
  else if (axis === 'vertical') { first.x = from.x; second.x = to.x; }
  for (const point of [first, second]) { point.x += offset.x; point.y += offset.y; }
  return [first, second];
}

/** Одна кубическая связь сохраняет порты и непрерывный изгиб без выравнивания в середине. */
export function connectionCurve(from, to, options) {
  const [first, second] = connectionControls(from, to, options);
  return `M ${from.x} ${from.y} C ${first.x} ${first.y} ${second.x} ${second.y} ${to.x} ${to.y}`;
}
