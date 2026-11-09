/** Возвращает смещение и доступную высоту popup в CSS-пикселях без изменения его якоря. */
export function menuViewportPlacement(rect, width, height, padding = 12) {
  let shiftX = 0;
  if (rect.left < padding) shiftX = padding - rect.left;
  else if (rect.right > width - padding) shiftX = width - padding - rect.right;
  return {shiftX, maxHeight:Math.max(0, height - rect.top - padding)};
}
