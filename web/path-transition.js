/** Короткие переходы SVG после раскрытия не меняют итоговую геометрию и факты карты. */
const DURATION = 280;

/** Тип карточки задаёт начало её SVG transform: условия центрированы, остальные — от левого верха. */
function cardOrigin(group, point) {
  const centered = group.classList.contains('path-decision') || group.classList.contains('path-entry-marker')
    || group.classList.contains('inline-hierarchy-card') || group.classList.contains('inline-method-card');
  return { x: point.x - (centered ? 0 : point.w / 2), y: point.y - (centered ? 0 : point.h / 2) };
}

/** Снимок текущих карточек нужен только на время действия; завершённые эффекты не сохраняем. */
export function capturePathTransition(view) {
  const positions = new Map(view.positions);
  // Повторное нажатие во время эффекта начинает следующий переход от видимого места.
  for (const [id, item] of view.nodeElements) {
    if (!item.group.getAnimations?.().some(animation => animation.playState === 'running')) continue;
    const transform = globalThis.getComputedStyle?.(item.group).transform;
    if (!transform?.startsWith('matrix(')) continue;
    const values = transform.slice(7, -1).split(',').map(Number), point = positions.get(id);
    if (!point || values.length !== 6 || values.some(value => !Number.isFinite(value))) continue;
    const origin = cardOrigin(item.group, point);
    positions.set(id, { ...point, x: values[4] + point.x - origin.x, y: values[5] + point.y - origin.y });
  }
  return positions;
}

/**
 * Анимирует перестроение от прежних экранных координат после компенсации камеры.
 * Новые детали проявляются с небольшим смещением; линии мягко появляются на новой геометрии.
 * Нажатая карточка стоит на месте. Без Web Animations или при reduced-motion эффекта нет.
 * Лимиты 160 карточек и 320 линий/подписей ограничивают стоимость эффекта; итоговые узлы сохраняются.
 */
export function animatePathTransition(view, before, offset = { x: 0, y: 0 }) {
  if (globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
  let count = 0;
  for (const [id, item] of view.nodeElements) {
    const group = item.group, point = view.positions.get(id);
    if (!point || !group.animate || count >= 160) continue;
    const to = cardOrigin(group, point), previous = before.get(id);
    const from = previous ? cardOrigin(group, previous) : { x: to.x - 20, y: to.y + 6 };
    if (previous) { from.x -= offset.x; from.y -= offset.y; }
    if (from.x === to.x && from.y === to.y) continue;
    const opacity = globalThis.getComputedStyle?.(group).opacity ?? '1';
    group.animate([
      { transform: `translate(${from.x}px, ${from.y}px)`, opacity: previous ? opacity : 0 },
      { transform: `translate(${to.x}px, ${to.y}px)`, opacity },
    ], { duration: DURATION, easing: 'cubic-bezier(.2,.8,.2,1)' });
    count++;
  }
  const connections = [...view.viewport.querySelectorAll('.path-edge'), ...view.viewport.querySelectorAll('.path-edge-label'), ...view.viewport.querySelectorAll('.inline-method-edge'), ...view.viewport.querySelectorAll('.inline-hierarchy-edge')];
  for (const path of connections.slice(0, 320)) {
    path.animate?.([{ opacity: 0 }, { opacity: globalThis.getComputedStyle?.(path).opacity ?? '1' }],
      { duration: DURATION, easing: 'ease-out' });
  }
}
