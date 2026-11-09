/** Return a visible overlay box, including visibility inherited from a collapsed parent. */
function visibleOverlay(element) {
  if (!element || element.hidden || element.closest?.('[hidden]')) return null;
  const box = element.getBoundingClientRect?.();
  return box?.width > 0 && box?.height > 0 ? box : null;
}

/** Restore an entirely offscreen canvas after native focus scrolling, before measuring its actions. */
export function ensureCanvasVisible(svg) {
  const box = svg?.getBoundingClientRect?.();
  const win = svg?.ownerDocument?.defaultView || globalThis;
  if (!box || !win.innerHeight || !svg.scrollIntoView) return false;
  if (box.bottom > 0 && box.top < win.innerHeight) return false;
  svg.scrollIntoView({ block: 'start', inline: 'nearest', behavior: 'instant' });
  return true;
}

/** Visible CSS-pixel canvas space excludes actual drawers, the sticky toolbar and lower controls. */
export function canvasViewportRect(svg) {
  const rect = svg.getBoundingClientRect?.();
  if (!rect) return { x: 0, y: 0, width: svg.clientWidth, height: svg.clientHeight };
  const doc = svg.ownerDocument || globalThis.document, win = doc?.defaultView || globalThis;
  let left = Math.max(rect.left, 0), right = Math.min(rect.right, win.innerWidth || rect.right);
  let top = Math.max(rect.top, 0), bottom = Math.min(rect.bottom, win.innerHeight || rect.bottom);
  const overlays = [
    { side: 'left', element: doc?.getElementById?.('workspace-left') },
    { side: 'right', element: doc?.getElementById?.('workspace-right') },
    { side: 'top', element: doc?.querySelector?.('.workspace-controls') },
    { side: 'bottom', element: doc?.querySelector?.('.map-controls') },
  ];
  for (const { side, element } of overlays) {
    const box = visibleOverlay(element);
    if (!box || box.right <= left || box.left >= right || box.bottom <= top || box.top >= bottom) continue;
    if (side === 'left') left = Math.max(left, box.right);
    else if (side === 'right') right = Math.min(right, box.left);
    else if (side === 'top') top = Math.max(top, box.bottom);
    else bottom = Math.min(bottom, box.top - 8);
  }
  // A crowded viewport must not fall back to an area covered by a drawer.
  return { x: left - rect.left, y: top - rect.top,
    width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
}

/** Keyboard focus pans an obscured action into view, preserving zoom and its exact event target. */
export function revealCanvasAction(view, control) {
  ensureCanvasVisible(view.svg);
  const box = control.getBoundingClientRect?.(), svg = view.svg?.getBoundingClientRect?.();
  if (!box || !svg || !box.width || !box.height || !view.camera) return false;
  const area = view.getCanvasViewportRect?.() || canvasViewportRect(view.svg);
  if (area.width < 36 || area.height < 36) return false;
  const left = box.left - svg.left, right = box.right - svg.left;
  const top = box.top - svg.top, bottom = box.bottom - svg.top;
  const dx = axisRevealDelta(left, right, area.x + 18, area.x + area.width - 18);
  const dy = axisRevealDelta(top, bottom, area.y + 18, area.y + area.height - 18);
  if (!dx && !dy) return false;
  view.camera.x += dx; view.camera.y += dy;
  view.transform(); view.onCameraChange?.();
  return true;
}

/** Oversized controls anchor their leading edge instead of alternating between two clipped sides. */
function axisRevealDelta(start, end, minimum, maximum) {
  if (end - start > maximum - minimum) return minimum - start;
  if (end > maximum) return maximum - end;
  if (start < minimum) return minimum - start;
  return 0;
}

/** Focus an exact visible occurrence; no same-name method substitutes for a missing ID. */
export function focusCanvasOccurrence(view, id, { camera, center = false } = {}) {
  const item = view.nodeElements?.get(id), point = view.positions?.get(id);
  if (camera) { view.camera = { ...camera }; view.transform(); }
  if (!item && !point) return false;
  if (center && point) {
    const area = view.getCanvasViewportRect?.() || canvasViewportRect(view.svg);
    if (area.width > 0 && area.height > 0) {
      view.camera.x = area.x + area.width / 2 - point.x * view.camera.scale;
      view.camera.y = area.y + area.height / 2 - point.y * view.camera.scale;
      view.transform();
    }
  }
  item?.group?.focus?.({ preventScroll: true });
  if (item?.group) revealCanvasAction(view, item.group);
  view.onCameraChange?.();
  return true;
}
