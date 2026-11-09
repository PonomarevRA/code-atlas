/** Выбирает место подписи вне карточек и подписей; необязательный индекс принадлежит текущей геометрии сцены. */
export function entryPathCaption(path, caption, positions, occupied, cardIndex = null) {
  const text = caption.length > 28 ? `${caption.slice(0, 27)}…` : caption;
  const commands = path.match(/[MC][^MC]*/g) || [];
  let from;
  for (const command of commands) {
    const values = command.slice(1).match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);
    const end = { x: values.at(-2), y: values.at(-1) };
    if (command[0] === 'C' && from) {
      for (const t of [.5, .25, .75]) {
        const u = 1 - t;
        const x = u ** 3 * from.x + 3 * u ** 2 * t * values[0] + 3 * u * t ** 2 * values[2] + t ** 3 * end.x;
        const curveY = u ** 3 * from.y + 3 * u ** 2 * t * values[1] + 3 * u * t ** 2 * values[3] + t ** 3 * end.y;
        for (const offset of [-10, 24]) {
          const y = curveY + offset;
          const box = { left: x - text.length * 3.5, right: x + text.length * 3.5, top: y - 12, bottom: y + 3 };
          let clear = true;
          for (const card of cardIndex ? cardIndex.query(box) : positions.values()) {
            if (box.left < card.x + card.w / 2 && box.right > card.x - card.w / 2
                && box.top < card.y + card.h / 2 && box.bottom > card.y - card.h / 2) { clear = false; break; }
          }
          if (!clear) continue;
          for (const label of occupied) {
            if (box.left < label.right && box.right > label.left && box.top < label.bottom && box.bottom > label.top) { clear = false; break; }
          }
          if (clear) { occupied.push(box); return { x, y, text }; }
        }
      }
    }
    from = end;
  }
  // В плотной области полная подпись остаётся в title связи и списке переходов.
  return null;
}

/** Свободный arm заканчивается под условием; две дуги оставляют его подпись вне карточки. */
export function entryPathStub(source) {
  const right = source.x + source.w / 2, below = source.y + source.h / 2 + 22;
  return `M ${right} ${source.y} C ${right + 32} ${source.y} ${right + 32} ${below} ${right} ${below}`
    + ` C ${right - source.w / 6} ${below} ${source.x + source.w / 6} ${below} ${source.x} ${below}`;
}
